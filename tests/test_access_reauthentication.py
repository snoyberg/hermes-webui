"""Production monitor/API in Chromium, synthetic transport, no WebUI server/state."""
from pathlib import Path
import os
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def test_server():
    """This browser component gate uses no backend or Agent installation."""
    yield


@pytest.fixture
def page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.context.route("**/*", lambda route: route.fulfill(
            status=200, content_type="text/html", body="<html></html>"))
        page.goto("http://reauth.invalid/hermes/?session=kept")
        html = (ROOT / "static/index.html").read_text()
        html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
        html = re.sub(r"<link\b[^>]*>", "", html)
        page.set_content(html)
        page.add_style_tag(content=(ROOT / "static/style.css").read_text())
        page.add_script_tag(content=(ROOT / "static/i18n.js").read_text())
        page.evaluate("""() => {
          document.documentElement.dataset.workspacePanel='closed';
          document.documentElement.classList.add('dark');
          window.nativeFetch=window.fetch.bind(window);
          window.calls=[];window.mode='ready';window.posts=0;
          window.fetch=async (input,init)=>{
            const req=new Request(input,init);calls.push({url:req.url,method:req.method,
              headers:Object.fromEntries(req.headers),body:await req.clone().text()});
            if(req.method==='POST')posts++;
            if(mode==='network')throw new TypeError('network');
            if(mode==='401'||mode==='403'||mode==='503')return new Response('',{status:+mode});
            if(mode==='native'&&req.url.includes('api/'))return new Response('',{status:401});
            if(mode==='html')return new Response('<html>Login</html>',{headers:{'Content-Type':'text/html'}});
            return new Response(JSON.stringify(req.url.includes('/health')?{status:'ok'}:{name:'default'}),
              {headers:{'Content-Type':'application/json'}});
          };
          window.refreshes=0;window.restarts=0;
          window.startGatewaySSE=()=>restarts++;
          window.refreshSession=async()=>{refreshes++};
        }""")
        ui = (ROOT / "static/ui.js").read_text().split("function _getSessionQueue(", 1)[0]
        page.add_script_tag(content=ui)
        api = (ROOT / "static/workspace.js").read_text().split("// Persist/restore expanded directory state", 1)[0]
        page.add_script_tag(content=api)
        yield page
        browser.close()


def test_request_headers_preserve_request_init_and_origin_boundary(page):
    result = page.evaluate("""async()=>{
      const request=new Request(location.origin+'/hermes/api/test',{method:'POST',
        headers:new Headers({'X-Original':'yes'}),body:'file bytes'});
      const second=request.clone();
      await fetch(request);
      await fetch(second,{headers:[['X-Override','yes']]});
      await fetch('/hermes/health',{headers:{'X-Requested-With':'custom'}});
      await fetch('https://foreign.invalid/api/test');
      await fetch('/not-app/api/test');
      return calls;
    }""")
    assert result[0]["headers"]["x-requested-with"] == "XMLHttpRequest"
    assert result[0]["headers"]["x-original"] == "yes"
    assert result[0]["body"] == "file bytes"
    assert result[1]["headers"]["x-override"] == "yes"
    assert "x-original" not in result[1]["headers"]
    assert result[2]["headers"]["x-requested-with"] == "custom"
    assert "x-requested-with" not in result[3]["headers"]
    assert "x-requested-with" not in result[4]["headers"]


@pytest.mark.parametrize("mode,reason", [("401", "auth"), ("native", "auth"),
    ("403", "network"), ("503", "network"), ("network", "network"), ("html", "network")])
def test_header_probe_classifies_auth_without_claiming_backend_health(page, mode, reason):
    result = page.evaluate("""async mode=>{
      window.mode=mode;
      await _showOfflineBannerIfProbeFails('network',{requireConsecutiveFailures:false});
      return {reason:_offlineReason,visible:_offlineVisible,calls,refreshes};
    }""", mode)
    assert result["visible"]
    assert result["reason"] == reason
    assert result["refreshes"] == 0
    assert result["calls"][0]["headers"]["x-requested-with"] == "XMLHttpRequest"


def test_api_401_is_error_not_navigation_or_replay_and_optout_is_preserved(page):
    result = page.evaluate("""async()=>{
      mode='401';let error;
      try{await api('/api/chat/start',{method:'POST',body:'{}'})}catch(e){error=e.status}
      const first={error,visible:_offlineVisible,posts,url:location.href};
      _hideOfflineBanner();
      try{await api('/api/profile/active',{redirect401:false})}catch(_){}
      return {first,optoutVisible:_offlineVisible};
    }""")
    assert result["first"]["error"] == 401
    assert result["first"]["visible"]
    assert result["first"]["posts"] == 1
    assert result["first"]["url"].endswith("/hermes/?session=kept")
    assert not result["optoutVisible"]


def test_recovery_preserves_draft_files_and_does_not_reload_on_failure(page):
    result = page.evaluate("""async()=>{
      S.session={session_id:'kept'};S.messages=[{role:'assistant',content:'kept'}];
      $('msg').value='unsent draft';const file=new File(['unsent bytes'],'draft.txt');
      S.pendingFiles=[file];showOfflineBanner('auth');mode='ready';
      await checkOfflineRecoveryNow();
      const first={visible:_offlineVisible,refreshes,restarts};
      showOfflineBanner('auth');window.refreshSession=async()=>{throw Error('late outage')};
      await checkOfflineRecoveryNow();
      return {first,visible:_offlineVisible,url:location.href,draft:$('msg').value,
        sameFile:S.pendingFiles[0]===file,bytes:await S.pendingFiles[0].text(),posts};
    }""")
    assert result["first"] == {"visible": False, "refreshes": 1, "restarts": 1}
    assert result["visible"]
    assert result["draft"] == "unsent draft"
    assert result["sameFile"] and result["bytes"] == "unsent bytes"
    assert result["posts"] == 0
    assert result["url"].endswith("/hermes/?session=kept")


@pytest.mark.parametrize("width", [1280, 390])
def test_rendered_banner_login_link_and_evidence(page, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.wait_for_timeout(350)  # Let the real shell's panel transitions settle.
    page.evaluate("showOfflineBanner('auth')")
    banner = page.locator('#offlineBanner')
    link = page.locator('#offlineSignIn')
    assert banner.is_visible()
    assert link.is_visible()
    assert link.inner_text() == 'Sign in again'
    assert link.get_attribute('target') == '_blank'
    assert 'noopener' in link.get_attribute('rel')
    assert link.get_attribute('href').startswith('http://reauth.invalid/hermes/login?next=')
    assert banner.evaluate('(el)=>el.scrollWidth<=el.clientWidth')
    box = link.bounding_box()
    assert box['height'] >= 44 and box['x'] >= 0 and box['x'] + box['width'] <= width
    evidence = os.environ.get('WEBUI_REAUTH_EVIDENCE')
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(evidence) / f'auth-{width}.png'))
    page.evaluate("showOfflineBanner('network')")
    assert not link.is_visible()


def test_signin_click_opens_new_tab_without_opener(page):
    original = page.url
    page.evaluate("showAuthRequiredBanner();$('msg').value='keep me'")
    with page.expect_popup() as opened:
        page.locator('#offlineSignIn').click()
    popup = opened.value
    popup.wait_for_load_state()
    assert '/hermes/login?next=' in popup.url
    assert popup.evaluate('window.opener===null')
    assert page.url == original
    assert page.locator('#msg').input_value() == 'keep me'
    popup.close()


def test_login_loop_guard_foreign_base_and_cross_origin_auth(page):
    result = page.evaluate("""async()=>{
      mode='401';await fetch('https://foreign.invalid/api/test');
      const foreignVisible=_offlineVisible;
      history.replaceState({},'', '/hermes/login?next=%2Fhermes%2F');
      const login=_authRecoveryLoginUrl();
      const base=document.createElement('base');base.href='https://foreign.invalid/';
      document.head.prepend(base);
      const unsafe=_authRecoveryLoginUrl();
      return {foreignVisible,login,unsafe};
    }""")
    assert not result['foreignVisible']
    assert result['login'] == 'http://reauth.invalid/hermes/login'
    assert result['unsafe'] is None


def test_sse_error_probe_and_network_mutation_are_not_replayed(page):
    page.evaluate("mode='401';recordClientSSEError('gateway-sessions')")
    page.wait_for_function("_offlineVisible&&_offlineReason==='auth'")
    assert page.evaluate("calls.some(c=>c.url.includes('/health')&&c.headers['x-requested-with']==='XMLHttpRequest')")
    result = page.evaluate("""async()=>{
      mode='network';calls=[];posts=0;
      try{await api('/api/chat/start',{method:'POST',body:'{}'})}catch(_){}
      return calls.filter(c=>c.method==='POST').length;
    }""")
    assert result == 1


def test_actual_session_refresh_rejects_malformed_and_stale_recovery(page):
    src = (ROOT / 'static/ui.js').read_text()
    refresh = 'async function refreshSession(' + src.split('async function refreshSession(', 1)[1].split('// ── Update banner', 1)[0]
    page.add_script_tag(content=refresh)
    result = page.evaluate("""async()=>{
      window.dismissReconnect=()=>{};
      S.session={session_id:'kept'};S.messages=[{content:'kept'}];
      const kept=S.session;let malformed=false;
      try{await refreshSession({recovery:true})}catch(_){malformed=true}
      const preserved=S.session===kept&&S.messages[0].content==='kept';
      let resolve;window.api=()=>new Promise(r=>resolve=r);
      const pending=refreshSession({recovery:true});
      S.session={session_id:'new-pane'};
      resolve({session:{session_id:'kept',messages:[]}});await pending;
      return {malformed,preserved,sid:S.session.session_id};
    }""")
    assert result == {'malformed': True, 'preserved': True, 'sid': 'new-pane'}


def test_native_eventsource_failure_uses_header_bearing_probe(page):
    requests = []

    def deny(route):
        requests.append((route.request.url, route.request.headers))
        route.fulfill(status=401, body='Authentication required')

    page.route('**/hermes/health?*', deny)
    page.route('**/hermes/api/**', deny)
    page.evaluate("""() => {
      _offlineRawFetch=nativeFetch;
      const source=new EventSource('/hermes/api/sessions/events');
      source.onerror=()=>{source.close();recordClientSSEError('gateway-sessions')};
    }""")
    page.wait_for_function("_offlineVisible&&_offlineReason==='auth'")
    assert any('/health?' in url and headers.get('x-requested-with') == 'XMLHttpRequest'
               for url, headers in requests)
    assert any('/api/sessions/events' in url and 'x-requested-with' not in headers
               for url, headers in requests)
    assert page.url.endswith('/hermes/?session=kept')


def test_probe_is_serialized_abort_is_not_an_outage_and_update_does_not_reload(page):
    result = page.evaluate("""async()=>{
      const opts=_appFetchOptions('/hermes/api/test',null);
      _offlineRawFetch=async()=>{throw new DOMException('aborted','AbortError')};
      try{await fetch('/hermes/api/test')}catch(_){}
      const abortVisible=_offlineVisible;
      let resolve;calls=[];
      _offlineRawFetch=(input,init)=>{calls.push(input);return new Promise(r=>resolve=r)};
      const a=_probeOfflineRecovery(),b=_probeOfflineRecovery();
      resolve(new Response('',{status:401}));await Promise.all([a,b]);
      const count=calls.length;
      showAuthRequiredBanner();window._restartingForUpdate=true;
      const recovery=await _recoverFromOfflineSoftly();
      return {abortVisible,count,recovery,visible:_offlineVisible,refreshes,
        header:opts.headers.get('X-Requested-With')};
    }""")
    assert result == {'abortVisible': False, 'count': 1, 'recovery': False,
                      'visible': True, 'refreshes': 0, 'header': 'XMLHttpRequest'}
