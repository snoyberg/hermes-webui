// Deterministic transport/worker barriers around the actual page and worker.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const {IDBFactory}=require(process.env.PUSH_FAKE_INDEXEDDB||'fake-indexeddb');
function barrier(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
function fixture(){
  const values=new Map([['hermes-private-push:work','enabled']]);
  const server=new Map([['work',true],['personal',false]]);
  const elements={privatePushStatus:{textContent:''},privatePushEnable:{},privatePushTest:{},privatePushDisable:{}};
  const hooks={};const writes=[];const handlers={};
  const sw={self:{location:{origin:'https://hermes.example'},registration:{scope:'https://hermes.example/'},
    addEventListener:(type,handler)=>handlers[type]=handler},indexedDB:new IDBFactory(),URL,Date,Promise};
  vm.createContext(sw);vm.runInContext(fs.readFileSync('static/sw.js','utf8'),sw);
  const registration={active:{postMessage(data,ports){
    writes.push(data);
    const replyPorts=hooks.ack?[{postMessage:reply=>hooks.ack(data,()=>ports[0].postMessage(reply))}]:ports;
    const deliver=()=>handlers.message({data,ports:replyPorts,source:{url:'https://hermes.example/'},waitUntil:p=>p});
    if(hooks.message)hooks.message(data,deliver);else deliver();
  }}};
  const context={Promise,URL,Uint8Array,MessageChannel,crypto:webcrypto,setTimeout,clearTimeout,
    window:{isSecureContext:true,PushManager:function(){},Notification:{}},
    navigator:{serviceWorker:{getRegistration:async()=>hooks.registration?hooks.registration():registration}},
    localStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)},
    S:{activeProfile:'work',activeProfileIsDefault:false},document:{getElementById:id=>elements[id]},t:key=>key,
    api:async(path,options)=>{
      const {profile}=JSON.parse(options.body);
      if(path.endsWith('/disable'))server.set(profile,false);
      const status={available:true,enabled:server.get(profile)||false};
      return hooks.response?hooks.response(path,profile,status):status;
    },showToast:()=>{},
  };
  vm.createContext(context);vm.runInContext(fs.readFileSync('static/private_push.js','utf8'),context);
  const consent=profile=>vm.runInContext(`privatePushState((store,done)=>{const r=store.get('profile:${profile}');r.onsuccess=()=>done(r.result);})`,sw);
  return {context,server,elements,hooks,writes,registration,values,consent};
}
async function completedDisable(){
  const f=fixture(),entered=barrier(),release=barrier();
  const actualWorker=f.context.privatePushWorker;let first=true;
  // Delay only the page continuation after the actual worker ack/commit.
  f.context.privatePushWorker=async(...args)=>{
    const registration=await actualWorker(...args);
    if(first&&args[1]){first=false;entered.resolve();await release.promise;}
    return registration;
  };
  const refresh=f.context.privatePushRefresh();await entered.promise;
  await f.context.privatePushAction('disable');
  assert.equal(f.context.privatePushOwnsAlerts(),false);
  assert.equal(await f.consent('work'),false);
  release.resolve();await refresh;
  assert.equal(f.server.get('work'),false);
  assert.equal(f.context.privatePushOwnsAlerts(),false,'stale refresh undid a completed Disable');
  assert.equal(await f.consent('work'),false);
  assert.match(f.elements.privatePushStatus.textContent,/Off/);
}
async function staleResponse(){
  const f=fixture(),entered=barrier(),release=barrier();let first=true;
  f.hooks.response=async(path,profile,status)=>{
    if(first&&path.endsWith('/status')){first=false;entered.resolve();await release.promise;}return status;
  };
  const refresh=f.context.privatePushRefresh();await entered.promise;
  await f.context.privatePushAction('disable');release.resolve();await refresh;
  assert.equal(f.server.get('work'),false);
  assert.equal(f.context.privatePushOwnsAlerts(),false,'stale status restored page suppression');
  assert.equal(await f.consent('work'),false,'stale status restored worker consent');
}
async function staleWorker(stage){
  const f=fixture(),entered=barrier(),release=barrier(),disabled=barrier();let first=true;
  f.hooks.response=async(path,profile,status)=>{if(path.endsWith('/disable'))disabled.resolve();return status;};
  if(stage==='registration')f.hooks.registration=async()=>{
    if(first){first=false;entered.resolve();await release.promise;}return f.registration;
  };
  else f.hooks.ack=(data,reply)=>{
    if(first&&data.enabled){first=false;entered.resolve();release.promise.then(reply);}else reply();
  };
  const refresh=f.context.privatePushRefresh();await entered.promise;
  const disable=f.context.privatePushAction('disable');await disabled.promise;
  // Drain the ready task: a newer worker write must not overtake this ack.
  await new Promise(setImmediate);
  const overtook=f.writes.some(write=>!write.enabled);
  release.resolve();await Promise.all([refresh,disable]);
  assert.equal(overtook,false,`${stage}: worker consent writes overtook an unconfirmed predecessor`);
  assert.equal(f.context.privatePushOwnsAlerts(),false,`${stage}: stale refresh restored page suppression`);
  assert.equal(await f.consent('work'),false,`${stage}: stale refresh overwrote worker disable`);
  assert.match(f.elements.privatePushStatus.textContent,/Off/);
}
async function switchedAction(){
  const f=fixture(),entered=barrier(),release=barrier();
  f.hooks.response=async(path,profile,status)=>{
    if(path.endsWith('/disable')){entered.resolve();await release.promise;throw new Error('old action failure');}return status;
  };
  const action=f.context.privatePushAction('disable');await entered.promise;
  f.context.S.activeProfile='personal';await f.context.privatePushRefresh();
  const text=f.elements.privatePushStatus.textContent;release.resolve();await action;
  assert.equal(f.elements.privatePushStatus.textContent,text,'old action error overwrote current profile settings');
}
async function switchedProfile(stage){
  const f=fixture(),entered=barrier(),release=barrier();let first=true;
  f.values.delete('hermes-private-push:work');
  if(stage==='response'||stage==='error')f.hooks.response=async(path,profile,status)=>{
    if(first){first=false;entered.resolve();await release.promise;if(stage==='error')throw new Error('old profile failure');}return status;
  };
  else f.hooks.message=(data,deliver)=>{
    if(first){first=false;entered.resolve();release.promise.then(deliver);}else deliver();
  };
  const refresh=f.context.privatePushRefresh();await entered.promise;
  f.context.S.activeProfile='personal';await f.context.privatePushRefresh();
  const text=f.elements.privatePushStatus.textContent;
  release.resolve();await refresh;
  assert.equal(f.elements.privatePushStatus.textContent,text,`${stage}: old profile changed current settings`);
  assert.equal(f.context.privatePushOwnsAlerts(),false);
  assert.equal(f.values.has('hermes-private-push:personal'),false);
  assert.equal(f.values.has('hermes-private-push:work'),false,`${stage}: stale profile wrote page storage`);
  if(stage!=='ack')assert.equal(f.writes.some(write=>write.profile==='work'),false,'stale response wrote worker consent');
}
(async()=>{
  const selected=process.argv[2];
  const cases={'action-profile-error':switchedAction,'completed-disable':completedDisable,response:staleResponse,registration:()=>staleWorker('registration'),ack:()=>staleWorker('ack'),
    'profile-response':()=>switchedProfile('response'),'profile-ack':()=>switchedProfile('ack'),'profile-error':()=>switchedProfile('error')};
  for(const [name,run] of Object.entries(cases))if(!selected||selected===name){await run();console.log(`race: ${name} passed`);}
})().catch(error=>{console.error(error);process.exitCode=1;});
