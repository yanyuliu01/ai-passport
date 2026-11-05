/** View decisions for the optional WeChat channel. No credentials enter this model. */
import type { DisplayBlock, Json, Message } from '../shared.js';
export type ChannelState = 'unbound' | 'binding' | 'connected' | 'unreachable' | 'relogin' | 'unknown';
export type BindState = 'starting' | 'waiting' | 'scanned' | 'need_code' | 'confirmed' | 'expired' | 'failed' | 'wrong_account' | 'cancelled';
export type DeliveryState = 'waiting' | 'sent' | 'failed' | 'unknown' | 'cancelled';
export interface WeixinView {
  state: ChannelState;
  account: { name: string } | null;
  host: { runtime_id: string; name: string; online: boolean } | null;
  local: boolean;
  reason: 'host_offline' | 'service' | null;
  undelivered: number;
  rev: number;
}
export type ChannelBadge = { kind: 'tag' | 'word'; text: string; tone: 'blue' | 'grey' | 'amber' }
  | { kind: 'unread' } | null;

export function weixinNavBadge(view: WeixinView, unread: boolean, platform: 'desktop' | 'android'): ChannelBadge {
  if (view.state === 'unbound') return { kind: 'tag', text: platform === 'desktop' ? '扫码连接' : '未连接', tone: platform === 'desktop' ? 'blue' : 'grey' };
  if (view.state === 'relogin') return { kind: 'word', text: '要重新扫码', tone: 'amber' };
  if (view.undelivered > 0) return { kind: 'word', text: '没发出', tone: 'amber' };
  if (view.state === 'unreachable') return { kind: 'word', text: '连不上', tone: 'grey' };
  return unread ? { kind: 'unread' } : null;
}

export function weixinDelivery(value: DeliveryState): { text: string; confirm: boolean } | null {
  return value === 'failed' || value === 'unknown' ? { text: '没确认发到微信', confirm: value === 'unknown' } : null;
}

/** A tracked channel bubble becomes visible only once its delivery is known. */
export function weixinVisibleBlocks(message: Message, blocks: DisplayBlock[]): DisplayBlock[] {
  if (!message.presentation?.channel_delivery_tracked) return blocks;
  const delivery = { ...message.presentation.channel_delivery, ...message.delivery };
  return blocks.filter(block => ['sent', 'failed', 'unknown'].includes(delivery[block.id] || ''));
}

export function weixinVisibleAttachments(message: Message, items: Json[]): Json[] {
  if (!message.presentation?.channel_delivery_tracked) return items;
  const delivery = { ...message.presentation.channel_delivery, ...message.delivery };
  return items.filter(item => ['sent', 'failed', 'unknown'].includes(
    delivery[String(message.presentation?.id || '') + '#media:' + String(item.id || '')] || ''));
}

export function weixinMessages(messages: Message[]): Message[] {
  return messages.filter(message => {
    const p = message.presentation;
    const kind = p?.author.kind || message.author?.kind;
    return message.conversation === 'weixin' && (kind === 'owner' || kind === 'xiaoyou')
      && (p ? p.placement === 'chat' : message.scope !== 'task' && message.kind !== 'activity'
          && !['desktop', 'external'].includes(message.source?.type || ''));
  });
}

export function weixinStateLine(view: WeixinView, offline: boolean): {
  label: string; tone: 'green' | 'grey' | 'amber'; warning?: string; rescan: boolean;
} {
  if (offline) return { label: '连不上', tone: 'grey', warning: '小鱼暂时离线，显示的是已保存的记录。', rescan: false };
  if (view.state === 'unknown') return { label: '状态待同步', tone: 'grey', warning: '微信连接状态暂不可用，显示的是已同步的记录。', rescan: false };
  if (view.state === 'relogin') return { label: '需要重新扫码', tone: 'amber', warning: '微信登录过期了，重新扫一下就好，记录都还在。', rescan: view.local };
  if (view.state === 'unreachable') return { label: '连不上', tone: 'grey',
    warning: view.reason === 'host_offline' ? `${view.host?.name || '连接微信的电脑'}没开机或断网了，微信消息要等它连上才收得到。` : '微信那边暂时连不上，正在重试。', rescan: false };
  if (view.state === 'connected') return { label: '已连接', tone: 'green', rescan: false };
  return { label: view.state === 'binding' ? '连接中' : '未连接', tone: 'grey', rescan: false };
}

export function weixinBindView(state: BindState): { steps: Array<'now' | 'todo' | 'done'>; caption: string } {
  switch (state) {
    case 'starting': return { steps: ['now', 'now', 'todo'], caption: '正在生成二维码…' };
    case 'waiting': return { steps: ['now', 'now', 'todo'], caption: '请用微信扫描二维码' };
    case 'scanned': return { steps: ['done', 'done', 'now'], caption: '等你在手机上确认' };
    case 'need_code': return { steps: ['done', 'done', 'now'], caption: '输入微信里显示的验证码' };
    case 'confirmed': return { steps: ['done', 'done', 'done'], caption: '连上了' };
    case 'expired': return { steps: ['todo', 'todo', 'todo'], caption: '二维码过期了' };
    case 'wrong_account': return { steps: ['todo', 'todo', 'todo'], caption: '这不是原来的微信账号' };
    default: return { steps: ['todo', 'todo', 'todo'], caption: state === 'cancelled' ? '已取消' : '没连上微信' };
  }
}
