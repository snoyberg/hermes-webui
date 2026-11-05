const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {IDBFactory}=require(process.env.PUSH_FAKE_INDEXEDDB||'fake-indexeddb');
const indexedDB=new IDBFactory();
const shown=[];let failDisplay=false;
function boot(){
  const handlers={};
  const context=vm.createContext({indexedDB,URL,Date,Promise,self:{
    location:{origin:'https://hermes.example'},
    registration:{scope:'https://hermes.example/',showNotification:async(title,options)=>{
      if(failDisplay)throw new Error('uncertain OS display');shown.push({title,...options});
    }},addEventListener:(type,handler)=>handlers[type]=handler,
  }});
  vm.runInContext(fs.readFileSync('static/sw.js','utf8'),context);
  const dispatch=async(type,extra)=>{let pending;handlers[type]({...extra,waitUntil:p=>{pending=p;}});await pending;};
  return {context,dispatch};
}
const payload={v:1,kind:'complete',profile:'work',session:'desktop',event:'d'.repeat(64)};
const push=data=>({data:{json:()=>data}});
(async()=>{
  let worker=boot();
  await worker.dispatch('message',{data:{type:'private-push-profile',profile:'work',enabled:true},
    source:{url:'https://hermes.example/'},ports:[{postMessage(){}}]});
  failDisplay=true;
  await assert.rejects(worker.dispatch('push',push(payload)),/uncertain OS display/);
  failDisplay=false;worker=boot(); // worker restart must retain the attempted display
  await worker.dispatch('push',push(payload));
  assert.equal(shown.length,1);
  assert.equal(shown[0].silent,true,'uncertain display must not become another audible retry');
  assert.equal(shown[0].renotify,false);
  await worker.dispatch('push',push(payload));
  assert.equal(shown.length,2,'replay after restart still must display');
  assert.equal(shown[1].tag,shown[0].tag);
  assert.equal(shown[1].silent,true);
  const original=worker.context.privatePushDatabase;
  worker.context.privatePushDatabase=async()=>{
    const db=await original();const transaction=db.transaction.bind(db);
    db.transaction=(...args)=>{const tx=transaction(...args);queueMicrotask(()=>tx.abort());return tx;};
    return db;
  };
  const unclaimed={...payload,event:'e'.repeat(64)};
  await assert.rejects(worker.dispatch('push',push(unclaimed)),/Push storage unavailable/);
  assert.equal(shown.length,2,'unknown consent/failed claim must not display');
  worker.context.privatePushDatabase=original;
  await worker.dispatch('push',push(unclaimed));
  assert.equal(shown.at(-1).silent,false,'aborted claim did not attempt display');
  const concurrent={...payload,event:'f'.repeat(64)};
  const start=shown.length;
  await Promise.all([worker.dispatch('push',push(concurrent)),worker.dispatch('push',push(concurrent))]);
  const pair=shown.slice(start);
  assert.equal(pair.length,2,'concurrent receipts both need visible display');
  assert.equal(pair.filter(n=>!n.silent).length,1,'only one concurrent receipt may request sound');
  assert.equal(pair[0].tag,pair[1].tag);
  const before=shown.length;
  await worker.dispatch('push',push({...payload,session:'SECRET PATH/invalid'}));
  await worker.dispatch('push',{data:{json:()=>{throw new Error('invalid JSON');}}});
  await worker.dispatch('push',push({...payload,profile:'unknown'}));
  assert.equal(shown.length,before,'malformed/unknown input must not create alerts or infer consent');
  console.log('worker failures: uncertain display/restart, aborted claim, concurrent replay and malformed/unknown consent passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
