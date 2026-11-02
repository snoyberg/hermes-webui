const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const root = process.argv[2];
const ui = fs.readFileSync(root+'/static/ui.js','utf8');
const workspace = fs.readFileSync(root+'/static/workspace.js','utf8');
function extract(src,start,end) { const a=src.indexOf(start); assert(a>=0,start); return src.slice(a,src.indexOf(end,a)); }
function scene(replies, stored=null) {
  const calls=[],toasts=[],reloads=[];
  const store=new Map(stored?[['hermes-native-agent-action-v2:owner',JSON.stringify({...stored,profile:'default',tracking:'restored'})]]:[]);
  const error={style:{},textContent:'',appendChild(){}};
  const button={disabled:false,textContent:''};
  const context={console,URL,AbortController,JSON,Promise,TypeError,Error,
    setTimeout:(fn,ms)=>{if(ms<30000)queueMicrotask(fn);return 1},clearTimeout(){},
    document:{baseURI:'http://localhost/',createElement:()=>({style:{},addEventListener(){}})},
    location:{href:'http://localhost/',pathname:'/',search:''},
    window:{_updateData:{agent:{native:true,behind:-1,update_available:true,can_apply:true,owner:'owner'},webui:{behind:1,channel:'kaladin'}}},
    localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},
    sessionStorage:{removeItem(){}},
    $:id=>id==='updateError'?error:id==='btnApplyUpdate'?button:null,
    showToast:(...a)=>toasts.push(a), _showUpdateError:(target,res)=>{error.textContent=res.message||res.status},
    _readHealthServerIdentity:async()=>'before',_waitForServerThenReload:o=>reloads.push(o),
    _formatUpdateApplyExceptionMessage:e=>e.message,_isUpdateApplyNetworkError:()=>false,
    fetch:async(url,opts)=>{
      if(new URL(url).pathname==='/api/updates')return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({agent:{owner:'owner'}})};
      calls.push({path:new URL(url).pathname,body:JSON.parse(opts.body),opts});
      const result=replies.shift();
      if(result instanceof Error) throw result;
      assert(result,'unexpected request '+url);
      if(result.httpStatus)return {ok:false,status:result.httpStatus,text:async()=> 'Unauthorized'};
      return {ok:true,headers:{get:()=> 'application/json'},json:async()=>result};
    }};
  context.window.location=context.location;
  vm.createContext(context);
  vm.runInContext(extract(workspace,'async function api(', '\nfunction '),context);
  vm.runInContext(extract(ui,'function _nativeAgentStored(', 'function _showUpdateError('),context);
  return {context,calls,toasts,reloads,store,error,button};
}
(async()=>{
  let s=scene([{ok:true,status:'accepted',action_id:'a'.repeat(32),owner:'owner'},
    {ok:false,status:'running',action_id:'a'.repeat(32)},
    {ok:true,status:'success',action_id:'a'.repeat(32),followups:[]},
    {ok:true,restart_scheduled:true}]);
  await s.context.applyUpdates();
  assert.deepStrictEqual(s.calls.map(c=>c.path),['/api/updates/apply','/api/updates/agent/status','/api/updates/agent/status','/api/updates/apply']);
  assert.strictEqual(s.calls[3].body.target,'webui');
  assert(s.calls.slice(0,3).every(c=>c.opts.redirect==='error'));
  assert.strictEqual(s.reloads.length,1);
  assert(!s.toasts.some(t=>String(t[0]).includes('accepted')));

  for(const state of ['unknown','superseded','partial','failed','refused']) {
    s=scene([{ok:true,status:'accepted',action_id:'a'.repeat(32),owner:'owner'},
      {ok:false,status:state,action_id:'a'.repeat(32),followups:[{step:'dependencies',reason:'repair needed'}]}]);
    await s.context.applyUpdates();
    assert.strictEqual(s.calls.length,2,state);
    assert.strictEqual(s.reloads.length,0,state);
    assert.strictEqual(s.toasts.length,0,state);
    assert(s.error.textContent.includes(state),state);
    assert(s.error.textContent.includes('repair needed'),state);
  }
  s=scene([new TypeError('Failed to fetch')]);
  await s.context.applyUpdates();
  assert.strictEqual(s.calls.length,1);
  assert.strictEqual(s.reloads.length,0);
  assert(JSON.parse(s.store.get('hermes-native-agent-action-v2:owner')).status==='unknown');
  await s.context.applyUpdates();
  assert.strictEqual(s.calls.length,1,'unknown submission must never replay');

  s=scene([{ok:true,status:'success',action_id:'a'.repeat(32),followups:[]}],
    {action_id:'a'.repeat(32),owner:'owner',status:'running'});
  await s.context._resumeNativeAgentUpdate();
  assert.strictEqual(s.calls[0].path,'/api/updates/agent/status');
  assert.strictEqual(s.calls.length,1);
  assert.strictEqual(s.reloads.length,0,'restored action never automatically reloads WebUI');
  s=scene([{httpStatus:401}]);
  await s.context.applyUpdates();
  assert.strictEqual(s.calls.length,1,'401 must not retry');
  assert.strictEqual(s.context.location.href,'http://localhost/','401 must not navigate');
  assert.strictEqual(s.reloads.length,0);
  assert(JSON.parse(s.store.get('hermes-native-agent-action-v2:owner')).status==='unknown');
  console.log(JSON.stringify({passed:9,failed:0,realNode:process.version}));
})().catch(e=>{console.error(e);process.exit(1)});
