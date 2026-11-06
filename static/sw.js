/**
 * Hermes WebUI Service Worker
 * Minimal PWA service worker — enables "Add to Home Screen".
 * No offline caching of API responses (the UI requires a live backend).
 * Caches only static shell assets so the app shell loads fast on repeat visits.
 */

// Cache version is injected by the server at request time (routes.py /sw.js handler).
// Bumps automatically whenever the git commit changes — no manual edits needed.
const CACHE_NAME = 'hermes-shell-__WEBUI_VERSION__';

// Static assets that form the app shell.
//
// Versioned assets (CSS + JS) include `?v=__WEBUI_VERSION__` to match the
// query string the page sends — see index.html. Without the version query
// here, every cache lookup against `?v=...` URLs would miss and fall through
// to network, defeating the pre-cache.
//
// Do not pre-cache './' or login assets here: under password auth they can be
// either the authenticated app shell or login code, and stale cached responses
// can make valid password submits fail until the user clears browser cache.
// Navigations populate './' only after a successful non-redirect network load.
const VQ = '?v=__WEBUI_VERSION__';
const SHELL_ASSETS = [
  './static/style.css' + VQ,
  './static/pwa-startup.js' + VQ,
  './static/boot.js' + VQ,
  './static/assistant_turn_anchors.js' + VQ,
  './static/a11y-helpers.js' + VQ,
  './static/ui.js' + VQ,
  './static/messages.js' + VQ,
  './static/private_push.js' + VQ,
  './static/sessions.js' + VQ,
  './static/panels.js' + VQ,
  './static/commands.js' + VQ,
  './static/icons.js' + VQ,
  './static/i18n.js' + VQ,
  './static/workspace.js' + VQ,
  './static/terminal.js' + VQ,
  './static/onboarding.js' + VQ,
  './static/vendor/smd.min.js' + VQ,
  './static/vendor/katex/0.16.22/katex.min.css' + VQ,
  './static/vendor/katex/0.16.22/katex.min.js' + VQ,
  './static/favicon.svg',
  './static/favicon-32.png',
  './manifest.json',
];

function deleteOldShellCaches() {
  return caches.keys().then((keys) =>
    Promise.all(
      keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
    )
  );
}

// Install: prune old shell caches first, then pre-cache the app shell. Doing
// this before caches.open(CACHE_NAME) avoids a temporary double-cache window on
// quota-sensitive browsers during frequent version bumps.
self.addEventListener('install', (event) => {
  event.waitUntil(
    deleteOldShellCaches().then(() =>
      caches.open(CACHE_NAME).then((cache) => {
        return cache.addAll(SHELL_ASSETS).catch((err) => {
          // Non-fatal: if any asset fails, still activate
          console.warn('[sw] Shell pre-cache partial failure:', err);
        });
      })
    )
  );
  self.skipWaiting();
});

// Activate: keep the old-cache cleanup as a safety net in case install was
// interrupted or an older worker was already waiting.
self.addEventListener('activate', (event) => {
  event.waitUntil(deleteOldShellCaches());
  self.clients.claim();
});

// Fetch strategy:
// - API calls (/api/*, /stream) → always network (never cache)
// - Login assets → always network (never cache stale auth code)
// - Page navigations → network-first so auth redirects/cookies are honored
// - Shell assets → network-first with cache fallback
// - Everything else → network-only
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never intercept cross-origin requests
  if (url.origin !== self.location.origin) return;

  // Never intercept the service worker script itself. Returning a cached sw.js
  // prevents the browser from seeing a new cache version after local patches.
  if (url.pathname.endsWith('/sw.js')) return;

  // Login assets must always hit the network. Older login.js builds have had
  // subpath-sensitive auth POST paths; if the service worker caches one, the
  // password can keep failing until the user manually clears browser cache.
  if (
    url.pathname.endsWith('/login') ||
    url.pathname.endsWith('/static/login.js')
  ) {
    return;
  }

  // API and streaming endpoints — always go to network.
  // The WebUI may be mounted under a subpath such as /hermes/, so API
  // requests can look like /hermes/api/sessions rather than /api/sessions.
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.includes('/api/') ||
    url.pathname.includes('/stream') ||
    url.pathname.startsWith('/health') ||
    url.pathname.includes('/health')
  ) {
    return; // let browser handle normally
  }

  // Page navigations must be network-first. A stale cached './' response can
  // otherwise hide the server's 302-to-login after auth expiry, or ignore a
  // freshly set login cookie until the user manually refreshes.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(new Request(event.request, { cache: 'no-store' })).then((response) => {
        if (
          event.request.method === 'GET' &&
          response.status === 200 &&
          !response.redirected
        ) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put('./', clone));
        }
        return response;
      }).catch(() => {
        return caches.match('./').then((cached) => cached || new Response(
          '<html><body style="font-family:sans-serif;padding:2rem;background:#1a1a1a;color:#ccc">' +
          '<h2>You are offline</h2>' +
          '<p>Hermes requires a server connection. Please check your network and try again.</p>' +
          '</body></html>',
          { headers: { 'Content-Type': 'text/html' } }
        ));
      })
    );
    return;
  }

  // Only explicit shell assets are cached. Everything else should hit the
  // network so stale one-off files (especially auth/login scripts) do not get
  // trapped in CacheStorage until a manual cache clear.
  const scopePath = new URL(self.registration.scope).pathname;
  const relPath = url.pathname.startsWith(scopePath)
    ? url.pathname.slice(scopePath.length)
    : url.pathname.replace(/^\/+/, '');
  const shellPath = './' + relPath.replace(/^\/+/, '') + url.search;
  if (!SHELL_ASSETS.includes(shellPath)) return;

  // Shell assets: network-first with cache fallback. This keeps offline support
  // but avoids executing stale JS/CSS after a local hotfix when WEBUI_VERSION
  // has not changed yet (e.g. before a guarded restart updates the ?v token).
  event.respondWith(
    fetch(new Request(event.request, { cache: 'no-store' })).then((response) => {
      if (
        event.request.method === 'GET' &&
        response.status === 200
      ) {
        const clone = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
      }
      return response;
    }).catch(() => caches.match(event.request).then((cached) => cached || new Response('Offline', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    })))
  );
});


// Private push state is distinct from the static shell cache. Updating the
// shell must not erase explicit device/profile consent or retry dedupe.
function privatePushDatabase(){
  return new Promise((resolve,reject)=>{
    const request=indexedDB.open('hermes-private-push-v1',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('state');
    request.onsuccess=()=>resolve(request.result);
    request.onerror=()=>reject(new Error('Push storage unavailable'));
  });
}
async function privatePushState(operation){
  const db=await privatePushDatabase();
  try{
    return await new Promise((resolve,reject)=>{
      const tx=db.transaction('state','readwrite');
      let result;
      operation(tx.objectStore('state'),value=>{result=value;});
      tx.oncomplete=()=>resolve(result);
      tx.onerror=()=>reject(new Error('Push storage unavailable'));
      tx.onabort=()=>reject(new Error('Push storage unavailable'));
    });
  }finally{db.close();}
}
function privatePushProfile(value){return typeof value==='string'&&/^[A-Za-z0-9_-]{1,64}$/.test(value);}
const privatePushConsentWrites=new Map();
self.addEventListener('message',event=>{
  const data=event.data||{};
  if(data.type!=='private-push-profile'||!privatePushProfile(data.profile)||typeof data.enabled!=='boolean')return;
  try{if(new URL(event.source.url).origin!==self.location.origin)return;}catch(_){return;}
  // Preserve message order even if opening storage stalls beyond the page's
  // ack timeout. A later Disable must be the final persisted consent value.
  const pending=(privatePushConsentWrites.get(data.profile)||Promise.resolve())
    .then(()=>privatePushState(store=>store.put(data.enabled,'profile:'+data.profile)));
  const tail=pending.catch(()=>{});
  privatePushConsentWrites.set(data.profile,tail);
  tail.then(()=>{if(privatePushConsentWrites.get(data.profile)===tail)privatePushConsentWrites.delete(data.profile);});
  event.waitUntil(pending
    .then(()=>{if(event.ports[0])event.ports[0].postMessage({ok:true});})
    .catch(()=>{if(event.ports[0])event.ports[0].postMessage({ok:false});}));
});
self.addEventListener('push',event=>{
  event.waitUntil((async()=>{
    let data;
    try{data=event.data.json();}catch(_){return;}
    if(!data||data.v!==1||!['complete','approval','clarify','test'].includes(data.kind)
       ||!privatePushProfile(data.profile)||typeof data.session!=='string'
       ||!(/^[A-Za-z0-9_-]{1,128}$/.test(data.session)||(data.kind==='test'&&data.session===''))
       ||typeof data.event!=='string'||!/^[a-f0-9]{64}$/.test(data.event))return;
    if(data.preview!==undefined&&(data.kind!=='complete'||typeof data.preview!=='string'
       ||!data.preview.trim()||new TextEncoder().encode(data.preview).length>3000))return;
    const key='event:'+data.event;
    const claimed=await privatePushState((store,done)=>{
      const enabled=store.get('profile:'+data.profile);
      enabled.onsuccess=()=>{
        if(enabled.result!==true){done(null);return;}
        const seen=store.get(key);
        seen.onsuccess=()=>{
          // Persist the attempt before display. An uncertain/failed display
          // must not turn a transport retry into another audible alert.
          store.put(Date.now(),key);done({replay:!!seen.result});
        };
      };
      // Bounded retention independent of worker lifetime; profile records survive.
      const cursor=store.openCursor();
      cursor.onsuccess=()=>{
        const row=cursor.result;
        if(!row)return;
        if(String(row.key).startsWith('event:')&&Number(row.value)<Date.now()-86400000)row.delete();
        row.continue();
      };
    });
    if(!claimed)return;
    const body=data.kind==='complete'?(data.preview||'A response is ready. Open Hermes to continue.')
      :data.kind==='test'?'Private push notifications are ready.'
      :'Open Hermes to review a pending request.';
    // WebKit requires a user-visible notification for every received push.
    // Retries replace the same notification without requesting sound.
    await self.registration.showNotification('Hermes',{
      body,tag:'hermes-push-'+data.event,renotify:false,silent:claimed.replay,
      icon:new URL('static/favicon-192.png',self.registration.scope).href,
      badge:new URL('static/favicon-32.png',self.registration.scope).href,
      data:{privatePush:true,profile:data.profile,session:data.session},
    });
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const push=event.notification.data;
  if(push&&push.privatePush===true){
    if(!privatePushProfile(push.profile)||typeof push.session!=='string'
       ||!(/^[A-Za-z0-9_-]{1,128}$/.test(push.session)||push.session===''))return;
    const target=new URL(push.session?'session/'+encodeURIComponent(push.session):'./',self.registration.scope);
    target.searchParams.set('profile',push.profile);
    // Do not focus a same-session tab in a different profile or navigate an
    // unrelated dirty composer. Boot resolves profile before conversation.
    event.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(clients=>{
      const exact=clients.find(client=>client.url===target.href&&'focus' in client);
      if(exact)return exact.focus();
      return self.clients.openWindow?self.clients.openWindow(target.href):undefined;
    }));
    return;
  }
  const rawUrl = (event.notification.data && event.notification.data.url) || './';
  const targetUrl = new URL(rawUrl, self.registration.scope || './').href;
  const targetPath = new URL(targetUrl).pathname;
  const samePath = (clientUrl) => {
    try { return new URL(clientUrl).pathname === targetPath; } catch (_e) { return false; }
  };
  const sameOrigin = (clientUrl) => {
    try { return new URL(clientUrl).origin === self.location.origin; } catch (_e) { return false; }
  };
  // A panel-intent navigation is still worth it, but ONLY for a sessionless
  // app-root target carrying the supported Tasks intent (the cron completion
  // emits `/?panel=tasks`; see `_notificationOptions` in static/messages.js).
  // Honoring that intent means an already-open tab on the app root must
  // actually LOAD it — boot's saved-chat restore otherwise wins and the click
  // opens the wrong panel. Only that case pays for a query-string match plus a
  // navigate.
  //
  // Every other notification keeps master's behaviour: match on pathname alone
  // and focus(). A session deep link inherits the current page's query string,
  // including `panel` (`_sessionUrlForSid` in static/sessions.js retains it),
  // so ANY `/session/<sid>` target must be classified as a session link, never
  // a panel intent — otherwise the inherited query is misread as the special
  // intent and an already-open chat is dropped onto the navigate branch: a full
  // reload that discards composer text still inside the 400ms draft-save
  // window. Pathname matching is what prevents a duplicate window there, and
  // the panel-intent classification is restricted to the sessionless app root.
  const targetCarriesPanelIntent = (() => {
    const scopeRoot = (() => {
      try { return new URL(self.registration.scope || './').pathname.replace(/\/+$/, '') || '/'; }
      catch (_e) { return '/'; }
    })();
    const targetRoot = (() => {
      try { return targetPath.replace(/\/+$/, '') || '/'; }
      catch (_e) { return '/'; }
    })();
    if (targetRoot !== scopeRoot) return false; // takes the application root, so the current chat may never be reloaded
    try {
      return new URL(targetUrl).searchParams.get('panel') === 'tasks';
    } catch (_e) { return false; }
  })();
  const samePathAndSearch = (clientUrl) => {
    try {
      const c = new URL(clientUrl);
      return c.pathname === targetPath && c.search === new URL(targetUrl).search;
    } catch (_e) { return false; }
  };
  event.waitUntil(
    self.clients.matchAll({type: 'window', includeUncontrolled: true}).then((clientList) => {
      // Focus fast path. A panel intent is only honored when the URL carries
      // one: then the query string is part of the identity of the target, so
      // pathname + search must both match. Every other notification matches on
      // pathname alone, exactly as before, so an already-open chat is focus()ed
      // rather than reloaded.
      const targetClient = clientList.find((client) => (
        targetCarriesPanelIntent ? samePathAndSearch(client.url) : samePath(client.url)
      ) && 'focus' in client);
      if (targetClient) return targetClient.focus();

      const openNotificationWindow = () => (
        self.clients.openWindow ? self.clients.openWindow(targetUrl) : undefined
      );
      // Only a panel intent may reload a same-pathname tab. The intent lives in
      // the query string, so focusing alone would drop it and the user would
      // land on the restored chat; navigating makes boot see it. A session
      // notification must NOT come through here — reloading an open chat
      // discards composer text still inside the 400ms draft-save window — so it
      // falls through to the original any-same-origin-tab branch below.
      if (targetCarriesPanelIntent) {
        const samePathnameClient = clientList.find((client) => samePath(client.url) && 'navigate' in client);
        if (samePathnameClient) {
          return samePathnameClient.navigate(targetUrl)
            .then((client) => (client && 'focus' in client ? client.focus() : samePathnameClient.focus()))
            .catch(() => samePathnameClient.focus());
        }
      }
      const focusableClient = clientList.find((client) => sameOrigin(client.url) && 'focus' in client && 'navigate' in client);
      if (focusableClient && 'navigate' in focusableClient) {
        return focusableClient.navigate(targetUrl)
          .then((client) => (client && 'focus' in client ? client.focus() : focusableClient.focus()))
          .catch(() => focusableClient.focus());
      }
      return openNotificationWindow();
    })
  );
});
