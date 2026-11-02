"""Generic Git update machinery must never own Agent maintenance."""
from unittest.mock import Mock

import pytest

import api.updates as updates


@pytest.mark.parametrize('target', ['agent', 'both'])
@pytest.mark.parametrize('operation', ['apply_update', 'apply_force_update', 'apply_clear_lock', '_apply_update_inner'])
def test_agent_mutation_refuses_before_generic_effects(monkeypatch, operation, target):
    effects = {}
    for name in ('_run_git', '_run_kaladin_git', '_fetch_channel_tags',
                 '_inventory_locks', '_schedule_restart',
                 '_ensure_gateway_restart_for_agent_update', '_restart_blocker_snapshot',
                 '_read_update_channel'):
        effects[name] = Mock(side_effect=AssertionError(f'generic effect reached: {name}'))
        monkeypatch.setattr(updates, name, effects[name])
    result = getattr(updates, operation)(target)
    assert result['ok'] is False
    assert result['status'] == 'native-maintenance-required'
    assert result['native_maintenance_required'] is True
    assert result['target'] == target
    assert 'native' in result['message'].lower()
    assert not result.get('restart_scheduled')
    assert not result.get('up_to_date')
    assert not result.get('lock_conflict')
    assert not result.get('conflict')
    for effect in effects.values():
        effect.assert_not_called()


@pytest.mark.parametrize('mode', ['cached', 'fresh', 'cache-hit', 'in-progress'])
def test_agent_status_does_not_reuse_generic_offer(monkeypatch, mode):
    import time
    monkeypatch.setattr(updates, '_update_cache', {
        'agent': {'name': 'agent', 'behind': 784, 'branch': 'origin/main',
                  'recovery': {'force': True, 'clear_lock': True}},
        'webui': {'name': 'webui', 'behind': 2, 'channel': 'kaladin'},
        'include_agent': True, 'channel': 'kaladin', 'checked_at': time.time(),
    })
    monkeypatch.setattr(updates, '_check_in_progress', mode == 'in-progress')
    checks = Mock(return_value={'name': 'webui', 'behind': 2, 'channel': 'kaladin'})
    monkeypatch.setattr(updates, '_check_repo', checks)
    if mode == 'cached':
        result = updates.cached_update_status(channel='kaladin')
    else:
        result = updates.check_for_updates(force=mode == 'fresh', channel='kaladin')
    agent = result['agent']
    assert agent['status'] == 'native-maintenance-required'
    assert agent['native_maintenance_required'] is True
    assert agent['behind'] is None  # unknown, not "up to date"
    assert agent['error']
    assert agent['recovery'] == {'force': False, 'clear_lock': False}
    assert 'branch' not in agent
    assert result['webui']['behind'] == 2
    assert all(call.args[1] == 'webui' for call in checks.call_args_list)


def test_ignored_agent_status_remains_ignored(monkeypatch):
    monkeypatch.setattr(updates, '_check_repo', Mock(return_value={'name': 'webui', 'behind': 0}))
    result = updates.check_for_updates(force=True, include_agent=False, channel='kaladin')
    assert result['agent'] == {'name': 'agent', 'behind': 0, 'ignored': True}
    assert updates.cached_update_status(include_agent=False, channel='kaladin')['agent'] == result['agent']


@pytest.mark.parametrize('path', ['/api/updates/apply', '/api/updates/force', '/api/updates/clear_lock'])
@pytest.mark.parametrize('target', ['agent', 'both', ['webui', 'agent'], 'webui,agent'])
def test_route_dispatch_cannot_bypass_agent_guard(monkeypatch, path, target):
    """Execute the real route block without importing live Agent/server startup."""
    import ast
    from pathlib import Path
    from types import SimpleNamespace

    source = Path(updates.__file__).with_name('routes.py').read_text()
    tree = ast.parse(source)
    block = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                 and ast.unparse(node.test) == f"parsed.path == '{path}'")
    function = ast.parse('def dispatch(handler, parsed, body):\n    pass').body[0]
    assert isinstance(function, ast.FunctionDef)
    function.body = [block]
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    scope = {'j': lambda handler, payload: (200, payload),
             'bad': lambda handler, message: (400, {'error': message})}
    exec(compile(module, '<real-update-route>', 'exec'), scope)
    git = Mock(side_effect=AssertionError('route reached generic Git'))
    monkeypatch.setattr(updates, '_run_git', git)
    monkeypatch.setattr(updates, '_schedule_restart', git)
    status, payload = scope['dispatch'](None, SimpleNamespace(path=path),
                                        {'target': target, 'channel': 'kaladin'})
    if target == 'agent':
        assert status == 200
        assert payload['ok'] is False
        assert payload['native_maintenance_required'] is True
    else:
        assert status == 400  # combined requests are not a supported route shape
    git.assert_not_called()


def test_stale_combined_client_offer_stops_before_webui_apply():
    from tests.test_update_apply_ui import _run_apply_updates_harness

    result = _run_apply_updates_harness(
        {'agent': {'behind': 784}, 'webui': {'behind': 2, 'channel': 'kaladin'}},
        [updates._agent_maintenance_required()],
    )
    assert result['apiCalls'] == ['agent']
    assert result['waitCalls'] == []
    assert result['showToasts'] == []
    assert result['errors'][0]['target'] == 'agent'
    assert 'Native Agent maintenance' in result['errors'][0]['message']
    assert result['inFlight'] is False


def test_maintenance_status_does_not_block_webui_only_client_apply():
    from tests.test_update_apply_ui import _run_apply_updates_harness

    result = _run_apply_updates_harness(
        {'agent': updates._agent_update_status(), 'webui': {'behind': 2, 'channel': 'kaladin'}},
        [{'ok': True, 'restart_scheduled': True}],
    )
    assert result['apiCalls'] == ['webui']
    assert len(result['waitCalls']) == 1
    assert result['errors'] == []


def test_manual_check_shows_native_maintenance_instead_of_up_to_date():
    import json
    from pathlib import Path
    import shutil
    import subprocess

    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for the update status harness')
    assert node is not None
    root = Path(updates.__file__).resolve().parents[1]
    script = r"""
const fs = require('fs');
const root = process.argv[1];
const data = JSON.parse(process.argv[2]);
const ui = fs.readFileSync(root+'/static/ui.js', 'utf8');
const panels = fs.readFileSync(root+'/static/panels.js', 'utf8');
const extract = (src,start,end) => src.slice(src.indexOf(start),src.indexOf(end,src.indexOf(start)));
eval(extract(ui,'function _formatUpdateTargetStatus(', 'function _isSafeUpdateCompareUrl('));
eval(extract(panels,'async function checkUpdatesNow(', '// ── Auxiliary Models'));
const elements = {};
for (const name of ['btnCheckUpdatesNow','checkUpdatesLabel','checkUpdatesSpinner','checkUpdatesStatus']) {
  elements[name] = {textContent:'',disabled:false,style:{}};
}
global.window = {};
global.$ = id => elements[id];
global.t = key => key;
global.api = async () => data;
let cleared = 0;
global._clearUpdateOffer = () => {cleared++;};
(async () => {
 await checkUpdatesNow('kaladin');
 console.log(JSON.stringify({status:elements.checkUpdatesStatus,cleared,disabled:elements.btnCheckUpdatesNow.disabled}));
})().catch(error => {console.error(error);process.exit(1);});
"""
    response = {'webui': {'behind': 0}, 'agent': updates._agent_update_status()}
    run = subprocess.run([node, '-e', script, str(root), json.dumps(response)],
                         capture_output=True, text=True, check=True, timeout=30)
    result = json.loads(run.stdout)
    assert 'Native Agent maintenance is required' in result['status']['textContent']
    assert 'settings_up_to_date' not in result['status']['textContent']
    assert result['status']['style']['color'] == 'var(--error)'
    assert result['cleared'] == 1
    assert result['disabled'] is False
