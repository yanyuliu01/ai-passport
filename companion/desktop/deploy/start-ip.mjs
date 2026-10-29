import {readFileSync} from 'node:fs';
import {createRelay} from './server.js';
import {RelayAccounts} from './accounts.js';
const token=process.env.XIAOYU_RELAY_TOKEN || '';
const accounts=new RelayAccounts('/data/accounts.json',token);
const server=createRelay(token,{key:readFileSync('/tls/server.key'),cert:readFileSync('/tls/server.crt'),minVersion:'TLSv1.2'},accounts,{releases:'/data/releases'});
server.listen(8443,'0.0.0.0',()=>console.log('Xiaoyu encrypted relay and account discovery ready'));
for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>server.close());
