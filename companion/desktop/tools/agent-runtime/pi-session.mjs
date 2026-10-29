import { isAbsolute, join } from 'node:path';

// Runtime's stable Pi adapter uses --session-id. The locked upstream CLI uses
// --session <file>; an explicit file preserves restart/continuation identity.
export function sessionArguments(args, home) {
  const result = [];
  for (let index = 0; index < args.length; index++) {
    if (args[index] !== '--session-id') { result.push(args[index]); continue; }
    const id = args[++index];
    if (!home || !isAbsolute(home) || typeof id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(id))
      throw new Error('Pi requires a private agent directory and a valid session identity');
    result.push('--session', join(home, 'sessions', id + '.jsonl'));
  }
  return result;
}
