const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const values=new Map();
const storage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)};
const writes=[];
let permissionCalls=0, subscribed=0, enabled=false;
const subscription={toJSON:()=>({endpoint:'https://web.push.apple.com/Qsynthetic',keys:{}})};
const worker={postMessage(data,ports){writes.push(data);ports[0].postMessage({ok:true});}};
const registration={active:worker,pushManager:{getSubscription:async()=>subscribed?subscription:null,
  subscribe:async options=>{assert(options.userVisibleOnly);subscribed++;return subscription;}}};
const elements={privatePushStatus:{textContent:''},privatePushEnable:{},privatePushTest:{},privatePushDisable:{}};
const context={console,Promise,URL,Uint8Array,MessageChannel,crypto:webcrypto,setTimeout,clearTimeout,
  atob:s=>Buffer.from(s,'base64').toString('binary'),
  window:{isSecureContext:true,PushManager:function(){},Notification:{}},
  Notification:{permission:'default',requestPermission:async()=>{permissionCalls++;return 'granted';}},
  navigator:{serviceWorker:{ready:Promise.resolve(registration),getRegistration:async()=>registration}},
  localStorage:storage,S:{activeProfile:'work',activeProfileIsDefault:false},
  document:{getElementById:id=>elements[id]},t:key=>key,
  api:async(path,options)=>{
    const body=JSON.parse(options.body);
    assert.equal(body.profile,'work');
    assert.match(body.device,/^[a-f0-9]{64}$/);
    if(path.endsWith('/enable')){
      assert.equal(context.privatePushOwnsAlerts(),true,'page producer must stop before server enrollment');
      enabled=true;
    }
    if(path.endsWith('/disable'))enabled=false;
    return {available:true,enabled,public_key:Buffer.alloc(65,1).toString('base64url'),profile:'work'};
  },showToast:()=>{},
};
context.window.Notification=context.Notification;
vm.createContext(context);
(async()=>{
  vm.runInContext(fs.readFileSync('static/private_push.js','utf8'),context);
  assert.equal(context.privatePushOwnsAlerts(),false);
  await context.privatePushAction('enable');
  assert.equal(permissionCalls,1);
  assert.equal(subscribed,1);
  assert.equal(context.privatePushOwnsAlerts(),true);
  assert.equal(writes[0].profile,'work');
  const messages=fs.readFileSync('static/messages.js','utf8');
  const start=messages.indexOf('function sendBrowserNotification(');
  const end=messages.indexOf('// ── /btw ephemeral stream',start);
  vm.runInContext(messages.slice(start,end),context);
  context.window._notificationsEnabled=true;
  context.Notification.permission='granted';
  context._showPwaNotification=async()=>{};
  // Push enrollment is the shared producer choice, even for force/page paths.
  assert.equal(context.sendBrowserNotification('SECRET','PRIVATE OUTPUT',{force:true}),false);
  await context.privatePushAction('test');
  assert.equal(subscribed,1);
  // A different profile must not inherit opt-in.
  context.S.activeProfile='personal';
  assert.equal(context.privatePushOwnsAlerts(),false);
  context.S.activeProfile='work';
  await context.privatePushAction('disable');
  assert.equal(context.privatePushOwnsAlerts(),false);
  assert.equal(writes.at(-1).enabled,false);
  assert.equal(subscribed,1,'disable must not unsubscribe other enrolled profiles');
  console.log('page: gesture enable, server enrollment, profile isolation, test and disable passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
