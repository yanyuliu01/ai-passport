import React, { useState } from 'react';
import type { ClientView, LocalAgent, SetupScan } from '../shared';
import xiaoyou from './images/xiaoyou.png';
import codex from './images/codex.png';
import claude from './images/claude.png';
import './onboarding.css';

type Step = 'welcome' | 'detect' | 'agents' | 'review' | 'join' | 'joining' | 'restoring' | 'starting' | 'result';
const authLabel = (agent: LocalAgent) => !agent.installed ? '未检测到' : agent.auth === 'signed-in' ? '已登录'
  : agent.auth === 'signed-out' ? '需要登录' : agent.id === 'dsh' ? '已安装 · API 配置单独确认' : '已安装 · 登录状态待验证';
export function Onboarding({ view, onDone, local = false }: { view: ClientView; onDone(): void; local?: boolean }) {
  const [step, setStep] = useState<Step>('welcome');
  const [scan, setScan] = useState<SetupScan>();
  const [selected, setSelected] = useState<string[]>([]), [service, setService] = useState('');
  const [key, setKey] = useState(''), [invitation, setInvitation] = useState('');
  const [loginStart, setLoginStart] = useState(false), [error, setError] = useState('');
  const [returnStep, setReturnStep] = useState<Step>('review');
  const busy = ['detect', 'starting', 'restoring'].includes(step);
  const connected = view.spaces.some(space => space.connected && !space.catchingUp);
  const current = scan?.services.find(item => item.id === service);
  const main = scan?.agents.find(item => item.id === 'dsh');
  const chosen = scan?.agents.filter(agent => selected.includes(agent.id)) || [];
  const stage = step === 'welcome' ? 0 : ['detect', 'agents', 'join'].includes(step) ? 1 : ['review'].includes(step) ? 2 : 3;
  async function detect() {
    setError(''); setStep('detect');
    try { const value = await window.xiaoyou.scanLocal(); setScan(value); setService(''); setSelected([]); setStep('agents'); }
    catch { setError('检测未完成，请重试。已有服务和记录没有改动。'); setStep('agents'); }
  }
  async function start() {
    setError(''); setReturnStep('review'); setStep('starting');
    try {
      await window.xiaoyou.enableLocal({ ...(service ? { service } : {}), agents: service ? [] : selected,
        ...(key ? { deepseekKey: key } : {}) });
      setKey('');
      setStep('result');
      if (loginStart) try { await window.xiaoyou.loginStart(true); } catch { setError('服务已连接，但登录时启动未开启，可在设置中重试。'); }
    } catch (problem) { setKey(''); setError(cleanError(problem)); setStep('review'); }
  }
  async function join() {
    setError(''); setReturnStep('join');
    try { await window.xiaoyou.join(invitation); setInvitation(''); setStep('joining'); }
    catch (problem) { setError(cleanError(problem)); }
  }
  async function restore() {
    setError(''); setReturnStep('welcome'); setStep('restoring');
    try { await window.xiaoyou.restoreConnections(); setStep('result'); }
    catch (problem) { setError(cleanError(problem)); setStep('welcome'); }
  }
  const canReview = Boolean(scan && (service || (main?.installed && (scan.hasDeepSeekKey || key.trim().length >= 12))));
  const heading: Record<Step, string> = { welcome: local ? '启用这台电脑的小幽' : '把小幽带到这台电脑', detect: '看看你已经有哪些帮手',
    agents: '让熟悉的帮手一起加入', review: '确认这次启用的内容', join: '连接你已有的小幽', joining: '等另一台设备确认',
    restoring: '正在恢复你确认过的连接', starting: '正在启用本机服务', result: connected ? '连接已经建立' : '正在补齐最近的对话' };
  return <main className="onboarding">
    <header className="onboarding-title"><span>小幽</span><small>{view.setup?.device || '这台电脑'} · 首次连接</small>
      {view.setup?.confirmed && !busy && <button onClick={onDone}>返回小幽</button>}</header>
    <div className="onboarding-body">
      <aside className="onboarding-aside"><img src={xiaoyou} alt="小幽" /><h2>换个地方，<br />接着聊。</h2><p>你的对话、正在做的事，<br />都留在同一个小幽里。</p>
        <ol aria-label="启用步骤">{['选择连接方式', '检测与选择', '确认启用', '连接与同步'].map((label, index) => <li key={label} aria-current={stage === index ? 'step' : undefined}><span>{index < stage ? '✓' : index + 1}</span>{label}</li>)}</ol>
      </aside>
      <section className="onboarding-panel" aria-busy={busy}>
        <small>开始使用 · {Math.min(stage + 1, 4)} / 4</small><h1 tabIndex={-1}>{heading[step]}</h1>
        {error && <div role="alert" className="onboarding-error">{error}</div>}
        {step === 'welcome' && <>
          <p className="onboarding-description">先选一种方式。确认前，小幽不会连接或更改本机已有服务。</p>
          <button className="onboarding-choice" onClick={() => void detect()}><strong>在这台电脑启用小幽 <span>→</span></strong><p>客户端管理本机服务，默认由 dsh + DeepSeek 回应；也能接入你已有的帮手。</p></button>
          <button className="onboarding-choice" onClick={() => { setError(''); setStep('join'); }}><strong>连接已有的小幽 <span>→</span></strong><p>从另一台设备取得邀请，配对后同步记录，继续原来的聊天。</p></button>
          {!!view.setup?.savedConnections.length && <details className="onboarding-saved"><summary>发现此前保存的连接 · 需要你确认一次</summary>
            {view.setup.savedConnections.map((item, index) => <p key={index}>{item.name}<small>{item.route}</small></p>)}
            <p>恢复后会继续同步，原待发送请求仍使用原来的服务和请求编号。</p><button onClick={() => void restore()}>确认恢复这些连接</button></details>}
        </>}
        {step === 'detect' && <div className="onboarding-wait" role="status"><span className="onboarding-spinner" />正在检测安装位置和登录状态…<p>只检查已知位置，不读取你的会话内容。</p></div>}
        {step === 'agents' && <>
          <p className="onboarding-description">检测只读。选择后，下一步确认接入；原助手的登录、模型配置和历史会话保持原样。</p>
          <div className="onboarding-row"><h3>本机主服务</h3><button onClick={() => void detect()}>重新检测</button></div>
          {scan?.services.length ? <div className="onboarding-services"><label className={'onboarding-radio ' + (!service ? 'selected' : '')}>
            <input type="radio" name="local-service" checked={!service} onChange={() => setService('')} /><span><strong>启用新的内置服务</strong><small>dsh + DeepSeek · 新的独立空间</small></span></label>
            {scan.services.map(item => <label className={'onboarding-radio ' + (service === item.id ? 'selected' : '')} key={item.id}>
              <input type="radio" name="local-service" checked={service === item.id} onChange={() => setService(item.id)} /><span><strong>复用已有小幽 · {item.name}</strong><small>{item.managed ? '本客户端已有配置' : '外部服务 · 不接管进程'} · {item.agents.join(' / ')}</small><details className="onboarding-location"><summary>配置位置</summary><code>{item.source}</code></details></span></label>)}</div> : null}
          {!service && <>
            <article className="onboarding-agent main"><span className="onboarding-agent-icon">D</span><div><strong>dsh + DeepSeek <em>默认主服务</em></strong><p>{main ? authLabel(main) : '未检测到'}</p><small>日常聊天 DeepSeek Flash · 执行任务 DeepSeek V4 Pro</small>{main?.location && <details className="onboarding-location"><summary>安装位置</summary><code>{main.location}</code></details>}</div></article>
            {!main?.installed && scan && <div className="onboarding-note">尚未安装 DeepSeek Harness。安装并完成配置后重新检测，或返回连接已有小幽。</div>}
            <h3 className="onboarding-subtitle">可选帮手</h3>
            {scan?.agents.filter(agent => agent.id !== 'dsh').map(agent => <label className={'onboarding-agent ' + (selected.includes(agent.id) ? 'selected' : '')} key={agent.id}>
              <img src={agent.id === 'codex' ? codex : claude} alt="" /><div><strong>{agent.name} {agent.version && <small>{agent.version}</small>}</strong><p>{authLabel(agent)}</p>
                <small>{agent.installed ? '复用本机登录 · 不导入历史会话' : '安装后可在设置里接入'}</small>{agent.location && <details className="onboarding-location"><summary>安装位置</summary><code>{agent.location}</code></details>}</div>
              <input type="checkbox" aria-label={'接入 ' + agent.name} checked={selected.includes(agent.id)} disabled={!agent.installed || agent.auth === 'signed-out'} onChange={event => setSelected(value => event.target.checked ? [...value, agent.id] : value.filter(id => id !== agent.id))} /></label>)}
            <label className="onboarding-key">DeepSeek API Key {scan?.hasDeepSeekKey && <small>本机已有可复用的 Key，留空即可</small>}
              <input type="password" autoComplete="off" value={key} maxLength={4096} placeholder={scan?.hasDeepSeekKey ? '已在本机配置' : '输入 DeepSeek API Key'} onChange={event => setKey(event.target.value)} />
            </label><p className="onboarding-footnote">Key 仅存入本机系统安全存储。登录状态检测不会调用模型，实际回复仍需验证。</p>
          </>}
          {service && <div className="onboarding-note">复用原服务会保留其主模型、帮手和记录；这次不改变原配置。你将在下一步确认连接。</div>}
          <footer><button onClick={() => { setKey(''); setStep('welcome'); }}>返回</button><button className="primary" disabled={!canReview} onClick={() => { setError(''); setStep('review'); }}>继续确认 →</button></footer>
        </>}
        {step === 'review' && <>
          <p className="onboarding-description">确认后才会连接服务、开始同步。以后打开客户端，会自动恢复你在这里确认的设置。</p>
          <dl className="onboarding-summary"><dt>本机服务</dt><dd>{current ? '复用 · ' + current.name : '客户端内置 Runtime'}</dd><dt>主响应者</dt><dd>{current ? '沿用原配置 · ' + current.agents.join(' / ') : 'dsh + DeepSeek'}</dd>
            <dt>帮手</dt><dd>{current ? '沿用原服务中的帮手' : chosen.map(agent => agent.name).join('、') || '暂不接入，可稍后添加'}</dd>
            <dt>连接方式</dt><dd>本机回环连接 · 不向公网开放 Runtime</dd><dt>后台运行</dt><dd>关闭窗口继续运行；退出客户端时停止自有服务</dd>
          </dl>
          {!current && chosen.length > 0 && <p className="onboarding-footnote">本客户端调用帮手时：Codex 使用 gpt-6.1-sol，Claude Code 使用 Sonnet。执行范围为本客户端工作目录，需要确认的操作仍会询问你。</p>}
          {current && <p className="onboarding-footnote">{current.managed ? '客户端会恢复自己已有的服务。' : '原服务继续由原来的方式管理，小幽不会停止或重启它。'}</p>}
          <label className="onboarding-check"><input type="checkbox" checked={loginStart} onChange={event => setLoginStart(event.target.checked)} />系统登录后启动小幽（可选）</label>
          <footer><button onClick={() => setStep('agents')}>上一步</button><button className="primary" onClick={() => void start()}>{current ? '确认连接这个服务' : '确认并启用本机服务'}</button></footer>
        </>}
        {step === 'join' && <>
          <p className="onboarding-description">在已连接的设备上打开“连接与设置”，生成邀请。原设备确认后，这里会补齐同一个空间的对话。</p>
          <label className="onboarding-key">小幽邀请链接<textarea value={invitation} maxLength={16384} autoComplete="off" placeholder="粘贴另一台设备生成的邀请" onChange={event => setInvitation(event.target.value)} /></label>
          <div className="onboarding-note">这一步只连接已有小幽，不新建本机空间，也不会自动启用本机服务。邀请有有效期，原设备需要在线。</div>
          <footer><button onClick={() => setStep('welcome')}>返回</button><button className="primary" disabled={!invitation.trim()} onClick={() => void join()}>请求连接 →</button></footer>
        </>}
        {step === 'starting' && <div className="onboarding-wait" role="status"><span className="onboarding-spinner" />{view.runtime.message}<p>正在核对服务身份，随后同步会话记录。</p></div>}
        {step === 'restoring' && <div className="onboarding-wait" role="status"><span className="onboarding-spinner" />恢复已保存连接…</div>}
        {step === 'joining' && <>
          <div className="onboarding-wait" role="status">{view.setup?.confirmed ? '配对已确认，正在同步最近记录…' : '请在原设备上确认这台电脑的加入请求。'}<p>{view.pairing.relayState}</p></div>
          {connected ? <button className="primary" onClick={() => { setStep('result'); }}>查看连接结果 →</button> : <button onClick={() => { setError('原邀请可能已过期，请从原设备重新生成后粘贴。'); setStep('join'); }}>使用新的邀请</button>}
        </>}
        {step === 'result' && <>
          <p className="onboarding-description">{view.setup?.service === 'none' ? '这台电脑通过已配对设备使用小幽。' : view.runtime.message}</p>
          <ul className="onboarding-status"><li><span>✓</span><div><strong>连接已确认</strong><p>{view.setup?.service === 'bundled' ? '本机服务由客户端管理' : view.setup?.service === 'external' ? '复用本机已有服务，不接管进程' : '使用已确认的设备与连接'}</p></div></li>
            <li><span>{connected ? '✓' : '…'}</span><div><strong>{connected ? '最近记录已同步' : '记录同步中，尚未确认完成'}</strong><p>{view.spaces.map(space => space.error || space.runtimeName).join(' · ') || '正在等待服务提供会话记录'}</p></div></li>
            <li><span>○</span><div><strong>模型实际回复待验证</strong><p>尚未发送测试消息，也未产生模型调用；可以进入聊天后验证。</p></div></li></ul>
          <footer><button onClick={() => setStep(returnStep)}>查看连接设置</button><button className="primary" onClick={onDone}>{connected ? '进入小幽 →' : '查看缓存与连接状态 →'}</button></footer>
        </>}
      </section>
    </div>
  </main>;
}
function cleanError(error: unknown): string { return error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': Error: /, '') : '操作未完成，请重试'; }
