"""Exercise the actual native status/reload route without server startup."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def _dispatch(path, body):
    import api.updates as updates
    source = Path(updates.__file__).with_name('routes.py').read_text()
    tree = ast.parse(source)
    block = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                 and ast.unparse(n.test) == "parsed.path in ('/api/updates/agent/status', '/api/updates/agent/reload')")
    fn = ast.parse('def dispatch(handler, parsed, body):\n    pass').body[0]
    fn.body = [block]
    scope = {'j': lambda handler, value: (200, value), 'bad': lambda handler, value: (400, value)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), '<native-route>', 'exec'), scope)
    return scope['dispatch'](None, SimpleNamespace(path=path), body)


@pytest.mark.parametrize('outcome,blocked,scheduled', [
    ('accepted', False, False), ('unknown', False, False), ('partial', False, False),
    ('failed', False, False), ('success', True, False), ('success', False, True)])
def test_separate_reload_requires_matching_success_and_existing_blockers(monkeypatch, outcome, blocked, scheduled):
    import api.updates as updates
    from api import native_agent_update as native
    monkeypatch.setattr(native, 'status', Mock(return_value={'status': outcome}))
    monkeypatch.setattr(updates, '_restart_blocker_snapshot', lambda: {'restart_blocked': blocked, 'active_runs': int(blocked)})
    restart = Mock()
    monkeypatch.setattr(updates, '_schedule_restart', restart)
    code, result = _dispatch('/api/updates/agent/reload', {'action_id': 'a'*32, 'owner': 'owner'})
    assert code == 200 and bool(result.get('restart_scheduled')) == scheduled
    assert restart.call_count == int(scheduled)
    native.status.assert_called_once_with('a'*32, 'owner')


def test_status_route_never_restarts_and_rejects_arbitrary_destination(monkeypatch):
    import api.updates as updates
    from api import native_agent_update as native
    status = Mock(return_value={'status': 'success', 'action_id': 'a'*32})
    monkeypatch.setattr(native, 'status', status)
    monkeypatch.setattr(updates, '_schedule_restart', Mock(side_effect=AssertionError('status must not restart')))
    assert _dispatch('/api/updates/agent/status', {'action_id': 'a'*32, 'owner': 'owner'})[1]['status'] == 'success'
    assert _dispatch('/api/updates/agent/status', {'url': 'http://arbitrary.invalid', 'argv': ['update']})[0] == 400
    assert status.call_count == 1
