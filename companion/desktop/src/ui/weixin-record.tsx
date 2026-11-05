import React, { useLayoutEffect, useRef } from 'react';
import type { Message } from '../shared';
import { weixinMessages, weixinStateLine, type WeixinView } from '../core/weixin';
import { Icon } from './components';
import './weixin.css';

export interface WeixinRecordProps {
  view: WeixinView;
  offline: boolean;
  messages: Message[];
  renderMessage(message: Message): React.ReactNode;
  onMenu?(): void;
  onRescan(): void;
}

/** Read-only view; all writes continue through the originating WeChat channel. */
export function WeixinRecord({ view, offline, messages, renderMessage, onMenu, onRescan }: WeixinRecordProps) {
  const status = weixinStateLine(view, offline), visible = weixinMessages(messages);
  const timeline = useRef<HTMLDivElement>(null), following = useRef(true);
  const latest = visible[visible.length - 1];
  useLayoutEffect(() => {
    const node = timeline.current;
    if (node && following.current) node.scrollTop = node.scrollHeight;
  }, [visible.length, latest?.id, latest?.rev]);
  return <section className="weixin-record">
    <header className="weixin-record-header"><h1>微信</h1>{onMenu && <button className="icon-button" aria-label="微信菜单" onClick={onMenu}><Icon name="more" /></button>}</header>
    <div className="weixin-record-status"><i className={'weixin-status-dot ' + status.tone} />{status.label}
      {view.account?.name && <> · {view.account.name}</>}{view.host?.name && <> · 在 {view.host.name} 上</>}
    </div>
    {status.warning && <div className="alert" role="status"><span>{status.warning}</span>
      {status.rescan && <button className="quiet-button" onClick={onRescan}>重新扫码</button>}
      {!status.rescan && view.state === 'relogin' && view.host?.name && <span>去 {view.host.name} 上重新扫码</span>}
    </div>}
    <div ref={timeline} className="timeline" aria-label="微信记录" aria-live="polite" onScroll={event => {
      const node = event.currentTarget;
      following.current = node.scrollHeight - node.scrollTop - node.clientHeight < 100;
    }}>
      {visible.length ? visible.map(renderMessage) : <div className="chat-empty"><p>还没有微信消息，在微信里给小鱼发一句试试。</p></div>}
    </div>
    <div className="weixin-readonly"><Icon name="weixin" /><span>在微信里接着说 · 这里只能看</span></div>
  </section>;
}
