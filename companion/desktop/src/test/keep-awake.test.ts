import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { keepAwakeWhileRunning } from '../core/keep-awake.js';

test('Mac startup holds an idle-sleep blocker until the client actually quits', () => {
  const active = new Set<number>();
  const blocker = {
    start(type: 'prevent-app-suspension') { assert.equal(type, 'prevent-app-suspension'); active.add(7); return 7; },
    stop(id: number) { return active.delete(id); },
  };
  const release = keepAwakeWhileRunning('darwin', blocker);
  assert.deepEqual([...active], [7]);
  release(); release();
  assert.equal(active.size, 0);
  keepAwakeWhileRunning('win32', blocker)();
  assert.equal(active.size, 0);
});
