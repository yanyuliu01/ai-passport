import { sessionArguments } from './pi-session.mjs';
process.argv = [process.argv[0], process.argv[1], ...sessionArguments(process.argv.slice(2), process.env.PI_CODING_AGENT_DIR)];
await import('./node_modules/@mariozechner/pi-coding-agent/dist/cli.js');
