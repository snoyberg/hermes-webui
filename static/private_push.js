// Explicit device/profile consent. The server is the only alert producer while
// enrolled; the existing page-only path remains unchanged for other profiles.
const _privatePushBusy=new Set();
const _privatePushRevision=new Map();
const _privatePushWorkerWrites=new Map();
function privatePushProfileName(){
  return S.activeProfileIsDefault?'default':(S.activeProfile||'default');
}
function privatePushOwnsAlerts(){
  try{return localStorage.getItem('hermes-private-push:'+privatePushProfileName())==='enabled';}
  catch(_){return false;}
}
function privatePushDevice(){
  let device=localStorage.getItem('hermes-private-push-device');
  if(!device||!/^[a-f0-9]{64}$/.test(device)){
    device=Array.from(crypto.getRandomValues(new Uint8Array(32)),x=>x.toString(16).padStart(2,'0')).join('');
    localStorage.setItem('hermes-private-push-device',device);
  }
  return device;
}
function privatePushText(key,fallback){return typeof t==='function'&&t(key)!==key?t(key):fallback;}
function privatePushSupported(){
  return !!(window.isSecureContext&&navigator.serviceWorker&&window.PushManager&&window.Notification);
}
async function privatePushRequest(action,profile,extra={}){
  return api('/api/push/'+action,{method:'POST',body:JSON.stringify({device:privatePushDevice(),profile,...extra})});
}
function privatePushWorker(profile,enabled,current=()=>true){
  // A delayed registration lookup or worker ack must not let an older consent
  // write land after Disable. Queue per profile; other profiles stay independent.
  const pending=(_privatePushWorkerWrites.get(profile)||Promise.resolve()).then(async()=>{
    if(!current())return null;
    const registration=await navigator.serviceWorker.getRegistration();
    if(!current())return null;
    if(!registration||!registration.active)throw new Error('Service worker is not ready. Reload Hermes and retry.');
    await new Promise((resolve,reject)=>{
      const channel=new MessageChannel();
      const timer=setTimeout(()=>{channel.port1.close();reject(new Error('Service worker did not confirm device settings.'));},4000);
      channel.port1.onmessage=event=>{
        clearTimeout(timer);channel.port1.close();
        if(event.data&&event.data.ok)resolve();else reject(new Error('Private push storage unavailable.'));
      };
      registration.active.postMessage({type:'private-push-profile',profile,enabled},[channel.port2]);
    });
    return registration;
  });
  const tail=pending.catch(()=>{});
  _privatePushWorkerWrites.set(profile,tail);
  tail.then(()=>{if(_privatePushWorkerWrites.get(profile)===tail)_privatePushWorkerWrites.delete(profile);});
  return pending;
}
function privatePushRender(status,profile){
  if(profile!==privatePushProfileName())return;
  const el=document.getElementById('privatePushStatus');
  if(el)el.textContent=status.available
    ?(status.enabled?privatePushText('push_enabled','Enabled for this device and profile.'):privatePushText('push_disabled','Off for this device and profile.'))
    :privatePushText('push_unavailable','Unavailable. Ask the server administrator to configure private Web Push.');
  for(const [id,disabled] of [['privatePushEnable',!status.available||status.enabled],
    ['privatePushTest',!status.enabled],['privatePushDisable',!status.enabled]]){
    const button=document.getElementById(id);if(button)button.disabled=disabled;
  }
}
async function privatePushRefresh(){
  const profile=privatePushProfileName();
  if(_privatePushBusy.has(profile))return;
  const revision=(_privatePushRevision.get(profile)||0)+1;
  _privatePushRevision.set(profile,revision);
  const current=()=>profile===privatePushProfileName()&&!_privatePushBusy.has(profile)
    &&_privatePushRevision.get(profile)===revision;
  if(!privatePushSupported()){
    privatePushRender({available:false,enabled:false},profile);
    const el=document.getElementById('privatePushStatus');
    if(el)el.textContent=privatePushText('push_unsupported','Requires HTTPS and Web Push support. On iPhone, open the Home Screen app.');
    return;
  }
  try{
    const status=await privatePushRequest('status',profile);
    if(!current())return;
    // Refresh never enrolls. Consent cannot be reconstructed from a browser
    // subscription alone, or accidentally migrate across profiles/devices.
    if(!status.available||!status.enabled){
      if(localStorage.getItem('hermes-private-push:'+profile)==='enabled')await privatePushWorker(profile,false,current);
      if(!current())return;
      localStorage.removeItem('hermes-private-push:'+profile);
    }else{
      await privatePushWorker(profile,true,current);
      if(!current())return;
      localStorage.setItem('hermes-private-push:'+profile,'enabled');
    }
    privatePushRender(status,profile);
  }catch(_){
    if(!current())return;
    // Keep existing suppression during a network failure; do not silently
    // switch back to content-bearing page alerts for an enrolled device.
    const el=document.getElementById('privatePushStatus');
    if(el)el.textContent=privatePushText('push_status_error','Unable to check push settings. Reconnect and retry.');
  }
}
async function privatePushAction(action){
  const profile=privatePushProfileName();
  if(_privatePushBusy.has(profile))return;
  _privatePushBusy.add(profile);
  _privatePushRevision.set(profile,(_privatePushRevision.get(profile)||0)+1);
  const wasEnabled=privatePushOwnsAlerts();
  try{
    if(!privatePushSupported())throw new Error(privatePushText('push_unsupported','Requires HTTPS and Web Push support. On iPhone, open the Home Screen app.'));
    let status;
    if(action==='enable'){
      // Must happen before the first awaited network call: iOS permission
      // requires the direct user gesture from Enable, not an automatic poll.
      const permission=await Notification.requestPermission();
      if(permission!=='granted')throw new Error(privatePushText('push_permission','Allow notifications in browser settings, then try Enable again.'));
      status=await privatePushRequest('status',profile);
      if(!status.available)throw new Error(privatePushText('push_unavailable','Private Web Push is not configured on this server.'));
      const registration=await privatePushWorker(profile,true);
      try{
        let subscription=await registration.pushManager.getSubscription();
        if(!subscription){
          const base64=status.public_key.replace(/-/g,'+').replace(/_/g,'/');
          const key=Uint8Array.from(atob(base64+'='.repeat((4-base64.length%4)%4)),c=>c.charCodeAt(0));
          subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
        }
        // Stop page producers before the server can enqueue its first push.
        // localStorage also carries that choice to other tabs on this device.
        localStorage.setItem('hermes-private-push:'+profile,'enabled');
        status=await privatePushRequest('enable',profile,{subscription:subscription.toJSON()});
        if(!status.available||!status.enabled)throw new Error('Server did not confirm enrollment.');
        localStorage.setItem('hermes-private-push:'+profile,'enabled');
      }catch(error){
        // A lost response can hide a committed enrollment. Read it back before
        // restoring page alerts; unknown must retain generic-only suppression.
        const observed=await privatePushRequest('status',profile).catch(()=>null);
        if(observed&&!observed.enabled&&!wasEnabled){
          localStorage.removeItem('hermes-private-push:'+profile);
          await privatePushWorker(profile,false).catch(()=>{});
        }
        throw error;
      }
    }else if(action==='disable'){
      status=await privatePushRequest('disable',profile);
      await privatePushWorker(profile,false);
      localStorage.removeItem('hermes-private-push:'+profile);
      // Keep the browser subscription: another explicitly enrolled profile
      // may use it. Server enrollment and worker consent are profile-scoped.
    }else if(action==='test'){
      status=await privatePushRequest('test',profile);
      if(status.queued&&typeof showToast==='function')showToast(privatePushText('push_test_queued','Test queued. Provider acceptance does not prove phone display.'));
    }else return;
    privatePushRender(status,profile);
  }catch(error){
    if(profile!==privatePushProfileName())return;
    const el=document.getElementById('privatePushStatus');
    if(el)el.textContent=error.message;
    if(typeof showToast==='function')showToast(error.message,4000,'error');
  }finally{
    _privatePushBusy.delete(profile);
  }
}
