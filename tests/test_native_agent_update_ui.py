"""Real Node flow and disposable Chromium component acceptance (not live app)."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_native_update_real_node_flow():
    node = shutil.which('node')
    assert node, 'Node is required for native update acceptance'
    result = subprocess.run([node, str(ROOT/'tests/native_agent_update_flow.cjs'), str(ROOT)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['passed'] == 9


def _between(source, start, end):
    pos = source.index(start)
    return source[pos:source.index(end, pos)]


@pytest.mark.parametrize('width,touch,outcome', [(1280, False, 'success'), (390, True, 'partial'), (390, True, 'unknown')])
def test_native_update_browser_component(width, touch, outcome):
    from playwright.sync_api import sync_playwright
    index = (ROOT/'static/index.html').read_text()
    banner = _between(index, '<div class="update-banner" id="updateBanner">', '<div class="update-banner" id="staleClientBanner">')
    ui = (ROOT/'static/ui.js').read_text()
    functions = _between(ui, 'function _nativeAgentStored(', 'function _showUpdateError(')
    api = _between((ROOT/'static/workspace.js').read_text(), 'async function api(', '\nfunction ')
    boot = "if(typeof _resumeNativeAgentUpdate==='function')_resumeNativeAgentUpdate().catch(()=>{});"
    assert boot in (ROOT/'static/boot.js').read_text()
    script = """
const $=id=>document.getElementById(id);
window._updateData={agent:{native:true,update_available:true,can_apply:true,behind:-1},webui:{behind:0}};
window.waits=[];window.toasts=[];
async function _readHealthServerIdentity(){return 'before';}
function _waitForServerThenReload(opts){window.waits.push(opts);}
function showToast(...args){window.toasts.push(args);}
function _showUpdateError(target,res){throw new Error('unexpected generic update error');}
function _formatUpdateApplyExceptionMessage(error){return error.message;}
function _isUpdateApplyNetworkError(){return false;}
""" + api + functions + boot
    html = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<style>'+ (ROOT/'static/style.css').read_text() + '</style>'
            '<body><main class="main">'+banner+'</main><script>'+script+'</script></body>')
    calls = []
    errors = []
    action_id = 'a'*32
    def route(request_route):
        request = request_route.request
        path = request.url.split('fixture.test', 1)[-1]
        if path == '/':
            request_route.fulfill(status=200, content_type='text/html', body=html)
            return
        if path.startswith('/api/'):
            body = request.post_data_json
            calls.append((path, body))
            if path == '/api/updates/apply':
                assert body == {'target': 'agent'}
                value = {'ok': True, 'status': 'accepted', 'owner': 'fixture', 'action_id': action_id}
            elif path == '/api/updates/agent/status':
                assert body == {'owner': 'fixture', 'action_id': action_id}
                value = {'ok': outcome == 'success', 'status': outcome, 'action_id': action_id,
                         'message': 'WebUI reload/recovery is separate.',
                         'followups': [{'step': 'dependencies', 'reason': 'manual repair remains'}]}
            elif path == '/api/updates/agent/reload':
                assert body == {'owner': 'fixture', 'action_id': action_id}
                value = {'ok': True, 'restart_scheduled': True}
            else:
                raise AssertionError(path)
            request_route.fulfill(status=200, content_type='application/json', body=json.dumps(value))
            return
        request_route.abort()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': width, 'height': 850}, has_touch=touch, is_mobile=touch)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', route)
        page.goto('http://fixture.test/')
        page.evaluate("document.getElementById('updateBanner').classList.add('visible')")
        button = page.locator('#btnApplyUpdate')
        if touch:
            button.tap()
        else:
            button.focus(); button.press('Enter')
        page.wait_for_function("([outcome])=>document.getElementById('updateError').textContent.includes('Agent: '+outcome)", arg=[outcome])
        assert 'manual repair remains' in page.locator('#updateError').inner_text()
        assert len(calls) == 2
        assert page.evaluate('window.waits.length') == 0
        assert page.evaluate('window.toasts.length') == 0
        assert json.loads(page.evaluate("localStorage.getItem('hermes-native-agent-action-v1')"))['action_id'] == action_id
        assert page.locator('#updateError').is_visible()
        assert page.locator('#updateError').bounding_box()['width'] > 80
        out = os.getenv('HERMES_NATIVE_UI_EVIDENCE')
        if out:
            page.screenshot(path=str(Path(out)/f'browser-{width}-{outcome}.png'))
        page.reload()
        page.wait_for_function("([outcome])=>document.getElementById('updateError').textContent.includes('Agent: '+outcome)", arg=[outcome])
        assert [path for path, _ in calls].count('/api/updates/apply') == 1
        assert calls[-1][0] == '/api/updates/agent/status'
        if outcome == 'success':
            page.get_by_role('button', name='Reload WebUI separately').click()
            page.wait_for_function('window.waits.length===1')
            assert calls[-1][0] == '/api/updates/agent/reload'
        assert errors == []
        context.close(); browser.close()
