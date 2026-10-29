// Isolated native-window helper acceptance; never uses the owner's account or providers.
import {accountFixture,loginShell,SHELL_MESH_SECRET,shellSpace} from './account-shell-fixture.mjs';
import {createRequire} from 'node:module';import {createServer} from 'node:http';
import {mkdtempSync,mkdirSync,writeFileSync} from 'node:fs';import {tmpdir} from 'node:os';
import {resolve,dirname,join} from 'node:path';import {fileURLToPath} from 'node:url';import assert from 'node:assert/strict';
const require=createRequire(process.env.XIAOYU_QA_PLAYWRIGHT||import.meta.url),{_electron}=require('playwright');
const desktop=resolve(dirname(fileURLToPath(import.meta.url)),'..'),folder=mkdtempSync(join(tmpdir(),'xiaoyu-helper-ui-')),bin=join(folder,'bin');mkdirSync(bin);
for(const name of ['dsh','codex','claude'])writeFileSync(join(bin,name),`#!/bin/sh\nif [ "$1" = "--version" ]; then echo "${name} fixture"; else echo '{"loggedIn":true}'; fi\n`,{mode:0o700});
const runtime='a'.repeat(32),token='synthetic-helper-token',names=new Set(['codex']),writes=[],errors=[];let seq=1,app,qa;
const agents=()=>[...[...names].map(name=>({name,type:'codex',model:'synthetic-model'})),{name:'cc@Windows',type:'mesh'}];
const usage=()=>({rev:seq,at:Date.now()/1000,accounts:[{id:'synthetic-account',updated_at:Date.now()/1000,windows:[{kind:'5h',left:82},{kind:'7d',left:28}]}],agents:agents().map(row=>({...row,account:'synthetic-account'}))});
const server=createServer(async(req,res)=>{
 if(req.headers.authorization!=='Bearer '+token){res.writeHead(401);res.end('{}');return;}
 const path=new URL(req.url,'http://localhost').pathname;let value={};
 if(req.method==='POST'){
  let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw||'{}');writes.push({path,body});
  if(path==='/v1/helpers'){names.add(body.name);seq++;value={helper:{name:body.name}};}
  else if(path.endsWith('/remove')){names.delete(path.split('/')[3]);seq++;value={removed:path.split('/')[3]};}
  else{res.writeHead(404);res.end('{}');return;}
 }else if(path==='/v1/info')value={runtime_id:runtime,subject_id:shellSpace,capabilities:['sync','all_conversations','sync_usage']};
 else if(path==='/v1/agents')value={agents:agents()};else if(path==='/v1/usage')value=usage();
 else if(path==='/v1/helpers/options')value={runtime_id:runtime,agents:agents(),added:[...names].filter(n=>n!=='codex'),removable:[...names],harnesses:[{harness:'codex',label:'Codex',installed:true,ways:['login'],providers:[],login_hint:'Synthetic fixture login'}],model_settings:[]};
 else if(path==='/v1/sync'){await new Promise(r=>setTimeout(r,80));value={runtime_id:runtime,subject_id:shellSpace,epoch:'helper-ui',seq,reset:false,more:false,messages:[],tasks:[],approvals:[],removed:[],usage:usage()};}
 else{res.writeHead(404);res.end('{}');return;}
 res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(value));
});
try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const port=server.address().port,config=join(folder,'config.json');
 writeFileSync(config,JSON.stringify({server:{port,token,name:'Synthetic Mac'},mesh:{secret:SHELL_MESH_SECRET},agents:{deepseek:{type:'dsh'}}}));
 qa=await accountFixture(folder,{runtimeId:runtime,spaceId:shellSpace,name:'Synthetic Mac',url:'http://127.0.0.1:'+port,token,capabilities:['sync','all_conversations']});
 const env={...process.env,...qa.env,PATH:bin+':'+process.env.PATH,XIAOYU_CONFIG:config,XIAOYU_DESKTOP_TEST_DATA:join(folder,'profile')};delete env.ELECTRON_RUN_AS_NODE;delete env.DEEPSEEK_API_KEY;
 app=await _electron.launch({executablePath:process.env.XIAOYU_SHELL_PACKAGED||join(desktop,'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'),args:process.env.XIAOYU_SHELL_PACKAGED?[]:[desktop],env});
 const page=await app.firstWindow();page.on('pageerror',e=>errors.push(e.message));await loginShell(page);
 await page.locator('.quota-value').first().getByText('28%',{exact:true}).waitFor();assert.equal(await page.locator('.side-helpers').getByText('cc@Windows',{exact:true}).count(),0);await page.getByRole('button',{name:'Codex 的用量',exact:true}).first().click();
 for(const value of ['82%','28%'])await page.locator('.usage-modal').getByText(value,{exact:true}).waitFor();await page.getByRole('button',{name:'关闭用量',exact:true}).click();
 await page.getByRole('button',{name:'设置',exact:true}).click();await page.getByLabel('管理哪台电脑').waitFor();await page.getByRole('button',{name:'添加帮手',exact:true}).click();
 await page.getByLabel('叫它什么').fill('extra');await page.getByRole('button',{name:'添加',exact:true}).click();
 const row=page.locator('.helper-line').filter({has:page.getByText('extra',{exact:true})});await row.waitFor();await row.getByRole('button',{name:'移除',exact:true}).click();
 await page.getByRole('dialog').getByText('从 Synthetic Mac 移除，已有任务继续完成，历史保留。',{exact:true}).waitFor();
 await page.getByRole('dialog').getByRole('button',{name:'移除',exact:true}).click();await row.waitFor({state:'detached'});
 assert.equal(writes.length,2);assert.equal(writes[0].body.name,'extra');assert.equal(writes[1].path,'/v1/helpers/extra/remove');assert.deepEqual(errors,[]);
 await page.screenshot({path:join(folder,'helper-settings.png')});
 console.log(JSON.stringify({desktop:await app.evaluate(({app})=>app.getVersion()),packaged:Boolean(process.env.XIAOYU_SHELL_PACKAGED),quotaRing:28,fiveHour:82,weekly:28,add:true,remove:true,sourceLabel:true,screenshot:join(folder,'helper-settings.png'),realModelTTFT:'NOT RUN'}));
}finally{if(app)await app.close();await qa?.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
