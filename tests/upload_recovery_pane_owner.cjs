// Compose upload acknowledgement with upstream issuing-pane ownership.
const assert=require('node:assert/strict');
const {runCase}=require('./upload_recovery.cjs');
(async()=>{
  const cases=[];
  for(const failed of [false,true]){
    cases.push(await runCase(failed?'offscreen-upload-admission-failed':'offscreen-upload-admission-accepted',{
      text:'Original attachment turn',startFailure:failed,
      setup:({context:c,S,input,second})=>{
        S.session.workspace='/issuing-workspace';
        const api=c.api;
        c.api=async(...args)=>{
          try{return await api(...args);}
          finally{
            S.session={session_id:'B',profile:'other',workspace:'/other',model:'other-model'};
            S.activeProfile='other';S.pendingFiles=[second];input.value='Newer B draft';
          }
        };
      },
      act:({context:c,S,input,state,second,records})=>{
        assert.equal(state.starts.length,1);
        assert.equal(state.starts[0].session_id,'A');
        assert.equal(state.starts[0].profile,'fixture');
        assert.equal(state.starts[0].workspace,'/issuing-workspace');
        assert.equal(state.starts[0].model,'fixture-model');
        assert.equal(S.session.session_id,'B');assert.equal(S.session.model,'other-model');
        assert.equal(input.value,'Newer B draft');assert.equal(S.pendingFiles[0],second);
        assert.equal(state.streams.length,0,'must not replace the viewed chat SSE');
        if(failed){
          assert.equal(records().length,1);assert.equal(records()[0].state,'uncertain');
          assert.equal(records()[0].sid,'A');assert.equal(c.INFLIGHT.A,undefined);
        }else{
          assert.equal(records().length,0);
          assert.equal(c.INFLIGHT.A.streamId,'fixture-stream','accepted offscreen upload must retain reattach identity');
          assert.equal(c.INFLIGHT.A.reattach,true);
        }
      },
    }));
  }
  console.log(JSON.stringify({caseCount:cases.length,cases}));
})().catch(e=>{console.error(e);process.exitCode=1;});
