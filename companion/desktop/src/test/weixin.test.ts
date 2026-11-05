import test from 'node:test';
import assert from 'node:assert/strict';
import { weixinNavBadge, weixinDelivery, weixinBindView, weixinStateLine, weixinMessages, weixinVisibleBlocks, weixinVisibleAttachments, type WeixinView } from '../core/weixin.js';
import type { Message } from '../shared.js';
import { listedSideChat, runtimeConversation } from '../core/presentation.js';
import { syncPath } from '../core/runtime-client.js';

const base: WeixinView = { state: 'connected', account: null, host: null, local: false, reason: null, undelivered: 0, rev: 1 };
test('the sync request declares the channel only for compatible runtimes', () => {
  const cursor = { epoch: 'epoch', cursor: '12' };
  assert.equal(new URL(syncPath(cursor), 'http://localhost').searchParams.get('states'), 'unavailable');
  assert.equal(new URL(syncPath(cursor, 20, 'default', true), 'http://localhost').searchParams.get('states'), 'unavailable,weixin');
});
test('channel sidebar shows one highest-priority state', () => {
  assert.deepEqual(weixinNavBadge({ ...base, state: 'unbound' }, false, 'desktop'), { kind: 'tag', text: '扫码连接', tone: 'blue' });
  assert.deepEqual(weixinNavBadge({ ...base, state: 'unbound' }, false, 'android'), { kind: 'tag', text: '未连接', tone: 'grey' });
  assert.deepEqual(weixinNavBadge({ ...base, state: 'relogin', undelivered: 1 }, true, 'desktop'), { kind: 'word', text: '要重新扫码', tone: 'amber' });
  assert.deepEqual(weixinNavBadge({ ...base, state: 'unreachable', undelivered: 1 }, true, 'desktop'), { kind: 'word', text: '没发出', tone: 'amber' });
  assert.deepEqual(weixinNavBadge(base, true, 'desktop'), { kind: 'unread' });
  assert.equal(weixinNavBadge(base, false, 'desktop'), null);
});
test('uncertain delivery is distinguished from definite failure', () => {
  assert.equal(weixinDelivery('sent'), null);
  assert.equal(weixinDelivery('waiting'), null);
  assert.equal(weixinDelivery('failed')?.confirm, false);
  assert.equal(weixinDelivery('unknown')?.confirm, true);
});
test('tracked channel bubbles appear on delivery, while legacy history stays visible', () => {
  const blocks = ['a', 'b', 'c', 'd'].map(id => ({ id, text: id, complete: true }));
  const legacy = { id: 'one', conversation: 'weixin', text: '', rev: 1, status: 'final', created_at: 1 } as Message;
  assert.equal(weixinVisibleBlocks(legacy, blocks).length, 4);
  const tracked = { ...legacy, presentation: { channel_delivery_tracked: true,
    channel_delivery: { a: 'sent', b: 'waiting', c: 'unknown', d: 'cancelled' } } } as unknown as Message;
  assert.deepEqual(weixinVisibleBlocks(tracked, blocks).map(block => block.id), ['a', 'c']);
  assert.deepEqual(weixinVisibleBlocks({ ...tracked, delivery: { b: 'sent' } }, blocks).map(block => block.id), ['a', 'b', 'c']);
  const media = { ...tracked, presentation: { ...tracked.presentation, id: 'origin/reply', channel_delivery_tracked: true,
    channel_delivery: { 'origin/reply#media:x': 'waiting', 'origin/reply#media:y': 'sent' } } } as Message;
  assert.deepEqual(weixinVisibleAttachments(media, [{ id: 'x' }, { id: 'y' }]), [{ id: 'y' }]);
});
test('only the connection host can offer a re-scan and offline records are labeled', () => {
  assert.equal(weixinStateLine({ ...base, state: 'relogin', local: true }, false).rescan, true);
  assert.equal(weixinStateLine({ ...base, state: 'relogin', local: false }, false).rescan, false);
  assert.match(weixinStateLine(base, true).warning!, /已保存/);
});
test('scan progress has stable step semantics', () => {
  assert.deepEqual(weixinBindView('waiting').steps, ['now', 'now', 'todo']);
  assert.deepEqual(weixinBindView('scanned').steps, ['done', 'done', 'now']);
  assert.deepEqual(weixinBindView('confirmed').steps, ['done', 'done', 'done']);
  assert.deepEqual(weixinBindView('expired').steps, ['todo', 'todo', 'todo']);
});
test('channel is never listed as a side chat', () => {
  const channel = runtimeConversation({ id: 'weixin', kind: 'weixin', title: '微信', created_at: 1 });
  assert.equal(channel?.kind, 'weixin');
  assert.equal(listedSideChat(channel!, 'weixin'), false);
  assert.equal(listedSideChat({ id: 'weixin', title: '微信', createdAt: 1 }, ''), false);
});
test('channel timeline shows only owner and Xiaoyu public messages', () => {
  const make = (id: string, kind: string, extra = {}): Message => ({ id, conversation: 'weixin', text: 'x', rev: 1, status: 'final', created_at: 1, author: { kind }, ...extra });
  const messages = [make('owner', 'owner'), make('reply', 'xiaoyou'), make('agent', 'agent'),
    make('activity', 'xiaoyou', { kind: 'activity' }), make('side', 'owner', { conversation: 'default' })];
  assert.deepEqual(weixinMessages(messages).map(message => message.id), ['owner', 'reply']);
});
