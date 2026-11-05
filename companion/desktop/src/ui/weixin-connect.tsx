import React, { useEffect, useRef, useState } from 'react';
import type { BindState, ChannelState } from '../core/weixin';
import { WeixinScan } from './weixin-scan';
import { WeixinRecord } from './weixin-record';
import type { Message } from '../shared';

/** The QR remains in this component; it is never cached or sent to peers. */
export function WeixinConnect({ space, runtime, computerName, messages, connected, renderMessage }: { space: string; runtime: string; computerName: string; messages: Message[]; connected: boolean; renderMessage(message: Message): React.ReactNode }) {
  const [state, setState] = useState<BindState>('starting');
  const [channelState, setChannelState] = useState<ChannelState>('unbound');
  const [qr, setQR] = useState(''), [error, setError] = useState(''), [details, setDetails] = useState(false);
  const generation = useRef(0), attempt = useRef('');

  async function cancel(id: string) {
    if (!id) return;
    await window.xiaoyu.weixinCancel({ space, runtime, attempt: id });
  }
  async function poll(id: string, turn: number, code?: string): Promise<void> {
    try {
      const result = await window.xiaoyu.weixinPoll({ space, runtime, attempt: id, ...(code ? { code } : {}) });
      if (generation.current !== turn || attempt.current !== id) return;
      const next = result.state as BindState;
      setState(next);
      if (next === 'confirmed') { attempt.current = ''; setQR(''); setChannelState('connected'); return; }
      if (next === 'waiting' || next === 'scanned')
        window.setTimeout(() => { if (generation.current === turn) void poll(id, turn); }, 1000);
    } catch (reason) {
      if (generation.current === turn) { setError(reason instanceof Error ? reason.message : '暂时无法连接微信'); setState('failed'); }
    }
  }
  async function start(): Promise<void> {
    const turn = ++generation.current, previous = attempt.current;
    setState('starting'); setError(''); setQR(''); setDetails(false);
    try {
      if (previous) { await cancel(previous); if (attempt.current === previous) attempt.current = ''; }
      const result = await window.xiaoyu.weixinBind({ space, runtime });
      if (generation.current !== turn) { void cancel(result.id).catch(() => {}); return; }
      attempt.current = result.id; setQR(result.qrImage); setState('waiting');
      void poll(result.id, turn);
    } catch (reason) {
      if (generation.current === turn) { setError(reason instanceof Error ? reason.message : '暂时无法连接微信'); setState('failed'); }
    }
  }
  useEffect(() => {
    const turn = ++generation.current;
    void window.xiaoyu.weixinStatus({ space, runtime }).then(value => {
      if (generation.current !== turn) return;
      if (value.state === 'connected' || value.state === 'unreachable') {
        setChannelState(value.local === false ? 'unknown' : value.state); setState('confirmed');
      }
      else if (value.state === 'unbound' && messages.some(message => message.conversation === 'weixin')) {
        setChannelState('unknown'); setState('confirmed');
      } else if (value.state === 'unbound') void start();
      else { setState('failed'); setError('微信连接暂不可用，请重试或在连接微信的电脑上查看。'); }
    }).catch(reason => { if (generation.current === turn) { setState('failed'); setError(reason instanceof Error ? reason.message : '暂时无法连接微信'); } });
    return () => { generation.current++; const id = attempt.current; attempt.current = ''; if (id) void cancel(id).catch(() => {}); };
  }, [space, runtime]);

  useEffect(() => {
    if (state !== 'confirmed' || !connected) return;
    let active = true;
    const timer = window.setInterval(() => {
      void window.xiaoyu.weixinStatus({ space, runtime }).then(value => {
        if (active && (value.state === 'connected' || value.state === 'unreachable' || value.state === 'unbound'))
          setChannelState(value.local === false ? 'unknown' : value.state);
      }).catch(() => { if (active) setChannelState('unreachable'); });
    }, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [space, runtime, state, connected]);

  if (state === 'confirmed') return <WeixinRecord
    view={{ state: channelState, account: null, host: { runtime_id: runtime, name: computerName, online: connected }, local: true, reason: channelState === 'unreachable' ? 'service' : null, undelivered: 0, rev: 0 }}
    offline={!connected} messages={messages} renderMessage={renderMessage} onRescan={() => void start()} />;
  return <>
    <WeixinScan state={state} qrImage={qr} computerName={computerName} error={error}
      onRetry={() => void start()} onCode={code => { if (attempt.current) void poll(attempt.current, generation.current, code); }}
      hasHistory={messages.some(message => message.conversation === 'weixin')}
      onDetails={() => setDetails(value => !value)} onHistory={() => {
        generation.current++; const id = attempt.current; attempt.current = '';
        if (id) void cancel(id).catch(() => {});
        setQR(''); setChannelState('unbound'); setState('confirmed');
      }} />
    {details && error && <div className="alert" role="alert">{error}</div>}
  </>;
}
