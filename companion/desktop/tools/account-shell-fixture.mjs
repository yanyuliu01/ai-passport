// Isolated native-window account fixture. Never contacts the owner's account service.
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {createHash,X509Certificate} from 'node:crypto';
import {generate} from 'selfsigned';
import {createRelay} from '../dist-main/relay/server.js';
import {RelayAccounts} from '../dist-main/relay/accounts.js';
import {AccountConnection} from '../dist-main/core/account.js';
import {HTTPRuntimeTransport} from '../dist-main/core/runtime-client.js';
export const SHELL_MESH_SECRET='Synthetic native-window account mesh identity';
export const shellSpace=createHash('sha256').update('xiaoyou-subject\0'+SHELL_MESH_SECRET).digest('hex').slice(0,32);
export async function accountFixture(folder,endpoint){
 const cert=await generate([{name:'commonName',value:'synthetic-shell-account'}],{keyType:'ec',curve:'P-256',algorithm:'sha256'});
 const master='Synthetic account shell fixture master secret';
 const relay=createRelay(master,{key:cert.private,cert:cert.cert},new RelayAccounts(join(folder,'accounts.json'),master));
 await new Promise(r=>relay.listen(0,'127.0.0.1',r));
 const server={url:'wss://127.0.0.1:'+relay.address().port,tlsFingerprint:createHash('sha256').update(new X509Certificate(cert.cert).raw).digest('hex')};
 const values=new Map(),path=join(folder,'bootstrap');mkdirSync(path);
 const bootstrap=new AccountConnection(path,{load:key=>values.get(key),save:(key,value)=>values.set(key,value)},new HTTPRuntimeTransport(),()=>({...endpoint,meshToken:SHELL_MESH_SECRET}),()=>{},()=>{},()=>{},'Bootstrap fixture',server);
 await bootstrap.login('shell_owner','synthetic-shell-passphrase',true);
 return {env:{XIAOYU_ACCOUNT_TEST_SERVER:JSON.stringify(server)},async close(){await bootstrap.stop();await new Promise(r=>relay.close(r));}};
}
export async function loginShell(page){
 await page.getByLabel('小鱼账号',{exact:true}).fill('shell_owner');
 await page.getByLabel('密码',{exact:true}).fill('synthetic-shell-passphrase');
 await page.getByRole('button',{name:'登录并连接',exact:true}).click();
 await page.getByRole('button',{name:'返回小鱼',exact:true}).click();
 await page.locator('.composer').waitFor();
}
