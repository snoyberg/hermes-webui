"""Real Chromium controls over production composer markup and recovery functions.

Only synthetic File bytes and intercepted HTTP are used; no Agent/server/microphone.
"""
import json
import re
from pathlib import Path

import pytest

from tests.test_cross_session_message_load_isolation import _extract_function

ROOT = Path(__file__).resolve().parents[1]


def _between(source, start, end):
    return source[source.index(start):source.index(end, source.index(start) + len(start))]


@pytest.mark.parametrize("width", [390, 1280])
def test_upload_recovery_browser(width, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    messages = (ROOT / "static/messages.js").read_text()
    ui = (ROOT / "static/ui.js").read_text()
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", (ROOT / "static/index.html").read_text(), flags=re.S)
    html = re.sub(r"<link\b[^>]*>", "", html)
    script = _between(messages, "function _normalizeGoalContinuationId(", "\nfunction _bgTaskCompleteRingBufferAdd(")
    script += _between(messages, "const _uploadRecoveries=", "async function _recoverCompressedSend(")
    script += _between(messages, "async function send(){", "\nasync function startRegeneration(")
    script += ui[ui.index("async function uploadPendingFiles(options={})"):]
    script += _between((ROOT / "static/workspace.js").read_text(), "async function api(", "\nfunction recordClientSSEError(")
    i18n = (ROOT / "static/i18n.js").read_text()
    # Use the real English copy with the production fallback helper kept outside this fixture.
    english = _between(i18n, "  en: {", "    offline_title:")
    translations = json.loads("{}")
    for key, value in re.findall(r"(upload_recovery_\w+): '([^']*)'", english):
        translations[key] = value
    fixture = r"""
const noop=()=>{};
Object.assign(window,{
  S:{session:{session_id:'A',title:'Voice note',profile:'fixture',model:'synthetic'},activeProfile:'fixture',pendingFiles:[],messages:[],busy:false},
  $:id=>document.getElementById(id),
  _sendInProgress:false,_sendInProgressSid:null,_pendingSelections:[],_forcedSkillDirectivePending:null,
  _approvalSessionId:null,_clarifySessionId:null,INFLIGHT:{},_queueDrainSid:null,
  MAX_UPLOAD_BYTES:1048576,_ARCHIVE_EXTS:/\.(zip|tar)$/i,
  _flushSelectionBlocksToComposer:noop,_clearStaleBusyStateBeforeSend:noop,
  _composerTextWithPendingSelections:()=>$('msg').value,
  _chatPayloadModelState:()=>({model:'synthetic',model_provider:'fixture-provider'}),
  autoResize:noop,updateSendBtn:noop,hideCmdDropdown:noop,clearLiveToolCards:noop,appendThinking:noop,
  renderMessages:noop,renderTray:noop,renderSessionListFromCache:noop,
  startApprovalPolling:noop,startClarifyPolling:noop,_fetchYoloState:noop,
  stopApprovalPolling:noop,stopClarifyPolling:noop,removeThinking:noop,
  _runOptionalPreStartUiStep:(_name,fn)=>fn(),_runOptionalPostStartUiStep:noop,
  setBusy:v=>{S.busy=v;},setStatus:noop,setComposerStatus:noop,showToast:noop,
  _uploadPendingFilesUpdateProgress:noop,_uploadPendingFilesCurrentSession:sid=>S.session.session_id===sid,
  _redirectIfUnauth:()=>false,_clearComposerDraft:()=>Promise.resolve(),attachLiveStream:noop,
  api:async(url,opts)=>{const res=await fetch(url,opts);if(!res.ok)throw new Error('uncertain response');return res.json();}
});
"""
    uploads, starts = [], []
    mode = {"fail": True, "unknown": False}
    sessions, drafts = {}, {}

    def route(request):
        url = request.request.url
        if url.endswith("/api/session/new"):
            sid = f"fresh-{len(sessions) + 1}"
            sessions[sid] = {"session_id": sid, "title": "Untitled", "profile": "fixture", "message_count": 0, "messages": []}
            request.fulfill(content_type="application/json", body=json.dumps({"session": sessions[sid]}))
        elif url.endswith("/api/session/draft"):
            body = request.request.post_data_json
            drafts[body["session_id"]] = body
            request.fulfill(content_type="application/json", body=json.dumps({"ok": True, "draft": body}))
        elif "/api/session?" in url:
            from urllib.parse import parse_qs, urlparse
            sid = parse_qs(urlparse(url).query)["session_id"][0]
            request.fulfill(content_type="application/json", body=json.dumps({"session": {**sessions[sid], "composer_draft": drafts.get(sid, {"text": "", "files": []})}}))
        elif url.endswith("/api/upload"):
            uploads.append(url)
            request.fulfill(status=503 if mode["fail"] else 200, content_type="application/json", body=json.dumps({"filename": "voice.webm", "path": "fixture/voice.webm", "size": 8}))
        elif url.endswith("/api/chat/start"):
            starts.append(request.request.post_data_json)
            request.fulfill(status=502 if mode["unknown"] else 200, content_type="application/json", body='{"stream_id":"synthetic-stream"}')
        elif url == "https://fixture.invalid/":
            request.fulfill(content_type="text/html", body=html)
        else:
            request.fulfill(status=404, body="")

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900}, has_touch=width < 640, is_mobile=width < 640)
        page.route("**/*", route)
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto("https://fixture.invalid/")
        page.add_style_tag(content=(ROOT / "static/style.css").read_text())
        page.add_script_tag(content=fixture)
        page.add_script_tag(content="window.translations=" + json.dumps(translations) + ";window.t=k=>translations[k]||k;")
        page.add_script_tag(content=script)
        if width < 640:
            page.wait_for_function("getComputedStyle(document.querySelector('.sidebar')).visibility==='hidden'")
        page.evaluate("""() => {
          $('msg').value='Please listen to my recording';
          S.pendingFiles=[new File(['SYNTHETIC'], 'voice.webm',{type:'audio/webm'})];
        }""")
        page.screenshot(path=str(tmp_path / f"before-{width}.png"), full_page=True)
        page.evaluate("send({goalContinuationId:'original-token'})")
        retry = page.get_by_role("button", name="Retry", exact=True)
        discard = page.get_by_role("button", name="Discard", exact=True)
        assert retry.is_visible() and discard.is_visible()
        assert len(starts) == 0
        page.screenshot(path=str(tmp_path / f"retained-{width}.png"), full_page=True)
        assert retry.bounding_box()["height"] >= 44
        assert discard.bounding_box()["height"] >= 44
        for control in [retry, discard]:
            box = control.bounding_box()
            assert 0 <= box["x"] and box["x"] + box["width"] <= width
        # Native click, repeated failure, newer draft and wrong-owner protection.
        (retry.tap if width < 640 else retry.click)()
        page.wait_for_function("[..._uploadRecoveries.values()][0].state==='failed'")
        assert len(uploads) == 2 and len(starts) == 0
        page.evaluate("$('msg').value='Newer draft';_setRestoredGoalContinuationDraft('newer-token','Newer draft');S.session={session_id:'B'}")
        retry.click()
        assert len(uploads) == 2
        page.evaluate("S.session={session_id:'A'}")
        mode["fail"] = False
        retry.focus()
        page.keyboard.press("Enter")
        page.wait_for_function("_uploadRecoveries.size===0")
        assert len(starts) == 1 and starts[0]["message"].startswith("Please listen")
        assert starts[0]["goal_continuation_id"] == "original-token"
        assert page.locator("#msg").input_value() == "Newer draft"
        assert page.evaluate("_peekRestoredGoalContinuationDraft()") == {"id": "newer-token", "text": "Newer draft"}
        page.evaluate("S.busy=false;S.pendingFiles=[new File(['SYNTHETIC'],'voice.webm')]")
        mode["fail"] = True
        page.evaluate("send()")
        discard.click()
        assert page.locator("#uploadRecoveries").is_hidden()
        assert page.evaluate("_uploadRecoveries.size") == 0
        # Unknown admission has no replay control, but can be explicitly discarded.
        mode["fail"], mode["unknown"] = False, True
        page.evaluate("S.pendingFiles=[new File(['SYNTHETIC'],'voice.webm')];send()")
        page.wait_for_function("[..._uploadRecoveries.values()][0].state==='uncertain'")
        assert retry.count() == 0
        assert "acknowledgement unknown" in page.locator("#uploadRecoveries").inner_text()
        page.screenshot(path=str(tmp_path / f"uncertain-{width}.png"), full_page=True)
        discard.click()
        assert len(starts) == 2
        # Reproduce the first-message dead end through real creation, draft clear,
        # completed New Chat and loadSession; only transport/render side effects are fake.
        session_source = (ROOT / "static/sessions.js").read_text()
        page.add_script_tag(content=_between(session_source, "let _loadingSessionId = null;", "const SESSION_VIEWED_COUNTS_KEY"))
        page.add_script_tag(content="let _newSessionInFlight=null;" + _extract_function(session_source, "newSession") + _extract_function(session_source, "loadSession"))
        page.evaluate("""() => {
          Object.assign(window,{
            _activeProject:null,NO_PROJECT_FILTER:'none',_sessionSourceFilter:'all',
            _messagesTruncated:false,_oldestIdx:0,_loadingOlder:false,_yoloEnabled:false,
            _setNewSessionPending:noop,_setSessionViewedCount:noop,updateQueueBadge:noop,
            syncTopbar:noop,_setActiveSessionUrl:noop,loadDir:async()=>{},
            _rearmActiveSessionStream:noop,_updateYoloPill:noop,hideApprovalCard:noop,
            _clearSameSessionForceReloadHint:noop,_clearDeferredActiveSessionExternalRefresh:noop,
            _resolveSessionModelForDisplaySoon:noop,_acknowledgeSessionVisit:noop,
            _selectLiveRecoveryInflight:()=>null,_mergePendingSessionMessage:noop,
            _isMessagingSession:()=>false,_hideHandoffHint:noop,
            _deferWorkspaceRefreshForSession:noop,_ensureMessagesLoaded:async()=>{S.messages=[];},
            applySessionTitleUpdate:noop
          });
          window._clearPendingSelections=noop;
          S.session=null;S.busy=false;$('msg').value='';
          S.pendingFiles=[new File(['SYNTHETIC'],'first-voice.webm',{type:'audio/webm'})];
        }""")
        mode["fail"], mode["unknown"] = True, False
        page.evaluate("send()")
        assert page.evaluate("S.session.session_id") == "fresh-1"
        assert page.evaluate("_newSessionInFlight") is None
        assert drafts["fresh-1"]["text"] == ""
        page.evaluate("newSession(false)")
        page.evaluate("$('msg').value='Other conversation draft';window.newerFile=new File(['NEWER'],'newer.webm');S.pendingFiles=[window.newerFile]")
        assert page.evaluate("S.session.session_id") == "fresh-2"
        page.screenshot(path=str(tmp_path / f"first-message-away-{width}.png"), full_page=True)
        mode["fail"] = False
        (retry.tap if width < 640 else retry.click)()
        try:
            page.wait_for_function("_uploadRecoveries.size===0", timeout=5000)
        finally:
            page.screenshot(path=str(tmp_path / f"first-message-after-retry-{width}.png"), full_page=True)
        assert starts[-1]["session_id"] == "fresh-1" and starts[-1]["profile"] == "fixture"
        assert drafts["fresh-2"]["text"] == "Other conversation draft"
        assert drafts["fresh-2"]["files"][0]["name"] == "newer.webm"
        page.screenshot(path=str(tmp_path / f"first-message-retried-{width}.png"), full_page=True)
        page.evaluate("S.busy=false;loadSession('fresh-2')")
        assert page.locator("#msg").input_value() == "Other conversation draft"
        assert page.evaluate("S.pendingFiles[0]===window.newerFile")
        # Real loadSession claims navigation before awaiting the source draft
        # write. A later load supersedes the earlier one without changing S yet.
        page.add_script_tag(content=_extract_function((ROOT / "static/sessions.js").read_text(), "loadSession"))
        page.evaluate("""() => {
          Object.assign(window,{
            _rearmActiveSessionStream:noop,_loadingSessionId:null,_loadSessionGeneration:0,
            _yoloEnabled:false,_updateYoloPill:noop,hideApprovalCard:noop,
            _saveComposerDraftNow:()=>new Promise(resolve=>window.draftReleases.push(resolve))
          });
          window.draftReleases=[];
          S.session={session_id:'A',profile:'fixture'};
          S.busy=false;S.pendingFiles=[new File(['SYNTHETIC'],'voice.webm')];
        }""")
        mode["fail"], mode["unknown"] = True, False
        page.evaluate("send()")
        page.evaluate("() => {window.navB=loadSession('B');window.navC=loadSession('C');}")
        assert page.evaluate("_loadingSessionId") == "C"
        assert page.evaluate("draftReleases.length") == 2
        before = len(uploads)
        mode["fail"] = False
        retry.click()
        assert len(uploads) == before and len(starts) == 3
        page.evaluate("draftReleases[0]();navB")
        assert page.evaluate("_loadingSessionId") == "C"
        assert page.evaluate("S.session.session_id") == "A"
        discard.click()
        assert page.evaluate("_uploadRecoveries.size") == 0
        # The pending C draft-write fixture is destroyed with this page only.
        assert page_errors == []
        browser.close()
    print(f"screenshots: {tmp_path}")
