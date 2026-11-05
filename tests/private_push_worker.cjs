// Synthetic browser worker fixture: actual sw.js, IndexedDB and event handlers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {indexedDB} = require(process.env.PUSH_FAKE_INDEXEDDB || 'fake-indexeddb');
const handlers = {};
const shown = [];
const opened = [];
const self = {
  location: {origin:'https://hermes.example'},
  registration: {scope:'https://hermes.example/app/', showNotification:async (...args)=>shown.push(args)},
  clients: {claim(){}, matchAll:async()=>[], openWindow:async url=>opened.push(url)},
  addEventListener: (type, handler)=>handlers[type]=handler,
};
const context=vm.createContext({self,indexedDB,URL,console,Date,Promise});
function loadWorker(){vm.runInContext(fs.readFileSync('static/sw.js','utf8'),context);}
loadWorker();
async function dispatch(type, extra){
  assert.equal(typeof handlers[type], 'function', `missing ${type} worker handler`);
  let pending;
  handlers[type]({...extra, waitUntil(p){pending=p;}});
  await pending;
}
(async()=>{
  await dispatch('message', {data:{type:'private-push-profile', profile:'work', enabled:true},
    source:{url:'https://hermes.example/app/'}, ports:[{postMessage(){}}]});
  const payload={v:1,kind:'approval',profile:'work',session:'desktop-started',event:'a'.repeat(64),
    title:'SECRET COMMAND',body:'SECRET QUESTION',url:'https://evil.test/'};
  await dispatch('push', {data:{json:()=>payload}});
  assert.equal(shown.length,1);
  assert.equal(shown[0][0],'Hermes');
  assert.equal(shown[0][1].body,'Open Hermes to review a pending request.');
  assert(!JSON.stringify(shown).includes('SECRET'));
  await dispatch('push', {data:{json:()=>payload}});
  assert.equal(shown.length,2, 'every enrolled push must display, including a transport retry');
  assert.equal(shown[1][1].tag,shown[0][1].tag,'retry must replace the same notification');
  assert.equal(shown[1][1].renotify,false);
  assert.equal(shown[1][1].silent,true,'retry must not request another audible alert');
  await dispatch('push', {data:{json:()=>({...payload,profile:'personal',event:'b'.repeat(64)})}});
  assert.equal(shown.length,2, 'foreign profile displayed');
  await dispatch('notificationclick', {notification:{close(){},data:shown[0][1].data}});
  assert.equal(opened[0],'https://hermes.example/app/session/desktop-started?profile=work');
  await dispatch('message', {data:{type:'private-push-profile',profile:'work',enabled:false},
    source:{url:'https://hermes.example/app/'}, ports:[{postMessage(){}}]});
  await dispatch('push', {data:{json:()=>({...payload,event:'c'.repeat(64)})}});
  assert.equal(shown.length,2,'disabled profile displayed');
  const originalDatabase=context.privatePushDatabase;
  let release,entered,secondEntered=false,calls=0;
  const firstEntered=new Promise(resolve=>{entered=resolve;});
  const blocked=new Promise(resolve=>{release=resolve;});
  context.privatePushDatabase=async()=>{
    if(++calls===1){entered();await blocked;}else secondEntered=true;
    return originalDatabase();
  };
  const consent=enabled=>dispatch('message',{data:{type:'private-push-profile',profile:'work',enabled},
    source:{url:'https://hermes.example/app/'},ports:[{postMessage(){}}]});
  const old=consent(true);await firstEntered;
  const newer=consent(false);await Promise.resolve();await Promise.resolve();
  const overtook=secondEntered;release();await Promise.all([old,newer]);
  context.privatePushDatabase=originalDatabase;
  assert.equal(overtook,false,'worker must order consent writes even after a page acknowledgment timeout');
  const finalConsent=await context.privatePushState((store,done)=>{
    const request=store.get('profile:work');request.onsuccess=()=>done(request.result);
  });
  assert.equal(finalConsent,false);
  console.log('worker: generic visible replay, profile opt-in, disable, ordered consent and click passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
