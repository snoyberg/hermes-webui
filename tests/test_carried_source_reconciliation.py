"""Integration seams between carried controls and upstream recovery/navigation."""
import json
import shutil
import subprocess

import pytest

from tests._sidebar_child_status_helpers import FAKE_DOM, ROOT, component_script
from tests.test_child_session_status import session
from tests.test_update_banner_fixes import extract_js_function

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def run_node(script):
    result = subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("channel", ["kaladin", "stable", "experimental"])
@pytest.mark.parametrize("behind", [0, 1])
def test_manual_check_preserves_channel_and_pre_await_recovery_generation(channel, behind):
    check = extract_js_function((ROOT / "static/panels.js").read_text(), "checkUpdatesNow")
    out = run_node(r"""
const elements={
  btnCheckUpdatesNow:{disabled:false},checkUpdatesLabel:{},
  checkUpdatesSpinner:{style:{}},checkUpdatesStatus:{style:{}}
};
const window={_updateRecoveryGeneration:7};
const calls=[];
const $=id=>elements[id];
const t=key=>key;
function _showUpdateBanner(data,generation){calls.push({data,generation});}
let request;
async function api(url,options){
  request={url,body:JSON.parse(options.body)};
  // A recovery action completed while the check request was in flight.
  window._updateRecoveryGeneration=8;
  return {webui:{behind:BEHIND},agent:null};
}
""".replace("BEHIND", str(behind)) + check + f"""
(async()=>{{
  await checkUpdatesNow({json.dumps(channel)});
  console.log(JSON.stringify({{request,calls,generation:window._updateRecoveryGeneration}}));
}})().catch(err=>{{console.error(err);process.exit(1);}});
""")
    assert out["request"] == {"url": "/api/updates/check", "body": {"force": True, "channel": channel}}
    assert out["generation"] == 8
    assert len(out["calls"]) == 1
    assert out["calls"][0]["generation"] == 7


@pytest.mark.parametrize("kind", ["parent", "fork", "delegated"])
@pytest.mark.parametrize("gesture", ["ordinary", "ctrl", "meta", "middle"])
@pytest.mark.parametrize("foreign", [False, True])
def test_real_rendered_row_navigation_uses_upstream_new_tab_path(kind, gesture, foreign):
    parent = session("parent", profile="default")
    child = session("child", parent_session_id="parent", relationship_type="child_session",
                    session_source="fork" if kind == "fork" else "other",
                    profile="foreign" if foreign else "default")
    if kind == "parent":
        parent["profile"] = "foreign" if foreign else "default"
    out = run_node(FAKE_DOM + component_script() + f"""
const raw={json.dumps([parent, child])};
const result=renderFixture(raw,raw,true,'other');
const child=flatten(result.element).find(e=>(e.className||'').split(' ').includes('session-child-session'));
const target={json.dumps(kind)}==='parent'?result.element:
  (child.tag==='button'?child:child.children.find(e=>e.tag==='button'));
const e={{button:{1 if gesture == 'middle' else 0},ctrlKey:{str(gesture == 'ctrl').lower()},
  metaKey:{str(gesture == 'meta').lower()},preventDefault(){{}},stopPropagation(){{}}}};
(async()=>{{
  if({json.dumps(gesture)}==='middle'){{
    for(const listener of target.listeners.mousedown||[]) listener(e);
    for(const listener of target.listeners.auxclick||[]) listener(e);
  }}else if({json.dumps(kind)}==='parent'){{
    if(!_consumeSessionNewTabClick(e,raw[0].session_id,raw[0])) await _openSidebarSession(raw[0]);
  }}else{{await target.onclick(e);}}
  console.log(JSON.stringify({{opened,newTabs}}));
}})().catch(err=>{{console.error(err);process.exit(1);}});
""")
    sid = "parent" if kind == "parent" else "child"
    if gesture == "ordinary":
        assert [row["sid"] for row in out["opened"]] == [sid]
        assert out["newTabs"] == []
    else:
        assert out["opened"] == []
        expected = [] if foreign else [[f"/session/{sid}" + ("?exact=1" if kind != "parent" else ""),
                                       "_blank", "noopener"]]
        assert out["newTabs"] == expected
