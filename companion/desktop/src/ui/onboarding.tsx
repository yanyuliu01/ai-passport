import React, { useState } from 'react';
import type { ClientView, LocalAgent, SetupScan } from '../shared';
import xiaoyu from './images/xiaoyu.png';
import codex from './images/codex.png';
import claude from './images/claude.png';
import './onboarding.css';
import { AccountPanel } from './account';

type Step = 'welcome' | 'detect' | 'agents' | 'review' | 'starting' | 'result';
const authLabel = (agent: LocalAgent) => !agent.installed ? '未检测到' : agent.bundled ? '随小鱼内置 · 无需另装' : agent.auth === 'signed-in' ? '已登录'
  : agent.auth === 'signed-out' ? '需要登录' : agent.id === 'dsh' ? '已安装 · API 配置单独确认' : '已安装 · 登录状态待验证';
export function Onboarding({ view, onDone, local = false }: { view: ClientView; onDone(): void; local?: boolean }) {
  const [first] = useState(() => !view.account?.signedIn || !view.setup?.confirmed || Boolean(view.setup?.firstRun?.required));
  return first ? <FirstConnection view={view} onDone={onDone} /> : <AdvancedSetup view={view} onDone={onDone} local={local} />;
}

function FirstConnection({ view, onDone }: { view: ClientView; onDone(): void }) {
  const [stage, setStage] = useState<'account' | 'helpers' | 'key'>(() => view.setup?.firstRun?.phase && view.setup.firstRun.phase !== 'key' ? 'key' : 'account');
  const [key, setKey] = useState(''), [error, setError] = useState('');
  const [scan, setScan] = useState<SetupScan>(), [scanning, setScanning] = useState(false);
  const [selected, setSelected] = useState<string[]>(view.setup?.firstRun?.selectedAgents || []);
  const [submitting, setSubmitting] = useState(false);
  const state = view.setup?.firstRun, phase = state?.phase || 'key';
  const active = ['validating', 'preparing', 'reply'].includes(phase), ready = phase === 'ready';
  const loggedIn = Boolean(view.account?.signedIn && view.account.provisioned);
  const step = !loggedIn ? 0 : active || ready ? 3 : stage === 'account' ? 0 : stage === 'helpers' ? 1 : 2;
  const headings = ['把小鱼带到这台电脑', '选择这台电脑上的帮手', '连接你的 DeepSeek', ready ? '小鱼已经准备好了' : '正在准备第一次聊天'];
  async function detect() {
    setStage('helpers'); setError(''); setScanning(true);
    try { const result = await window.xiaoyu.scanLocal(); setScan(result);
      setSelected(previous => previous.filter(id => result.agents.some(agent => agent.id === id && agent.installed && agent.auth !== 'signed-out')));
    } catch (problem) { setError(cleanError(problem)); }
    finally { setScanning(false); }
  }
  async function start() {
    setError(''); setSubmitting(true);
    try { await window.xiaoyu.startFirstRun({ ...(key.trim() ? { key } : {}), agents: selected }); setKey(''); }
    catch (problem) { setError(cleanError(problem)); }
    finally { setSubmitting(false); }
  }
  return <main className="onboarding">
    <header className="onboarding-title"><span>小鱼</span><small>首次使用</small></header>
    <div className="onboarding-body">
      <aside className="onboarding-aside"><img src={xiaoyu} alt="小鱼" /><h2>装好，<br />就能聊。</h2>
        <p>运行环境已经随小鱼备好。<br />选择帮手，用自己的账号和 Key 开始。</p>
        <ol aria-label="开始使用">{['登录小鱼账号', '选择本机帮手', '填写 DeepSeek Key', '收到第一条回复'].map((label, index) =>
          <li key={label} aria-current={step === index ? 'step' : undefined}><span>{index < step ? '✓' : index + 1}</span>{label}</li>)}</ol>
      </aside>
      <section className="onboarding-panel" aria-busy={active || submitting || scanning}>
        <small>开始使用 · {step + 1} / 4</small><h1>{headings[step]}</h1>
        {(error || state?.problem) && <p className="onboarding-error" role="alert">{error || state?.problem}</p>}
        {step === 0 && <AccountPanel view={view.account} configureLabel="下一步" onConfigure={loggedIn ? () => void detect() : undefined} />}
        {step === 1 && <>
          <p className="onboarding-description">扫描本机已安装的 Codex 和 Claude Code，用你已有的登录接入。只有勾选的帮手会交给小鱼使用。</p>
          {scanning && <p role="status"><span className="onboarding-spinner" />正在检测安装位置和登录状态…</p>}
          <div className="onboarding-agents">{['codex', 'claude', 'pi'].map(id => {
            const agent = scan?.agents.find(item => item.id === id), usable = Boolean(agent?.installed && agent.auth !== 'signed-out');
            return <label className="onboarding-radio" key={id}>
              <input type="checkbox" aria-label={'接入 ' + (id === 'claude' ? 'Claude Code' : id === 'codex' ? 'Codex' : 'Pi')}
                checked={selected.includes(id)} disabled={scanning || !usable} onChange={event => setSelected(previous => event.target.checked ? [...previous, id] : previous.filter(item => item !== id))} />
              <span><strong>{id === 'claude' ? 'Claude Code' : id === 'codex' ? 'Codex' : 'Pi'}</strong>
                <small>{agent ? authLabel(agent) : scanning ? '检测中' : '尚未检测'}{agent?.version ? ' · ' + agent.version : ''}</small>
                {agent?.installed && agent.auth === 'unknown' && !agent.bundled && <small>登录状态待验证，接入后的实际任务会进一步确认。</small>}
                {agent?.auth === 'signed-out' && <small>请先在该助手中登录，然后重新扫描。</small>}
              </span></label>;
          })}</div>
          <p className="onboarding-footnote">扫描不调用模型、不读取登录密钥、不导入历史。Codex 使用 gpt-6.1-sol，Claude Code 使用 Sonnet；需要批准的操作仍会询问你。内置 Pi 可做问答，不开放文件和命令工具。WorkBuddy、豆包工作后续接入。</p>
          <footer><button disabled={scanning} onClick={() => void detect()}>重新扫描</button>
            <button className="primary" disabled={scanning} onClick={() => setStage('key')}>{selected.length ? '使用所选帮手，继续 →' : '暂不接入帮手，继续 →'}</button></footer>
        </>}
        {step === 2 && <>
          <p className="onboarding-description">DeepSeek 提供聊天模型。你自己的 API Key 只保存在这台电脑，不会上传到小鱼账号服务器。</p>
          <button onClick={() => void window.xiaoyu.openLink('https://platform.deepseek.com/api_keys')}>去 DeepSeek 申请 Key ↗</button>
          <label className="onboarding-key">DeepSeek API Key
            <input type="password" autoComplete="off" maxLength={4096} value={key} onChange={event => setKey(event.target.value)}
              placeholder={state?.hasKey ? '本机已有你的 Key，可以留空复用' : '粘贴你的 API Key'} />
          </label>
          <p className="onboarding-footnote">点击后会进行一次简短 Key 验证，再请小鱼发来第一条问候，产生少量 DeepSeek 调用费用。</p>
          <footer>{phase === 'key' && <button disabled={submitting} onClick={() => void detect()}>上一步</button>}
            <button className="primary" disabled={submitting || !state?.hasKey && key.trim().length < 12} onClick={() => void start()}>验证并开始聊天</button></footer>
        </>}
        {step === 3 && <>
          {!ready && <div className="onboarding-wait" role="status"><span className="onboarding-spinner" />
            {phase === 'validating' ? '正在验证你的 Key…' : phase === 'preparing' ? '正在准备本机服务，首次打开可能需要一点时间…' : '连接已建立，正在等待小鱼的第一条回复…'}
            <p>准备和验证在后台进行，不会改动已有聊天或帮手的登录。</p></div>}
          {ready && <><p>这是小鱼通过本机服务发来的真实回复：</p><p className="onboarding-first-reply">{state?.reply}</p>
            <p className="onboarding-footnote">第一条问候会保留在主对话。所选帮手已随本机服务配置，之后可在设置中管理。</p></>}
          <footer>{ready ? <button className="primary" onClick={onDone}>进入聊天 →</button> :
            <button onClick={() => void window.xiaoyu.checkFirstRun().catch(problem => setError(cleanError(problem)))}>重新检查进度</button>}</footer>
        </>}
      </section>
    </div>
  </main>;
}

function AdvancedSetup({ view, onDone, local = false }: { view: ClientView; onDone(): void; local?: boolean }) {
  const [step, setStep] = useState<Step>('welcome');
  const [scan, setScan] = useState<SetupScan>();
  const [selected, setSelected] = useState<string[]>([]), [service, setService] = useState('');
  const [key, setKey] = useState('');
  const [loginStart, setLoginStart] = useState(false), [error, setError] = useState('');
  const [returnStep, setReturnStep] = useState<Step>('review');
  const busy = ['detect', 'starting'].includes(step);
  const connected = view.spaces.some(space => space.connected && !space.catchingUp);
  const current = scan?.services.find(item => item.id === service);
  const main = scan?.agents.find(item => item.id === 'dsh');
  const chosen = scan?.agents.filter(agent => selected.includes(agent.id)) || [];
  const stage = step === 'welcome' ? 0 : ['detect', 'agents'].includes(step) ? 1 : ['review'].includes(step) ? 2 : 3;
  async function detect() {
    setError(''); setStep('detect');
    try { const value = await window.xiaoyu.scanLocal(); setScan(value); setService(''); setSelected([]); setStep('agents'); }
    catch { setError('检测未完成，请重试。已有服务和记录没有改动。'); setStep('agents'); }
  }
  async function start() {
    setError(''); setReturnStep('review'); setStep('starting');
    try {
      await window.xiaoyu.enableLocal({ ...(service ? { service } : {}), agents: service ? [] : selected,
        ...(key ? { deepseekKey: key } : {}) });
      setKey('');
      setStep('result');
      if (loginStart) try { await window.xiaoyu.loginStart(true); } catch { setError('服务已连接，但登录时启动未开启，可在设置中重试。'); }
    } catch (problem) { setKey(''); setError(cleanError(problem)); setStep('review'); }
  }
  const canReview = Boolean(scan && (service || (main?.installed && (scan.hasDeepSeekKey || key.trim().length >= 12))));
  const heading: Record<Step, string> = { welcome: local ? '启用这台电脑的小鱼' : '把小鱼带到这台电脑', detect: '看看你已经有哪些帮手',
    agents: '让熟悉的帮手一起加入', review: '确认这次启用的内容', starting: '正在启用本机服务', result: connected ? '连接已经建立' : '正在补齐最近的对话' };
  return <main className="onboarding">
    <header className="onboarding-title"><span>小鱼</span><small>{view.setup?.device || '这台电脑'} · 首次连接</small>
      {view.setup?.confirmed && !busy && <button onClick={onDone}>返回小鱼</button>}</header>
    <div className="onboarding-body">
      <aside className="onboarding-aside"><img src={xiaoyu} alt="小鱼" /><h2>换个地方，<br />接着聊。</h2><p>你的对话、正在做的事，<br />都留在同一个小鱼里。</p>
        <ol aria-label="启用步骤">{['登录小鱼账号', '检测与选择', '确认启用', '连接与同步'].map((label, index) => <li key={label} aria-current={stage === index ? 'step' : undefined}><span>{index < stage ? '✓' : index + 1}</span>{label}</li>)}</ol>
      </aside>
      <section className="onboarding-panel" aria-busy={busy}>
        <small>开始使用 · {Math.min(stage + 1, 4)} / 4</small><h1 tabIndex={-1}>{heading[step]}</h1>
        {error && <div role="alert" className="onboarding-error">{error}</div>}
        {step === 'welcome' && <>
          <AccountPanel view={view.account} onConfigure={view.account?.signedIn && view.account.provisioned ? () => void detect() : undefined} /><p className="onboarding-description">登录同一账号即可连接自己的电脑。帮手继续在各自电脑运行，已有服务和记录保留。</p>

        </>}
        {step === 'detect' && <div className="onboarding-wait" role="status"><span className="onboarding-spinner" />正在检测安装位置和登录状态…<p>只检查已知位置，不读取你的会话内容。</p></div>}
        {step === 'agents' && <>
          <p className="onboarding-description">检测只读。选择后，下一步确认接入；原助手的登录、模型配置和历史会话保持原样。</p>
          <div className="onboarding-row"><h3>本机主服务</h3><button onClick={() => void detect()}>重新检测</button></div>
          {scan?.services.length ? <div className="onboarding-services"><label className={'onboarding-radio ' + (!service ? 'selected' : '')}>
            <input type="radio" name="local-service" checked={!service} onChange={() => setService('')} /><span><strong>启用新的内置服务</strong><small>dsh + DeepSeek · 新的独立空间</small></span></label>
            {scan.services.map(item => <label className={'onboarding-radio ' + (service === item.id ? 'selected' : '')} key={item.id}>
              <input type="radio" name="local-service" checked={service === item.id} onChange={() => setService(item.id)} /><span><strong>复用已有小鱼 · {item.name}</strong><small>{item.managed ? '本客户端已有配置' : '外部服务 · 不接管进程'} · {item.agents.join(' / ')}</small><details className="onboarding-location"><summary>配置位置</summary><code>{item.source}</code></details></span></label>)}</div> : null}
          {!service && <>
            <article className="onboarding-agent main"><span className="onboarding-agent-icon">D</span><div><strong>dsh + DeepSeek <em>默认主服务</em></strong><p>{main ? authLabel(main) : '未检测到'}</p><small>日常聊天 DeepSeek Flash · 执行任务 DeepSeek V4 Pro</small>{main?.location && <details className="onboarding-location"><summary>安装位置</summary><code>{main.location}</code></details>}</div></article>
            {!main?.installed && scan && <div className="onboarding-note">内置 DeepSeek Harness 不可用，请重新安装小鱼，或连接已有小鱼。</div>}
            <h3 className="onboarding-subtitle">可选帮手</h3>
            {scan?.agents.filter(agent => agent.id !== 'dsh').map(agent => <label className={'onboarding-agent ' + (selected.includes(agent.id) ? 'selected' : '')} key={agent.id}>
              {agent.id === 'pi' ? <span className="onboarding-agent-icon">Pi</span> : <img src={agent.id === 'codex' ? codex : claude} alt="" />}<div><strong>{agent.name} {agent.version && <small>{agent.version}</small>}</strong><p>{authLabel(agent)}</p>
                <small>{agent.id === 'pi' ? '使用本机 DeepSeek Key · 仅问答，不开放文件和命令工具' : agent.installed ? '复用本机登录 · 不导入历史会话' : '安装后可在设置里接入'}</small>{agent.location && <details className="onboarding-location"><summary>安装位置</summary><code>{agent.location}</code></details>}</div>
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
          {!current && chosen.length > 0 && <p className="onboarding-footnote">本客户端调用帮手时：Codex 使用 gpt-6.1-sol，Claude Code 使用 Sonnet，Pi 使用 DeepSeek 且不开放文件和命令工具。执行范围为本客户端工作目录，需要确认的操作仍会询问你。</p>}
          {current && <p className="onboarding-footnote">{current.managed ? '客户端会恢复自己已有的服务。' : '原服务继续由原来的方式管理，小鱼不会停止或重启它。'}</p>}
          <label className="onboarding-check"><input type="checkbox" checked={loginStart} onChange={event => setLoginStart(event.target.checked)} />系统登录后启动小鱼（可选）</label>
          <footer><button onClick={() => setStep('agents')}>上一步</button><button className="primary" onClick={() => void start()}>{current ? '确认连接这个服务' : '确认并启用本机服务'}</button></footer>
        </>}
        {step === 'starting' && <div className="onboarding-wait" role="status"><span className="onboarding-spinner" />{view.runtime.message}<p>正在核对服务身份，随后同步会话记录。</p></div>}
        {step === 'result' && <>
          <p className="onboarding-description">{view.setup?.service === 'none' ? '这台电脑通过已配对设备使用小鱼。' : view.runtime.message}</p>
          <ul className="onboarding-status"><li><span>✓</span><div><strong>连接已确认</strong><p>{view.setup?.service === 'bundled' ? '本机服务由客户端管理' : view.setup?.service === 'external' ? '复用本机已有服务，不接管进程' : '使用已确认的设备与连接'}</p></div></li>
            <li><span>{connected ? '✓' : '…'}</span><div><strong>{connected ? '最近记录已同步' : '记录同步中，尚未确认完成'}</strong><p>{view.spaces.map(space => space.error || space.runtimeName).join(' · ') || '正在等待服务提供会话记录'}</p></div></li>
            <li><span>○</span><div><strong>模型实际回复待验证</strong><p>尚未发送测试消息，也未产生模型调用；可以进入聊天后验证。</p></div></li></ul>
          <footer><button onClick={() => setStep(returnStep)}>查看连接设置</button><button className="primary" onClick={onDone}>{connected ? '进入小鱼 →' : '查看缓存与连接状态 →'}</button></footer>
        </>}
      </section>
    </div>
  </main>;
}
function cleanError(error: unknown): string { return error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': Error: /, '') : '操作未完成，请重试'; }
