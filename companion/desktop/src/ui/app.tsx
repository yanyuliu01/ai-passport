import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { ClientView, DesktopAPI, HelperView, Json, Message, SpaceView, PhoneConnection } from '../shared';
import { activeTask, unverifiedTask, helperMatchesTask, mainMessages, taskStatusLabel, messageBlocks, authorLabel, projection, displayIssue } from '../core/presentation';
import { Avatar, elapsed, HelperPill, Icon, QuotaAvatar, shortTime, taskKey } from './components';
import { Directory } from './directory';
import { DisplayText } from './display-text';
import { Onboarding } from './onboarding';
import './style.css';

declare global { interface Window { xiaoyou: DesktopAPI } }
const empty: ClientView = { version: '0.1.7', spaces: [], selectedSpace: '', background: true,
  runtime: { state: 'disconnected', message: '等待你确认连接方式' }, pairing: { joined: 0, relayState: '未设置跨网入口', requests: [] } };
type Page = 'home' | 'chat' | 'work' | 'settings' | 'native';
type TaskRef = { id: string; runtime: string };
function App() {
  const [directoryOpen,setDirectoryOpen]=useState(false);
  const [view, setView] = useState<ClientView>(empty), [loaded, setLoaded] = useState(false), [setupOpen, setSetupOpen] = useState(false);
  const [page, setPage] = useState<Page>('home'), [sidebarOpen, setSidebarOpen] = useState(false);
  const [text, setText] = useState(''), [problem, setProblem] = useState(''), [working, setWorking] = useState(false), [sending, setSending] = useState(false);
  const [selectedTask, setSelectedTask] = useState<TaskRef>(), [target, setTarget] = useState<TaskRef & { legacy: boolean }>();
  const [historyLimit, setHistoryLimit] = useState(100), [completedOpen, setCompletedOpen] = useState(false), [unverifiedOpen, setUnverifiedOpen] = useState(false), [attachmentsOpen, setAttachmentsOpen] = useState(false);
  const [usage, setUsage] = useState<string>(), [detail, setDetail] = useState<Json>(), [detailError, setDetailError] = useState(''), [allSteps, setAllSteps] = useState(false), [highlight, setHighlight] = useState('');
  const detailGeneration = useRef(0), selection = useRef('');
  const bottom = useRef<HTMLDivElement>(null), input = useRef<HTMLTextAreaElement>(null);
  const space = view.spaces.find(item => item.id === view.selectedSpace) || view.spaces[0];
  const tasks = space?.tasks.filter(task => Boolean(task.agent || task.monitoring_only) && (!task.shared_archived || task.state==='waiting')) || [];
  const selected = tasks.find(task => task.id === selectedTask?.id && task._runtimeId === selectedTask?.runtime);
  const linkedTask = tasks.find(task => task.id === target?.id && task._runtimeId === target?.runtime);
  const helpers = space?.helpers || [], helper = helpers.find(item => item.id === usage);
  const conversation = space?.conversations.find(item => item.id === space.selectedConversation);
  const messages = space ? target?.legacy ? space.messages.filter(message => message.task === target.id && message._runtimeId === target.runtime)
    : mainMessages(space.messages, space.selectedConversation) : [];
  const pending = space?.pending.filter(item => (target?.legacy ? item.payload.card === target.id && item.runtimeId === target.runtime : !item.payload.card || item.payload.reply_scope === 'main')
    && !messages.some(message => message._runtimeId === item.runtimeId && (message.id === item.messageId || message.id === item.messageId + ':you'))) || [];
  const waiting = space?.pending.some(item => item.state !== 'blocked' &&
    (target?.legacy ? item.payload.card === target.id && item.runtimeId === target.runtime : !item.payload.card || item.payload.reply_scope === 'main'))
    || messages.some(message => ['queued', 'streaming'].includes(message.status) && message.author?.kind !== 'owner');
  const waitingProblem = space?.pending.find(item => item.state === 'accepted' && item.error &&
    (target?.legacy ? item.payload.card === target.id && item.runtimeId === target.runtime : !item.payload.card || item.payload.reply_scope === 'main'))?.error;
  const awaiting = tasks.filter(task => activeTask(task) && task.state === 'waiting'), running = tasks.filter(task => activeTask(task) && task.state !== 'waiting');
  const unverified = tasks.filter(unverifiedTask).slice().reverse();
  const completed = tasks.filter(task => !activeTask(task) && !unverifiedTask(task)).slice().reverse();
  selection.current = space?.id + '/' + space?.selectedConversation;
  const title = page === 'home' ? '首页' : page === 'chat' ? target?.legacy ? '任务续聊' : conversation?.id === 'default' ? '主对话' : conversation?.title || '主对话'
    : page === 'work' ? '工作记录' : page === 'native' ? '原会话记录' : '连接与设置';
  const messageVersion = messages.filter(message => ['final', 'done', 'failed', 'cancelled'].includes(message.status)).map(message => message.id + ':' + message.rev).join('|');
  useEffect(() => {
    void window.xiaoyou.snapshot().then(value => { setView(value); if (!value.setup?.confirmed) setSetupOpen(true); setLoaded(true); }).catch(() => setProblem('暂时无法连接后台服务'));
    return window.xiaoyou.subscribe(setView);
  }, []);
  useEffect(() => { setHistoryLimit(100); setText(''); setUsage(undefined); }, [space?.id, space?.selectedConversation]);
  useEffect(() => { setSelectedTask(undefined); setDetail(undefined); setTarget(undefined); setPage('home'); }, [space?.id]);
  useEffect(() => {
    if (page === 'chat') bottom.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    if (space && page === 'chat' && document.hasFocus() && document.visibilityState === 'visible') void window.xiaoyou.markRead({ space: space.id, conversation: space.selectedConversation }).catch(() => {});
  }, [messageVersion, page, space?.id, space?.selectedConversation]);
  useEffect(() => {
    let current = true; detailGeneration.current++; setDetail(undefined); setDetailError(''); setAllSteps(false);
    if (!space || !selectedTask || !['work', 'native'].includes(page)) return;
    void window.xiaoyou.taskDetail({ space: space.id, ...selectedTask }).then(value => { if (current) setDetail(value); }).catch(() => { if (current) setDetailError('暂时无法读取进展。已有工作记录仍保留，可稍后重试。'); });
    return () => { current = false; };
  }, [space?.id, selectedTask?.id, selectedTask?.runtime, selected?.rev, selected?.seq, page]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setUsage(undefined); setAttachmentsOpen(false); setSidebarOpen(false); } };
    window.addEventListener('keydown', escape); return () => window.removeEventListener('keydown', escape);
  }, []);
  useEffect(() => {
    if (!highlight) return;
    const node = [...document.querySelectorAll<HTMLElement>('[data-message-id]')].find(node => node.dataset.messageId === highlight || node.dataset.messageId === highlight + ':you');
    node?.scrollIntoView({ block: 'center' });
    const timer = setTimeout(() => setHighlight(''), 2500); return () => clearTimeout(timer);
  }, [highlight, page, messageVersion]);
  async function readAction(action: () => Promise<unknown>) { try { await action(); } catch (error) { setProblem(clean(error)); } }
  async function run(action: () => Promise<unknown>) {
    setProblem(''); setWorking(true);
    try { await action(); } catch (error) { setProblem(clean(error)); } finally { setWorking(false); }
  }
  function openTask(task: Json) { setSelectedTask({ id: String(task.id), runtime: String(task._runtimeId) }); setPage('work'); setSidebarOpen(false); }
  function go(next: Page) { setPage(next); setSidebarOpen(false); setAttachmentsOpen(false); }
  async function chooseConversation(id: string) {
    if (!space) return;
    await run(() => window.xiaoyou.selectConversation({ space: space.id, conversation: id })); setTarget(undefined); go('chat');
  }
  async function supplement(task: Json) {
    if (!space) return;
    const conversationId = String(task.conversation || 'default');
    const legacy = !space.connections?.find(item => item.runtime === task._runtimeId)?.capabilities.includes('task_audit');
    if (space.selectedConversation !== conversationId) await window.xiaoyou.selectConversation({ space: space.id, conversation: conversationId });
    setTimeout(() => { setTarget({ id: String(task.id), runtime: String(task._runtimeId), legacy }); setPage('chat'); input.current?.focus(); }, 0);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (!space || !text.trim() || sending) return;
    const value = text, source = selection.current; setSending(true); setProblem('');
    try {
      await window.xiaoyou.send({ space: space.id, text: value, ...(target ? { card: target.id, runtime: target.runtime, mainReply: !target.legacy } : {}) });
      if (selection.current === source) setText(current => current === value ? '' : current);
    } catch (error) { setProblem(clean(error)); } finally { setSending(false); }
  }
  async function branch(message: Message) {
    if (!space) return;
    await run(() => window.xiaoyou.createConversation({ space: space.id, title: '', message: message.id, runtime: String(message._runtimeId) })); setTarget(undefined); go('chat');
  }
  const latestTaskMessage = new Map<string, string>();
  for (const message of messages) if (message.task && message.author?.kind !== 'owner') latestTaskMessage.set(String(message._runtimeId) + '/' + message.task, message.id);
  if (!loaded) return <main className="app-loading" role="status">{problem || '正在读取本机设置…'}</main>;
  if (!view.setup?.confirmed || setupOpen) return <Onboarding view={view} onDone={() => { setSetupOpen(false); setPage('home'); }} />;
  return <div className={'shell ' + (sidebarOpen ? 'sidebar-is-open' : '')}>
    {sidebarOpen && <button className="sidebar-shade" aria-label="关闭侧边栏" onClick={() => setSidebarOpen(false)} />}
    <aside className="sidebar" aria-label="小幽导航">
      <div className="identity"><Avatar size={42} /><div><strong>小幽</strong><small><i className={'dot ' + (space?.connected ? 'online' : '')} />{space?.connected ? '记录已连接' : '离线 · 保留本机记录'}</small></div></div>
      <button className={'nav ' + (page === 'home' ? 'selected' : '')} onClick={() => go('home')}><Icon name="home" /><span>首页</span></button>
      <button className={'nav ' + (page === 'chat' && conversation?.id === 'default' && !target?.legacy ? 'selected' : '')} onClick={() => void chooseConversation('default')}><Icon name="chat" /><span>主对话</span>{space?.conversations.find(item => item.id === 'default')?.unread && page !== 'chat' && <i className="unread-dot" />}</button>
      <button className={'nav work-nav ' + (page === 'work' || page === 'native' ? 'selected' : '')} onClick={() => go('work')}><Icon name="work" /><span>工作记录</span>{awaiting.length > 0 ? <b className="badge waiting">{awaiting.length}</b> : running.length > 0 ? <b className="badge">{running.length}</b> : null}</button>
      <div className="side-heading"><span>旁聊</span><button className="icon-button" aria-label="新建旁聊" disabled={!space || working} onClick={() => space && void run(async () => { await window.xiaoyou.createConversation({ space: space.id, title: '' }); setTarget(undefined); go('chat'); })}><Icon name="plus" /></button></div>
      <div className="side-conversations">{space?.conversations.filter(item => item.id !== 'default' && !item.archived).sort((a, b) => Number(Boolean(b.pinned))-Number(Boolean(a.pinned)) || (b.updatedAt || b.createdAt) - (a.updatedAt || a.createdAt)).map(item => <button key={item.id} className={'side-chat ' + (page === 'chat' && space.selectedConversation === item.id ? 'selected' : '')} onClick={() => void chooseConversation(item.id)}><span>{item.title}</span>{item.unread && space.selectedConversation !== item.id ? <i className="unread-dot" /> : <small>{shortTime(item.updatedAt)}</small>}</button>)}
        {!space?.conversations.some(item => item.id !== 'default') && <p className="side-empty">点 ＋ 开一段旁聊</p>}</div>
      <button className="nav" onClick={()=>setDirectoryOpen(true)}>统一会话目录</button>
      <div className="side-heading helpers-heading"><span>帮手</span></div>
      <div className="side-helpers">{helpers.map(item => <div className="side-helper" key={item.id}><QuotaAvatar helper={item} onClick={() => setUsage(item.id)} /><button onClick={() => { const task = tasks.find(task => helperMatchesTask(item, task) && activeTask(task)); task ? openTask(task) : setUsage(item.id); }}><span>{item.label}</span><small>{tasks.some(task => helperMatchesTask(item, task) && activeTask(task)) ? tasks.some(task => helperMatchesTask(item, task) && task.state === 'waiting') ? '等你决定' : '进行中' : item.connected ? '暂无工作' : '离线'}</small></button></div>)}
        {!helpers.length && <p className="side-empty">尚无帮手数据</p>}</div>
      <div className="sidebar-bottom"><button className={'nav ' + (page === 'settings' ? 'selected' : '')} onClick={() => go('settings')}><Icon name="settings" /><span>连接与设置</span></button><small>小幽桌面版 {view.version}</small></div>
    </aside>
    <main className="workspace"><header className="topbar"><div className="topbar-title"><button className={'icon-button ' + (page === 'native' ? '' : 'menu-toggle')} aria-label={page === 'native' ? '返回工作详情' : '打开侧边栏'} onClick={() => page === 'native' ? go('work') : setSidebarOpen(true)}><Icon name={page === 'native' ? 'back' : 'menu'} /></button><h1>{title}</h1></div><button className={'connection-status ' + (space?.connected ? 'ready' : '')} onClick={() => go('settings')} title="服务连接与模型实际可用性分别验证"><i className={'dot ' + (space?.connected ? 'online' : '')} />{space?.catchingUp ? '正在同步记录' : space?.connected ? '记录已同步' : '离线记录'}</button></header>
      {page !== 'home' && page !== 'chat' && <button className="return-home" onClick={() => go('home')}><Icon name="home" />返回首页</button>}
      {problem && <div role="alert" className="alert"><span>{problem}</span><button className="icon-button" aria-label="关闭提示" onClick={() => setProblem('')}><Icon name="close" /></button></div>}
      {page === 'home' && <section className="home-page"><Avatar size={156} /><h2>小幽在这里</h2><p>不管在哪台设备，接着聊。</p><div className="home-helpers">{helpers.map(item => <HelperPill key={item.id} helper={item} tasks={tasks} onUsage={() => setUsage(item.id)} onTask={openTask} />)}</div><div className="home-actions"><button className="primary" onClick={() => void chooseConversation('default')}><Icon name="chat" />继续主对话</button><button onClick={() => go('settings')}><Icon name="phone" />连接手机</button></div><p className="home-connection-note">{space?.connected ? '电脑记录已连接 · 手机连接需单独验证' : '电脑连接恢复中 · 本机记录保留'}</p>{awaiting.length > 0 && <button className="quiet-button" onClick={() => go('work')}>{awaiting.length} 件事等你决定 <Icon name="next" /></button>}</section>}
      {page === 'chat' && <>
        <section className="companion-header"><Avatar size={64} /><div className="companion-intro"><div><strong>小幽</strong><span>换个地方，接着聊。</span></div><div className="helper-pills">{helpers.map(item => <HelperPill key={item.id} helper={item} tasks={tasks} onUsage={() => setUsage(item.id)} onTask={openTask} />)}{!helpers.length && <small>帮手信息暂未上报</small>}</div></div></section>
        <section className="timeline" aria-label="聊天记录" aria-live="polite">
          {conversation?.context && <div className="conversation-context"><Icon name="quote" /><div><strong>来自{conversation.context.conversation === 'default' ? '主对话' : '另一段聊天'}</strong><p>{String(conversation.context.text || '')}</p></div></div>}
          {target?.legacy && <p className="legacy-note">当前服务使用任务会话续聊，这里的消息只属于这项工作。</p>}
          {messages.length === 0 && pending.length === 0 && <div className="chat-empty"><p>{conversation?.id && conversation.id !== 'default' ? '从这里开始这段旁聊。' : '想说什么，都可以从这里开始。'}</p>{conversation?.id !== 'default' && <small>发出第一句话后，标题会随会话记录更新。</small>}{!space && <button className="primary" onClick={() => setSetupOpen(true)}>连接小幽</button>}</div>}
          {messages.length > historyLimit && <button className="history-more" onClick={() => setHistoryLimit(value => value + 100)}>查看更早的消息</button>}
          {messages.slice(-historyLimit).map(message => {
            const task = tasks.find(task => task.id === message.task && task._runtimeId === message._runtimeId);
            const showTask = task && activeTask(task) && latestTaskMessage.get(taskKey(task)) === message.id;
            return <Bubble key={String(message._canonical || message.id)} message={message} task={showTask ? task : undefined} onTask={openTask} onBranch={() => void branch(message)} highlight={highlight === message.id || highlight + ':you' === message.id} canBranch={Boolean(space?.connections?.find(item => item.runtime === message._runtimeId)?.capabilities.includes('side_chats'))} />;
          })}
          {pending.map(item => <article className="message-row mine" key={item.clientId}><div className="message-bubble"><p>{item.payload.text}</p><small>{item.state === 'accepted' ? '已接收，等待回复' : item.state === 'blocked' ? item.error : item.error || '已存本机，等待发送'}</small></div></article>)}
          {waiting && <div className="thinking" role="status"><span className="working-dot" />{waitingProblem ? '回复状态待确认：' + waitingProblem : space?.connected ? '正在等待小幽回复…' : '连接恢复中，回复状态待确认…'}</div>}
          {space?.approvals.filter(approval => String(approval.conversation || 'default') === space.selectedConversation).map(approval => <Decision key={String(approval._canonical || approval.id)} approval={approval} disabled={working} onDecision={decision => void run(() => window.xiaoyou.approve({ space: space.id, id: String(approval.id), runtime: String(approval._runtimeId), decision }))} />)}
          <div ref={bottom} />
        </section>
        <div className="compose-area">{linkedTask && <div className="task-target"><Icon name="quote" /><span>补充：{String(linkedTask.title)}</span><button className="icon-button" aria-label="取消任务关联" onClick={() => setTarget(undefined)}><Icon name="close" /></button></div>}
          <form className="composer" onSubmit={submit}><textarea ref={input} aria-label="消息" value={text} disabled={!space} placeholder={target ? '补充这件事的要求…' : '跟小幽说…'} rows={1} maxLength={32000} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(event); } }} />
            <button className="icon-button" type="button" aria-label="语音输入" title="语音输入暂不可用" onClick={() => setProblem('当前桌面版本暂未接入语音输入，请先使用文字。')}><Icon name="mic" /></button>
            <div className="attachment-wrap"><button className="icon-button" type="button" aria-label="添加照片或文件" aria-expanded={attachmentsOpen} onClick={() => setAttachmentsOpen(value => !value)}><Icon name="plus" /></button>{attachmentsOpen && <div className="attachment-menu" role="menu"><button type="button" role="menuitem" onClick={() => { setAttachmentsOpen(false); setProblem('当前桌面连接暂不支持发送照片，可在手机 App 发送。'); }}><Icon name="image" />照片</button><button type="button" role="menuitem" onClick={() => { setAttachmentsOpen(false); setProblem('当前桌面连接暂不支持发送文件。'); }}><Icon name="file" />文件</button></div>}</div>
            <button className="send" type="submit" aria-label="发送" disabled={!space || !text.trim() || sending}><Icon name="up" /></button>
          </form><p className="compose-hint">{space?.connected ? 'Enter 发送 · Shift + Enter 换行' : '离线时消息先保存在本机，连接恢复后再发送'}</p></div>
      </>}
      {page === 'work' && <section className="work-page"><div className="work-list"><h2>等你决定{awaiting.length ? ' · ' + awaiting.length : ''}</h2>{awaiting.length === 0 && <p className="quiet-empty">暂时没有需要确认的事</p>}{awaiting.map(task => <TaskRow key={taskKey(task)} task={task} selected={taskKey(task) === (selected ? taskKey(selected) : '')} onClick={() => openTask(task)} />)}
        <h2>进行中{running.length ? ' · ' + running.length : ''}</h2>{running.length === 0 && <p className="quiet-empty">现在没有进行中的工作</p>}{running.map(task => <TaskRow key={taskKey(task)} task={task} selected={taskKey(task) === (selected ? taskKey(selected) : '')} onClick={() => openTask(task)} />)}
        {unverified.length > 0 && <><button className="completed-toggle" aria-expanded={unverifiedOpen} onClick={() => setUnverifiedOpen(value => !value)}><Icon name={unverifiedOpen ? 'down' : 'next'} />状态待确认 {unverified.length}</button>{unverifiedOpen && unverified.map(task => <TaskRow key={taskKey(task)} task={task} selected={taskKey(task) === (selected ? taskKey(selected) : '')} onClick={() => openTask(task)} />)}</>}
        <button className="completed-toggle" aria-expanded={completedOpen} onClick={() => setCompletedOpen(value => !value)}><Icon name={completedOpen ? 'down' : 'next'} />已完成 / 已结束 {completed.length}</button>{completedOpen && completed.map(task => <TaskRow key={taskKey(task)} task={task} selected={taskKey(task) === (selected ? taskKey(selected) : '')} onClick={() => openTask(task)} />)}</div>
        <div className="work-detail">{selected ? <><div className="task-detail-title"><Avatar type={String(selected.worker_agent || selected.agent)} size={40} /><h2>{String(selected.title || '一项工作')}</h2></div>
          <div className={'task-state ' + (selected.state === 'waiting' ? 'waiting' : '')}><strong>{selected._staleEpoch ? '状态待确认' : taskStatusLabel(selected)}</strong><p>{String(selected.progress || selected.brief || '暂无新的进展')}</p><small>{selected._staleEpoch ? '历史记录，状态待核实' : elapsed(selected.last_meaningful_at || selected.updated_at)}</small>{Boolean(selected.relay_state) && <p>{relayLabel(String(selected.relay_state))}</p>}</div>
          {space?.approvals.filter(approval => approval._runtimeId === selected._runtimeId && (approval.card === selected.id || approval.task === selected.id || (selected.approval as Json | undefined)?.id === approval.id)).map(approval => <Decision key={String(approval.id)} approval={approval} disabled={working} onDecision={decision => void run(() => window.xiaoyou.approve({ space: space.id, id: String(approval.id), runtime: String(approval._runtimeId), decision }))} />)}
          <div className="task-detail-actions"><button className="primary" disabled={working || Boolean(selected._staleEpoch) || Boolean(selected.monitoring_only)} onClick={() => void run(() => supplement(selected))}>{space?.connections?.find(item => item.runtime === selected._runtimeId)?.capabilities.includes('task_audit') ? '补充要求' : '继续任务'}</button>
            {canOpenNative(selected, space) && <button disabled={working} onClick={() => space && void run(() => window.xiaoyou.openNative({ space: space.id, id: String(selected.id), runtime: String(selected._runtimeId) }))}>在 Codex 打开 <Icon name="external" /></button>}</div>
          <div className="progress-heading"><h3>最近进展</h3><button className="quiet-button" onClick={() => setAllSteps(value => !value)}>{allSteps ? '收起' : '全部'}</button></div>
          {detailError && <p className="quiet-empty">{detailError}</p>}{Boolean(detail?.cached) && <p className="quiet-empty">本机缓存，尚未核实最新状态</p>}
          <Progress detail={detail} all={allSteps} />
          {allSteps && detail?.more === true && <button className="quiet-button" onClick={() => space && selectedTask && void readAction(async () => { const generation = detailGeneration.current; const previous = await window.xiaoyou.taskDetail({ space: space.id, ...selectedTask, before: Number(detail.before) }); if (generation !== detailGeneration.current) return; setDetail(value => ({ ...previous, events: [...(previous.events as Json[] || []), ...(value?.events as Json[] || [])] })); })}>加载更早进展</button>}
          <div className="task-meta"><span>{space?.connections?.find(item => item.runtime === selected._runtimeId)?.name || '来源设备'}</span><button className="quiet-button" onClick={() => void readAction(async () => { await chooseConversation(String(selected.conversation || 'default')); setHighlight(String(selected.origin_message || '')); })}>来源聊天 <Icon name="next" /></button><button className="quiet-button" onClick={() => go('native')}>查看原会话 <Icon name="next" /></button></div>
          <details className="execution-info"><summary><Icon name="info" />执行信息</summary><dl><dt>执行者</dt><dd>{String(selected.worker_agent || selected.agent || '未记录')}</dd><dt>目录</dt><dd>{String(selected.worker_cwd || selected.project || '未记录')}</dd><dt>会话</dt><dd>{String(selected.worker_session || selected.session_id || '未记录')}</dd></dl></details>
          {activeTask(selected) && <button className="quiet-button stop-task" disabled={working || Boolean(selected._staleEpoch)} onClick={() => space && void run(() => window.xiaoyou.cancel({ space: space.id, id: String(selected.id), runtime: String(selected._runtimeId) }))}><Icon name="stop" />停止这项工作</button>}
        </> : <div className="work-unselected">选一项工作，查看进展。</div>}</div></section>}
      {page === 'native' && <section className="native-page"><h2>{String(selected?.title || '原会话记录')}</h2><p className="native-notice">这里只查看已记录内容。补充要求经小幽转交；记录缺失不代表原会话没有更多内容。</p>{Boolean(detail?.cached) && <p className="native-notice">当前显示本机缓存</p>}{detailError && <p className="native-notice">{detailError}</p>}
        {Array.isArray(detail?.entries) && (detail.entries as Json[]).map((entry, index) => <article className={'native-entry ' + (entry.role === 'you' ? 'owner' : '')} key={String(entry.id || index)}><small>{entry.role === 'you' ? '你' : String(entry.author || selected?.agent || '小幽')} · {shortTime(Number(entry.at) * 1000)}</small><p>{String(entry.text || '')}</p></article>)}
        {(!detail || !(detail.entries as Json[] | undefined)?.length) && <p className="quiet-empty">尚无可读取的原会话片段。</p>}{selected && !selected.monitoring_only && <button className="primary" onClick={() => void run(() => supplement(selected))}>补充要求</button>}</section>}
      {page === 'settings' && <Settings view={view} space={space} disabled={working} onSetup={() => setSetupOpen(true)} onRun={run} onSelect={id => void run(() => window.xiaoyou.select(id))} />}
    </main>
    {directoryOpen && space && <Directory key={space.id} space={space} onClose={()=>setDirectoryOpen(false)}/>}
    {helper && <Usage helper={helper} tasks={tasks} disabled={working} onClose={() => setUsage(undefined)} onRefresh={() => space && void readAction(() => window.xiaoyou.refreshHelpers(space.id))} onTask={task => { setUsage(undefined); openTask(task); }} />}
  </div>;
}
function Bubble({ message, task, onTask, onBranch, canBranch, highlight }: { message: Message; task?: Json; onTask(task: Json): void; onBranch(): void; canBranch: boolean; highlight: boolean }) {
  const mine = message.author?.kind === 'owner' || message.kind === 'user';
  const blocks = messageBlocks(message), view = projection(message), issue = displayIssue(message);
  if (!mine && !blocks.length && !issue && !['failed', 'cancelled'].includes(view?.status || message.status)) return null;
  return <article data-message-id={message.id} className={'message-row ' + (mine ? 'mine' : '') + (highlight ? ' highlighted' : '')}>{!mine && <div className="reply-author"><Avatar size={25} /><span>{authorLabel(message)}</span></div>}{blocks.map(block => <div className="message-bubble" key={block.id} data-block-id={block.id}><DisplayText block={block} /><div className="message-meta"><small>{shortTime(message.created_at * 1000)}</small><button className="message-branch" disabled={!canBranch} title={canBranch ? '从这条消息开旁聊' : '当前服务暂不支持从消息分出旁聊'} onClick={onBranch}>开旁聊</button></div></div>)}{(issue || view && (!view.complete || ['failed', 'cancelled'].includes(view.status))) && <small role="status">{issue || view?.status_label}</small>}{task && <button className={'inline-work ' + (task.state === 'waiting' ? 'waiting' : '')} onClick={() => onTask(task)}><Avatar type={String(task.worker_agent || task.agent)} size={22} /><span>{taskStatusLabel(task)}</span><Icon name="next" /></button>}</article>;
}
function Decision({ approval, disabled, onDecision }: { approval: Json; disabled: boolean; onDecision(decision: 'allow' | 'deny'): void }) {
  return <div className="decision"><strong>有件事需要你决定</strong><p>{String(approval.detail || approval.tool || '请查看操作内容')}</p>{approval.remote_actionable !== false && !approval._staleEpoch ? <div><button disabled={disabled} onClick={() => onDecision('deny')}>拒绝</button><button className="primary" disabled={disabled} onClick={() => onDecision('allow')}>允许这次操作</button></div> : <small>{approval._staleEpoch ? '正在核实最新状态' : '请在原电脑确认'}</small>}</div>;
}
function TaskRow({ task, selected, onClick }: { task: Json; selected: boolean; onClick(): void }) {
  const stale = activeTask(task) && task.state !== 'waiting' && Number(task.last_meaningful_at || task.updated_at) > 0 && Date.now() - Number(task.last_meaningful_at || task.updated_at) * 1000 > 300_000;
  return <button className={'task-row ' + (selected ? 'selected' : '')} onClick={onClick}><Avatar type={String(task.worker_agent || task.agent)} size={31} /><span>{String(task.title || '一项工作')}</span>{unverifiedTask(task) ? <span title="状态待确认"><Icon name="clock" /></span> : task.state === 'waiting' ? <i className="waiting-dot" /> : stale ? <span className="stale-task" title={elapsed(task.last_meaningful_at || task.updated_at)}><Icon name="clock" /></span> : activeTask(task) ? <i className="task-spinner" /> : <Icon name="check" />}</button>;
}
function Progress({ detail, all }: { detail?: Json; all: boolean }) {
  const events = Array.isArray(detail?.events) ? detail.events as Json[] : [], entries = Array.isArray(detail?.entries) ? detail.entries as Json[] : [];
  const rows = events.length ? events : entries.filter(entry => entry.kind !== 'user' && entry.role !== 'you');
  return <div className="progress-list">{(all ? rows : rows.slice(-3)).map((row, index) => <div key={String(row.id || index)}><Icon name={['completed', 'tool_result'].includes(String(row.phase)) ? 'check' : row.phase === 'relay_queued' ? 'clock' : 'quote'} /><p>{String(row.text || '')}<small>{shortTime(Number(row.created_at || row.at) * 1000)}</small></p></div>)}{detail && !rows.length && <p className="quiet-empty">尚未记录进展。</p>}{!detail && <p className="quiet-empty">正在读取已有进展…</p>}</div>;
}
function Usage({ helper, tasks, disabled, onClose, onRefresh, onTask }: { helper: HelperView; tasks: Json[]; disabled: boolean; onClose(): void; onRefresh(): void; onTask(task: Json): void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => { const previous = document.activeElement as HTMLElement; close.current?.focus(); return () => previous?.focus(); }, []);
  const task = tasks.find(task => helperMatchesTask(helper, task) && activeTask(task));
  const stale = !helper.connected || !helper.updatedAt || Date.now() - helper.updatedAt > 120_000;
  return <div className="modal-shade" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section className="usage-modal" role="dialog" aria-modal="true" aria-labelledby="usage-title" onKeyDown={event => {
    if (event.key !== 'Tab') return;
    const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
    if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1)?.focus(); }
    else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0]?.focus(); }
  }}><header><div><Avatar type={helper.type} size={38} /><h2 id="usage-title">{helper.label}</h2></div><button ref={close} className="icon-button" aria-label="关闭用量" onClick={onClose}><Icon name="close" /></button></header>
    <p className="usage-model">{helper.model || '模型未上报'}{helper.effort ? ' · ' + helper.effort : ''}</p>{([['5 小时剩余', helper.fiveHour], ['本周剩余', helper.week]] as const).map(([label, window]) => <div className="usage-window" key={label}><div><span>{label}</span><strong>{window.left === null ? '暂无数据' : window.left + '%'}</strong></div><div className="usage-track"><i style={{ width: window.left === null ? '0%' : window.left + '%' }} /></div><small>{window.resetsAt ? new Date(window.resetsAt).toLocaleString('zh-CN') + ' 重置' : '重置时间未上报'}</small></div>)}
    {(helper.members?.length || 0) > 1 && <details className="helper-devices"><summary>设备与上报来源 · {new Set(helper.members?.map(member => member.device)).size} 台</summary>{helper.members?.map(member => <p key={member.id}>{member.device} · {member.name}<small>{member.connected ? '服务可达' : '离线'} · 5 小时 {member.fiveHour.left === null ? '未上报' : member.fiveHour.left + '%'} · 本周 {member.week.left === null ? '未上报' : member.week.left + '%'}</small></p>)}<small>上方采用有数据的最近来源；不同设备的额度不相加，也不据此认定为同一账号。</small></details>}
    <p className="usage-freshness">{stale ? '缓存或暂无最新数据' : '最近上报'} · {helper.updatedAt ? new Date(helper.updatedAt).toLocaleTimeString('zh-CN') : '尚未上报'}<br />{helper.device}</p><footer><button disabled={disabled || !helper.connected} onClick={onRefresh}><Icon name="refresh" />刷新用量</button>{task && <button className="primary" onClick={() => onTask(task)}>查看当前工作 <Icon name="next" /></button>}</footer>
  </section></div>;
}
function Settings({ view, space, disabled, onSetup, onRun, onSelect }: { view: ClientView; space?: SpaceView; disabled: boolean; onSetup(): void; onRun(action: () => Promise<unknown>): Promise<void>; onSelect(id: string): void }) {
  const [invitation, setInvitation] = useState(''), [relayURL, setRelayURL] = useState(''), [relayToken, setRelayToken] = useState('');
  const [phone, setPhone] = useState<PhoneConnection>(), [phoneBusy, setPhoneBusy] = useState(false), [phoneNotice, setPhoneNotice] = useState('');
  async function checkPhone() { setPhoneBusy(true); setPhoneNotice(''); try { setPhone(await window.xiaoyou.phoneConnection()); } catch { setPhoneNotice('检测失败，请确认本机服务后重试。'); } finally { setPhoneBusy(false); } }
  useEffect(() => { void checkPhone(); }, [space?.id, view.setup?.service]);
  return <section className="settings-page"><h2>手机扫码，和各处的小幽接上话</h2><p className="settings-intro">查看服务、设备和同步状态。关闭窗口后，小幽继续在后台运行。</p>
    <RelayHostSettings view={view} disabled={disabled} onRun={onRun} />
    <article className="settings-card vertical"><h3>异地连接手机或电脑</h3><p>手机 App 0.30.1 起支持电脑内置节点，点击「扫码连接电脑」，扫码后核对设备指纹，再在本机确认。一个手机可添加多台电脑，已有连接会保留。</p><p>{view.pairing.relayState} · 已配对 {view.pairing.joined} 台</p><button disabled={disabled || view.pairing.relayState !== '加密通信入口已连接' || Boolean(view.relayHost?.enabled && !view.relayHost.publicURL)} onClick={() => void onRun(() => window.xiaoyou.invite())}>生成连接二维码</button>{view.pairing.relayState === '未设置跨网入口' && <p>先启用上方的常开电脑节点，或在高级设置连接已有入口。</p>}{view.relayHost?.enabled && !view.relayHost.publicURL && <p>节点已在本机运行；补充公网地址并确认入站条件后才能生成异地邀请。</p>}{view.pairing.invitation && <><PairingCode link={view.pairing.invitation} /><details><summary>复制邀请链接</summary><textarea aria-label="生成的邀请" readOnly value={view.pairing.invitation} /><small>仅发送给你自己的设备，邀请含一次性连接凭据。</small></details></>}</article>
    <details className="settings-card vertical phone-setup"><summary>已有网络连接（兼容入口）</summary><h3><Icon name="phone" />连接手机 App</h3><p>手机连接的是这台电脑提供的小幽服务。桌面记录已同步，并不表示手机已经连通。不同网络请使用下方的加密邀请，需要双方可达的 WSS 入口。</p><ol><li>电脑侧检测当前可用入口。</li><li>复制连接串，发送到你自己的手机；在手机「连接与设置」中粘贴并连接。</li><li>用手机发一句话，再检查两端是否出现同一条记录。</li></ol><button disabled={phoneBusy} onClick={() => void checkPhone()}>{phoneBusy ? '正在核对服务身份…' : '重新检测手机入口'}</button>{phone && <><div className="phone-status-grid"><span>同一网络<strong>{phone.lan === 'computer-verified' ? '电脑侧入口已验证' : '未开放局域网入口'}</strong></span><span>异地连接<strong>{phone.remote === 'existing-private-network' ? '可使用已有私有网络' : '未配置可达入口'}</strong></span></div><p className="phone-note">{phone.note}</p>{phone.routes.map(route => <div className="phone-route" key={route.url}><span>{route.kind === 'lan' ? '同一局域网' : '现有私有网络（如 Tailscale）'}<small>{route.url}</small></span><button disabled={disabled} onClick={() => void onRun(async () => { await window.xiaoyou.copyPhoneConnection(route.url); setPhoneNotice('已复制含连接凭据的连接串。仅发给自己的手机；粘贴后不要留在公开位置。'); })}>复制手机连接串</button></div>)}</>}{phoneNotice && <p role="status">{phoneNotice}</p>}</details>
    <article className="settings-card"><div><h3>本机服务</h3><p>{view.runtime.message}</p><small>{view.setup?.service === 'bundled' ? '客户端管理' : view.setup?.service === 'external' ? '复用已有服务' : '当前通过已配对设备使用服务'}</small></div><button disabled={disabled} onClick={onSetup}>检测与配置</button></article>
    <article className="settings-card vertical"><h3>当前连接</h3>{space?.connections?.map(item => <div className="connection-row" key={item.runtime}><i className={'dot ' + (item.connected ? 'online' : '')} /><span>{item.name}<small>{item.local ? '本机回环连接' : '已配对设备 · 加密通道'}</small></span><small>{item.connected ? '已连接' : '等待恢复'}</small></div>)}{!space?.connections?.length && <p>暂无连接</p>}{view.spaces.length > 1 && <label>当前空间<select value={space?.id} onChange={event => onSelect(event.target.value)}>{view.spaces.map(item => <option key={item.id} value={item.id}>{item.name} · {item.runtimeName}</option>)}</select></label>}</article>
    {view.pairing.requests.map(request => <article className="decision" key={request.id}><h3>{request.name} 请求加入</h3><p>确认这是你正在配对的设备。</p><code>{request.fingerprint}</code><button disabled={disabled} className="primary" onClick={() => void onRun(() => window.xiaoyou.acceptJoin(request.id))}>确认加入</button></article>)}
    <article className="settings-card vertical"><h3>连接已有小幽</h3><p>在已有设备上生成邀请，再粘贴到这里。</p><textarea aria-label="小幽邀请链接" value={invitation} onChange={event => setInvitation(event.target.value)} /><button disabled={!invitation.trim() || disabled} onClick={() => void onRun(async () => { await window.xiaoyou.join(invitation); setInvitation(''); })}>请求加入</button></article>
    <article className="settings-card"><div><h3>登录时启动</h3><p>系统登录后在后台恢复已确认的连接。</p></div><button disabled={disabled} onClick={() => void onRun(() => window.xiaoyou.loginStart(true))}>开启</button><button disabled={disabled} onClick={() => void onRun(() => window.xiaoyou.loginStart(false))}>关闭</button></article>
    <details className="advanced"><summary>高级连接设置</summary><p>首次部署通信入口时使用。入口只转发密文；手机和电脑都主动连接，不需要处于同一网络，也不使用 Tailscale。扫码不会自动创建公网入口。</p><label>WSS 入口地址<input placeholder="wss://你的通信入口" value={relayURL} onChange={event => setRelayURL(event.target.value)} /></label><label>入口访问密钥<input type="password" autoComplete="off" value={relayToken} onChange={event => setRelayToken(event.target.value)} /></label><button disabled={disabled || !relayURL.trim() || relayToken.length < 32} onClick={() => void onRun(async () => { await window.xiaoyou.setRelay({url: relayURL.trim(), token: relayToken}); setRelayToken(''); })}>保存并连接入口</button><button disabled={disabled} onClick={() => void onRun(() => window.xiaoyou.importConfig())}>选择已有 Runtime 配置</button><button disabled={disabled} onClick={() => void onRun(() => window.xiaoyou.configureRelay())}>导入通信入口</button></details>
  </section>;
}
function RelayHostSettings({ view, disabled, onRun }: { view: ClientView; disabled: boolean; onRun(action: () => Promise<unknown>): Promise<void> }) {
  const node = view.relayHost;
  const [port, setPort] = useState(String(node?.port || 18795)), [address, setAddress] = useState(node?.publicURL || '');
  useEffect(() => { setPort(String(node?.port || 18795)); setAddress(node?.publicURL || ''); }, [node?.port, node?.publicURL]);
  return <article className="settings-card vertical"><h3>让这台常开电脑承接通信</h3>
    <p>节点随客户端在后台运行，转发手机和电脑之间的加密数据。其他电脑连接到这个入口；无需安装 Tailscale。</p>
    <p role="status">{node?.listening ? '本机节点已启动' : node?.enabled ? '节点启动失败，可重试' : '本机节点未启用'}{node?.publicCheck === 'computer-reachable' ? ' · 电脑侧公网地址可达，待手机异网验证' : ' · 手机异网通信尚未验证'}</p>
    <label>本机监听端口<input aria-label="节点监听端口" type="number" min={1024} max={65535} value={port} disabled={node?.listening || disabled} onChange={event => setPort(event.target.value)} /></label>
    <label>公网 IP 或域名（首次可稍后填写）<input aria-label="节点公网地址" placeholder="公网 IP:端口 或 域名:端口" value={address} onChange={event => setAddress(event.target.value)} /></label>
    <div><button className="primary" disabled={disabled} onClick={() => void onRun(() => window.xiaoyou.startRelayHost({port: Number(port), publicURL: address.trim()}))}>{node?.listening ? '保存公网地址' : '启用本机节点'}</button>{node?.enabled && <button disabled={disabled} onClick={() => void onRun(() => window.xiaoyou.stopRelayHost())}>停止节点</button>}{node?.listening && node.publicURL && <button disabled={disabled} onClick={() => void onRun(() => window.xiaoyou.checkRelayHost())}>检测公网地址</button>}</div>
    {node?.error && <p role="alert">{node.error}</p>}
    {node?.listening && <><p>本机端口：{node.port} · 局域网地址：{node.localAddresses.join('、') || '暂未检测到'}</p><p>若使用路由器公网 IPv4，将公网 TCP 端口映射到这台电脑的 {node.port} 端口，再填写公网地址。仅启动节点或扫描二维码，不代表跨网已打通。</p><small>证书与设备身份随扫码绑定，无需手动申请域名证书。关闭窗口继续运行；电脑休眠、关机或退出客户端后此入口会中断。</small></>}
  </article>;
}
function PairingCode({ link }: { link: string }) {
  const [code, setCode] = useState<{dataURL: string; expiresAt: number}>(), [now, setNow] = useState(Date.now()), [error, setError] = useState('');
  useEffect(() => { let current = true; setCode(undefined); setError(''); void window.xiaoyou.invitationQR(link).then(value => { if (current) setCode(value); }).catch(() => { if(current) setError('二维码无法生成，请重新生成邀请'); }); const timer = setInterval(() => setNow(Date.now()), 1000); return () => { current = false; clearInterval(timer); }; }, [link]);
  return <div className="pairing-code">{code && now < code.expiresAt ? <><img src={code.dataURL} width={320} height={320} alt="用手机小幽扫描此二维码连接电脑" /><p>手机「连接与设置 → 扫码连接电脑」</p><small>还剩 {Math.max(0,Math.ceil((code.expiresAt-now)/1000))} 秒 · 扫码后请核对设备指纹并在电脑确认</small></> : <p>{error || (code ? '二维码已过期，请重新生成' : '正在生成二维码…')}</p>}</div>;
}
function canOpenNative(task: Json, space?: SpaceView): boolean { return Boolean(task.worker_session && !task._staleEpoch && !activeTask(task)
  && space?.connections?.some(item => item.runtime === task._runtimeId && item.local && item.connected && item.capabilities.includes('desktop_handoff'))
  && space.helpers?.some(item => item.runtime === task._runtimeId && item.name === (task.worker_agent || task.agent) && item.type === 'codex')); }
function clean(error: unknown): string { return error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': Error: /, '') : '操作未完成，请重试'; }
function relayLabel(state: string): string { return ({ supplement_received: '已收到补充要求', relay_queued: '等待转交', received: '已收到补充要求', queued: '等待转交', relayed: '已转交原会话', executing: '正在执行补充要求' } as Record<string, string>)[state] || '补充要求状态：' + state; }
createRoot(document.getElementById('root')!).render(<App />);
