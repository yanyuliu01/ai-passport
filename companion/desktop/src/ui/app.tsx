import { AccountPanel } from './account';
import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { ClientView, DesktopAPI, HelperView, Json, Message, SpaceView } from '../shared';
import { localHelpers, activeTask, unverifiedTask, helperMatchesTask, mainMessages, taskStatusLabel, messageBlocks, messageStatusText, authorLabel, projection, displayIssue, connectionWord, listedSideChat, listedTask, workSection, taskProgress, requestOf, reviewItems, reviewMissing, reviewLabel, desktopRunning, sessionState, readableProgress, clock, splitQuote, withQuote, quoteSnippet, relayLabel, bubbleDelay, helperState, type ReviewOutcome } from '../core/presentation';
import { Avatar, elapsed, HelperPill, Icon, QuotaAvatar, shortTime, taskKey } from './components';
import { Directory } from './directory';
import { HelperSettings } from './helpers';
import { DisplayText } from './display-text';
import { Onboarding } from './onboarding';
import './style.css';

declare global { interface Window { xiaoyu: DesktopAPI } }
const empty: ClientView = { version: '0.1.17', spaces: [], selectedSpace: '', background: true,
  runtime: { state: 'disconnected', message: '等待你确认连接方式' }, pairing: { joined: 0, relayState: '未设置跨网入口', requests: [] } };
type Page = 'chat' | 'work' | 'settings' | 'native' | 'helper';
type TaskRef = { id: string; runtime: string };
function App() {
  const [directoryOpen,setDirectoryOpen]=useState(false);
  const [view, setView] = useState<ClientView>(empty), [loaded, setLoaded] = useState(false), [setupOpen, setSetupOpen] = useState(false);
  const [page, setPage] = useState<Page>('chat'), [sidebarOpen, setSidebarOpen] = useState(false);
  const [text, setText] = useState(''), [problem, setProblem] = useState(''), [working, setWorking] = useState(false), [sending, setSending] = useState(false);
  const [selectedTask, setSelectedTask] = useState<TaskRef>(), [target, setTarget] = useState<TaskRef & { legacy: boolean }>();
  const [historyLimit, setHistoryLimit] = useState(100), [completedOpen, setCompletedOpen] = useState(false), [attachmentsOpen, setAttachmentsOpen] = useState(false);
  const [review, setReview] = useState<{ runtime: string; record: string }>(), [timerRows, setTimerRows] = useState<Json[]>([]);
  const [stopping, setStopping] = useState(''), [reconcileNote, setReconcileNote] = useState('');
  const [usage, setUsage] = useState<string>(), [detail, setDetail] = useState<Json>(), [detailError, setDetailError] = useState(''), [allSteps, setAllSteps] = useState(false), [highlight, setHighlight] = useState('');
  const [quote, setQuote] = useState<string>(), [menu, setMenu] = useState<{ x: number; y: number; text: string; author: string }>();
  const [openHelper, setOpenHelper] = useState<string>(), [helperAll, setHelperAll] = useState(false), [manageHelpers, setManageHelpers] = useState(false);
  const [sessionText, setSessionText] = useState(''), [errorDetail, setErrorDetail] = useState<string>();
  const detailGeneration = useRef(0), selection = useRef('');
  const bottom = useRef<HTMLDivElement>(null), input = useRef<HTMLTextAreaElement>(null);
  const space = view.spaces.find(item => item.id === view.selectedSpace) || view.spaces[0];
  const tasks = space?.tasks.filter(task => Boolean(task.agent || task.monitoring_only) && (!task.shared_archived || task.state==='waiting')) || [];
  const selected = tasks.find(task => task.id === selectedTask?.id && task._runtimeId === selectedTask?.runtime);
  const linkedTask = tasks.find(task => task.id === target?.id && task._runtimeId === target?.runtime);
  const helpers = localHelpers(space?.helpers || [], space?.connections), helper = helpers.find(item => item.id === usage);
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
  const work = tasks.filter(listedTask);
  const awaiting = work.filter(task => workSection(task) === 'waiting'), running = work.filter(task => workSection(task) === 'running');
  const completed = work.filter(task => workSection(task) === 'ended').slice().reverse();
  const desktopNow = tasks.filter(task => desktopRunning(task));
  const connection = space?.catchingUp ? '正在同步' : connectionWord(Boolean(space), Boolean(space?.connected));
  selection.current = space?.id + '/' + space?.selectedConversation;
  const title = page === 'chat' ? target?.legacy ? '任务续聊' : conversation?.id === 'default' ? '主对话' : conversation?.title || '主对话'
    : page === 'work' ? '在做的事' : page === 'native' ? '原会话' : page === 'helper' ? '' : '设置';
  const messageVersion = messages.filter(message => ['final', 'done', 'failed', 'cancelled'].includes(message.status)).map(message => message.id + ':' + message.rev).join('|');
  useEffect(() => {
    void window.xiaoyu.snapshot().then(value => { setView(value); if (!value.setup?.confirmed) setSetupOpen(true); setLoaded(true); }).catch(() => setProblem('暂时无法连接后台服务'));
    return window.xiaoyu.subscribe(setView);
  }, []);
  useEffect(() => { setHistoryLimit(100); setText(''); setUsage(undefined); setQuote(undefined); }, [space?.id, space?.selectedConversation]);
  useEffect(() => { setSelectedTask(undefined); setDetail(undefined); setTarget(undefined); setPage('chat'); }, [space?.id]);
  useEffect(() => setReconcileNote(''), [selectedTask?.id, selectedTask?.runtime]);
  useEffect(() => {
    if (page === 'chat') bottom.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    if (space && page === 'chat' && document.hasFocus() && document.visibilityState === 'visible') void window.xiaoyu.markRead({ space: space.id, conversation: space.selectedConversation }).catch(() => {});
  }, [messageVersion, page, space?.id, space?.selectedConversation]);
  useEffect(() => {
    let current = true; detailGeneration.current++; setDetail(undefined); setDetailError(''); setAllSteps(false);
    if (!space || !selectedTask || !['work', 'native'].includes(page)) return;
    setSessionText('');
    void window.xiaoyu.taskDetail({ space: space.id, ...selectedTask }).then(value => { if (current) setDetail(value); }).catch(() => { if (current) setDetailError('进展暂时读不到，稍后会自动重试。'); });
    return () => { current = false; };
  }, [space?.id, selectedTask?.id, selectedTask?.runtime, selected?.rev, selected?.seq, page]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setUsage(undefined); setAttachmentsOpen(false); setSidebarOpen(false); setMenu(undefined); } };
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
  function openTask(task: Json) {
    // A "continue this computer session" card is part of that session: open the session.
    const session = task.desktop_card ? tasks.find(item => item.id === task.desktop_card && item._runtimeId === task._runtimeId) : undefined;
    const shown = session || task;
    setSelectedTask({ id: String(shown.id), runtime: String(shown._runtimeId) }); setPage('work'); setSidebarOpen(false);
  }
  function showHelper(id: string) { setOpenHelper(id); setHelperAll(false); go('helper'); }
  function go(next: Page) { setPage(next); setSidebarOpen(false); setAttachmentsOpen(false); }
  async function chooseConversation(id: string) {
    if (!space) return;
    await run(() => window.xiaoyu.selectConversation({ space: space.id, conversation: id })); setTarget(undefined); go('chat');
  }
  async function supplement(task: Json) {
    if (!space) return;
    const conversationId = String(task.conversation || 'default');
    const legacy = !space.connections?.find(item => item.runtime === task._runtimeId)?.capabilities.includes('task_audit');
    if (space.selectedConversation !== conversationId) await window.xiaoyu.selectConversation({ space: space.id, conversation: conversationId });
    setTimeout(() => { setTarget({ id: String(task.id), runtime: String(task._runtimeId), legacy }); setPage('chat'); input.current?.focus(); }, 0);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (!space || !text.trim() || sending) return;
    const value = text, source = selection.current; setSending(true); setProblem('');
    try {
      await window.xiaoyu.send({ space: space.id, text: withQuote(quote, value), ...(target ? { card: target.id, runtime: target.runtime, mainReply: !target.legacy } : {}) });
      if (selection.current === source) { setText(current => current === value ? '' : current); setQuote(undefined); }
    } catch (error) { setProblem(clean(error)); } finally { setSending(false); }
  }
  /** Right-click a bubble: 复制 / 引用 the selected part, or the whole bubble when nothing is selected. */
  function bubbleMenu(event: React.MouseEvent, whole: string, author: string) {
    event.preventDefault();
    const picked = window.getSelection()?.toString().trim();
    const inside = picked && (event.currentTarget as HTMLElement).contains(window.getSelection()?.anchorNode || null);
    setMenu({ x: event.clientX, y: event.clientY, text: inside ? picked! : whole, author });
  }
  async function sendToSession(task: Json) {
    if (!space || !sessionText.trim()) return;
    const value = sessionText;
    await run(async () => { await window.xiaoyu.continueSession({ space: space.id, id: String(task.id), runtime: String(task._runtimeId), text: withQuote(quote, value) }); setSessionText(''); setQuote(undefined); });
  }
  async function branch(message: Message) {
    if (!space) return;
    await run(() => window.xiaoyu.createConversation({ space: space.id, title: '', message: message.id, runtime: String(message._runtimeId) })); setTarget(undefined); go('chat');
  }
  function loadTimers() {
    if (!space || !space.connections?.some(item => item.capabilities.includes('timer_effect_review'))) { setTimerRows([]); return; }
    void window.xiaoyu.timerReviews({ space: space.id }).then(setTimerRows).catch(() => setTimerRows([]));
  }
  useEffect(() => { if (page === 'work') loadTimers(); }, [page, space?.id]);
  function reviewable(message: Message): boolean {
    const failed = message.status === 'failed' || projection(message)?.status === 'failed';
    // One link per request: on the reply, or on your message when no reply came back.
    if (message.id.endsWith(':you') && messages.some(item => item.id === requestOf(message.id) + ':reply' && item._runtimeId === message._runtimeId)) return false;
    return failed && Boolean(requestOf(message.id)) && Boolean(space?.connections?.find(item => item.runtime === message._runtimeId)?.capabilities.includes('request_effect_review'));
  }
  const latestTaskMessage = new Map<string, string>();
  for (const message of messages) if (message.task && message.author?.kind !== 'owner') latestTaskMessage.set(String(message._runtimeId) + '/' + message.task, message.id);
  if (!loaded) return <main className="app-loading" role="status">{problem || '正在读取本机设置…'}</main>;
  if (!view.account?.signedIn || !view.setup?.confirmed || view.setup.firstRun?.required || setupOpen) return <Onboarding key={view.account?.username || 'signed-out'} view={view} onDone={() => { setSetupOpen(false); setPage('chat'); }} />;
  return <div className={'shell ' + (sidebarOpen ? 'sidebar-is-open' : '')}>
    {sidebarOpen && <button className="sidebar-shade" aria-label="关闭侧边栏" onClick={() => setSidebarOpen(false)} />}
    <aside className="sidebar" aria-label="小鱼导航">
      <div className="identity"><Avatar size={42} /><div><strong>小鱼</strong><small><i className={'dot ' + (space?.connected ? 'online' : '')} />{connection}</small></div></div>
      <button className={'nav ' + (page === 'chat' && conversation?.id === 'default' && !target?.legacy ? 'selected' : '')} onClick={() => void chooseConversation('default')}><Icon name="chat" /><span>主对话</span>{space?.conversations.find(item => item.id === 'default')?.unread && page !== 'chat' && <i className="unread-dot" />}</button>
      <button className={'nav work-nav ' + (page === 'work' || page === 'native' ? 'selected' : '')} onClick={() => go('work')}><Icon name="work" /><span>在做的事</span>{awaiting.length > 0 ? <b className="badge waiting">{awaiting.length}</b> : running.length > 0 ? <b className="badge">{running.length}</b> : null}</button>
      <div className="side-heading"><span>旁聊</span><button className="icon-button" aria-label="新建旁聊" disabled={!space || working} onClick={() => space && void run(async () => { await window.xiaoyu.createConversation({ space: space.id, title: '' }); setTarget(undefined); go('chat'); })}><Icon name="plus" /></button></div>
      <div className="side-conversations">{space?.conversations.filter(item => listedSideChat(item, page === 'chat' ? space.selectedConversation : '')).sort((a, b) => Number(Boolean(b.pinned))-Number(Boolean(a.pinned)) || (b.updatedAt || b.createdAt) - (a.updatedAt || a.createdAt)).map(item => <button key={item.id} className={'side-chat ' + (page === 'chat' && space.selectedConversation === item.id ? 'selected' : '')} onClick={() => void chooseConversation(item.id)}><span>{item.title}</span>{item.unread && space.selectedConversation !== item.id ? <i className="unread-dot" /> : <small>{clock(item.updatedAt)}</small>}</button>)}
        {!space?.conversations.some(item => listedSideChat(item, page === 'chat' ? space.selectedConversation : '')) && <p className="side-empty">点 ＋ 开一段旁聊</p>}</div>
      {/* 帮手固定在设置上面：旁聊再多也不会把它们挤下去（和手机一致）。点帮手进它的页面。 */}
      <div className="side-pinned">
        <div className="side-heading helpers-heading"><span>帮手</span><button className="icon-button" aria-label="添加或管理帮手" disabled={!space} onClick={() => setManageHelpers(true)}><Icon name="plus" /></button></div>
        <div className="side-helpers">{helpers.map(item => <div className={'side-helper ' + (page === 'helper' && openHelper === item.id ? 'selected' : '')} key={item.id}><QuotaAvatar helper={item} onClick={() => setUsage(item.id)} /><button onClick={() => showHelper(item.id)}><span>{item.label}</span><small>{helperState(item, tasks)}</small></button></div>)}
          {!helpers.length && <p className="side-empty">尚无帮手数据</p>}</div>
      </div>
      <div className="sidebar-bottom"><button className={'nav ' + (page === 'settings' ? 'selected' : '')} onClick={() => go('settings')}><Icon name="settings" /><span>设置</span></button><small>小鱼桌面版 {view.version}</small></div>
    </aside>
    <main className="workspace"><header className="topbar"><div className="topbar-title"><button className={'icon-button ' + (page === 'native' ? '' : 'menu-toggle')} aria-label={page === 'native' ? '返回工作详情' : '打开侧边栏'} onClick={() => page === 'native' ? go('work') : setSidebarOpen(true)}><Icon name={page === 'native' ? 'back' : 'menu'} /></button><h1>{title}</h1></div>{(!space?.connected || space?.catchingUp) && <button className={'connection-status ' + (space?.connected ? 'ready' : '')} onClick={() => go('settings')}><i className={'dot ' + (space?.connected ? 'online' : '')} />{connection}</button>}</header>
      {problem && <div role="alert" className="alert"><span>{problem}</span><button className="quiet-button" onClick={() => setErrorDetail(problem)}>详情</button><button className="icon-button" aria-label="关闭提示" onClick={() => setProblem('')}><Icon name="close" /></button></div>}
      {page === 'chat' && <>
        <section className="companion-header"><Avatar size={64} /><div className="companion-intro"><div><strong>小鱼</strong><span>换个地方，接着聊。</span></div><div className="helper-pills">{helpers.map(item => <HelperPill key={item.id} helper={item} tasks={tasks} onUsage={() => setUsage(item.id)} onTask={openTask} />)}{!helpers.length && <small>帮手信息暂未上报</small>}</div></div></section>
        <section className="timeline" aria-label="聊天记录" aria-live="polite">
          {conversation?.context && <div className="conversation-context"><Icon name="quote" /><div><strong>来自{conversation.context.conversation === 'default' ? '主对话' : '另一段聊天'}</strong><p>{String(conversation.context.text || '')}</p></div></div>}
          {target?.legacy && <p className="legacy-note">当前服务使用任务会话续聊，这里的消息只属于这项工作。</p>}
          {messages.length === 0 && pending.length === 0 && <div className="chat-empty"><p>{conversation?.id && conversation.id !== 'default' ? '从这里开始这段旁聊。' : '想说什么，都可以从这里开始。'}</p>{conversation?.id !== 'default' && <small>发出第一句话后，标题会随会话记录更新。</small>}{!space && <button className="primary" onClick={() => setSetupOpen(true)}>连接小鱼</button>}</div>}
          {messages.length > historyLimit && <button className="history-more" onClick={() => setHistoryLimit(value => value + 100)}>查看更早的消息</button>}
          {messages.slice(-historyLimit).map(message => {
            const task = tasks.find(task => task.id === message.task && task._runtimeId === message._runtimeId);
            const showTask = task && activeTask(task) && latestTaskMessage.get(taskKey(task)) === message.id;
            return <Bubble key={String(message._canonical || message.id)} message={message} task={showTask ? task : undefined} onTask={openTask} highlight={highlight === message.id || highlight + ':you' === message.id}
              onMenu={bubbleMenu} onReview={reviewable(message) ? () => setReview({ runtime: String(message._runtimeId), record: 'messages/' + requestOf(message.id) }) : undefined} />;
          })}
          {pending.map(item => <article className="message-row mine" key={item.clientId}><div className="message-bubble"><p>{item.payload.text}</p><small>{item.state === 'accepted' ? '已接收，等待回复' : item.state === 'blocked' ? item.error : item.error || '已存本机，等待发送'}</small></div></article>)}
          {waiting && <div className="thinking" role="status"><span className="working-dot" />{waitingProblem ? '回复状态待确认：' + waitingProblem : space?.connected ? '正在等待小鱼回复…' : '连接恢复中，回复状态待确认…'}</div>}
          {space?.approvals.filter(approval => String(approval.conversation || 'default') === space.selectedConversation).map(approval => <Decision key={String(approval._canonical || approval.id)} approval={approval} disabled={working} onDecision={decision => void run(() => window.xiaoyu.approve({ space: space.id, id: String(approval.id), runtime: String(approval._runtimeId), decision }))} />)}
          <div ref={bottom} />
        </section>
        <div className="compose-area">{linkedTask && <div className="task-target"><Icon name="quote" /><span>补充：{String(linkedTask.title)}</span><button className="icon-button" aria-label="取消任务关联" onClick={() => setTarget(undefined)}><Icon name="close" /></button></div>}
          <form className="composer" onSubmit={submit}><textarea ref={input} aria-label="消息" value={text} disabled={!space} placeholder={target ? '补充这件事的要求…' : '跟小鱼说…'} rows={1} maxLength={32000} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(event); } }} />
            {/* 桌面版还不能发语音和图片：不放点了只会报错的按钮（和手机一致）。 */}
            <button className="send" type="submit" aria-label="发送" disabled={!space || !text.trim() || sending}><Icon name="up" /></button>
          </form>{quote && <QuoteBar text={quote} onClear={() => setQuote(undefined)} />}<p className="compose-hint">{space?.connected ? 'Enter 发送 · Shift + Enter 换行' : '连不上，消息先存在本机，连上后自动发送'}</p></div>
      </>}
      {page === 'work' && <section className="work-page"><div className="work-list">{timerRows.length > 0 && <><h2>需要你核对</h2>{timerRows.map(row => <button key={String(row._runtimeId) + String(row.timer_id || row.source_id)} className="task-row timer-review" disabled={!row.reviewable || !row.timer_id}
          title={row.reviewable ? '' : String(row.reason || '暂时不能核对')} onClick={() => setReview({ runtime: String(row._runtimeId), record: 'timers/' + String(row.timer_id) })}>
          <Icon name="clock" /><span>{row.kind === 'promise' ? '提醒' : '记忆整理'}结果不明</span><small>{row.reviewable ? reviewLabel(row.status) : String(row.reason || '暂时不能核对')}</small></button>)}
        {timerRows.some(row => row.more) && <p className="quiet-empty">还有更多，处理完这些后会接着列出</p>}</>}
        {awaiting.length > 0 && <h2>等你确认</h2>}{awaiting.map(task => <TaskRow key={taskKey(task)} task={task} selected={taskKey(task) === (selected ? taskKey(selected) : '')} onClick={() => openTask(task)} />)}
        {running.length > 0 && <h2>进行中</h2>}{awaiting.length + running.length + desktopNow.length === 0 && <p className="quiet-empty">现在没有在做的事。在聊天里交代一件事，小鱼会安排帮手。</p>}{running.map(task => <TaskRow key={taskKey(task)} task={task} selected={taskKey(task) === (selected ? taskKey(selected) : '')} onClick={() => openTask(task)} />)}
        {desktopNow.length > 0 && <h2>电脑上在跑</h2>}{desktopNow.map(task => <TaskRow key={taskKey(task)} task={task} selected={taskKey(task) === (selected ? taskKey(selected) : '')} onClick={() => openTask(task)} />)}
        {completed.length > 0 && <button className="completed-toggle" aria-expanded={completedOpen} onClick={() => setCompletedOpen(value => !value)}><Icon name={completedOpen ? 'down' : 'next'} />已结束 {completed.length}</button>}{completedOpen && completed.map(task => <TaskRow key={taskKey(task)} task={task} selected={taskKey(task) === (selected ? taskKey(selected) : '')} onClick={() => openTask(task)} />)}</div>
        <div className="work-detail">{selected ? <><div className="task-detail-title"><Avatar type={String(selected.worker_agent || selected.agent)} size={40} /><h2>{String(selected.title || '一件事')}</h2></div>
          <div className={'task-state ' + (selected.state === 'waiting' ? 'waiting' : '')}><strong>{stopping === taskKey(selected) && activeTask(selected) ? '正在停止…' : selected._staleEpoch ? '状态待确认' : taskStatusLabel(selected)}</strong><p>{taskProgress(selected) || '暂无新的进展'}</p><small>{selected._staleEpoch ? '状态待确认' : elapsed(selected.last_meaningful_at || selected.updated_at)}</small>{Boolean(relayLabel(String(selected.relay_state || ''))) && <p>{relayLabel(String(selected.relay_state))}</p>}</div>
          {space?.approvals.filter(approval => approval._runtimeId === selected._runtimeId && (approval.card === selected.id || approval.task === selected.id || (selected.approval as Json | undefined)?.id === approval.id)).map(approval => <Decision key={String(approval.id)} approval={approval} disabled={working} onDecision={decision => void run(() => window.xiaoyu.approve({ space: space.id, id: String(approval.id), runtime: String(approval._runtimeId), decision }))} />)}
          {selected.monitoring_only ? <SessionChat task={selected} tasks={tasks} detail={detail} state={sessionState(selected, Boolean(space?.connected))} text={sessionText} busy={working}
            onText={setSessionText} onSend={() => void sendToSession(selected)} onMenu={bubbleMenu} onDetails={setErrorDetail} /> : null}
          <div className="task-detail-actions">{!selected.monitoring_only && <button className={workSection(selected) === 'ended' ? '' : 'primary'} disabled={working || Boolean(selected._staleEpoch)} onClick={() => void run(() => supplement(selected))}>{workSection(selected) === 'ended' ? '接着让它做…' : '补充要求'}</button>}
            {canOpenNative(selected, space) && <button disabled={working} onClick={() => space && void run(() => window.xiaoyu.openNative({ space: space.id, id: String(selected.id), runtime: String(selected._runtimeId) }))}>在 Codex 打开 <Icon name="external" /></button>}</div>
          <div className="progress-heading"><h3>最近进展</h3><button className="quiet-button" onClick={() => setAllSteps(value => !value)}>{allSteps ? '收起' : '全部' + (progressCount(detail) ? ' ' + progressCount(detail) : '')}</button></div>
          {detailError && <p className="quiet-empty">{detailError}</p>}{Boolean(detail?.cached) && <p className="quiet-empty">连不上，显示上次的记录</p>}
          <Progress detail={detail} all={allSteps} />
          {allSteps && detail?.more === true && <button className="quiet-button" onClick={() => space && selectedTask && void readAction(async () => { const generation = detailGeneration.current; const previous = await window.xiaoyu.taskDetail({ space: space.id, ...selectedTask, before: Number(detail.before) }); if (generation !== detailGeneration.current) return; setDetail(value => ({ ...previous, events: [...(previous.events as Json[] || []), ...(value?.events as Json[] || [])] })); })}>加载更早进展</button>}
          <div className="task-meta"><span>{space?.connections?.find(item => item.runtime === selected._runtimeId)?.name || '来源设备'}</span><button className="quiet-button" onClick={() => void readAction(async () => { await chooseConversation(String(selected.conversation || 'default')); setHighlight(String(selected.origin_message || '')); })}>来源聊天 <Icon name="next" /></button><button className="quiet-button" onClick={() => go('native')}>原会话 <Icon name="next" /></button></div>
          <details className="execution-info"><summary><Icon name="info" />执行信息</summary><dl><dt>执行者</dt><dd>{String(selected.worker_agent || selected.agent || '未记录')}</dd><dt>目录</dt><dd>{String(selected.worker_cwd || selected.project || '未记录')}</dd><dt>会话</dt><dd>{String(selected.worker_session || selected.session_id || '未记录')}</dd></dl></details>
          {activeTask(selected) && <button className="quiet-button stop-task" disabled={working || Boolean(selected._staleEpoch)} onClick={() => space && void run(async () => { await window.xiaoyu.cancel({ space: space.id, id: String(selected.id), runtime: String(selected._runtimeId) }); setStopping(taskKey(selected)); })}><Icon name="stop" />停止</button>}
          {unverifiedTask(selected) && !selected.monitoring_only && Boolean(selected.run_id) && space?.connections?.find(item => item.runtime === selected._runtimeId)?.capabilities.includes('execution_reconciliation')
            && <div className="reconcile"><button disabled={working} onClick={() => space && void run(async () => { setReconcileNote(''); const status = await window.xiaoyu.reconcile({ space: space.id, id: String(selected.id), runtime: String(selected._runtimeId), run: String(selected.run_id) }); setReconcileNote(status === 'confirmed' ? '核对好了，结果已回到原对话' : '没找到足够的完成证据，保持待确认'); })}>核对结果</button>{reconcileNote && <small role="status">{reconcileNote}</small>}</div>}
        </> : <div className="work-unselected">选一件事，查看进展。</div>}</div></section>}
      {page === 'native' && <section className="native-page"><h2>{String(selected?.title || '原会话记录')}</h2><p className="native-notice">这里是同步过来的片段，完整对话在来源电脑上。</p>{Boolean(detail?.cached) && <p className="native-notice">当前显示本机缓存</p>}{detailError && <p className="native-notice">{detailError}</p>}
        {Array.isArray(detail?.entries) && (detail.entries as Json[]).map((entry, index) => <article className={'native-entry ' + (entry.role === 'you' ? 'owner' : '')} key={String(entry.id || index)}><small>{entry.role === 'you' ? '你' : String(entry.author || selected?.agent || '小鱼')} · {shortTime(Number(entry.at) * 1000)}</small><p>{String(entry.text || '')}</p></article>)}
        {(!detail || !(detail.entries as Json[] | undefined)?.length) && <p className="quiet-empty">还没有同步过来的内容。</p>}{selected && !selected.monitoring_only && <button onClick={() => void run(() => supplement(selected))}>在这段对话里接着说…</button>}</section>}
      {page === 'helper' && <HelperPage helper={helpers.find(item => item.id === openHelper)} tasks={tasks} connected={Boolean(space?.connected)} all={helperAll} onAll={() => setHelperAll(value => !value)}
        onUsage={() => openHelper && setUsage(openHelper)} onManage={() => setManageHelpers(true)} onTask={openTask} />}
      {page === 'settings' && <Settings view={view} space={space} disabled={working} onSetup={() => setSetupOpen(true)} onRun={run} onSelect={id => void run(() => window.xiaoyu.select(id))} onDirectory={() => setDirectoryOpen(true)} />}
    </main>
    {manageHelpers && <div className="modal-shade" onMouseDown={event => { if (event.target === event.currentTarget) setManageHelpers(false); }}><section className="usage-modal manage-helpers" role="dialog" aria-modal="true" aria-label="管理帮手">
      <header><h2>帮手</h2><button className="icon-button" aria-label="关闭" onClick={() => setManageHelpers(false)}><Icon name="close" /></button></header>
      <HelperSettings space={space} helpers={space?.helpers || []} disabled={working} onRun={run} /></section></div>}
    {menu && <BubbleMenu x={menu.x} y={menu.y} onClose={() => setMenu(undefined)} onCopy={() => { void navigator.clipboard.writeText(menu.text); setMenu(undefined); }}
      onQuote={() => { setQuote(quoteSnippet(menu.author, menu.text)); setMenu(undefined); (page === 'work' ? null : input.current)?.focus(); }} />}
    {errorDetail && <ErrorDetail text={errorDetail} onClose={() => setErrorDetail(undefined)} />}
    {directoryOpen && space && <Directory key={space.id} space={space} onClose={()=>setDirectoryOpen(false)}/>}
    {review && space && <EffectReview key={review.runtime + '/' + review.record} space={space.id} target={review} onClose={() => { setReview(undefined); loadTimers(); }} />}
    {helper && <Usage helper={helper} tasks={tasks} disabled={working} onClose={() => setUsage(undefined)} onRefresh={() => space && void readAction(() => window.xiaoyu.refreshHelpers(space.id))} onTask={task => { setUsage(undefined); openTask(task); }} />}
  </div>;
}
/** When the last reply bubble came (or is queued to come) out, shared by every message. */
let lastRevealAt = 0;
function queueReveal(text: string): number {
  const now = Date.now(), at = lastRevealAt ? Math.max(now, lastRevealAt + bubbleDelay(text)) : now;
  lastRevealAt = at; return at;
}
function Bubble({ message, task, onTask, onReview, onMenu, highlight }: { message: Message; task?: Json; onTask(task: Json): void; onReview?: () => void; onMenu(event: React.MouseEvent, text: string, author: string): void; highlight: boolean }) {
  const mine = message.author?.kind === 'owner' || message.kind === 'user';
  const blocks = messageBlocks(message), view = projection(message), issue = displayIssue(message), status = messageStatusText(message);
  // 刚到的回复气泡排队出，和手机一致：离上一条至少隔“打这条字”的时间（bubbleDelay），跨消息共用一个队列；
  // 没轮到的不占位置，其间显示“正在输入”。
  const fresh = useRef(!mine && Date.now() - Number(message.updated_at || message.created_at || 0) * 1000 < 15_000);
  const times = useRef<number[]>([]);
  while (times.current.length < blocks.length) times.current.push(fresh.current ? queueReveal(String(blocks[times.current.length]?.text || '')) : 0);
  const [, tick] = useState(0);
  const now = Date.now(), shown = times.current.filter(at => at <= now).length, next = times.current.find(at => at > now);
  useEffect(() => {
    if (next === undefined) return;
    const timer = setTimeout(() => tick(value => value + 1), next - Date.now());
    return () => clearTimeout(timer);
  }, [next, shown]);
  if (!mine && !blocks.length && !issue && !['failed', 'cancelled'].includes(view?.status || message.status)) return null;
  const author = mine ? '我' : authorLabel(message) || '小鱼';
  return <article data-message-id={message.id} className={'message-row ' + (mine ? 'mine' : '') + (highlight ? ' highlighted' : '')}>{!mine && <div className="reply-author"><Avatar size={25} /><span>{authorLabel(message)}</span></div>}
    {blocks.slice(0, shown).map(block => {
      // 你引用过的那句：像微信，灰色小块贴在你的气泡下面（和手机一致）。
      const quoted = mine ? splitQuote(String(block.text || '')) : { body: String(block.text || '') };
      const shown = quoted.quote ? { ...block, text: quoted.body } : block;
      return <div key={block.id} className="bubble-stack"><div className="message-bubble" data-block-id={block.id} onContextMenu={event => onMenu(event, quoted.body, author)}><DisplayText block={shown} /><div className="message-meta"><small>{shortTime(message.created_at * 1000)}</small></div></div>
        {quoted.quote && <div className="quoted">{quoted.quote}</div>}</div>;
    })}{shown < blocks.length && <div className="typing-dots" aria-label="正在输入"><i /><i /><i /></div>}
    {status && <small role="status">{status}</small>}{onReview && <button className="quiet-button review-link" onClick={onReview}>核对这次操作 <Icon name="next" /></button>}{task && <button className={'inline-work ' + (task.state === 'waiting' ? 'waiting' : '')} onClick={() => onTask(task)}><Avatar type={String(task.worker_agent || task.agent)} size={22} /><span>{taskStatusLabel(task)}</span><Icon name="next" /></button>}</article>;
}
/** 右键气泡，照微信：深色小条，复制、引用；选中了一部分就只取那一部分。 */
function BubbleMenu({ x, y, onCopy, onQuote, onClose }: { x: number; y: number; onCopy(): void; onQuote(): void; onClose(): void }) {
  useEffect(() => { const close = () => onClose(); window.addEventListener('mousedown', close); window.addEventListener('scroll', close, true); return () => { window.removeEventListener('mousedown', close); window.removeEventListener('scroll', close, true); }; }, []);
  return <div className="bubble-menu" role="menu" style={{ left: Math.min(x, window.innerWidth - 140), top: Math.max(8, y - 64) }} onMouseDown={event => event.stopPropagation()}>
    <button role="menuitem" onClick={onCopy}><Icon name="copy" /><span>复制</span></button><button role="menuitem" onClick={onQuote}><Icon name="quote" /><span>引用</span></button></div>;
}
function QuoteBar({ text, onClear }: { text: string; onClear(): void }) {
  return <div className="quote-bar"><span>{text}</span><button className="icon-button" aria-label="取消引用" onClick={onClear}><Icon name="close" /></button></div>;
}
/** 出错的“详情”：界面上一句人话，原始报错在这里看、能复制（和手机一致）。 */
function ErrorDetail({ text, onClose }: { text: string; onClose(): void }) {
  const full = text + '\n\n时间：' + new Date().toLocaleString('zh-CN');
  return <div className="modal-shade" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section className="usage-modal" role="dialog" aria-modal="true" aria-label="报错详情">
    <header><h2>出了什么问题</h2><button className="icon-button" aria-label="关闭" onClick={onClose}><Icon name="close" /></button></header>
    <pre className="error-detail">{full}</pre><footer><span /><button className="primary" onClick={() => void navigator.clipboard.writeText(full)}>复制</button></footer></section></div>;
}
/**
 * 电脑会话的页面像跟它的聊天：最近几轮对话；能接着说时下面就是输入框，不能时写清楚为什么
 * （开在电脑上 / 在跑 / 接不上）。从这里说的那一句另有一张卡（desktop_card），它的进度写在这里。
 */
function SessionChat({ task, tasks, detail, state, text, busy, onText, onSend, onMenu, onDetails }: { task: Json; tasks: Json[]; detail?: Json; state: string; text: string; busy: boolean;
  onText(value: string): void; onSend(): void; onMenu(event: React.MouseEvent, text: string, author: string): void; onDetails(text: string): void }) {
  const who = String(task.worker_agent || task.agent || '') === 'codex' ? 'Codex' : 'Claude';
  const latest = tasks.filter(item => item.desktop_card === task.id && item._runtimeId === task._runtimeId).sort((a, b) => Number(b.created_at || 0) - Number(a.created_at || 0))[0];
  const recentLatest = latest && Date.now() - Number(latest.created_at || 0) * 1000 < 6 * 3600_000 ? latest : undefined;
  const failed = recentLatest?.state === 'failed';
  const note = recentLatest && activeTask(recentLatest) ? '正在把你的话送进去，' + who + ' 回完会出现在下面。'
    : failed ? '刚才那句没送到：' + String(recentLatest!.brief || '').replace(/^.*?没做成：/, '')
    : state === 'open' ? '它正开在电脑上，从这里发的话进不了那个窗口。到电脑上继续，或者关掉那个窗口后再从这里说。'
    : state === 'running' ? '它这一轮还在跑，结束后可以从这里接着说。' : state === 'unavailable' ? '这个会话现在接不上，只能看。' : '';
  const entries = (Array.isArray(detail?.entries) ? detail!.entries as Json[] : []).filter(entry => String(entry.text || '').trim()).slice(-8);
  return <div className="session-chat">
    {note && <p className={'session-note ' + (failed ? 'failed' : '')}>{note}{failed && <button className="quiet-button" onClick={() => onDetails(String(recentLatest!.brief || ''))}>详情</button>}</p>}
    {entries.length > 0 && <h3>最近的对话</h3>}
    {entries.map((entry, index) => { const mine = entry.role === 'you'; return <article key={String(entry.id || index)} className={'message-row ' + (mine ? 'mine' : '')}>
      <div className="message-bubble" onContextMenu={event => onMenu(event, String(entry.text || ''), mine ? '我' : who)}><p>{String(entry.text || '')}</p><div className="message-meta"><small>{clock(Number(entry.at) * 1000)}</small></div></div></article>; })}
    {state === 'ready' && <form className="composer session-composer" onSubmit={event => { event.preventDefault(); onSend(); }}>
      <textarea aria-label={'跟 ' + who + ' 说'} value={text} rows={1} maxLength={32000} placeholder={'跟 ' + who + ' 说…'} onChange={event => onText(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); onSend(); } }} />
      <button className="send" type="submit" aria-label="发送" disabled={busy || !text.trim()}><Icon name="up" /></button></form>}
  </div>;
}
/** 帮手页（和手机一致）：头像、名字、一句状态、用量一行；一个“最近”列表，派给它的事和电脑会话合在一起。 */
function HelperPage({ helper, tasks, connected, all, onAll, onUsage, onManage, onTask }: { helper?: HelperView; tasks: Json[]; connected: boolean; all: boolean; onAll(): void; onUsage(): void; onManage(): void; onTask(task: Json): void }) {
  if (!helper) return <section className="helper-page"><p className="quiet-empty">这位帮手现在不在列表里。</p></section>;
  const now = Date.now();
  const at = (task: Json) => Math.max(Number(task.created_at || 0), Number(task.observed_at || 0)) * 1000;
  const mine = tasks.filter(task => helperMatchesTask(helper, task) && !task.desktop_card).sort((a, b) => at(b) - at(a));
  const visible = mine.filter(task => all || !task.monitoring_only || sessionState(task, connected, now) !== 'unavailable');
  const shown = all ? visible : visible.slice(0, 5);
  const usage = [helper.fiveHour.left === null ? '' : '5 小时 ' + helper.fiveHour.left + '%', helper.week.left === null ? '' : '本周 ' + helper.week.left + '%'].filter(Boolean).join(' · ');
  const word = (task: Json) => task.monitoring_only ? ({ running: '在跑', open: '开在电脑上', unavailable: '接不上', ready: '' } as Record<string, string>)[sessionState(task, connected, now)]
    : activeTask(task) ? (task.state === 'waiting' ? '等你确认' : '在做') : task.state === 'done' ? '✓' : taskStatusLabel(task);
  return <section className="helper-page">
    <div className="helper-hero"><Avatar type={helper.type} size={88} /><h2>{helper.label}</h2><p>{helperState(helper, tasks)}</p>{usage && <button className="quiet-button" onClick={onUsage}>{usage} ›</button>}
      <button className="icon-button helper-more" aria-label="管理帮手" onClick={onManage}><Icon name="more" /></button></div>
    <div className="progress-heading"><h3>{all ? '全部' : '最近'}</h3>{(mine.length > shown.length || all) && <button className="quiet-button" onClick={onAll}>{all ? '收起' : '全部 ' + mine.length + ' ›'}</button>}</div>
    {shown.length === 0 && <p className="quiet-empty">还没有。在聊天里说「让{helper.label}做……」，小鱼会安排。</p>}
    <div className="helper-list">{shown.map(task => <button key={taskKey(task)} className="task-row" onClick={() => onTask(task)}><span><strong>{String(task.title || '一件事')}</strong><small>{(task.monitoring_only ? '电脑上' : '小鱼派的') + ' · ' + clock(at(task))}</small></span><em>{word(task)}</em></button>)}</div>
  </section>;
}
/**
 * The owner's word on a request whose operations ended unknown. Records a disposition only: nothing
 * is re-run, retried or rolled back. The saved body is resent unchanged after a 503 or a lost receipt.
 */
function EffectReview({ space, target, onClose }: { space: string; target: { runtime: string; record: string }; onClose(): void }) {
  const [view, setView] = useState<Json>(), [problem, setProblem] = useState(''), [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<Record<string, ReviewOutcome | undefined>>({});
  const load = async (message = '') => {
    setView(undefined); setChosen({}); setProblem(message);
    try {
      const value = await window.xiaoyu.effectReview({ space, ...target });
      setView(value); setChosen((value.saved_outcomes || (value.review as Json | undefined)?.outcomes || {}) as Record<string, ReviewOutcome>);
    } catch (error) { setProblem([message, clean(error)].filter(Boolean).join('。')); }
  };
  useEffect(() => { void load(); }, [space, target.runtime, target.record]);
  const items = view ? reviewItems(view) : [], saved = Boolean(view?.saved), editable = view?.status === 'needs_review' && !saved;
  const missing = typeof view?.retry_problem === 'string' && view.retry_problem
    ? view.retry_problem : editable ? reviewMissing(items, chosen) : '';
  async function submit() {
    setBusy(true); setProblem('');
    try {
      const result = await window.xiaoyu.submitEffectReview({ space, ...target, revision: String(view?.revision || ''), outcomes: chosen as Record<string, ReviewOutcome> });
      if (result.status === 'reviewed') { onClose(); return; }
      setView({ ...result, saved: true });
    } catch (error) {
      const text = clean(error);
      await load(text.startsWith('记录已变化') ? text + '。请查看最新操作并重新选择。' : '提交未完成：' + text);
    } finally { setBusy(false); }
  }
  return <div className="modal-shade" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section className="usage-modal review-modal" role="dialog" aria-modal="true" aria-labelledby="review-title">
    <header><h2 id="review-title">核对这次操作</h2><button className="icon-button" aria-label="关闭核对" onClick={onClose}><Icon name="close" /></button></header>
    {!view && !problem && <p className="quiet-empty">正在读取原操作…</p>}
    {view && <><p className="review-state">{reviewLabel(view.status)}</p><p className="review-note">这是你的人工确认，记录在原对话里；不会重做、重试或撤销任何操作。</p>
      {items.map(item => <div className="review-item" key={item.id}><strong>{item.name || '一项操作'}</strong>{item.detail && <code>{item.detail}</code>}{!item.canApply && <small>还没开始执行，只能确认为未生效</small>}
        <div className="review-choices">{(['applied', 'not_applied'] as const).map(value => <button key={value} className={chosen[item.id] === value ? 'chosen' : ''}
          disabled={!editable || busy || (value === 'applied' && !item.canApply)} onClick={() => setChosen(current => ({ ...current, [item.id]: value }))}>{value === 'applied' ? '已生效' : '未生效'}</button>)}</div></div>)}
      {view.status === 'recorded' && !saved && <p>请在保留原确认的设备上重试发出说明。</p>}
      {(editable || saved) && view.status !== 'reviewed' && <footer><small>{missing}</small><button className="primary" disabled={busy || Boolean(missing)} onClick={() => void submit()}>{editable ? '提交确认' : '重试发出说明'}</button></footer>}</>}
    {problem && <p role="alert" className="review-problem">{problem}</p>}
  </section></div>;
}
function Decision({ approval, disabled, onDecision }: { approval: Json; disabled: boolean; onDecision(decision: 'allow' | 'deny'): void }) {
  return <div className="decision"><strong>等你确认</strong><p>{String(approval.detail || approval.tool || '请查看操作内容')}</p>{approval.remote_actionable !== false && !approval._staleEpoch ? <div><button disabled={disabled} onClick={() => onDecision('deny')}>拒绝</button><button className="primary" disabled={disabled} onClick={() => onDecision('allow')}>允许这次</button></div> : <small>{approval._staleEpoch ? '状态待确认' : '请在电脑上确认'}</small>}</div>;
}
function TaskRow({ task, selected, onClick }: { task: Json; selected: boolean; onClick(): void }) {
  const stale = activeTask(task) && task.state !== 'waiting' && Number(task.last_meaningful_at || task.updated_at) > 0 && Date.now() - Number(task.last_meaningful_at || task.updated_at) * 1000 > 300_000;
  return <button className={'task-row ' + (selected ? 'selected' : '')} onClick={onClick}><Avatar type={String(task.worker_agent || task.agent)} size={31} /><span>{String(task.title || '一件事')}</span>{unverifiedTask(task) ? <span title="状态待确认"><Icon name="clock" /></span> : task.state === 'waiting' ? <i className="waiting-dot" /> : stale ? <span className="stale-task" title={elapsed(task.last_meaningful_at || task.updated_at)}><Icon name="clock" /></span> : activeTask(task) ? <i className="task-spinner" /> : <Icon name="check" />}</button>;
}
function progressCount(detail?: Json): number {
  const events = Array.isArray(detail?.events) ? detail.events as Json[] : [], entries = Array.isArray(detail?.entries) ? detail.entries as Json[] : [];
  return events.length || entries.filter(entry => entry.kind !== 'user' && entry.role !== 'you').length;
}
function Progress({ detail, all }: { detail?: Json; all: boolean }) {
  const events = Array.isArray(detail?.events) ? detail.events as Json[] : [], entries = Array.isArray(detail?.entries) ? detail.entries as Json[] : [];
  const rows = (events.length ? events : entries.filter(entry => entry.kind !== 'user' && entry.role !== 'you')).filter(row => readableProgress(String(row.text || '')));
  return <div className="progress-list">{(all ? rows : rows.slice(-3)).map((row, index) => <div key={String(row.id || index)}><Icon name={['completed', 'tool_result'].includes(String(row.phase)) ? 'check' : row.phase === 'relay_queued' ? 'clock' : 'quote'} /><p>{readableProgress(String(row.text || ''))}<small>{clock(Number(row.created_at || row.at) * 1000)}</small></p></div>)}{detail && !rows.length && <p className="quiet-empty">尚未记录进展。</p>}{!detail && <p className="quiet-empty">正在读取已有进展…</p>}</div>;
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
    <p className="usage-freshness">{helper.quotaProblem ? '额度刷新未成功，以下为上次收到的数据。' : ''}</p><p className="usage-freshness">头像圈显示已知额度中较少的剩余额度。</p><p className="usage-model">{helper.model || '模型未上报'}{helper.effort ? ' · ' + helper.effort : ''}</p>{([['5 小时剩余', helper.fiveHour], ['本周剩余', helper.week]] as const).map(([label, window]) => <div className="usage-window" key={label}><div><span>{label}</span><strong>{window.unavailable ? '当前未提供此额度' : window.left === null ? '暂无数据' : window.left + '%'}</strong></div>{!window.unavailable && <><div className="usage-track"><i style={{ width: window.left === null ? '0%' : window.left + '%' }} /></div><small>{window.resetsAt ? new Date(window.resetsAt).toLocaleString('zh-CN') + ' 重置' : '重置时间未上报'}</small></>}</div>)}
    {(helper.members?.length || 0) > 1 && <details className="helper-devices"><summary>设备与上报来源 · {new Set(helper.members?.map(member => member.device)).size} 台</summary>{helper.members?.map(member => <p key={member.id}>{member.device} · {member.name}<small>{member.connected ? '服务可达' : '离线'} · 5 小时 {member.fiveHour.left === null ? '未上报' : member.fiveHour.left + '%'} · 本周 {member.week.left === null ? '未上报' : member.week.left + '%'}</small></p>)}<small>上方采用有数据的最近来源；不同设备的额度不相加，也不据此认定为同一账号。</small></details>}
    <p className="usage-freshness">{stale ? '缓存或暂无最新数据' : '最近上报'} · {helper.updatedAt ? new Date(helper.updatedAt).toLocaleTimeString('zh-CN') : '尚未上报'}<br />{helper.device}</p><footer><button disabled={disabled || !helper.connected} onClick={onRefresh}><Icon name="refresh" />刷新用量</button>{task && <button className="primary" onClick={() => onTask(task)}>查看当前工作 <Icon name="next" /></button>}</footer>
  </section></div>;
}
/**
 * 设置按“东西在哪”分组，和手机一致：电脑、这台电脑、已归档、关于。每一行是“名字 · 现在的状态 · 一个动作”，
 * 不堆按钮。帮手不在这里：加帮手用侧边栏“帮手”旁的 ＋，管理某位帮手进它的页面点 ⋯。
 */
function Settings({ view, space, disabled, onSetup, onRun, onSelect, onDirectory }: { view: ClientView; space?: SpaceView; disabled: boolean; onSetup(): void; onRun(action: () => Promise<unknown>): Promise<void>; onSelect(id: string): void; onDirectory(): void }) {
  const [loginStart, setLoginStart] = useState<boolean>();
  useEffect(() => { void window.xiaoyu.loginStartState().then(setLoginStart).catch(() => setLoginStart(undefined)); }, []);
  const archived = space?.conversations.filter(item => item.archived && !item.deleted && item.id !== 'default') || [];
  const runtime = space?.connections?.find(item => item.local)?.runtime || space?.connections?.[0]?.runtime || '';
  const others = space?.connections?.filter(item => !item.local) || [];
  if (!view.account?.signedIn) return <section className="settings-page"><div className="settings-group">电脑</div><AccountPanel view={view.account} onConfigure={onSetup} /></section>;
  const connected = Boolean(space?.connected);
  return <section className="settings-page">
    <div className="settings-group">电脑</div>
    <div className="settings-list">
      <Row label="小鱼账号" value={view.account.username || ''} action={<button disabled={disabled} onClick={() => void onRun(() => window.xiaoyu.accountLogout())}>退出登录</button>} />
      <Row label="连接" value={<><i className={'dot ' + (connected ? 'online' : '')} />{connected ? '已连接' : view.account.message || '没连上'}</>}
        action={!connected ? <button disabled={disabled} onClick={() => void window.xiaoyu.accountRefresh()}>重新连接</button> : undefined} />
      {others.map(item => <Row key={item.runtime} label={item.name} value={<><i className={'dot ' + (item.connected ? 'online' : '')} />{item.connected ? '已连接' : '离线'}</>} />)}
    </div>
    <div className="settings-group">这台电脑</div>
    <div className="settings-list">
      <Row label="本机服务" value={view.runtime.message} hint="关掉窗口也在后台；退出客户端才停" action={<button disabled={disabled} onClick={onSetup}>检测与配置</button>} />
      <Row label="开机时启动" hint="系统登录后在后台恢复连接" action={<button className={'switch ' + (loginStart ? 'on' : '')} role="switch" aria-checked={Boolean(loginStart)} aria-label="开机时启动" disabled={disabled || loginStart === undefined}
        onClick={() => void onRun(async () => { await window.xiaoyu.loginStart(!loginStart); setLoginStart(!loginStart); })}><i /></button>} />
    </div>
    <div className="settings-group">已归档</div>
    <div className="settings-list">
      {archived.length === 0 ? <Row label="没有归档的旁聊" /> : archived.map(item => <Row key={item.id} label={item.title || '旁聊'} value={clock(item.updatedAt)}
        action={<button disabled={disabled || !item.directoryId || !space} onClick={() => space && void onRun(() => window.xiaoyu.directoryEdit({ space: space.id, runtime, id: String(item.directoryId), revision: Number(item.metadataRevision || 0), fields: { archived: false } }))}>放回</button>} />)}
    </div>
    <div className="settings-group">关于</div>
    <div className="settings-list">
      <Row label="小鱼桌面版" value={view.version} />
      <details className="settings-row developer"><summary>开发者</summary><div>
        <button disabled={!space} onClick={onDirectory}>统一会话目录</button>
        {view.spaces.length > 1 && <label>当前空间<select value={space?.id} onChange={event => onSelect(event.target.value)}>{view.spaces.map(item => <option key={item.id} value={item.id}>{item.name} · {item.runtimeName}</option>)}</select></label>}
      </div></details>
    </div>
  </section>;
}
function Row({ label, value, hint, action }: { label: string; value?: React.ReactNode; hint?: string; action?: React.ReactNode }) {
  return <div className="settings-row"><div className="settings-row-text"><span>{label}</span>{hint && <small>{hint}</small>}</div>{value !== undefined && <span className="settings-row-value">{value}</span>}{action}</div>;
}
function canOpenNative(task: Json, space?: SpaceView): boolean { return Boolean(task.worker_session && !task._staleEpoch && !activeTask(task)
  && space?.connections?.some(item => item.runtime === task._runtimeId && item.local && item.connected && item.capabilities.includes('desktop_handoff'))
  && space.helpers?.some(item => item.runtime === task._runtimeId && item.name === (task.worker_agent || task.agent) && item.type === 'codex')); }
function clean(error: unknown): string { return error instanceof Error ? error.message.replace(/^Error invoking remote method '[^']+': Error: /, '') : '操作未完成，请重试'; }
createRoot(document.getElementById('root')!).render(<App />);
