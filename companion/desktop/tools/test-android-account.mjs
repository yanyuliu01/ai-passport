// Synthetic local account interoperability. No personal credentials, deployment or phone SLA claim.
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,readdirSync,rmSync} from 'node:fs';
import {tmpdir,homedir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash,X509Certificate} from 'node:crypto';
import {generate} from 'selfsigned';
import {createRelay} from '../dist-main/relay/server.js';
import {RelayAccounts} from '../dist-main/relay/accounts.js';
import {AccountConnection} from '../dist-main/core/account.js';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../../..');
const cache=process.env.GRADLE_USER_HOME||join(homedir(),'.gradle');
function jar(group,name,version){const base=join(cache,'caches/modules-2/files-2.1',group,name,version);for(const d of readdirSync(base)){const f=readdirSync(join(base,d)).find(f=>f.endsWith('.jar'));if(f)return join(base,d,f);}throw Error('Missing '+name);}
const cp=[jar('org.bouncycastle','bcprov-jdk18on','1.86'),jar('com.squareup.okhttp3','okhttp','4.12.0'),jar('com.squareup.okio','okio-jvm','3.6.0'),jar('org.jetbrains.kotlin','kotlin-stdlib','1.9.20')].join(':');
const folder=mkdtempSync(join(tmpdir(),'xiaoyou-phone-account-')),classes=join(folder,'classes');
const run=(command,args)=>new Promise((resolve,reject)=>{const p=spawn(command,args,{stdio:['ignore','pipe','pipe']});let output='';p.stdout.on('data',d=>output+=d);p.stderr.on('data',d=>output+=d);p.on('error',reject);p.on('close',code=>code?reject(Error(output)):resolve(output));});
const java=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin/java'):'java',javac=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin/javac'):'javac';
const main=join(root,'companion/android/app/src/main/java/com/yanyuliu/pockethub');
await run(javac,['-cp',cp,'-d',classes,...['Json','RelayCrypto','PinnedRelayTLS','PeerAvailability','SharedRelaySocket','RelayConnection','AccountAPI','AccountIdentity','RuntimeSelection'].map(f=>join(main,f+'.java')),join(root,'companion/android/app/src/test/java/com/yanyuliu/pockethub/AccountProtocolHarness.java')]);
const cert=await generate([{name:'commonName',value:'synthetic-account-relay'}],{keyType:'ec',curve:'P-256',algorithm:'sha256'});
const master='synthetic-account-master-secret-for-local-test';
const relay=createRelay(master,{key:cert.private,cert:cert.cert},new RelayAccounts(join(folder,'accounts.json'),master));
let a,b;
const secret='Synthetic shared identity for phone account interoperability';
const space=createHash('sha256').update('xiaoyou-subject\0'+secret).digest('hex').slice(0,32);
try{
 await new Promise(r=>relay.listen(0,'127.0.0.1',r));
 const server={url:'wss://127.0.0.1:'+relay.address().port,tlsFingerprint:createHash('sha256').update(new X509Certificate(cert.cert).raw).digest('hex')};
 function connection(id){mkdirSync(join(folder,id));const map=new Map();const endpoint={runtimeId:id.repeat(32),spaceId:space,name:'Computer '+id,url:'http://127.0.0.1:8765',token:'synthetic-local-runtime-token',meshToken:secret,capabilities:['sync']};
 return new AccountConnection(join(folder,id),{load:key=>map.get(key),save:(key,value)=>map.set(key,value)}, {request:async()=>({runtime_id:endpoint.runtimeId,subject_id:space,capabilities:['sync']})},()=>endpoint,()=>{},()=>{},()=>{},endpoint.name,server);}
 a=connection('a');b=connection('b');
 await a.login('fixture_owner','synthetic-account-passphrase',true);await b.login('fixture_owner','synthetic-account-passphrase');
 console.log(await run(java,['-cp',classes+':'+cp,'com.yanyuliu.pockethub.AccountProtocolHarness',server.url,server.tlsFingerprint]));
}finally{await a?.stop();await b?.stop();await new Promise(r=>relay.close(r));rmSync(folder,{recursive:true,force:true});}
