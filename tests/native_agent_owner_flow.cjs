const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[2]+'/static/ui.js','utf8');
const block=source.slice(source.indexOf('function _nativeAgentStored('),source.indexOf('function _showUpdateError('));
function scene(){
  const store=new Map(),calls=[],renders=[];let reply;
  const c={console,JSON,Promise,Error,Date,Math,S:{activeProfile:'A'},
    window:{_updateData:{agent:{owner:'A',native:true,update_available:true,can_apply:true}}},
    $:()=>null,showToast(){},_readHealthServerIdentity:async()=>'before',
    _formatUpdateApplyExceptionMessage:e=>e.message,_isUpdateApplyNetworkError:()=>false,
    localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v)},
    setTimeout:fn=>{queueMicrotask(fn);return 1},
    api:async(path,options)=>{
      if(path==='/api/updates')return {agent:{owner:c.S.activeProfile}};
      calls.push({path,body:JSON.parse(options.body)});
      return reply(path,options);
    }};
  vm.createContext(c);vm.runInContext(block,c);
  c._renderNativeAgentOutcome=r=>renders.push(r);
  return {c,store,calls,renders,setReply:fn=>{reply=fn}};
}
function deferred(){let resolve;const promise=new Promise(r=>{resolve=r});return {promise,resolve};}
(async()=>{
  // Pending A acceptance must retain A's evidence while B can submit independently.
  let s=scene(),a=deferred();
  s.setReply(async()=>a.promise);
  const pending=s.c.applyUpdates();await new Promise(setImmediate);
  await s.c.applyUpdates();assert.strictEqual(s.calls.length,1,'same owner double click is single flight');
  s.c.S.activeProfile='B';
  s.setReply(async path=>path.endsWith('/apply')?{status:'accepted',owner:'B',action_id:'b'.repeat(32)}:{status:'success',action_id:'b'.repeat(32)});
  await s.c.applyUpdates();
  assert.strictEqual(s.calls.filter(x=>x.path.endsWith('/apply')).length,2,'A must not block B');
  const bBefore=JSON.stringify([...s.store].filter(([k])=>k.includes('B')));
  a.resolve({status:'accepted',owner:'A',action_id:'a'.repeat(32)});await pending;
  assert.strictEqual(JSON.stringify([...s.store].filter(([k])=>k.includes('B'))),bBefore,'late A cannot clobber B');
  assert([...s.store.values()].some(v=>JSON.parse(v).owner==='A'&&JSON.parse(v).action_id==='a'.repeat(32)),'A correlation retained');
  assert.strictEqual(s.renders.at(-1).status,'success','late A cannot repaint B');

  // Lost acceptance stays owned, non-replaying; unrelated B and unowned legacy do not block.
  s=scene();s.store.set('hermes-native-agent-action-v1',JSON.stringify({status:'unknown'}));
  s.setReply(async()=>{throw new Error('lost acceptance')});await s.c._submitNativeAgentUpdate();
  await s.c._submitNativeAgentUpdate();assert.strictEqual(s.calls.length,1);
  assert([...s.store.values()].some(v=>JSON.parse(v).owner==='A'&&JSON.parse(v).status==='unknown'));
  s.c.S.activeProfile='B';await s.c._submitNativeAgentUpdate();assert.strictEqual(s.calls.length,2);
  assert.strictEqual(s.store.get('hermes-native-agent-action-v1'),JSON.stringify({status:'unknown'}),'legacy evidence retained');

  // Older poll cannot overwrite replacement action even for the same owner.
  s=scene();s.c.window._nativeAgentView={owner:'A',profile:'A'};
  const old={owner:'A',profile:'A',tracking:'old',action_id:'a'.repeat(32),status:'running'};
  s.c._nativeAgentSave(old);const poll=deferred();s.setReply(async()=>poll.promise);
  const polling=s.c._pollNativeAgentUpdate(old);await new Promise(setImmediate);
  assert.strictEqual(s.calls.length,1,'old status request must actually be in flight');
  const newer={...old,tracking:'new',action_id:'b'.repeat(32)};s.c._nativeAgentSave(newer);
  poll.resolve({status:'success',action_id:old.action_id});await polling;
  assert.strictEqual(s.c._nativeAgentStored('A').action_id,newer.action_id);
  assert.strictEqual(s.renders.length,0,'superseded poll cannot render');
  // In-flight old-profile polls cannot save or render after switching owners.
  for(const installationOnly of [false,true]){
    s=scene();s.c.window._nativeAgentView={owner:'A',profile:'A'};s.c._nativeAgentSave(old);
    const wait=deferred();s.setReply(async()=>wait.promise);
    const p=s.c._pollNativeAgentUpdate(old);await new Promise(setImmediate);assert.strictEqual(s.calls.length,1);
    if(!installationOnly)s.c.S.activeProfile='B';
    s.c.window._nativeAgentView={owner:'B',profile:s.c.S.activeProfile};
    const b={owner:'B',profile:s.c.S.activeProfile,tracking:'B',action_id:'b'.repeat(32),status:'running'};
    s.c._nativeAgentSave(b);
    wait.resolve({status:'success',action_id:old.action_id});await p;
    assert.strictEqual(s.c._nativeAgentStored('A').status,'running');
    assert.strictEqual(s.c._nativeAgentStored('B').tracking,'B');assert.strictEqual(s.renders.length,0);
  }
  // Switching while the inert identity GET is pending sends no consumptive POST.
  s=scene();const identity=deferred();s.c.api=async()=>identity.promise;
  const resolving=s.c._submitNativeAgentUpdate();await new Promise(setImmediate);
  s.c.S.activeProfile='B';identity.resolve({agent:{owner:'A'}});await resolving;
  assert.strictEqual(s.store.size,0);assert.strictEqual(s.calls.length,0);
  // Newer installation resolution wins even when the profile label is unchanged.
  s=scene();const older=deferred();let gets=0;
  s.c.api=async()=>++gets===1?older.promise:{agent:{owner:'B'}};
  const first=s.c._nativeAgentResolveOwner();await new Promise(setImmediate);
  await s.c._nativeAgentResolveOwner();older.resolve({agent:{owner:'A'}});
  assert.strictEqual(await first,null);assert.strictEqual(s.c.window._nativeAgentView.owner,'B');
  // A parallel read cannot regress an already observed terminal outcome.
  s=scene();s.c.window._nativeAgentView={owner:'A',profile:'A'};s.c._nativeAgentSave(old);
  const late=deferred();s.setReply(async()=>late.promise);
  const latePoll=s.c._pollNativeAgentUpdate(old);await new Promise(setImmediate);
  s.c._nativeAgentSave({...old,status:'success'},old);
  late.resolve({status:'running',action_id:old.action_id});await latePoll;
  assert.strictEqual(s.c._nativeAgentStored('A').status,'success');assert.strictEqual(s.renders.length,0);
  console.log(JSON.stringify({passed:8,failed:0}));
})().catch(e=>{console.error(e);process.exit(1)});
