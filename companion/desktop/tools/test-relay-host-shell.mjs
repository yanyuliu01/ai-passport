import {accountFixture,loginShell,SHELL_MESH_SECRET,shellSpace} from './account-shell-fixture.mjs';
// Isolated native UI regression. Requires Playwright for Electron; no real Runtime/provider is called.
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {mkdtempSync,mkdirSync,writeFileSync} from 'node:fs';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
const require=createRequire(process.env.XIAOYU_QA_PLAYWRIGHT || import.meta.url);
const {_electron}=require('playwright');
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../../..'),desktop=root+'/companion/desktop',folder=mkdtempSync(join(tmpdir(),'xiaoyu-shell-qa-')),bin=join(folder,'bin');mkdirSync(bin);
for(const id of ['dsh','codex','claude'])writeFileSync(join(bin,id),`#!/bin/sh\nif [ "$1" = "--version" ]; then echo "${id} 1.2.3"; elif [ "$1" = "login" ]; then echo "Logged in using ChatGPT"; else echo '{"loggedIn":true}'; fi\n`,{mode:0o700});
const runtime='1'.repeat(32),space=shellSpace,token='synthetic-shell-token-123456',now=Date.now()/1000;
let seq=1,posts=[],decisions=0,requests=0;
let conversations=[{id:'default',title:'主对话',created_at:0,updated_at:now,revision:1},{id:'side-one',title:'客户端细节',created_at:now-60,updated_at:now,revision:1}];
let messages=[{id:'one:you',conversation:'default',text:'把客户端的主界面接上吧。',created_at:now-10,status:'final',rev:1,scope:'main',kind:'user',author:{kind:'owner'}},{id:'one:reply',conversation:'default',text:'主对话、旁聊和工作记录已经分开。我会先接好真实状态，再检查各个入口。',created_at:now-9,status:'final',rev:1,scope:'main',kind:'reply',author:{kind:'assistant'},task:'task-a'},
{id:'hidden',conversation:'default',text:'DO-NOT-SHOW-PRIVATE-TRANSCRIPT',created_at:now,status:'final',rev:1,scope:'task',source:{type:'desktop'},author:{kind:'assistant'}}];
let tasks=[{id:'task-a',title:'检查客户端连接状态',conversation:'default',state:'working',agent:'codex',worker_agent:'codex',worker_session:'native-session-fixture',worker_cwd:'/synthetic/workdir',origin_message:'one:you',last_meaningful_at:now,updated_at:now,seq:1,rev:1,progress:'正在检查状态切换'},
{id:'task-b',title:'确认一次操作',conversation:'default',state:'waiting',agent:'claude',updated_at:now,seq:1,rev:1,approval:{id:'approval-a'}}];
let approvals=[{id:'approval-a',card:'task-b',conversation:'default',status:'pending',rev:1,detail:'隔离测试：允许读取测试目录？'}];
const agents=[{name:'deepseek',type:'dsh',default:true,model:'deepseek-flash'},{name:'codex',type:'codex',model:'reported-model'},{name:'claude',type:'claude_code',model:'sonnet'}];
const usage={accounts:[{id:'PRIVATE-ACCOUNT-PATH',updated_at:now,windows:[{kind:'5h',left:32,resets_at:now+3600},{kind:'7d',left:71,resets_at:now+86400}]}],agents:[{name:'codex',type:'codex',account:'PRIVATE-ACCOUNT-PATH',model:'reported-model'},{name:'claude',type:'claude_code',model:'sonnet'}]};
const server=createServer(async(req,res)=>{
 requests++;
 if(req.headers.authorization!=='Bearer '+token){res.writeHead(401);res.end('{}');return;}
 const url=new URL(req.url,'http://localhost');let body='';for await(const chunk of req)body+=chunk;body=body?JSON.parse(body):{};
 let value={};
 if(url.pathname==='/v1/info')value={runtime_id:runtime,subject_id:space,capabilities:['sync','side_chats','all_conversations','task_audit','sync_usage']};
 else if(url.pathname==='/v1/agents')value={agents};
 else if(url.pathname==='/v1/usage')value=usage;
 else if(url.pathname==='/v1/sync'){if(url.searchParams.get('after')===String(seq))await new Promise(r=>setTimeout(r,100));value={runtime_id:runtime,subject_id:space,epoch:'fixture',seq:String(seq),reset:false,more:false,conversations,messages,tasks,approvals,removed:[],usage};}
 else if(url.pathname==='/v1/conversations'&&req.method==='POST'){const source=messages.find(m=>m.id===body.from_message);const item={id:body.id,title:'新旁聊',created_at:Date.now()/1000,updated_at:Date.now()/1000,metadata_at:Date.now()/1000,revision:1,...(source?{context:{id:source.id,conversation:source.conversation,text:source.text}}:{})};conversations.push(item);seq++;value=item;}
 else if(url.pathname==='/v1/messages'&&req.method==='POST'){
   posts.push(body);const id='sent-'+posts.length;
   const time=Date.now()/1000;messages.push({id:id+':you',conversation:body.conversation,text:body.text,created_at:time,status:'final',rev:1,scope:body.card&&!body.reply_scope?'task':'main',kind:'user',author:{kind:'owner'}});
   messages.push({id:id+':reply',conversation:body.conversation,text:'隔离测试回应：'+body.text,created_at:time+.01,status:'final',rev:1,scope:body.card&&!body.reply_scope?'task':'main',kind:'reply',author:{kind:'assistant'},...(body.card?{task:body.card}:{})});
   if(body.card){tasks[0]={...tasks[0],relay_state:'relay_queued',seq:++seq,rev:++tasks[0].rev};}
   conversations=conversations.map(c=>c.id===body.conversation?{...c,title:c.id==='default'?'主对话':body.text.slice(0,20),metadata_at:time,updated_at:time,revision:c.revision+1}:c);seq++;value={id,status:'done'};
 }else if(url.pathname.startsWith('/v1/messages/'))value={id:url.pathname.split('/').at(-1),status:'done',rev:2};
 else if(url.pathname==='/v1/cards')value={cards:tasks.map(t=>({...t,entries:[{id:'native-public',role:'you',author:'owner',at:now,text:'原会话的测试指令'},{id:'native-reply',role:'xiaoyou',author:'codex',at:now,text:'已记录的测试执行片段'}]}))};
 else if(url.pathname.endsWith('/activity'))value={events:[{id:'event-a',seq:1,at:now,phase:'tool_start',text:'读取测试状态'},{id:'event-b',seq:2,at:now+1,phase:'relay_queued',text:'补充要求等待转交'}],more:false,before:1};
 else if(url.pathname.startsWith('/v1/approvals/')){decisions++;approvals=[{...approvals[0],status:'allowed',rev:2}];tasks[1]={...tasks[1],state:'working',rev:2,seq:++seq};value={};}
 else {res.writeHead(404);res.end('{}');return;}
 res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(value));
});
let app;let accountQA;const errors=[];
const wait=async(fn,ms=8000)=>{const end=performance.now()+ms;while(!(await fn())){if(performance.now()>end)throw new Error('QA timeout');await new Promise(r=>setTimeout(r,25));}};
try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const port=server.address().port,config=join(folder,'existing.json');writeFileSync(config,JSON.stringify({server:{name:'隔离测试电脑',port,token},mesh:{secret:SHELL_MESH_SECRET},agents:{deepseek:{type:'dsh'},codex:{type:'codex'}}}));
 accountQA=await accountFixture(folder,{runtimeId:runtime,spaceId:space,name:'Fixture computer',url:'http://127.0.0.1:'+port,token,capabilities:['sync']});
 const env={...process.env,...accountQA.env,PATH:bin+':'+process.env.PATH,XIAOYU_CONFIG:config,XIAOYU_DESKTOP_TEST_DATA:join(folder,'profile')};delete env.ELECTRON_RUN_AS_NODE;delete env.DEEPSEEK_API_KEY;
 const packaged=process.env.XIAOYU_SHELL_PACKAGED;
 app=await _electron.launch({executablePath:packaged||desktop+'/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron',args:packaged?[]:[desktop],env,timeout:30000});
 const page=await app.firstWindow();page.on('pageerror',e=>errors.push(e.message));
 await loginShell(page);
 await page.getByRole('heading',{name:'主对话',exact:true}).waitFor();
 await page.screenshot({path:root+'/build/xiaoyu-phone-home.png'});
 await page.locator('.composer').waitFor();
 await page.getByRole('heading',{name:'主对话',exact:true}).waitFor();await page.getByText('Codex',{exact:true}).first().waitFor();
 await page.screenshot({path:root+'/build/xiaoyu-shell-main.png'});
 if((await page.locator('.timeline').innerText()).includes('DO-NOT-SHOW'))throw new Error('Private session leaked into main chat');
 if(await page.locator('.helper-pills').getByText('Codex',{exact:true}).count()!==1)throw new Error('Duplicate visible Codex');
 const snap=await page.evaluate(()=>window.xiaoyu.snapshot());if(JSON.stringify(snap).includes('PRIVATE-ACCOUNT-PATH')||JSON.stringify(snap).includes(token))throw new Error('Private metadata reached renderer');
 await page.getByRole('button',{name:'Codex 的用量',exact:true}).first().click();await page.locator('.usage-modal').getByText('32%',{exact:true}).waitFor();
 await page.getByRole('button',{name:'关闭用量',exact:true}).click();
 await page.getByLabel('消息',{exact:true}).fill('主对话测试');await page.getByRole('button',{name:'发送',exact:true}).click();await page.getByText('隔离测试回应：主对话测试',{exact:true}).waitFor();
 await page.getByRole('button',{name:'在做的事',exact:false}).first().click();await page.getByRole('button',{name:'检查客户端连接状态',exact:false}).first().click();
 await page.getByText('读取测试状态',{exact:false}).waitFor();await page.screenshot({path:root+'/build/xiaoyu-shell-work.png'});
 await page.getByRole('button',{name:'原会话',exact:false}).click();await page.getByText('已记录的测试执行片段',{exact:true}).waitFor();if(await page.locator('textarea').count())throw new Error('Native transcript is editable');
 await page.getByRole('button',{name:'返回工作详情',exact:true}).click();
 await page.getByRole('button',{name:'补充要求',exact:true}).click();await page.locator('.task-target').waitFor();
 await page.getByLabel('消息',{exact:true}).fill('增加一条测试要求');await page.getByRole('button',{name:'发送',exact:true}).click();await wait(()=>posts.some(p=>p.card==='task-a'&&p.reply_scope==='main'));
 await page.getByRole('button',{name:'取消任务关联',exact:true}).click();
 const source=page.locator('[data-message-id="one:reply"]');await source.hover();await source.getByRole('button',{name:'开旁聊',exact:true}).click();await page.getByText('来自主对话',{exact:true}).waitFor();
 await page.getByLabel('消息',{exact:true}).fill('只属于旁聊');await page.getByRole('button',{name:'发送',exact:true}).click();await page.getByText('隔离测试回应：只属于旁聊',{exact:true}).waitFor();
 await page.locator('.sidebar').getByRole('button',{name:'主对话',exact:true}).click();await page.getByRole('heading',{name:'主对话',exact:true}).waitFor();if((await page.locator('.timeline').innerText()).includes('只属于旁聊'))throw new Error('Side chat contaminated main');
 await page.getByRole('button',{name:'添加照片或文件',exact:true}).click();await page.getByRole('menuitem',{name:'照片',exact:true}).waitFor();
 const plus=await page.getByRole('button',{name:'添加照片或文件',exact:true}).boundingBox(),send=await page.getByRole('button',{name:'发送',exact:true}).boundingBox();if(plus.x>=send.x)throw new Error('Attachment button is not before Send');
 await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'允许这次',exact:true}).click();await wait(()=>decisions===1);await wait(()=>page.locator('.decision').count().then(n=>n===0));
 await app.evaluate(({BrowserWindow})=>{const w=BrowserWindow.getAllWindows()[0];w.setMinimumSize(600,500);w.setContentSize(760,780);});
 await page.getByRole('button',{name:'打开侧边栏',exact:true}).click();await page.locator('.sidebar-shade').waitFor();
 await page.getByRole('button',{name:'关闭侧边栏',exact:true}).click();
 await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setContentSize(1120,780));
 await page.getByRole('button',{name:'设置',exact:true}).click();
 await page.getByRole('heading',{name:'已登录的电脑',exact:true}).waitFor();
 const settings=await page.locator('.settings-page').innerText();
 if(/启用本机节点|连接串|WSS 入口地址|请求加入|生成连接二维码/.test(settings))throw Error('Retired connection mode remains visible');
 await page.screenshot({path:root+'/build/xiaoyu-account-settings.png',fullPage:true});
 await app.close();app=await _electron.launch({executablePath:packaged||desktop+'/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron',args:packaged?[]:[desktop],env,timeout:30000});
 const restoredPage=await app.firstWindow();await restoredPage.locator('.composer').waitFor();
 const restored=await restoredPage.evaluate(()=>window.xiaoyu.snapshot());
 if(!restored.account.signedIn || restored.relayHost)throw Error('Account did not restore exclusively');
 if(errors.length)throw new Error(errors.join('; '));
 const result={accountLogin:'PASS',retiredModesAbsent:'PASS',accountRestart:'PASS',independentHome:'PASS',version:'0.1.17',packaged:Boolean(packaged),provider:'synthetic-http-fixture',realModel:false,mainSidebar:'PASS',realMetadataProjection:'PASS',completeBubble:'PASS',taskDetails:'PASS',readOnlyTranscript:'PASS',originBoundSupplement:'PASS',sideChatIsolation:'PASS',rightAttachmentMenu:'PASS',approvalConvergence:'PASS',responsiveSidebar:'PASS',credentialIsolation:'PASS'};
 writeFileSync(root+'/build/xiaoyu-node-shell-qa.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{if(app)await app.close();await accountQA?.close();await new Promise(r=>server.close(r));server.closeAllConnections();}
