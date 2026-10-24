// Read-only source-extraction probe; no server, microphone or real network.
// Usage: node probe.cjs <source-directory> <label>
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = process.argv[2];
const label = process.argv[3] || root;
const load = name => fs.readFileSync(path.join(root, name), 'utf8');
const messages = load('messages.js'), ui = load('ui.js'), boot = load('boot.js');
const apiSource=between(load('workspace.js'),'async function api(','\nfunction recordClientSSEError(');
function between(source, start, end) {
  const a=source.indexOf(start), b=source.indexOf(end,a+start.length);
  assert(a>=0 && b>a, `missing extraction seam: ${start} / ${end}`);
  return source.slice(a,b);
}
const sendSource=between(messages,'async function send(){','\nasync function startRegeneration(');
const goalSource=between(messages,'function _normalizeGoalContinuationId(','\nfunction _bgTaskCompleteRingBufferAdd(');
const restoreSource=between(messages,'function _restoreComposerDraftAfterFailedSend(','\nasync function send(');
const uploadSource=ui.slice(ui.indexOf('async function uploadPendingFiles(options={})'));
assert(uploadSource.startsWith('async function uploadPendingFiles'), 'missing uploader');
const rawSource=between(boot,'  async function _sendRawAudio(blob){','  function _commitTranscript(');

async function runCase(name, options={}) {
  const input={value:options.text||'',dataset:{}};
  const state={uploads:[],starts:[],status:[],tray:[],clears:[],saves:[],streams:[],queued:[],toasts:[]};
  const file=new File([new Uint8Array([82,73,70,70,0,1,2,3])], 'voice-input-synthetic.webm',{type:'audio/webm'});
  const second=new File(['synthetic second fixture'], 'second.webm',{type:'audio/webm'});
  const S={session:{session_id:'A',title:'Synthetic fixture',model:'fixture-model',profile:'fixture'},pendingFiles:[file],busy:false,messages:[],activeProfile:'fixture'};
  if(options.partial) S.pendingFiles.push(second);
  let ordinal=0, release;
  const hold=options.hold?new Promise(resolve=>{release=resolve;}):null;
  const noop=()=>{};
  const context={console,File,Blob,FormData,URL,Map,Set,Promise,Date,TypeError,Error,AbortController,setTimeout,clearTimeout,MAX_UPLOAD_BYTES:1024*1024,_ARCHIVE_EXTS:/\.(zip|tar)$/i,
    document:{baseURI:'https://fixture.invalid/',querySelector:()=>null},location:{href:'https://fixture.invalid/'},
    window:{_micPendingSend:false},S,ta:input,_sendInProgress:false,_sendInProgressSid:null,_pendingSelections:[],_forcedSkillDirectivePending:null,
    INFLIGHT:{},_approvalSessionId:null,_clarifySessionId:null,_queueDrainSid:null,
    $:id=>id==='msg'?input:{style:{},dataset:{}},
    _flushSelectionBlocksToComposer:noop,_clearStaleBusyStateBeforeSend:noop,
    _composerTextWithPendingSelections:()=>input.value,
    _chatPayloadModelState:()=>({model:'fixture-model',model_provider:'fixture-provider'}),
    queueSessionMessage:(sid,payload)=>state.queued.push({sid,payload}),
    autoResize:noop,updateSendBtn:noop,hideCmdDropdown:noop,clearLiveToolCards:noop,appendThinking:noop,
    renderMessages:noop,renderSessionListFromCache:noop,startApprovalPolling:noop,startClarifyPolling:noop,_fetchYoloState:noop,
    stopApprovalPolling:noop,stopClarifyPolling:noop,hideApprovalCard:noop,hideClarifyCard:noop,removeThinking:noop,
    _runOptionalPreStartUiStep:(_name,fn)=>fn(),_runOptionalPostStartUiStep:noop,
    _recoverCompressedSend:async()=>false,
    setBusy:v=>{S.busy=v;},
    renderTray:()=>state.tray.push(S.pendingFiles.map(f=>f.name)),
    setStatus:v=>state.status.push(v),setComposerStatus:v=>state.status.push(v),
    showToast:v=>state.toasts.push(v),t:(key,...args)=>`${key}${args.length?':'+args.join(','):''}`,
    _uploadPendingFilesUpdateProgress:noop,_uploadPendingFilesCurrentSession:sid=>S.session.session_id===sid,
    _redirectIfUnauth:()=>false,
    _clearComposerDraft:(sid,text,files)=>{state.clears.push({sid,text,fileNames:files.map(f=>f.name)});return Promise.resolve();},
    _saveComposerDraftNow:(sid,text,files)=>state.saves.push({sid,text,fileNames:files.map(f=>f.name)}),
    api:async(url,opts)=>{assert.equal(url,'/api/chat/start');const payload=JSON.parse(opts.body);state.starts.push(payload);if(options.startFailure)throw new Error('synthetic start refusal');return {stream_id:'fixture-stream'};},
    attachLiveStream:(sid,stream,files)=>state.streams.push({sid,stream,files}),
    fetch:async(url,opts)=>{
      if(url.endsWith('/api/chat/start')){
        state.starts.push(JSON.parse(opts.body));
        if(options.startNetwork)throw new TypeError('response lost after possible acceptance');
        return {ok:!options.startUnauth,status:options.startUnauth?401:200,
          headers:{get:()=> 'application/json'},json:async()=>options.startMalformed?{}:{stream_id:'fixture-stream'}};
      }
      const i=ordinal++;
      const f=opts.body.get('file');
      state.uploads.push({url,name:f.name,size:f.size,session:opts.body.get('session_id')});
      if(hold) await hold;
      if(options.newDraft) input.value='new draft typed during upload';
      if(options.switchSession){S.session={session_id:'B',title:'Other fixture'};S.pendingFiles=[second];input.value='other session draft';}
      if(options.networkFailure) throw new TypeError('synthetic network interrupted');
      if(options.jsonFailure) return {ok:true,json:async()=>{throw new SyntaxError('synthetic invalid response');}};
      const fail=options.failure || (options.partial && i===1);
      if(fail) return {ok:false,text:async()=> 'synthetic HTTP 503',status:503};
      return {ok:true,json:async()=>({filename:f.name,path:`fixture/${f.name}`,mime:f.type,size:f.size,is_image:false})};
    },
  };
  vm.createContext(context);
  vm.runInContext(goalSource+'\n'+between(messages,'const _uploadRecoveries=','async function _recoverCompressedSend(')+restoreSource+'\n'+uploadSource+'\n'+sendSource+'\n'+rawSource,context);
  if(options.restoredToken) context._setRestoredGoalContinuationDraft(options.restoredToken,input.value);
  if(options.realApi)vm.runInContext(apiSource,context);
  if(options.rawHandoff){
    S.pendingFiles=[];
    context.window._micPendingSend=true;
    const realSend=context.send;
    let task;
    context.send=(...args)=>{task=realSend(...args);return task;};
    await context._sendRawAudio(new Blob(['synthetic raw recorder handoff'],{type:'audio/webm'}));
    await task;
  }else{
    const first=context.send({goalContinuationId:options.token||''});
    if(options.hold){await Promise.resolve();await Promise.resolve();await context.send();release();}
    await first;
  }
  await Promise.resolve();await Promise.resolve();
  const records=()=>vm.runInContext('[..._uploadRecoveries.values()]',context);
  if(options.act)await options.act({context,S,input,state,file,second,records,options,release});
  if(options.retry) await context.send();
  const result={name,pendingFiles:S.pendingFiles.map(f=>f.name),input:input.value,uploads:state.uploads.length,starts:state.starts.length,
    attachments:state.starts.map(p=>(p.attachments||[]).map(f=>f.name)),status:state.status,clears:state.clears.length,saves:state.saves.length,
    streams:state.streams.length,queued:state.queued.length,sendLockReleased:!context._sendInProgress};
  assert.equal(result.sendLockReleased,true);
  assert.equal(state.starts.length<=1,true,'one turn at most');
  return result;
}
(async()=>{
  const result=await runCase('text-and-audio',{failure:true,text:'Please listen'});
  assert.equal(result.starts,0,'must not admit an incomplete text-only turn');
  const cases=[result];
  for(const [name,config] of [
    ['all-fail',{failure:true}],
    ['text-audio',{failure:true,text:'Please listen'}],
    ['partial',{partial:true,text:'Both recordings'}],
    ['network',{networkFailure:true}],
    ['bad-json',{jsonFailure:true}],
    ['new-draft',{failure:true,newDraft:true}],
    ['other-session',{failure:true,switchSession:true}],
    ['other-profile',{failure:true}],
    ['repeat-failure',{failure:true}],
    ['discard',{failure:true}],
    ['unknown-start',{startFailure:true}],
    ['lost-start-response',{startNetwork:true,realApi:true,unknown:true}],
    ['malformed-start-response',{startMalformed:true,realApi:true,unknown:true}],
    ['start-auth-expired',{startUnauth:true,realApi:true,unknown:true}],
    ['navigate-success',{switchSession:true}],
    ['single-flight',{failure:true}],
    ['raw-handoff',{failure:true,rawHandoff:true}],
    ['goal-retry-newer-draft',{failure:true,text:'Original continuation',token:' original-token '}],
    ['restored-goal-retry-newer-draft',{failure:true,text:'Restored continuation',restoredToken:' restored-token '}],
    ['ordinary-retry-newer-goal-draft',{failure:true,text:'Original user turn'}],
  ]){
    config.act=async({context:c,S,input,state,file,second,records,options})=>{
      assert.equal(state.starts.length,(options.startFailure||options.unknown)?1:0,'no incomplete turn');
      const record=records()[0];assert(record,'failed request retained');
      assert.equal(record.sid,'A');assert.equal(record.profile,'fixture');
      if(!options.rawHandoff)assert.equal(record.items[0].file,file);
      assert.equal(record.items[0].file instanceof File,true);
      assert.equal(record.items[0].file.size>0,true);
      const id=record.id;
      if(options.startFailure||options.unknown){
        assert.equal(record.state,'uncertain');
        await c._retryUploadRecovery(id);assert.equal(state.starts.length,1);
        c._discardUploadRecovery(id);assert.equal(records().length,0);return;
      }
      assert.equal(record.state,'failed');
      if(name==='other-profile'){
        S.activeProfile='other';await c._retryUploadRecovery(id);
        assert.equal(state.uploads.length,1);assert.equal(state.starts.length,0);
        S.activeProfile='fixture';
      }
      if(options.switchSession){
        await c._retryUploadRecovery(id);assert.equal(state.starts.length,0);
        assert.equal(input.value,'other session draft');assert.equal(S.pendingFiles[0],second);
        S.session={session_id:'A',title:'Synthetic fixture'};options.switchSession=false;
      }
      if(name==='discard'){
        c._discardUploadRecovery(id);assert.equal(records().length,0);
        assert.equal(record.items.length,0);assert.equal(record.text,'');
        await c._retryUploadRecovery(id);assert.equal(state.uploads.length,1);return;
      }
      const before=state.uploads.length;
      const acknowledged=record.items.filter(item=>item.uploaded).length;
      const originalText=record.text;
      input.value='newer untouched draft';S.pendingFiles=[second];
      const goalCase=name.includes('retry-newer-');
      if(goalCase){
        assert.equal(record.goalContinuationId,(options.token||options.restoredToken||'').trim());
        c._setRestoredGoalContinuationDraft('newer-token',input.value);
      }
      if(name==='repeat-failure'){
        await c._retryUploadRecovery(id);assert.equal(record.state,'failed');
        assert.equal(state.starts.length,0);assert.equal(record.items[0].file,file);
      }
      options.failure=false;options.networkFailure=false;options.jsonFailure=false;options.partial=false;options.newDraft=false;
      if(name==='single-flight'){
        const first=c._retryUploadRecovery(id);await c._retryUploadRecovery(id);await first;
      }else await c._retryUploadRecovery(id);
      assert.equal(state.starts.length,1);assert.equal(records().length,0);
      assert.equal(input.value,'newer untouched draft');assert.equal(S.pendingFiles[0],second);
      assert.equal(state.starts[0].session_id,'A');assert.equal(state.starts[0].profile,'fixture');
      if(goalCase){
        assert.equal(state.starts[0].goal_continuation_id,(options.token||options.restoredToken||'').trim(),'retry uses only its retained token');
        assert.equal(input.dataset.goalContinuationId,'newer-token','retry must not consume newer draft token');
        assert.equal(input.dataset.goalContinuationText,'newer untouched draft');
        assert.equal(c._takeRestoredDraftGoalContinuationId(input.value),'newer-token','newer draft still owns its token');
      }
      if(originalText)assert(state.starts[0].message.startsWith(originalText));
      assert.equal(state.starts[0].attachments.length,options.rawHandoff?1:(name==='partial'?2:1));
      if(name!=='repeat-failure')assert.equal(state.uploads.length-before,(name==='partial'?2:1)-acknowledged,'retry only missing uploads');
      assert.equal(record.items.length,0,'release File references');
    };
    cases.push(await runCase(name,config));
  }
  console.log(JSON.stringify({caseCount:cases.length,cases}));
})().catch(err=>{console.error(err);process.exitCode=1;});
