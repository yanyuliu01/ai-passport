import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {request} from 'node:https';
import {createHash,randomBytes,X509Certificate} from 'node:crypto';
import {generate} from 'selfsigned';
import WebSocket from 'ws';
import {RelayAccounts} from '../relay/accounts.js';
import {createRelay} from '../relay/server.js';
import {EndpointCrypto} from '../core/crypto.js';
import {pinnedAgent} from '../core/pinned-tls.js';
import {AccountConnection} from '../core/account.js';
import type {SecretStore} from '../core/client.js';
import type {Endpoint,Json} from '../shared.js';
const master='test-relay-master-secret-at-least-32-characters';
function vault():SecretStore{const m=new Map();return{load:key=>m.get(key),save:(key,value)=>m.set(key,structuredClone(value))};}
async function until(check:()=>boolean){const deadline=Date.now()+7000;while(!check()){if(Date.now()>deadline)throw Error('test condition timeout');await new Promise(r=>setTimeout(r,10));}}
async function fixture(t:import('node:test').TestContext){
 const folder=mkdtempSync(join(tmpdir(),'xiaoyou-accounts-'));
 const cert=await generate([{name:'commonName',value:'localhost'}],{keySize:2048});
 const pin=createHash('sha256').update(new X509Certificate(cert.cert).raw).digest('hex');
 const store=join(folder,'accounts.json'),accounts=new RelayAccounts(store,master);
 const server=createRelay(master,{key:cert.private,cert:cert.cert},accounts);
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
 const port=(server.address() as import('node:net').AddressInfo).port;
 const config={url:'wss://127.0.0.1:'+port,tlsFingerprint:pin};
 t.after(async()=>{await new Promise<void>(r=>server.close(()=>r()));rmSync(folder,{recursive:true,force:true});});
 async function call(path:string,body:Json,token?:string):Promise<{status:number;body:Json}>{return new Promise((resolve,reject)=>{
  const agent=pinnedAgent(pin);const req=request('https://127.0.0.1:'+port+'/v1/account/'+path,{method:'POST',agent,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})}},res=>{const chunks:Buffer[]=[];res.on('data',b=>chunks.push(b));res.on('end',()=>{agent.destroy();resolve({status:res.statusCode!,body:JSON.parse(Buffer.concat(chunks).toString())});});});req.on('error',reject);req.end(JSON.stringify(body));});}
 const identity=(await EndpointCrypto.create('test-computer')).identity;
 return{folder,store,accounts,server,config,call,identity};
}
test('account credentials are salted/hashed and renewed or logged-out sessions cannot be reused',async t=>{
 const f=await fixture(t),password='a private test password';
 const one=await f.call('register',{username:'owner',password,device:f.identity});assert.equal(one.status,200);
 const text=readFileSync(f.store,'utf8');assert.ok(!text.includes(password));assert.ok(!text.includes(String(one.body.token)));
 const next=new RelayAccounts(f.store,master);assert.ok(next.authorizeRelay(String(one.body.relayToken),String(one.body.id)));
 assert.ok(!next.authorizeRelay(String(one.body.relayToken),'a'.repeat(32)));
 const login=await f.call('login',{username:'OWNER',password,device:f.identity});assert.equal(login.status,200);
 assert.equal((await f.call('presence',{},String(one.body.token))).status,401);
 assert.equal((await f.call('presence',{},String(login.body.token))).status,200);
 assert.equal((await f.call('logout',{},String(login.body.token))).status,200);
 assert.equal((await f.call('presence',{},String(login.body.token))).status,401);
});
test('invalid credentials are generic and authentication attempts are bounded',async t=>{
 const f=await fixture(t);await f.call('register',{username:'owner',password:'correct test password',device:f.identity});
 const wrong=await f.call('login',{username:'owner',password:'wrong test password',device:f.identity});
 const absent=await f.call('login',{username:'absent',password:'wrong test password',device:f.identity});
 assert.equal(wrong.status,401);assert.deepEqual(wrong,absent);
 const results=await Promise.all(Array.from({length:10},()=>f.call('login',{username:'owner',password:'wrong test password',device:f.identity})));
 assert.ok(results.some(r=>r.status===429));
});
test('account tokens cannot cross relay rooms and legacy relay credentials keep working',async t=>{
 const f=await fixture(t),login=await f.call('register',{username:'owner',password:'correct test password',device:f.identity});
 async function connect(token:string,room:string):Promise<number>{return new Promise((resolve,reject)=>{
  const agent=pinnedAgent(f.config.tlsFingerprint);const ws=new WebSocket(f.config.url+'/v1/connect?device='+randomBytes(16).toString('hex')+'&room='+room,{agent,headers:{Authorization:'Bearer '+token}});
  ws.on('open',()=>{ws.terminate();agent.destroy();resolve(101);});ws.on('unexpected-response',(_,r)=>{r.resume();ws.terminate();agent.destroy();resolve(r.statusCode!);});ws.on('error',()=>{});
  setTimeout(()=>{ws.terminate();reject(Error('connect timeout'));},3000).unref();});}
 assert.equal(await connect(String(login.body.relayToken),String(login.body.id)),101);
 assert.equal(await connect(String(login.body.relayToken),'f'.repeat(32)),401);assert.equal(await connect(master,'f'.repeat(32)),101);
});
test('directory discovery is account-scoped and cannot replace an existing space or forge another device',async t=>{
 const f=await fixture(t),a=await f.call('register',{username:'owner',password:'correct test password',device:f.identity});
 const second=(await EndpointCrypto.create('second')).identity;
 const b=await f.call('login',{username:'owner',password:'correct test password',device:second});
 const outsider=await f.call('register',{username:'other',password:'other private password',device:second});
 function invite(identity:typeof second,subject='a'.repeat(32)){return 'xiaoyou://join#'+Buffer.from(JSON.stringify({v:1,space:subject,device:identity,relay:{...f.config,room:a.body.id,token:a.body.relayToken},token:'j'.repeat(43),expires:Date.now()+120000})).toString('base64url');}
 const invitation=invite(f.identity);assert.equal((await f.call('presence',{invitation},String(a.body.token))).status,200);
 assert.equal((await f.call('presence',{},String(b.body.token))).body.invitation,invitation);
 assert.equal((await f.call('presence',{},String(outsider.body.token))).body.invitation,undefined);
 assert.equal((await f.call('presence',{invitation},String(b.body.token))).status,400);
 assert.equal((await f.call('presence',{invitation:invite(second,'b'.repeat(32))},String(b.body.token))).status,409);
});
test('account login restores identity with old computer offline, then both computers run independently',async t=>{
 const f=await fixture(t),secret='shared mesh identity for this test only';
 const space=createHash('sha256').update('xiaoyou-subject\0'+secret).digest('hex').slice(0,32);
 const aVault=vault(),bVault=vault(),eventsA:Endpoint[]=[],eventsB:Endpoint[]=[];
 const localA:Endpoint={runtimeId:'1'.repeat(32),spaceId:space,name:'old computer',url:'http://127.0.0.1:1',token:'private-a',meshToken:secret,capabilities:['sync']};
 let localB:Endpoint|undefined;
 const a=new AccountConnection(f.folder,aVault,{request:async()=>({source:'A'})},()=>localA,()=>{},e=>eventsA.push(e),()=>{},'old computer',f.config);
 const bFolder=mkdtempSync(join(tmpdir(),'xiaoyou-account-new-'));
 const b=new AccountConnection(bFolder,bVault,{request:async()=>({source:'B'})},()=>localB,()=>{},e=>eventsB.push(e),()=>{},'new computer',f.config);
 t.after(()=>{a.stop();b.stop();rmSync(bFolder,{recursive:true,force:true});});
 await a.login('owner','eight123',true);a.stop();await new Promise(r=>setTimeout(r,50));
 await b.login('owner','eight123');
 assert.equal(bVault.load('account-mesh-secret'),secret);assert.equal(b.view().provisioned,true);assert.equal(eventsB.length,0);
 assert.equal(b.view().pairing.requests.length,0);
 await a.restore();await until(()=>eventsB.length>0);
 assert.equal(bVault.load('account-mesh-secret'),secret);assert.equal((await b.request(eventsB[0],'GET','/v1/info')).source,'A');
 localB={...localA,runtimeId:'2'.repeat(32),name:'new computer',token:'private-b'};
 await b.refresh();await until(()=>eventsA.some(e=>e.runtimeId===localB!.runtimeId));
 assert.equal((await a.request(eventsA.find(e=>e.runtimeId===localB!.runtimeId)!,'GET','/v1/info')).source,'B');
 assert.equal(readFileSync(f.store,'utf8').includes(secret),false);
 await b.logout();assert.equal(b.view().signedIn,false);assert.equal(a.view().signedIn,true);
});

test('account-linked real Runtimes exchange configured files and keep independent execution', {timeout:45000}, async t=>{
 const {spawn}=await import('node:child_process');const {createServer}=await import('node:net');
 const {mkdirSync,writeFileSync,existsSync}=await import('node:fs');
 const {fileURLToPath}=await import('node:url');const {resolve}=await import('node:path');
 const {HTTPRuntimeTransport,identify}=await import('../core/runtime-client.js');
 const f=await fixture(t), transport=new HTTPRuntimeTransport(),secret=randomBytes(32).toString('hex');
 const root=resolve(fileURLToPath(new URL('../../../..',import.meta.url)));
 const children:import('node:child_process').ChildProcess[]=[];
 t.after(async()=>{for(const child of children){child.kill('SIGTERM');await Promise.race([new Promise(r=>child.once('exit',r)),new Promise(r=>setTimeout(r,2000))]);if(child.exitCode===null)child.kill('SIGKILL');}});
 async function runtime(name:string,meshSecret:string){
  const home=join(f.folder,name);mkdirSync(home);mkdirSync(join(home,'shared'));
  if(name==='old')writeFileSync(join(home,'shared','notes.txt'),'authorized shared file from old Runtime');
  const listener=createServer();await new Promise<void>(r=>listener.listen(0,'127.0.0.1',r));const number=(listener.address() as import('node:net').AddressInfo).port;await new Promise<void>(r=>listener.close(()=>r()));
  const token=randomBytes(32).toString('hex'),config=join(home,'config.json');
  writeFileSync(config,JSON.stringify({server:{host:'127.0.0.1',port:number,token,name},state_dir:'state',persona_file:'persona.txt',desktop_sync:false,agents:{echo:{type:'echo'}},xiaoyou:{default_agent:'echo',voice_agent:null},mesh:{secret:meshSecret,peers:[],advertise:[],share:{folders:{notes:join(home,'shared')}}},usage:{enabled:false}}));
  writeFileSync(join(home,'persona.txt'),'Synthetic account interoperability test.');
  const env={...Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('XIAOYOU_'))),PYTHONPATH:join(root,'runtime'),PYTHONUNBUFFERED:'1'};
  const child=spawn(process.env.XIAOYOU_TEST_PYTHON||'python3',['-m','xiaoyou_runtime','--config',config],{cwd:home,env,stdio:'ignore'});children.push(child);
  let result:Endpoint|undefined;const end=Date.now()+8000;
  while(!result&&Date.now()<end){try{result=await identify(transport,'http://127.0.0.1:'+number,token,name);}catch{await new Promise(r=>setTimeout(r,30));}}
  assert.ok(result,'isolated Runtime starts');return{...result,meshToken:meshSecret};
 }
 const old=await runtime('old',secret),av=vault(),bv=vault();let fresh:Endpoint|undefined;
 const seenA:Endpoint[]=[],seenB:Endpoint[]=[];
 const aDir=join(f.folder,'a-client'),bDir=join(f.folder,'b-client');mkdirSync(aDir);mkdirSync(bDir);
 const a=new AccountConnection(aDir,av,transport,()=>old,()=>{},e=>seenA.push(e),()=>{},'old',f.config);
 const b=new AccountConnection(bDir,bv,transport,()=>fresh,()=>{},e=>seenB.push(e),()=>{},'new',f.config);
 t.after(async()=>{a.stop();b.stop();await new Promise(r=>setTimeout(r,100));});
 await a.login('owner','correct test password',true);await b.login('owner','correct test password');
 assert.equal(bv.load('account-mesh-secret'),secret);assert.equal(a.view().pairing.requests.length,0);
 await a.refresh();
 fresh=await runtime('new',bv.load<string>('account-mesh-secret')!);assert.equal(fresh.spaceId,old.spaceId);assert.notEqual(fresh.runtimeId,old.runtimeId);
 await b.refresh();await until(()=>seenA.some(e=>e.runtimeId===fresh!.runtimeId)&&seenB.some(e=>e.runtimeId===old.runtimeId));
 const shared=join(f.folder,'new/shared/notes.txt');await until(()=>existsSync(shared));assert.equal(readFileSync(shared,'utf8'),'authorized shared file from old Runtime');
 const fromOld=await b.request(seenB.find(e=>e.runtimeId===old.runtimeId)!,'GET','/v1/info');
 const fromNew=await a.request(seenA.find(e=>e.runtimeId===fresh!.runtimeId)!,'GET','/v1/info');
 assert.equal(fromOld.runtime_id,old.runtimeId);assert.equal(fromNew.runtime_id,fresh.runtimeId);
 assert.equal(readFileSync(f.store,'utf8').includes(secret),false);
 t.diagnostic('Two actual local Runtime processes, account-only enrollment, encrypted identity recovery, selected-folder replication and source-bound RPC passed. No model/UI TTFT claim.');
});

test('account recovery is persistent and immutable, and cannot be fetched with another account session', async t=>{
 const f=await fixture(t),password='private fixture password';
 const a=await f.call('register',{username:'owner',password,device:f.identity});
 const other=await f.call('register',{username:'other',password,device:f.identity});
 const backup={v:1,nonce:'a'.repeat(24),tag:'b'.repeat(32),ciphertext:'c'.repeat(64)},space='d'.repeat(32);
 assert.equal((await f.call('vault',{space,vault:backup},String(a.body.token))).status,200);
 const altered={...backup,tag:'f'.repeat(32)};
 assert.deepEqual((await f.call('vault',{space,vault:altered},String(a.body.token))).body.vault,backup);
 assert.equal((await f.call('vault',{space:'e'.repeat(32),vault:backup},String(a.body.token))).status,409);
 assert.equal((await f.call('vault',{},String(other.body.token))).body.vault,undefined);
 assert.equal((await f.call('vault',{},'invalid')).status,401);
 const next=new RelayAccounts(f.store,master);assert.ok(next.authorizeRelay(String(a.body.relayToken),String(a.body.id)));
 const login=await f.call('login',{username:'owner',password,device:f.identity});assert.deepEqual(login.body.vault,backup);
});
test('modified account recovery envelope fails closed without replacing local identity',async t=>{
 const f=await fixture(t),aVault=vault(),bVault=vault();
 const a=new AccountConnection(f.folder,aVault,{request:async()=>({})},()=>undefined,()=>{},()=>{},()=>{},'old',f.config);
 const b=new AccountConnection(f.folder,bVault,{request:async()=>({})},()=>undefined,()=>{},()=>{},()=>{},'new',f.config);
 t.after(()=>{a.stop();b.stop();});
 await a.login('owner','private fixture password',true);a.stop();
 const state=JSON.parse(readFileSync(f.store,'utf8'));
 state.accounts[0].vault.tag='0'.repeat(32);
 // Simulate an altered stored envelope in the authenticated server response, not a password failure.
 const accounts=f.accounts as unknown as {state:typeof state};accounts.state=state;
 await assert.rejects(b.login('owner','private fixture password'),/恢复失败/);
 assert.ok(!bVault.load('account-mesh-secret'));assert.equal(b.view().provisioned,false);assert.equal(b.view().signedIn,false);
});

test('eight-character passwords register and log in; seven-character passwords are rejected',async t=>{
 const f=await fixture(t),password='eight123';
 assert.equal((await f.call('register',{username:'short',password:'seven12',device:f.identity})).status,400);
 const registered=await f.call('register',{username:'owner',password,device:f.identity});assert.equal(registered.status,200);
 const login=await f.call('login',{username:'owner',password,device:f.identity});assert.equal(login.status,200);
 assert.equal((await f.call('login',{username:'owner',password:'seven12',device:f.identity})).status,400);
 assert.ok(!readFileSync(f.store,'utf8').includes(password));
});
