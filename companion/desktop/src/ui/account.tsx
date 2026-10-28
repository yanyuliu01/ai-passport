import React, { useState } from 'react';
import type { AccountView } from '../shared';
export function AccountPanel({ view, onConfigure }: { view?: AccountView; onConfigure?: () => void }) {
  const [username, setUsername] = useState(''), [password, setPassword] = useState('');
  const [register, setRegister] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function login(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await window.xiaoyou.accountLogin({ username, password, register }); }
    catch (problem) { setError(problem instanceof Error ? problem.message.replace(/^Error invoking remote method '[^']+': Error: /,'') : '登录未完成，请重试'); }
    finally { setPassword(''); setBusy(false); }
  }
  async function logout() {
    setBusy(true); setError('');
    try { await window.xiaoyou.accountLogout(); }
    catch { setError('退出未完成，请检查网络后重试'); }
    finally { setBusy(false); }
  }
  return <article className="settings-card vertical account-card">
    <h3>{view?.signedIn ? '小幽账号 · ' + view.username : '用小幽账号连接自己的电脑'}</h3>
    <p>每台电脑运行自己的 Runtime，通过同一账号互通。手机扫码连接，换网络自动恢复。</p>
    {error && <p role="alert">{error}</p>}
    {view?.signedIn ? <>
      <p role="status">{view.message}</p>
      {!view.provisioned && <small>登录后自动恢复原来的互通身份，无需原电脑在场。</small>}
      {view.provisioned && onConfigure && <button className="primary" onClick={onConfigure}>配置这台电脑的 Runtime</button>}
      <button disabled={busy} onClick={() => void window.xiaoyou.accountRefresh()}>重新连接</button>
      <button disabled={busy} onClick={() => void logout()}>退出账号</button>
    </> : <form onSubmit={event => void login(event)}>
      <label>小幽账号<input name="username" autoComplete="username" value={username} maxLength={40} required pattern="[A-Za-z0-9][A-Za-z0-9_-]{2,39}" onChange={event => setUsername(event.target.value)} placeholder="3–40 位字母、数字、下划线" /></label>
      <label>密码<input name="password" type="password" autoComplete={register ? 'new-password' : 'current-password'} value={password} minLength={8} maxLength={256} required onChange={event => setPassword(event.target.value)} placeholder="至少 8 位" /></label>
      <button className="primary" type="submit" disabled={busy || view?.busy}>{busy ? '正在连接…' : register ? '创建小幽账号' : '登录并连接'}</button>
      <button type="button" disabled={busy} onClick={() => { setRegister(!register); setPassword(''); setError(''); }}>{register ? '已有账号，去登录' : '首次使用，创建账号'}</button>
      <small>使用你自建服务器上的小幽账号。密码不保存在客户端，登录凭据由系统安全存储保护。</small>
    </form>}
  </article>;
}
