import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { messageBlocks, messageStatusText, projection, mainMessages, taskStatusLabel, displayIssue } from '../core/presentation.js';
import { orderMessages } from '../core/message-order.js';
import { ClientDatabase } from '../core/database.js';
import type { Message, SyncPage } from '../shared.js';

const fixture = JSON.parse(readFileSync(new URL('../../../../tests/fixtures/presentation-v1.json', import.meta.url), 'utf8')) as {
  cases: { name: string; wire: Message; visible: string[]; rendered: string[]; placement: string; status_text: string }[];
};

test('the status line under each message matches Android, including a reply cut short', () => {
  for (const row of fixture.cases) assert.equal(messageStatusText(row.wire), row.status_text, row.name);
  const cut = fixture.cases.find(row => row.name === 'interrupted-reply')!;
  assert.equal(projection(cut.wire)?.interrupted, true);
  assert.deepEqual(messageBlocks(cut.wire).map(block => block.text), ['秋天先从风里来']);
  assert.equal(messageStatusText({ ...cut.wire, presentation: { ...cut.wire.presentation!, version: 2 } as never }), '显示规则版本不兼容');
});

test('desktop consumes the same golden Gateway blocks as Android', () => {
  for (const row of fixture.cases) {
    assert.deepEqual(messageBlocks(row.wire).map(block => block.text), row.visible, row.name);
    assert.deepEqual(messageBlocks(row.wire).map(block => block.runs!.map(run => run.text).join('')), row.rendered, row.name);
    assert.equal(projection(row.wire)?.placement, row.placement);
    assert.equal(mainMessages([row.wire], 'default').length, row.placement === 'chat' ? 1 : 0);
  }
});
test('replicated arrival order cannot change canonical message order', () => {
  const one = fixture.cases[0].wire, next = fixture.cases.find(row=>row.name==='same-text-new-turn')!.wire;
  const owner: Message = { ...one, id: 'turn:you', author: { kind: 'owner', name: 'you' },
    presentation: { ...one.presentation!, id: 'a/turn:you', order: [10, one.presentation!.turn_id, 0, 'a/turn:you'] } };
  assert.deepEqual(orderMessages([next, one, owner]).map(m => m.id), ['turn:you', 'turn:reply', 'next:reply']);
  assert.deepEqual(orderMessages([owner, next, one]).map(m => m.id), ['turn:you', 'turn:reply', 'next:reply']);
});
test('projection survives SQLite recovery and a newer stale partial cannot replace final', t => {
  const folder = mkdtempSync(join(tmpdir(), 'xiaoyou-projection-')), path = join(folder, 'cache.sqlite');
  t.after(() => rmSync(folder, { recursive: true, force: true }));
  const db = new ClientDatabase(path), runtime = 'a'.repeat(32), space = 'b'.repeat(32);
  const page = (wire: Message, seq: number): SyncPage => ({ runtime_id: runtime, subject_id: space, epoch: 'test', seq,
    reset: false, more: false, messages: [{ ...wire, rev: seq }], tasks: [], approvals: [], removed: [] });
  db.apply(space, runtime, page(fixture.cases[1].wire, 1));
  db.apply(space, runtime, page(fixture.cases[3].wire, 2));
  assert.deepEqual(messageBlocks(db.records<Message>(space, 'message')[0]), []);
  db.apply(space, runtime, page(fixture.cases[0].wire, 3));
  db.apply(space, runtime, page(fixture.cases[1].wire, 4));
  db.close();
  const restored = new ClientDatabase(path);
  try { assert.deepEqual(messageBlocks(restored.records<Message>(space, 'message')[0]).map(b => b.text), fixture.cases[0].visible); }
  finally { restored.close(); }
});
test('unknown presentation revisions and native task states remain explicit', () => {
  assert.equal(displayIssue({ presentation: { version: 2 } }), '显示规则版本不兼容');
  assert.equal(taskStatusLabel({ state: 'done', presentation: { version: 1, status: 'turn_ended', status_label: '本轮已结束' } }), '本轮已结束');
  assert.equal(messageBlocks({ ...fixture.cases[1].wire, presentation: undefined }).length, 0);
});

test('same-revision snapshot upgrades only presentation and preserves pending work', t => {
  const folder=mkdtempSync(join(tmpdir(),'xiaoyou-policy-upgrade-')), db=new ClientDatabase(join(folder,'cache.sqlite'));
  t.after(()=>{db.close();rmSync(folder,{recursive:true,force:true});});
  const runtime='a'.repeat(32),space='b'.repeat(32),wire=fixture.cases[0].wire;
  const legacy={...wire,presentation:undefined};
  const page=(message:Message):SyncPage=>({runtime_id:runtime,subject_id:space,epoch:'test',seq:9,reset:false,more:false,messages:[message],tasks:[],approvals:[],removed:[]});
  db.apply(space,runtime,page(legacy)); db.enqueue(space,runtime,'must survive');
  db.apply(space,runtime,page({...wire,text:'same revision cannot change text'}));
  assert.equal(db.records<Message>(space,'message')[0].text,wire.text);
  db.apply(space,runtime,{...page(wire),presentation_policy:{version:1}});
  assert.deepEqual(messageBlocks(db.records<Message>(space,'message')[0]).map(b=>b.text),fixture.cases[0].visible);
  assert.equal(db.pending(space).length,1); assert.equal(db.get('presentation-policy:'+runtime,0),1);
});
