// Local protocol/fault integration only; does not establish real phone/public-network acceptance.
import {createServer} from 'node:http';import {spawn} from 'node:child_process';import {mkdtempSync,readFileSync,writeFileSync,rmSync,readdirSync} from 'node:fs';import{tmpdir,homedir}from'node:os';import{join,resolve,dirname}from'node:path';import{fileURLToPath}from'node:url';
import QRCode from 'qrcode';import {generate} from 'selfsigned';import {X509Certificate,createHash} from 'node:crypto';
import{createRelay}from'../dist-main/relay/server.js';import{PeerNetwork}from'../dist-main/core/peers.js';import{ClientDatabase}from'../dist-main/core/database.js';import{HTTPRuntimeTransport}from'../dist-main/core/runtime-client.js';
const desktop=resolve(dirname(fileURLToPath(import.meta.url)),'..'),root=resolve(desktop,'../..');
const java=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin/java'):'java',javac=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin/javac'):'javac';
const cache=process.env.GRADLE_USER_HOME||join(homedir(),'.gradle');
function jar(group,artifact,version){const base=join(cache,'caches/modules-2/files-2.1',group,artifact,version);for(const folder of readdirSync(base)){const file=readdirSync(join(base,folder)).find(f=>f.endsWith('.jar'));if(file)return join(base,folder,file)}throw Error('Missing '+artifact)}
const cp=[jar('org.bouncycastle','bcprov-jdk18on','1.86'),jar('com.squareup.okhttp3','okhttp','4.12.0'),jar('com.squareup.okio','okio-jvm','3.6.0'),jar('org.jetbrains.kotlin','kotlin-stdlib','1.9.20')].join(':');
const folder=mkdtempSync(join(tmpdir(),'xiaoyu-java-relay-')),classes=join(folder,'classes');
const run=(command,args)=>new Promise((resolve,reject)=>{const p=spawn(command,args,{stdio:['ignore','pipe','pipe']});let output='';p.stdout.on('data',d=>output+=d);p.stderr.on('data',d=>output+=d);p.on('error',reject);p.on('close',code=>code?reject(Error(output)):resolve(output))});
await run(javac,['-cp',cp,'-d',classes,...['Json.java','RelayCrypto.java','PinnedRelayTLS.java','PeerAvailability.java','SharedRelaySocket.java','RelayConnection.java'].map(f=>join(root,'companion/android/app/src/main/java/com/yanyuliu/pockethub',f)),...['RelayProtocolHarness.java','PinnedRelayTLSHarness.java'].map(f=>join(root,'companion/android/app/src/test/java/com/yanyuliu/pockethub',f))]);
const pems=await generate([{name:'commonName',value:'synthetic-node'}],{keyType:'ec',curve:'P-256',algorithm:'sha256'}),tls={key:pems.private,cert:pems.cert},tlsFingerprint=createHash('sha256').update(new X509Certificate(pems.cert).raw).digest('hex');
const token='synthetic-relay-token-'+ 'x'.repeat(32),runtimeToken='synthetic-runtime-token',runtime='1'.repeat(32),space='2'.repeat(32),values=new Map();let relay,network,network2,db2,db,child,posts=0,voice=0,image=0,sync=false,restart;
const server=createServer(async(req,res)=>{if(req.headers.authorization!=='Bearer '+runtimeToken){res.writeHead(401);res.end('{}');return}let body='';for await(const chunk of req)body+=chunk;let value={};const url=new URL(req.url,'http://localhost');
if(url.pathname==='/v1/info')value={runtime_id:runtime,subject_id:space,capabilities:['sync','sync_usage']};
else if(url.pathname==='/v1/messages'){posts++;if(!body.includes('手机异地协议测试'))throw Error('Wrong text');if(posts===1){const port=relay.address().port;await new Promise(r=>relay.close(r));restart=setTimeout(()=>{relay=createRelay(token,tls);relay.listen(port,'127.0.0.1')},300)}value={id:'accepted-once',status:'done',reply:'synthetic reply'};}
else if(url.pathname==='/v1/voice'){if(body!=='synthetic-wav'||req.headers['content-type']!=='audio/wav')throw Error('Wrong WAV');voice++;value={id:'voice-once',status:'done'}}
else if(url.pathname==='/v1/images'){if(body!=='synthetic-image')throw Error('Wrong image');image++;value={id:'file-1'}}
else if(url.pathname==='/v1/artifacts/file-1/content'){res.writeHead(200,{'Content-Type':'image/jpeg'});res.end('synthetic-image');return}
else if(url.pathname==='/v1/sync'){sync=url.searchParams.get('wait')==='20';value={runtime_id:runtime,subject_id:space,epoch:'fixture',seq:1,messages:[],tasks:[],approvals:[]}}
res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));});
try{await new Promise(r=>server.listen(0,'127.0.0.1',r));relay=createRelay(token,tls);await new Promise(r=>relay.listen(0,'127.0.0.1',r));let headers=0;relay.prependListener('request',()=>headers++);await run(java,['-cp',classes+':'+cp,'com.yanyuliu.pockethub.PinnedRelayTLSHarness','wss://127.0.0.1:'+relay.address().port,'0'.repeat(64)]);if(headers)throw Error('Wrong pin leaked HTTP headers');db=new ClientDatabase(join(folder,'cache.sqlite'));
const endpoint={runtimeId:runtime,spaceId:space,name:'Desktop protocol fixture',url:'http://127.0.0.1:'+server.address().port,token:runtimeToken,capabilities:['sync','sync_usage']};
network=new PeerNetwork(db,{load:key=>values.get(key),save:(key,value)=>values.set(key,value)},new HTTPRuntimeTransport(),()=>endpoint,{changed:state=>{for(const request of state.requests)void network.accept(request.id)},endpoint(){}},'Desktop fixture');await network.start();network.configure({url:'wss://127.0.0.1:'+relay.address().port,tlsFingerprint,token,room:'3'.repeat(32)});
const invitation=await network.invite();writeFileSync(join(folder,'invitation.txt'),invitation);
await QRCode.toFile(join(folder,'invitation.png'),invitation,{width:420,margin:4,errorCorrectionLevel:'M'});
const qrjar=jar('com.google.zxing','core','3.5.4');
await run(javac,['-cp',qrjar,'-d',classes,join(desktop,'tools/java/QRCodeDecode.java')]);
await run(java,['-cp',classes+':'+qrjar,'QRCodeDecode',join(folder,'invitation.png'),join(folder,'invitation.txt')]);
db2=new ClientDatabase(join(folder,'cache2.sqlite'));const values2=new Map();
const endpoint2={...endpoint,runtimeId:'4'.repeat(32),spaceId:'5'.repeat(32),name:'Second computer fixture'};
network2=new PeerNetwork(db2,{load:key=>values2.get(key),save:(key,value)=>values2.set(key,value)},{request:async()=>({runtime_id:endpoint2.runtimeId,subject_id:endpoint2.spaceId,capabilities:['sync']})},()=>endpoint2,{changed:state=>{for(const request of state.requests)void network2.accept(request.id)},endpoint(){}},'Second computer fixture');
await network2.start();network2.configure({url:'wss://127.0.0.1:'+relay.address().port,tlsFingerprint,token,room:'3'.repeat(32)});
writeFileSync(join(folder,'invitation2.txt'),await network2.invite());
await run(java,['-cp',classes+':'+cp,'com.yanyuliu.pockethub.RelayProtocolHarness',join(folder,'invitation.txt'),join(folder,'result.json'),join(folder,'invitation2.txt')]);
if(posts!==1||voice!==1||image!==1||!sync)throw Error('Expected one execution and bounded polling: '+JSON.stringify({posts,voice,image,sync}));
const result={pinnedWSS:true,javaWrongPinBlockedBeforeHeaders:true,qrDecodedByAndroidCore:true,...JSON.parse(readFileSync(join(folder,'result.json'))),singleMessageExecution:posts===1};writeFileSync(join(root,'build/xiaoyu-android-relay-interop.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{clearTimeout(restart);network?.stop();network2?.stop();if(relay?.listening)await new Promise(r=>relay.close(r));await new Promise(r=>server.close(r));db?.close();db2?.close();rmSync(folder,{recursive:true,force:true})}
