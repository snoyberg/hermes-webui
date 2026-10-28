// Compose production session creation/navigation/draft cleanup with the upload gate.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assert=require('node:assert/strict');
const {runCase}=require('./upload_recovery.cjs');
const root=process.argv[2];
const sessions=fs.readFileSync(path.join(root,'sessions.js'),'utf8');
function between(start,end){return sessions.slice(sessions.indexOf(start),sessions.indexOf(end,sessions.indexOf(start)+start.length));}
function fn(name){const start=sessions.search(new RegExp(`^(?:async )?function ${name}\\(`,'m'));const tail=sessions.slice(start);const end=tail.slice(1).search(/\n(?:async )?function /);assert(start>=0&&end>=0);return tail.slice(0,end+1);}
const lifecycle=between('let _loadingSessionId = null;','const SESSION_VIEWED_COUNTS_KEY')
  +'\nlet _newSessionInFlight=null;\n'+fn('newSession')+'\n'+fn('loadSession');
async function scenario(name,extra={}){
  let owner,drafts={},savedFiles={},created=0,reads=[];
  return runCase(name,{failure:true,realApi:true,...extra,
    setup:async({context:c,S,input})=>{
      const noop=()=>{};
      const store=new Map();
      Object.assign(c,{localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)},
        applySessionTitleUpdate:noop,
        _activeProject:null,NO_PROJECT_FILTER:'none',_sessionSourceFilter:'all',_messagesTruncated:false,_oldestIdx:0,
        _loadingOlder:false,_yoloEnabled:false,
        _setNewSessionPending:noop,_setSessionViewedCount:noop,updateQueueBadge:noop,syncTopbar:noop,
        _setActiveSessionUrl:noop,loadDir:async()=>{},_rearmActiveSessionStream:noop,_updateYoloPill:noop,
        _clearSameSessionForceReloadHint:noop,_clearDeferredActiveSessionExternalRefresh:noop,
        _resolveSessionModelForDisplaySoon:noop,_acknowledgeSessionVisit:noop,
        _selectLiveRecoveryInflight:()=>null,_mergePendingSessionMessage:noop,
        _isMessagingSession:()=>false,_hideHandoffHint:noop,_sessionProfileMismatchFromError:()=>null,
        _resolveSessionIdFromSidebarLineage:sid=>sid==='new-1'?'wrong-destination':sid,
        _deferWorkspaceRefreshForSession:noop,_ensureMessagesLoaded:async()=>{S.messages=[];},
      });
      c.window._clearPendingSelections=noop;
      c.window.location={pathname:'/',search:'',href:'https://fixture.invalid/'};
      vm.runInContext(lifecycle,c);
      const uploadFetch=c.fetch;
      c.fetch=async(url,opts)=>{
        const response=data=>({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>data});
        const body=JSON.parse(typeof opts.body==='string'?opts.body:'{}');
        if(url.endsWith('/delayed-auth')){
          await new Promise(resolve=>{c.releaseAuth=resolve;});
          return {ok:false,status:401};
        }
        if(url.endsWith('/api/session/new')){
          const sid='new-'+(++created);
          const session={session_id:sid,title:'Untitled',messages:[],message_count:0,profile:'fixture',workspace:'fixture-workspace'};
          savedFiles[sid]=session;
          if(sid==='new-1')session.continuation_session_id='wrong-destination';
          return response({session:{...session}});
        }
        if(url.endsWith('/api/session/draft')){
          drafts[body.session_id]={text:body.text,files:body.files,goal_continuation_id:body.goal_continuation_id};
          return response({ok:true,draft:drafts[body.session_id]});
        }
        if(url.includes('/api/session?')){
          const sid=new URL(url).searchParams.get('session_id');reads.push(sid);
          if(extra.metadataFailure){return {ok:false,status:extra.metadataFailure,headers:{get:()=> 'application/json'},text:async()=> 'synthetic metadata refusal'};}
          if(extra.metadataGate)await extra.metadataGate;
          return response({session:{...savedFiles[sid],composer_draft:drafts[sid]||{text:'',files:[]}}});
        }
        return uploadFetch(url,opts);
      };
      // Start exactly as a first message from the blank composer, not an invented owner.
      S.session=null;
      if(extra.metadataFailure===401)c.pendingAuth=c.api('/delayed-auth');
    },
    act:async({context:c,S,input,state,file,second,records,options})=>{
      const record=records()[0];owner=record.sid;
      assert.equal(owner,'new-1');assert.equal(record.title,'Untitled');
      assert.equal(vm.runInContext('_newSessionInFlight',c),null,'creation lock settles before upload');
      assert.equal(record.items[0].file,file);assert.equal(record.state,'failed');assert.equal(state.starts.length,0);
      assert.equal(drafts[owner].text,'','submit clears the new-chat draft');
      if(c.pendingAuth){c.releaseAuth();await c.pendingAuth;assert.equal(c.window.location.href,'https://fixture.invalid/');}
      // A completed New Chat leaves the first, still-empty conversation off the sidebar.
      await c.newSession(false);
      assert.equal(S.session.session_id,'new-2');
      input.value='Newer unrelated draft';S.pendingFiles=[second];
      c._setRestoredGoalContinuationDraft('newer-goal-token',input.value);
      if(extra.profileMismatch)S.activeProfile='other';
      options.failure=false;options.partial=false;
      const retry=c._retryUploadRecovery(record.id);
      if(extra.duringOpen)await extra.duringOpen({c,S,input,state,record});
      await retry;
      assert.equal(c.window.location.href,'https://fixture.invalid/','retained bytes prevent authentication unload');
      if(extra.metadataFailure||extra.profileMismatch||extra.duringOpen){
        assert.equal(state.starts.length,0,'failed or superseded navigation must not admit');
        assert.equal(record.state,'failed');assert.equal(record.items[0].file,file);
        if(extra.profileMismatch)assert.equal(reads.length,0,'no implicit profile switch');
        if(extra.metadataFailure===401){
          c._discardUploadRecovery(record.id);
          await c.api('/api/session?session_id='+owner);
          assert(c.window.location.href.startsWith('login?next='),'ordinary auth redirect resumes after discard');
        }
        return;
      }
      assert.equal(state.starts.length,1,'Retry must reopen the hidden first-message owner and send');
      assert.equal(S.session.session_id,owner);assert.deepEqual(reads,[owner]);
      assert.equal(state.starts[0].session_id,owner);assert.equal(state.starts[0].profile,'fixture');
      assert.equal(state.starts[0].workspace,'fixture-workspace');
      assert.equal(drafts['new-2'].text,'Newer unrelated draft');assert.equal(drafts['new-2'].files[0].name,second.name);
      if(extra.partial){
        assert.equal(state.starts[0].attachments.length,2);
        assert.equal(state.uploads.filter(u=>u.name===file.name).length,1,'acknowledged file is not uploaded again');
      }
      assert.equal(state.uploads.length,extra.partial?3:2);assert.equal(records().length,0);assert.equal(record.items.length,0);
      S.busy=false;await c.loadSession('new-2');
      assert.equal(input.value,'Newer unrelated draft','newer draft survives the round trip');
      assert.equal(S.pendingFiles[0],second,'newer draft retains actual File bytes, not just metadata');
      assert.equal(c._peekRestoredGoalContinuationDraft().id,'newer-goal-token');
      await c._clearComposerDraft('new-2',input.value,[second]);
      assert.equal(vm.runInContext("_composerDraftLocalFallbackBySid.has('new-2')",c),false,'submit clears retained newer-draft references');
    },
  });
}
(async()=>{
  const cases=[];
  cases.push(await scenario('first-upload-new-chat-retry'));
  cases.push(await scenario('profile-mismatch',{profileMismatch:true}));
  cases.push(await scenario('missing-owner',{metadataFailure:404}));
  cases.push(await scenario('auth-expired',{metadataFailure:401}));
  cases.push(await scenario('partial-first-upload',{failure:false,partial:true}));
  let release;
  const metadataGate=new Promise(r=>{release=r;});
  cases.push(await scenario('superseded-reopen',{
    metadataGate,
    duringOpen:async({c,S,state,record})=>{
      for(let i=0;i<5;i++)await new Promise(r=>setTimeout(r,0));
      assert.equal(record.state,'opening');
      await c._retryUploadRecovery(record.id);
      c._discardUploadRecovery(record.id);
      assert.equal(record.items.length,1,'opening protects retained File ownership');
      assert.equal(state.uploads.length,1,'duplicate Retry cannot upload');
      await c.newSession(false);
      assert.equal(S.session.session_id,'new-3');
      release();
    },
  }));
  console.log(JSON.stringify({caseCount:cases.length,cases}));
})().catch(e=>{console.error(e);process.exitCode=1;});
