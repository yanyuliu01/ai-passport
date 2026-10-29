import {accountFixture,loginShell,SHELL_MESH_SECRET,shellSpace} from './account-shell-fixture.mjs';
// Real isolated Electron rendering of the shared wire fixtures. Never calls a provider.
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {mkdtempSync,mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import assert from 'node:assert/strict';
const require=createRequire(process.env.XIAOYU_QA_PLAYWRIGHT||import.meta.url);
const {_electron}=require('playwright');
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../../..'),desktop=join(root,'companion/desktop');
const folder=mkdtempSync(join(tmpdir(),'xiaoyu-display-qa-')),bin=join(folder,'bin');mkdirSync(bin);
for(const id of ['dsh','codex','claude'])writeFileSync(join(bin,id),`#!/bin/sh\nif [ "$1" = "--version" ]; then echo "${id} 1.2.3"; else echo '{"loggedIn":true}'; fi\n`,{mode:0o700});
const cases=JSON.parse(readFileSync(join(root,'tests/fixtures/presentation-v1.json'),'utf8')).cases;
const sample=name=>structuredClone(cases.find(row=>row.name===name).wire);
const runtime='a'.repeat(32),space=shellSpace,token='synthetic-presentation-qa-token';
let seq=100,current='one-complete-one-unfinished',future=false,app,liveStage=-1,historyBlocked=false,disconnected=false;
let accountQA;
const policy={version:1},errors=[];
const server=createServer(async(req,res)=>{
 if(req.headers.authorization!=='Bearer '+token){res.writeHead(401);res.end('{}');return;}
 const url=new URL(req.url,'http://localhost');let value={};
 if(disconnected && (url.pathname==='/v1/sync'||url.pathname.startsWith('/v1/messages/'))){res.writeHead(503);res.end('{"error":"synthetic offline"}');return;}
 const liveMessages=()=>{
  const owner=sample('owner-literal-separator'),reply=sample('two-complete-messages');
  for(const [record,suffix,rank] of [[owner,':you',0],[reply,':reply',1]]){
   record.id='live'+suffix;record.rev=1000+liveStage;record.created_at=20;record.conversation='default';
   record.presentation.id=runtime+'/live'+suffix;record.presentation.turn_id=runtime+'/live';
   record.presentation.order=[20,runtime+'/live',rank,runtime+'/live'+suffix];
  }
  owner.text='当前请求测试';owner.presentation.blocks=[{id:runtime+'/live:you#0',text:owner.text,complete:true}];
  reply.reply_to=owner.id;reply.text='当前请求已经回复';reply.presentation.blocks=[{id:runtime+'/live:reply#0',text:reply.text,complete:true}];
  return liveStage<1?[]:liveStage===1?[owner]:[owner,reply];
 };
 if(url.pathname==='/v1/messages'&&req.method==='POST'){
  for await(const _ of req){}liveStage=0;value={id:'live',status:'queued'};
 }else if(url.pathname==='/v1/messages/live'){
  await new Promise(resolve=>setTimeout(resolve,100));
  value={id:'live',rev:liveStage,status:liveStage<2?'running':'done',message_snapshot:{runtime_id:runtime,subject_id:space,epoch:'display-fixture',messages:liveMessages()}};
 }else
 if(url.pathname==='/v1/info')value={runtime_id:runtime,subject_id:space,capabilities:['sync','sync_usage','all_conversations','presentation_v1'],presentation_policy:policy};
 else if(url.pathname==='/v1/agents')value={agents:[]};
 else if(url.pathname==='/v1/usage')value={};
 else if(url.pathname==='/v1/sync'){
  if(url.searchParams.get('after')===String(seq))await new Promise(resolve=>setTimeout(resolve,100));
  const owner=sample('owner-literal-separator'),reply=sample(current);owner.rev=reply.rev=seq;
  if(future)reply.presentation.version=2;
  value={runtime_id:runtime,subject_id:space,epoch:'display-fixture',seq,reset:false,more:historyBlocked,
   presentation_policy:policy,conversations:[{id:'default',title:'主对话',created_at:0}],messages:[reply,owner],tasks:[],approvals:[],removed:[],usage:{}};
 }else{res.writeHead(404);res.end('{}');return;}
 res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(value));
});
async function launch(env){
 const packaged=process.env.XIAOYU_SHELL_PACKAGED;
 const instance=await _electron.launch({executablePath:packaged||desktop+'/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron',args:packaged?[]:[desktop],env,timeout:30000});
 const page=await instance.firstWindow();page.on('pageerror',error=>errors.push(error.message));return [instance,page];
}
async function until(predicate){const end=performance.now()+8000;while(!await predicate()){if(performance.now()>end)throw Error('Presentation window did not converge');await new Promise(resolve=>setTimeout(resolve,25));}}
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const port=server.address().port,config=join(folder,'fixture.json');
 writeFileSync(config,JSON.stringify({server:{name:'显示规则隔离测试',port,token},mesh:{secret:SHELL_MESH_SECRET},agents:{deepseek:{type:'dsh'}}}));
 accountQA=await accountFixture(folder,{runtimeId:runtime,spaceId:space,name:'Fixture computer',url:'http://127.0.0.1:'+port,token,capabilities:['sync']});
 const env={...process.env,...accountQA.env,PATH:bin+':'+process.env.PATH,XIAOYU_CONFIG:config,XIAOYU_DESKTOP_TEST_DATA:join(folder,'profile')};
 delete env.ELECTRON_RUN_AS_NODE;delete env.DEEPSEEK_API_KEY;
 let page;[app,page]=await launch(env);
 await loginShell(page);
 let reply=page.locator('[data-message-id="turn:reply"]');
 await until(()=>reply.locator('[data-block-id]').count().then(n=>n===1));
 assert.match(await reply.innerText(),/First message/);assert.doesNotMatch(await page.locator('.timeline').innerText(),/Unfinished/);
 current='empty-snapshot-retracts-blocks';seq++;
 await until(()=>reply.count().then(n=>n===0));
 current='two-complete-messages';seq++;
 await until(()=>reply.locator('[data-block-id]').count().then(n=>n===2));
 assert.deepEqual(await reply.locator('[data-block-id]>p').allTextContents(),['First message','Second message']);
 const ids=await page.locator('.timeline [data-message-id]').evaluateAll(rows=>rows.map(row=>row.dataset.messageId));
 assert.deepEqual(ids,['turn:you','turn:reply']);
 await page.screenshot({path:join(root,'build/presentation-blocks-window.png')});
 current='shared-formatting-runs';seq++;
 await until(()=>reply.locator('strong').count().then(n=>n===2));
 assert.equal(await reply.locator('[data-block-id]>p').innerText(),'Ready\n•  First\n•  Second');
 await page.screenshot({path:join(root,'build/presentation-formatting-window.png')});
 future=true;seq++;
 await page.getByText('显示规则版本不兼容',{exact:true}).waitFor();
 future=false;current='two-complete-messages';seq++;
 await until(()=>reply.locator('[data-block-id]').count().then(n=>n===2));
 historyBlocked=true;
 await page.getByRole('textbox',{name:'消息',exact:true}).fill('当前请求测试');
 await page.getByRole('button',{name:'发送',exact:true}).click();
 await page.getByText('已接收，等待回复',{exact:true}).waitFor();
 await page.locator('.thinking').waitFor();
 liveStage=1;
 await until(()=>page.locator('[data-message-id="live:you"]').count().then(n=>n===1));
 assert.equal(await page.locator('.thinking').count(),1,'Owner synchronization must not clear the wait state');
 await app.close();[app,page]=await launch(env);
 await page.locator('.composer').waitFor();
 await page.locator('.thinking').waitFor();
 disconnected=true;
 await until(()=>page.locator('.thinking').innerText().then(text=>/状态待确认/.test(text)));
 disconnected=false;const recoveredAt=performance.now();liveStage=2;
 await page.getByText('当前请求已经回复',{exact:true}).waitFor();
 const recoveryMs=Math.round(performance.now()-recoveredAt);
 assert.ok(recoveryMs<=3000,`Current reply recovery took ${recoveryMs}ms`);
 await until(()=>page.locator('.thinking').count().then(n=>n===0));
 assert.equal(await page.locator('[data-message-id="live:you"]').count(),1);
 assert.equal(await page.locator('[data-message-id="live:reply"]').count(),1);
 await page.getByRole('button',{name:'正在同步',exact:true}).waitFor();
 await page.screenshot({path:join(root,'build/reply-wait-window.png')});
 historyBlocked=false;
 await app.close();[app,page]=await launch(env);
 await page.locator('.composer').waitFor();
 reply=page.locator('[data-message-id="turn:reply"]');
 await until(()=>reply.locator('[data-block-id]').count().then(n=>n===2));
 assert.equal(await page.locator('[data-message-id="turn:reply"]').count(),1);
 assert.deepEqual(errors,[]);
 const result={desktop:await app.evaluate(({app})=>app.getVersion()),platform:process.platform,packaged:Boolean(process.env.XIAOYU_SHELL_PACKAGED),provider:'synthetic-shared-wire-fixture',partialCompleteBlock:'PASS',emptyRetraction:'PASS',
  finalBlockBoundaries:'PASS',canonicalOrder:'PASS',formattingRuns:'PASS',futurePolicyWarning:'PASS',restartSameMessage:'PASS',
  acceptedWait:'PASS',ownerOnlyWait:'PASS',acceptedRestart:'PASS',offlineWait:'PASS',liveReplyDuringHistoryReplay:'PASS',recoveryMs,
  nativeCodexWindow:'NOT RUN',nativeClaudeWindow:'NOT RUN',phone:'NOT RUN',realModelTTFT:'NOT RUN'};
 writeFileSync(join(root,'build/presentation-window-validation.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}finally{if(app)await app.close();await accountQA?.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
