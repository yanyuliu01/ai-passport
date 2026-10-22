import type { Message } from '../shared.js';
import { projection } from './presentation.js';

export function compareDisplay(a: Message, b: Message): number {
  const left = projection(a)?.order || [a.created_at, '', a.author?.kind === 'owner' ? 0 : 1, key(a)];
  const right = projection(b)?.order || [b.created_at, '', b.author?.kind === 'owner' ? 0 : 1, key(b)];
  for (let index = 0; index < 4; index++) {
    if (left[index] === right[index]) continue;
    return left[index] < right[index] ? -1 : 1;
  }
  return 0;
}

function key(message: Message): string {
  if (typeof message._canonical === 'string') return message._canonical;
  const origin = String(message.mesh_origin || message.source?.runtime_id || message._runtimeId || '');
  const prefix = origin.slice(0, 12) + '.';
  return origin + '/' + (message.id.startsWith(prefix) ? message.id.slice(prefix.length) : message.id);
}

/** Timestamps may be equal or skewed; a reply must follow its known parent. */
export function orderMessages(messages: Message[]): Message[] {
  const sorted = messages.slice().sort(compareDisplay);
  if (messages.every(message => projection(message))) return sorted;
  const byKey = new Map(sorted.map(message => [key(message), message]));
  const priority = new Map(sorted.map((message, index) => [key(message), index]));
  const waiting = new Set<string>(), children = new Map<string, string[]>();
  for (const message of sorted) {
    if (typeof message.reply_to !== 'string') continue;
    const own = key(message), origin = own.slice(0, own.indexOf('/'));
    const prefix = origin.slice(0, 12) + '.';
    const parent = origin + '/' + (message.reply_to.startsWith(prefix) ? message.reply_to.slice(prefix.length) : message.reply_to);
    if (parent === own || !byKey.has(parent)) continue;
    waiting.add(own); children.set(parent, [...children.get(parent) || [], own]);
  }
  const ready = sorted.filter(message => !waiting.has(key(message))).map(key);
  const result: Message[] = [], emitted = new Set<string>();
  while (ready.length) {
    const id = ready.shift()!;
    emitted.add(id); result.push(byKey.get(id)!);
    for (const child of children.get(id) || []) {
      const rank = priority.get(child)!;
      let left = 0, right = ready.length;
      while (left < right) {
        const middle = (left + right) >>> 1;
        if (priority.get(ready[middle])! < rank) left = middle + 1; else right = middle;
      }
      ready.splice(left, 0, child);
    }
  }
  // Malformed cyclic references remain visible; they must not hang the renderer.
  return result.concat(sorted.filter(message => !emitted.has(key(message))));
}
