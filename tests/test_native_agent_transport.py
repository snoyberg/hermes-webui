"""Real loopback/Unix transport, AST-extracted native owners, no live bootstrap.

Set HERMES_NATIVE_CONTRACT_SOURCE to the read-only Agent checkout to exercise
its actual ticket, endpoint and action response contracts. No application import.
"""
import ast
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import socketserver
import sys
import subprocess
import tempfile
import threading
import time
import types
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from api import native_agent_update as native


def _extract(path, names, scope):
    nodes = []
    for node in ast.parse(path.read_text()).body:
        if getattr(node, 'name', None) in names:
            node.decorator_list = []
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope


def _module(monkeypatch, name, scope=None):
    value = types.ModuleType(name)
    value.__dict__.update(scope or {})
    monkeypatch.setitem(sys.modules, name, value)
    return value


@pytest.fixture
def transport(monkeypatch, request):
    source = os.getenv('HERMES_NATIVE_CONTRACT_SOURCE')
    if not source:
        pytest.skip('set HERMES_NATIVE_CONTRACT_SOURCE for real native contract gate')
    source = Path(source)
    # A short disposable path fits AF_UNIX even when pytest's root is long.
    with tempfile.TemporaryDirectory(prefix='n181-', dir=Path.cwd().parent) as directory:
        home = Path(directory)
        home.chmod(0o700)
        install = home / 'source'
        (install/'hermes_cli').mkdir(parents=True)
        (install/'hermes_bootstrap.py').write_text('')
        (install/'hermes_cli/__init__.py').write_text('')
        (install/'hermes_cli/main.py').write_text('import time; time.sleep(120)')
        launcher = _extract(source/'hermes_cli/_launchers.py',
                            {'runtime_command', '_inline_string_literal'},
                            {'Path': Path, 'os': os, 'sys': sys, 'PIN_DEFAULT_HOME_FLAG': '_hermes_pin_default_home'})
        command = launcher['runtime_command'](install, ['gateway', 'run'], python=sys.executable, home=home)
        working = _extract(source/'hermes_cli/gateway.py', {'_stable_service_working_dir'},
                           {'Path': Path, 'get_hermes_home': lambda: home, 'PROJECT_ROOT': install})
        child = subprocess.Popen(command, cwd=working['_stable_service_working_dir']())
        assert Path(f'/proc/{child.pid}/cwd').resolve() == home != install
        request.addfinalizer(lambda: (child.terminate(), child.wait(timeout=5)))
        monkeypatch.setattr(native, '_installation', lambda: (install, home))
        package = _module(monkeypatch, 'hermes_cli', {'__path__': []})
        _module(monkeypatch, 'gateway', {'__path__': []})
        _module(monkeypatch, 'gateway.drain_control', {'RESTART_WAIT_REASON': 'restart_wait'})
        for name, relative in [('hermes_cli.gateway_runtime', 'hermes_cli/gateway_runtime.py'),
                               ('hermes_cli.gateway_runtime_discovery', 'hermes_cli/gateway_runtime_discovery.py'),
                               ('gateway.runtime_bootstrap', 'gateway/runtime_bootstrap.py')]:
            module = _module(monkeypatch, name, {'__file__': str(source/relative)})
            # These three modules contain only stdlib imports and definitions.
            exec(compile((source/relative).read_text(), str(source/relative), 'exec'), module.__dict__)
        discovery = sys.modules['hermes_cli.gateway_runtime_discovery']
        client = _module(monkeypatch, 'hermes_cli.gateway_client', {'__file__': str(install/'hermes_cli/gateway_client.py'),
            'os': os, 'time': time, 'json': json, 'Path': Path, '_UNKNOWN_TICKET_PARAMS': 'PermissionError: invalid bootstrap parameters'})
        _extract(source/'hermes_cli/gateway_client.py', {'GatewayClientError','private_control_query','_session_ticket'}, client.__dict__)
        package.gateway_client = client
        store = sys.modules['gateway.runtime_bootstrap'].TicketStore('fixture-owner', frozenset({str(home)}))
        issued = []
        # Native control handler method, no server/startup module import.
        control_scope = {'Optional': Optional}
        tree = ast.parse((source/'gateway/control_socket.py').read_text())
        method = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == 'GatewayControlServer'
                      for n in c.body if isinstance(n, ast.FunctionDef) and n.name == '_session_ticket')
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<native-ticket-owner>', 'exec'), control_scope)
        owner = types.SimpleNamespace(ticket_store=store)
        payload = {'pid': child.pid, 'kind': 'hermes-gateway', 'hermes_home': str(home),
                   'runtime_protocol': 1, 'authority_epoch': 1, 'instance_id': 'fixture-owner',
                   'state': 'ready', 'supervisor': 'none', 'capabilities': ['session-authority-v1'],
                   'served_profiles': [{'home': str(home), 'profile_id': str(home)}]}
        class Private(socketserver.StreamRequestHandler):
            def handle(self):
                assert discovery.socket_peer_uid(self.request) == os.getuid()
                request = json.loads(self.rfile.readline(65536))
                if request['verb'] == 'identify':
                    result = dict(payload)
                else:
                    assert request['verb'] == 'session-ticket'
                    issued.append(dict(request['params']))  # no ticket value retained
                    result = control_scope['_session_ticket'](owner, request, 'uid:fixture')
                self.wfile.write(json.dumps({'ok': True, 'protocol': 1, 'id': 1, 'result': result}).encode()+b'\n')
        private = socketserver.ThreadingUnixStreamServer(str(home/'gateway.sock'), Private)
        (home/'gateway.sock').chmod(0o600)
        # The repo guard only understands (host, port); permit this ONE
        # task-owned Unix socket, retaining its ordinary network guard otherwise.
        import socket
        from tests.conftest import _REAL_SOCKET_CONNECT
        guarded_connect = socket.socket.connect
        def connect(sock, address):
            if sock.family == socket.AF_UNIX and address == str(home/'gateway.sock'):
                return _REAL_SOCKET_CONNECT(sock, address)
            return guarded_connect(sock, address)
        monkeypatch.setattr(socket.socket, 'connect', connect)
        state = {'receipt': None, 'running': True, 'posts': 0, 'http': [], 'redirect': False,
                 'source': {'behind': 2, 'channel': 'stable', 'sourceVersion': '0.22.6', 'commits': []}}
        action_scope = {'Any': Any, 'Optional': Optional, 'Path': Path, 're': re, 'asyncio': asyncio,
                        'contextlib': contextlib, 'secrets': secrets,
                        '_server_path': lambda name: install if name == 'PROJECT_ROOT' else home,
                        'is_commit_build': lambda root: False, '_dashboard_local_update_managed_externally': lambda: False,
                        'http_failure': lambda *args: contextlib.nullcontext(),
                        'detect_install_method': lambda root: 'git', 'recommended_update_command_for_method': lambda method: 'hermes update',
                        'get_version_info': lambda: types.SimpleNamespace(derived_version='0.22.5'),
                        '_config_profile_scope': lambda profile: contextlib.nullcontext(),
                        '_NON_APPLYABLE_MESSAGES': {}, '_ACTION_PROCS': {}, '_ACTION_IDS': {}, '_ACTION_RESULTS': {},
                        '_ACTION_LOG_FILES': {'hermes-update': 'action.log'}, '_UPDATE_REFUSAL_ERROR_CODES': {},
                        '_UPDATE_ACTION_STARTED_RE': re.compile(r'^=== hermes-update started .+ ([0-9a-f]{32}) ===$'),
                        '_UPDATE_ACTION_COMPLETED_RE': re.compile(r'^=== hermes-update completed ([0-9a-f]{32}) ===$'),
                        '_record_completed_action': lambda *args, **kw: None,
                        '_read_latest_receipt': lambda: state['receipt'],
                        '_tail_lines': lambda path, lines: path.read_text().splitlines()[-lines:] if path.exists() else []}
        _module(monkeypatch, 'hermes_cli.update_contract', {'evaluate_update_admission': lambda root: state.get('admission'), 'record_refusal_receipt': lambda value: None})
        def source_check(**kwargs):
            state.setdefault('source_calls', []).append(kwargs)
            return state['source']
        _module(monkeypatch, 'hermes_cli.source_check', {'check_for_updates': source_check})
        _module(monkeypatch, 'hermes_cli.update_receipt', {'read_receipt_for_action': lambda action_id: state['receipt'] if (state['receipt'] or {}).get('action_id') == action_id else None})
        _module(monkeypatch, 'hermes_cli.logs', {'log_file_path': lambda name: home/'update.log'})
        def spawn(argv, name, *, env_overrides):
            assert argv == ['update'] and name == 'hermes-update'
            state['posts'] += 1
            action_id = env_overrides['HERMES_ACTION_ID']
            (home/'action.log').write_text('=== hermes-update started 2026-11-02 18:00:00 '+action_id+' ===\n')
            (home/'hermes-update.action_id').write_text(action_id)
            proc = types.SimpleNamespace(pid=42, poll=lambda: None if state['running'] else 0, wait=lambda **kw: None)
            action_scope['_ACTION_PROCS'][name] = proc
            action_scope['_ACTION_IDS'][name] = action_id
            return proc
        action_scope['_spawn_hermes_action'] = spawn
        action_scope['_finish_action'] = lambda *args: None
        _extract(source/'hermes_cli/web_routers/actions.py', {
            'update_hermes', 'check_hermes_update', '_source_release_label', '_update_refused',
            'get_action_status', '_completed_exit_code', '_latest_update_receipt_summary',
            '_latest_spawned_update_action_id', '_durable_completed_update_action_id', '_persisted_action_id'}, action_scope)
        class HTTP(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.dispatch()
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.dispatch()
            def dispatch(self):
                assert 'Origin' not in self.headers
                grant = store.redeem(self.headers['x-hermes-gateway-ticket'], profile_id=str(home), purpose='native-http')
                assert grant['capabilities'] == frozenset({'http:owner'})
                state['http'].append((self.command, self.path))
                if state['redirect']:
                    self.send_response(302); self.send_header('Location', 'http://127.0.0.1:1/credential-trap'); self.end_headers(); return
                path = urlsplit(self.path).path
                if self.command == 'POST' and path == '/api/hermes/update':
                    result = asyncio.run(action_scope['update_hermes']())
                elif path == '/api/hermes/update/check':
                    force = parse_qs(urlsplit(self.path).query).get('force') == ['true']
                    result = asyncio.run(action_scope['check_hermes_update'](force=force))
                elif path == '/api/actions/hermes-update/status':
                    result = asyncio.run(action_scope['get_action_status']('hermes-update', lines=2000))
                else:
                    raise AssertionError('not allowlisted')
                body = json.dumps(result).encode()
                self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        http = ThreadingHTTPServer(('127.0.0.1', 0), HTTP)
        payload['api_origin'] = 'http://127.0.0.1:'+str(http.server_port)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (private, http)]
        for thread in threads:
            thread.start()
        try:
            yield state, issued, payload, home
        finally:
            for server in (private, http):
                server.shutdown(); server.server_close()
            for thread in threads:
                thread.join(3)


def test_root_home_cwd_gateway_serves_named_profile(transport, monkeypatch):
    state, issued, payload, control = transport
    profile = control/'profiles'/'named'
    profile.mkdir(parents=True)
    install, _ = native._installation()
    monkeypatch.setattr(native, '_installation', lambda: (install, profile))
    payload['served_profiles'].append({'home': str(profile), 'profile_id': str(profile)})
    # Owner discovery only: no ticket or native API request for this layout proof.
    home, endpoint = native._owner()
    assert home == profile
    assert endpoint.profile_id == str(profile)
    assert issued == [] and state['http'] == []


def test_manual_force_reaches_native_source_check_and_cached_get_is_inert(transport, monkeypatch):
    import api.updates as updates
    state, issued, _, _ = transport
    monkeypatch.setattr(updates, '_update_cache', {'agent': None, 'webui': None, 'checked_at': 0})
    monkeypatch.setattr(updates, '_check_repo', lambda *args: {'name': 'webui', 'behind': 0})
    assert updates.check_for_updates()['agent']['behind'] == 2
    assert state['source_calls'][-1]['force'] is False
    assert updates.check_for_updates(force=True)['agent']['behind'] == 2
    assert state['source_calls'][-1]['force'] is True
    assert state['http'] == [('GET', '/api/hermes/update/check'),
                             ('GET', '/api/hermes/update/check?force=true')]
    updates.cached_update_status()
    assert len(issued) == len(state['http']) == 2


def test_real_native_ticket_http_check_submit_duplicate_matching_outcome(transport):
    state, issued, payload, home = transport
    offer = native.check()
    assert offer['native'] and offer['behind'] == 2 and offer['channel'] == 'stable'
    accepted = native.submit()
    assert accepted['status'] == 'accepted'
    duplicate = native.submit()
    assert duplicate['already_running'] and duplicate['action_id'] == accepted['action_id']
    assert state['posts'] == 1  # actual native process-local reuse, not durable idempotency
    assert native.status(accepted['action_id'], accepted['owner'])['status'] == 'running'
    state['running'] = False
    state['receipt'] = {'action_id': accepted['action_id'], 'outcome': 'success',
                        'followups': [{'step': 'dependencies', 'reason': 'manual repair'}]}
    result = native.status(accepted['action_id'], accepted['owner'])
    assert result['status'] == 'success' and result['followups'][0]['step'] == 'dependencies'
    assert all(p['purpose'] == 'native-http' and p['profile_id'] == str(home) for p in issued)
    assert len(issued) == len(state['http']) == 5
    assert not any('ticket' in str(value) for value in (offer, accepted, result))


def test_real_native_latest_unrelated_receipt_is_unknown_and_no_replay(transport):
    state, _, _, home = transport
    accepted = native.submit()
    state['running'] = False
    state['receipt'] = {'action_id': 'b'*32, 'outcome': 'success'}
    result = native.status(accepted['action_id'], accepted['owner'])
    assert result['status'] == 'unknown' and state['posts'] == 1
    (home/'action.log').write_text('=== hermes-update started 2026-11-02 18:00:00 '+ 'b'*32+' ===\n')
    assert native.status(accepted['action_id'], accepted['owner'])['status'] == 'superseded'
    assert state['posts'] == 1


def test_native_redirect_never_forwards_ticket_or_retries(transport):
    state, issued, _, _ = transport
    state['redirect'] = True
    assert native.submit()['status'] == 'unknown'
    assert len(issued) == len(state['http']) == 1
    assert state['posts'] == 0


def test_wrong_installation_refuses_before_ticket(transport, monkeypatch):
    state, issued, _, home = transport
    monkeypatch.setattr(native, '_installation', lambda: (home, home))
    assert native.check()['status'] == 'native-maintenance-required'
    assert issued == [] and state['http'] == []


@pytest.mark.parametrize('source,status', [({'behind': None}, 'unknown'),
    ({'behind': 0, 'held': True, 'channel': 'stable'}, 'held'), ({'behind': -1}, 'available')])
def test_native_check_unknown_held_and_unknown_count(transport, source, status):
    state, _, _, _ = transport
    state['source'] = source
    result = native.check()
    assert result['status'] == status
    assert result['behind'] == source['behind']


def test_native_install_admission_refusal_is_not_accepted(transport):
    state, _, _, _ = transport
    state['admission'] = types.SimpleNamespace(code='externally-managed', message='Managed installation', update_command='')
    result = native.submit()
    assert result['status'] == 'refused' and not result['ok']
    assert 'action_id' not in result and state['posts'] == 0


@pytest.mark.parametrize('mutation', ['public-endpoint', 'foreign-profile', 'protocol',
                                     'foreign-home', 'foreign-source', 'foreign-process'])
def test_owner_identity_refusals_never_mint_ticket(transport, mutation, monkeypatch):
    state, issued, payload, home = transport
    if mutation == 'public-endpoint':
        payload['api_origin'] = 'http://192.0.2.1:9000'
    elif mutation == 'foreign-profile':
        payload['served_profiles'] = [{'profile_id': '/foreign', 'home': '/foreign'}]
    elif mutation == 'foreign-home':
        payload['hermes_home'] = str(home/'foreign')
    elif mutation == 'foreign-source':
        # Matching client module alone cannot certify a foreign launch root.
        foreign = home/'foreign'
        foreign.mkdir()
        monkeypatch.setattr(native, '_installation', lambda: (foreign, home))
        monkeypatch.setattr(sys.modules['hermes_cli.gateway_client'], '__file__', str(foreign/'hermes_cli/gateway_client.py'))
    elif mutation == 'foreign-process':
        payload['pid'] = os.getpid()
    else:
        payload['runtime_protocol'] = 2
    result = native.submit()
    assert result['status'] == 'native-maintenance-required'
    assert issued == [] and state['posts'] == 0
