import {createRelay} from './server.js';
const server=createRelay(process.env.XIAOYOU_RELAY_TOKEN || '');
// This listener is inside the isolated Compose network. Only Caddy publishes host ports.
server.listen(8795,'0.0.0.0',()=>process.stdout.write('Encrypted relay ready\n'));
for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>server.close());
