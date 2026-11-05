import React, { useEffect, useState } from 'react';
import { weixinBindView, type BindState } from '../core/weixin';
import { Icon } from './components';
import './weixin.css';

export interface WeixinScanProps {
  state: BindState;
  qrImage?: string;
  expiresAt?: number;
  computerName: string;
  accountName?: string;
  reconnect?: boolean;
  hasHistory?: boolean;
  error?: string;
  onRetry(): void;
  onCode(code: string): void;
  onDetails(): void;
  onHistory(): void;
}

/** Visual component only. Its parent owns binding, authorization and cancellation. */
export function WeixinScan(props: WeixinScanProps) {
  const [now, setNow] = useState(() => Date.now()), [code, setCode] = useState('');
  useEffect(() => {
    if (props.state !== 'waiting' || !props.expiresAt) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [props.state, props.expiresAt]);
  const progress = weixinBindView(props.state);
  const qr = props.qrImage?.startsWith('data:image/png;base64,') ? props.qrImage : undefined;
  const seconds = props.expiresAt ? Math.max(0, Math.ceil(props.expiresAt - now / 1000)) : null;
  const dim = ['scanned', 'need_code', 'expired'].includes(props.state);
  const failed = props.state === 'failed' || props.state === 'wrong_account';
  const descriptions = ['打开手机微信，点右上角「＋」→「扫一扫」',
    props.reconnect && props.accountName ? `用原来的微信「${props.accountName}」扫左边的二维码` : '扫左边的二维码',
    '在手机上点「确认」'];
  return <section className="weixin-scan" aria-label="微信扫码连接">
    <div className="weixin-scan-icon"><Icon name="weixin" /></div>
    <h2>{props.reconnect ? '重新扫码' : '把小鱼接进微信'}</h2>
    <p className="weixin-scan-sub">{props.reconnect ? '微信登录过期了，扫一下就好，记录都还在。' : '连上之后，在微信里就能和小鱼说话。'}</p>
    <div className="weixin-scan-card">
      <div className="weixin-scan-left">
        <div className={'weixin-qr' + (dim ? ' dim' : '') + (failed ? ' failed' : '') + (props.state === 'starting' ? ' loading' : '')}>
          {qr && props.state !== 'confirmed' && <img src={qr} alt="微信连接二维码" />}
          {props.state === 'starting' && <span className="task-spinner" aria-label="正在生成二维码" />}
          {props.state === 'scanned' && <div className="weixin-qr-overlay"><span className="weixin-check"><Icon name="check" /></span>已扫码</div>}
          {props.state === 'expired' && <div className="weixin-qr-overlay">二维码过期了<button className="primary" onClick={props.onRetry}>换一个</button></div>}
          {props.state === 'confirmed' && <span className="weixin-check big"><Icon name="check" /></span>}
          {failed && <div className="weixin-qr-overlay"><span>没连上微信</span><small>{props.state === 'wrong_account' ? `这不是原来的微信${props.accountName ? `「${props.accountName}」` : ''}。要换微信号，先断开再连。` : props.error || '暂时无法连接微信。'}</small></div>}
        </div>
        <div className="weixin-scan-caption" role="status">
          {props.state === 'waiting' && seconds !== null ? <>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')} 后过期 · <button className="quiet-button" onClick={props.onRetry}>换一个</button></> : null}
          {props.state !== 'waiting' && props.state !== 'need_code' && !failed && progress.caption}
          {props.state === 'waiting' && seconds === null && progress.caption}
          {props.state === 'need_code' && <form className="weixin-code" onSubmit={event => { event.preventDefault(); if (/^[0-9]{1,16}$/.test(code)) props.onCode(code); }}>
            <label htmlFor="weixin-code">输入微信里显示的验证码</label><div><input id="weixin-code" inputMode="numeric" maxLength={16} value={code} onChange={event => setCode(event.target.value)} /><button className="primary" type="submit">确定</button></div>
          </form>}
          {failed && <><button className="primary" onClick={props.onRetry}>重试</button><button className="quiet-button" onClick={props.onDetails}>详情</button></>}
        </div>
      </div>
      <ol className="weixin-scan-steps">{descriptions.map((description, index) => <li key={description} className={progress.steps[index]}><i>{progress.steps[index] === 'done' ? <Icon name="check" /> : index + 1}</i><span>{description}</span></li>)}</ol>
    </div>
    <p className="weixin-scan-notes">由这台电脑（{props.computerName}）连着微信，它开着才收得到。<br />连上后，小鱼可以查询已同步的对话记录；微信消息不会混进主对话。</p>
    {props.hasHistory && <button className="quiet-button" onClick={props.onHistory}>查看以前的微信记录 ›</button>}
  </section>;
}
