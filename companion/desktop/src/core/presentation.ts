import type { Conversation, DisplayBlock, DisplayProjection, HelperView, Json, Message } from '../shared.js';

/** Consume Gateway decisions; legacy heuristics apply only when the field is absent. */
export function projection(record: Json): DisplayProjection | undefined {
  const value = record.presentation as DisplayProjection | undefined;
  return value?.version === 1 && Array.isArray(value.blocks) && Array.isArray(value.order)
    && value.order.length === 4 && typeof value.author?.label === 'string' ? value : undefined;
}
export const displayIssue = (record: Json): string => record.presentation && !projection(record) ? '显示规则版本不兼容' : '';
export function messageBlocks(record: Message): DisplayBlock[] {
  const view = projection(record);
  if (view) return view.blocks.filter(block => block.complete);
  return ['final', 'done', 'failed', 'cancelled'].includes(record.status || 'final') && record.text
    ? [{ id: record.id, text: record.text, complete: true }] : [];
}
export function authorLabel(record: Json): string {
  return projection(record)?.author.label || String((record.author as Json)?.name || '小幽');
}
export function taskStatusLabel(record: Json): string {
  const view = record.presentation as Json | undefined;
  return view ? view.version === 1 ? String(view.status_label || '') : '显示规则版本不兼容' : taskLabel(String(record.state));
}
export const activeTask = (task: Json) => !task._staleEpoch && ((task.presentation as Json)?.version === 1
  ? ['running', 'waiting'].includes(String((task.presentation as Json).group)) : ['working', 'talking', 'waiting'].includes(String(task.state)));
export const unverifiedTask = (task: Json) => Boolean(task._staleEpoch) || ((task.presentation as Json)?.version === 1
  ? (task.presentation as Json).group === 'uncertain' : !activeTask(task) && !['done', 'failed', 'cancelled', 'turn_ended'].includes(String(task.state)));
export const conversationID = (id: string) => id.length > 0 && id.length <= 64 && !/[\s/\\\x00-\x1f]/u.test(id) && id !== '__desktop__';
const finite = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : undefined;
export function runtimeConversation(raw: unknown): Conversation | undefined {
  if (!raw || typeof raw !== 'object') return;
  const row = raw as Json;
  if (typeof row.id !== 'string' || !conversationID(row.id) || typeof row.title !== 'string' || row.title.length > 200) return;
  return { id: row.id, title: row.id === 'default' ? '主对话' : row.title,
    createdAt: finite(row.createdAt) ?? (finite(row.created_at) || 0) * 1000,
    updatedAt: finite(row.updatedAt) ?? (finite(row.updated_at) || 0) * 1000,
    revision: finite(row.revision) || 0, metadataAt: finite(row.metadataAt) ?? (finite(row.metadata_at) || 0) * 1000,
    metadataRevision: finite(row.metadata_revision) ?? finite(row.metadataRevision) ?? 0, directoryId: String(row.directory_id || row.directoryId || ''), pinned: row.pinned === true, archived: row.archived === true,
    origin: typeof row.origin === 'string' ? row.origin : '', ...(row.title_source === 'pending' ? { pending: true } : {}),
    ...(row.context && typeof row.context === 'object' ? { context: row.context as Json } : {}) };
}
export function newerConversation(next: Conversation, old: Conversation): boolean {
  if (next.origin === old.origin && (next.metadataRevision || 0) !== (old.metadataRevision || 0)) return (next.metadataRevision || 0) > (old.metadataRevision || 0);
  if ((next.metadataAt || 0) !== (old.metadataAt || 0)) return (next.metadataAt || 0) > (old.metadataAt || 0);
  if ((next.origin || '') !== (old.origin || '')) return (next.origin || '') > (old.origin || '');
  return (next.revision || 0) > (old.revision || 0) || (next.updatedAt || 0) > (old.updatedAt || 0);
}
export function helperViews(runtime: string, device: string, agents: Json[], usage: Json, connected: boolean): HelperView[] {
  const rows = Array.isArray(usage.agents) ? usage.agents as Json[] : [];
  const accounts = Array.isArray(usage.accounts) ? usage.accounts as Json[] : [];
  const source = agents.length ? agents : rows;
  return source.filter(agent => agent.default !== true && typeof agent.name === 'string' && typeof agent.type === 'string')
    .map(agent => {
      const base = String(agent.name).split('@')[0];
      const type = agent.type === 'mesh' ? /^(codex)$/i.test(base) ? 'codex' : /^(cc|claude|claude_code|tailor)$/i.test(base) ? 'claude_code' : /^(deepseek|dsh)$/i.test(base) ? 'dsh' : 'mesh' : String(agent.type);
      const details = rows.find(row => row.name === agent.name), account = typeof details?.account === 'string' ? accounts.find(row => row.id === details.account) : undefined;
      const windows = account?.windows as Json | undefined;
      const window = (id: string) => {
        const raw = (Array.isArray(windows) ? windows.find(row => row && (row as Json).kind === id) : windows?.[id]) as Json | undefined, left = finite(raw?.left), reset = finite(raw?.resets_at);
        const expired = reset !== undefined && reset * 1000 <= Date.now();
        return { left: expired || left === undefined ? null : Math.max(0, Math.min(100, left)), resetsAt: reset ? reset * 1000 : null };
      };
      return { id: runtime + '/' + String(agent.name), runtime, device: agent.type === 'mesh' && String(agent.name).includes('@') ? String(agent.name).split('@').slice(1).join('@') : device, name: String(agent.name), type, connected,
        label: type === 'codex' ? 'Codex' : type === 'claude_code' ? 'Claude Code' : String(agent.name),
        model: typeof details?.model === 'string' ? details.model : typeof agent.model === 'string' ? agent.model : '',
        effort: typeof details?.effort === 'string' ? details.effort : typeof agent.effort === 'string' ? agent.effort : '',
        fiveHour: window('5h'), week: window('7d'), updatedAt: (finite(account?.updated_at) || 0) * 1000 };
    }).filter(helper => helper.type !== 'dsh');
}
export function mainMessages(messages: Message[], conversation: string): Message[] {
  return messages.filter(message => message.conversation === conversation && (projection(message)
    ? projection(message)!.placement === 'chat' : message.scope !== 'task' && message.kind !== 'activity'
    && !['desktop', 'external'].includes(message.source?.type || '')));
}
/** The only connection words the owner sees, shared with the phone: connected, cannot connect (retrying) or not paired. */
export function connectionWord(paired: boolean, connected: boolean): string {
  return !paired ? '需要配对' : connected ? '已连接' : '连不上，正在重试';
}
/** A side chat nobody has written in yet is not kept in the list, except the one open now. */
export function listedSideChat(item: Conversation, open: string): boolean {
  return item.id !== 'default' && !item.archived && (!item.pending || item.id === open);
}
/** 在做的事 lists only work handed to a helper; observed computer sessions are reference material. */
export const listedTask = (task: Json) => Boolean(task.agent) && !task.monitoring_only;
export type WorkSection = 'waiting' | 'running' | 'ended';
/** Three groups; a task whose state is unconfirmed has not ended, so it stays with the running ones. */
export function workSection(task: Json): WorkSection {
  if (activeTask(task)) return task.state === 'waiting' || (task.presentation as Json)?.group === 'waiting' ? 'waiting' : 'running';
  return unverifiedTask(task) ? 'running' : 'ended';
}
export function taskLabel(state: string): string {
  return ({ working: '进行中', talking: '正在回复', waiting: '等你确认', done: '已完成', failed: '未完成', cancelled: '已停止', unavailable: '连不上', unknown: '状态待确认', turn_ended: '本轮已结束' } as Record<string, string>)[state] || '等待更新';
}

/** One visible helper per product. Device routes and independent quota reports remain in details. */
export function unifiedHelpers(rows: HelperView[]): HelperView[] {
  const groups = new Map<string, HelperView[]>();
  for (const row of rows) {
    const id = ['codex', 'claude_code'].includes(row.type) ? row.type : row.id;
    const members = groups.get(id) || []; members.push(row); groups.set(id, members);
  }
  return [...groups].map(([id, members]) => {
    const candidates = members.slice().sort((a, b) => {
      const quality = (row: HelperView) => Number(row.connected) * 2 + Number(row.fiveHour.left !== null || row.week.left !== null);
      return quality(b) - quality(a) || b.updatedAt - a.updatedAt || a.id.localeCompare(b.id);
    });
    return { ...candidates[0], id, connected: members.some(member => member.connected), members };
  });
}
export function helperMatchesTask(helper: HelperView, task: Json): boolean {
  return (helper.members || [helper]).some(member => member.runtime === task._runtimeId && member.name === (task.worker_agent || task.agent));
}
