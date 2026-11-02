"""Fixed Agent maintenance adapter; native owns admission, execution and receipts.

No gateway ensure/discovery fallback, HTTP redirects, proxy environment, retries,
credential persistence, arbitrary URL or command execution. Importing is inert.
"""
import ast
import hashlib
import os
import http.client
import ipaddress
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, urlsplit

_ACTION_ID = re.compile(r'^[0-9a-f]{32}$')
_STARTED = re.compile(r'^=== hermes-update started \d{4}-\d\d-\d\d \d\d:\d\d:\d\d ([0-9a-f]{32}) ===$')
_PATHS = {
    'check': ('GET', '/api/hermes/update/check'),
    'submit': ('POST', '/api/hermes/update'),
    'status': ('GET', '/api/actions/hermes-update/status'),
}
_MAX_REPLY = 262144


class _NotSent(ValueError):
    """Preparation refused before an HTTP action request could leave."""


def _installation():
    from api.config import _AGENT_DIR
    from api.profiles import get_active_hermes_home
    if _AGENT_DIR is None:
        raise ValueError('native unavailable')
    return Path(_AGENT_DIR).resolve(strict=True), Path(get_active_hermes_home()).resolve(strict=True)


def _identity():
    install, home = _installation()
    return hashlib.sha256((str(install) + '\0' + str(home)).encode()).hexdigest()


def _process_installation(pid, install, control):
    """Read the native isolated launcher's source binding, never execute argv.

    Native services deliberately chdir to their state home. The POSIX launcher
    and runtime_command instead bind imports with sys.path.insert(0, root).
    This proves launch provenance, not loaded-version equality after an update.
    Private peer authentication and native profile/instance grants still apply.
    """
    proc = Path(f'/proc/{pid}')
    if proc.stat().st_uid != os.getuid():
        return False
    if (proc/'cwd').resolve(strict=True) not in {install, control}:
        return False
    with (proc/'cmdline').open('rb') as stream:
        raw = stream.read(131073)
    if len(raw) > 131072:
        return False
    argv = raw.rstrip(b'\0').decode().split('\0')
    if len(argv) != 6 or argv[1:3] != ['-I', '-c'] or argv[4:] != ['gateway', 'run']:
        return False
    tree = ast.parse(argv[3])
    roots = []
    main = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'hermes_cli.main':
            main = any(alias.name == 'main' for alias in node.names)
        if not isinstance(node, ast.Call):
            continue
        if (isinstance(node.func, ast.Attribute) and node.func.attr == 'insert'
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == 'path'
                and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == 'sys'
                and len(node.args) == 2 and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == 0 and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            roots.append(Path(node.args[1].value).resolve())
        if (isinstance(node.func, ast.Attribute) and node.func.attr == 'run_module'
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == 'hermes_cli.main'):
            main = True
    return bool(roots and roots[0] == install and main)


def _owner():
    """Identify via private read-only queries and the native launch contract."""
    install, home = _installation()
    if sys.platform != 'linux':
        raise ValueError('installation proof unavailable')
    from hermes_cli import gateway_client
    from hermes_cli.gateway_runtime import _endpoint
    from hermes_cli.gateway_runtime_discovery import query_identify
    if Path(gateway_client.__file__).resolve().parents[1] != install:
        raise ValueError('native client installation mismatch')
    root = home.parent.parent if home.parent.name == 'profiles' else home
    for control in dict.fromkeys((home, root)):
        try:
            payload = query_identify(control, timeout=2)
        except (FileNotFoundError, ConnectionRefusedError):
            continue
        endpoint = _endpoint(payload, home, control)
        if endpoint.state != 'ready' or endpoint.endpoint is None:
            raise ValueError('native owner not ready')
        pid = payload.get('pid')
        if (type(pid) is not int or pid <= 1 or payload.get('kind') != 'hermes-gateway'
                or Path(payload.get('hermes_home') or '').resolve() != control
                or not _process_installation(pid, install, control)):
            raise ValueError('native installation mismatch')
        return home, endpoint.endpoint
    raise ValueError('native owner unavailable')


def _request(operation, *, force=False):
    method, path = _PATHS[operation]  # fixed server-side allowlist
    try:
        home, endpoint = _owner()
        parsed = urlsplit(endpoint.api_origin)
        if (parsed.scheme != 'http' or not parsed.hostname or not parsed.port
                or not ipaddress.ip_address(parsed.hostname).is_loopback
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('invalid native endpoint')
        from hermes_cli.gateway_client import _session_ticket
        ticket = _session_ticket(home, endpoint, purpose='native-http', refuse_restart_wait=True)
    except Exception:
        raise _NotSent('Native owner unavailable or installation identity unverified') from None
    # The grant binds task-local profile scope. Native selectors are names, NOT
    # endpoint.profile_id (a canonical home path). Omit rather than forward it.
    query = {'lines': '2000'} if operation == 'status' else {}
    if operation == 'check' and force:
        query['force'] = 'true'
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request(method, path + ('?' + urlencode(query) if query else ''),
                           body=b'{}' if method == 'POST' else None,
                           headers={'x-hermes-gateway-ticket': ticket,
                                    'Content-Type': 'application/json'})
        response = connection.getresponse()
        # Never follow redirects or relay authentication details to the browser.
        if response.status != 200:
            raise ValueError('native request refused')
        data = response.read(_MAX_REPLY + 1)
        if len(data) > _MAX_REPLY:
            raise ValueError('oversized native reply')
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError('invalid native reply')
        return result
    finally:
        connection.close()


def check(*, force=False):
    try:
        data = _request('check', force=force)
        behind = data.get('behind')
        if behind is not None and (type(behind) is not int or behind < -1):
            raise ValueError('invalid behind')
        can_apply = data.get('can_apply') is True
        available = data.get('update_available') is True
        return {'name': 'agent', 'native': True, 'owner': _identity(),
                'behind': behind, 'update_available': available, 'can_apply': can_apply,
                'status': ('unknown' if behind is None else 'held' if data.get('held') else
                           'available' if available else 'checked'),
                'message': str(data.get('message') or '')[:1000],
                'current_version': data.get('current_version'), 'channel': data.get('channel'),
                'error': None if behind is not None and (can_apply or not available) else
                         str(data.get('message') or 'Native update unavailable.'),
                'recovery': {'force': False, 'clear_lock': False}}
    except Exception:
        return {'name': 'agent', 'behind': None, 'status': 'native-maintenance-required',
                'native_maintenance_required': True,
                'error': 'Native Agent maintenance unavailable or installation identity unverified. No fallback was run.',
                'recovery': {'force': False, 'clear_lock': False}}


def submit():
    try:
        try:
            owner = _identity()
        except Exception:
            raise _NotSent('installation unavailable') from None
        data = _request('submit')
        if data.get('ok') is False:
            return {'ok': False, 'status': 'refused', 'message': str(data.get('message') or 'Native update refused.')[:1000]}
        action_id = data.get('action_id')
        if data.get('ok') is not True or not isinstance(action_id, str) or not _ACTION_ID.fullmatch(action_id):
            raise ValueError('missing accepted identity')
        return {'ok': True, 'status': 'accepted', 'action_id': action_id,
                'owner': owner, 'already_running': data.get('already_running') is True}
    except _NotSent:
        return {'ok': False, 'target': 'agent', 'status': 'native-maintenance-required',
                'native_maintenance_required': True,
                'message': 'Native Agent maintenance unavailable or installation identity unverified. No update or restart was started.'}
    except Exception:
        # May already have been accepted. Never automatically replay the POST.
        return {'ok': False, 'status': 'unknown',
                'message': 'Native acceptance unknown. Do not retry automatically; inspect native maintenance.'}


def status(action_id, owner):
    result = {'ok': False, 'status': 'unknown', 'action_id': action_id,
              'message': 'Native result unknown; no automatic retry. Native history may be unavailable.'}
    if not isinstance(action_id, str) or not _ACTION_ID.fullmatch(action_id):
        return result
    try:
        if owner != _identity():
            return result
        data = _request('status')
        receipt = data.get('receipt')
        lines = data.get('lines') or []
        latest = data.get('action_id')
        for line in reversed(lines):
            if isinstance(line, str) and line.startswith('=== hermes-update started '):
                match = _STARTED.fullmatch(line.strip())
                latest = match.group(1) if match else None
                break
        if latest and latest != action_id:
            return {**result, 'status': 'superseded', 'message': 'Another native action superseded this one; its outcome is unknown.'}
        if isinstance(receipt, dict) and receipt.get('action_id') == action_id:
            outcome = receipt.get('outcome')
            if outcome in {'success', 'partial', 'failed', 'refused'}:
                return {**result, 'ok': outcome == 'success', 'status': outcome,
                        'message': 'Native Agent outcome: ' + outcome + '. WebUI reload/recovery is separate.',
                        'followups': receipt.get('followups') or [],
                        'user_action': receipt.get('user_action')}
        if latest == action_id and data.get('running') is True:
            return {**result, 'status': 'running', 'message': 'Native Agent update running; interruption/manual repair may be required.'}
    except Exception:
        pass
    return result
