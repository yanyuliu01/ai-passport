import { quotaLeft } from '../core/presentation';
import React from 'react';
import type { HelperView, Json } from '../shared';
import { activeTask, helperMatchesTask } from '../core/presentation';
import xiaoyou from './images/xiaoyou.png';
import codex from './images/codex.png';
import claude from './images/claude.png';
const paths: Record<string, React.ReactNode> = {
  home: <><path d="m3 10 9-7 9 7v11H3Z" /><path d="M9 21v-8h6v8" /></>, phone: <><rect x="6" y="2" width="12" height="20" rx="3" /><path d="M10 18h4" /></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />, chat: <path d="M21 11a8 8 0 0 1-8 8H6l-4 3V11a9 9 0 0 1 19 0Z" />,
  work: <><rect x="3" y="7" width="18" height="14" rx="3" /><path d="M8 7V4h8v3M3 12c5 3 13 3 18 0M10 13h4" /></>,
  settings: <><path d="M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Z" /><circle cx="12" cy="12" r="3" /></>,
  plus: <path d="M12 5v14M5 12h14" />, up: <path d="m6 11 6-6 6 6M12 5v14" />,
  back: <path d="m15 5-7 7 7 7" />, next: <path d="m9 5 7 7-7 7" />, down: <path d="m5 9 7 7 7-7" />,
  close: <path d="m6 6 12 12M6 18 18 6" />, mic: <><rect x="9" y="2" width="6" height="13" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8" /></>,
  image: <><rect x="3" y="3" width="18" height="18" rx="3" /><circle cx="8" cy="8" r="1" /><path d="m3 17 6-5 4 3 4-6 4 5" /></>,
  file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h6" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>, info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
  refresh: <><path d="M20 7a9 9 0 1 0 1 9M20 2v6h-6" /></>, external: <><path d="M14 3h7v7M10 14 21 3M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5" /></>,
  check: <path d="m5 12 4 4L19 6" />, stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
  quote: <><path d="M4 4v9a3 3 0 0 0 3 3h13M15 11l5 5-5 5" /></>, more: <><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></>,
};
export function Icon({ name }: { name: string }) { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.info}</svg>; }
export function Avatar({ type = 'xiaoyou', size = 32 }: { type?: string; size?: number }) {
  const source = type.includes('codex') ? codex : /claude|^cc$/.test(type) ? claude : xiaoyou;
  return <img className="portrait" src={source} width={size} height={size} alt="" />;
}
export const taskKey = (task: Json) => String(task._runtimeId) + '/' + String(task.id);
export function helperTask(helper: HelperView, tasks: Json[]) { return tasks.find(task => helperMatchesTask(helper, task) && activeTask(task)); }
export function QuotaAvatar({ helper, onClick }: { helper: HelperView; onClick(): void }) {
  const left = quotaLeft(helper), stale = !helper.connected || !helper.updatedAt || Date.now() - helper.updatedAt > 120_000;
  const level = left === null || stale ? 'unknown' : left > 40 ? 'normal' : left > 15 ? 'warn' : 'low';
  return <button className="quota-avatar" aria-label={helper.label + ' 的用量'} onClick={onClick} data-level={level}>
    <svg viewBox="0 0 42 42" aria-hidden="true"><circle className="quota-track" cx="21" cy="21" r="19" />{left !== null && <circle className="quota-fill" cx="21" cy="21" r="19" pathLength="100" strokeDasharray={`${left} 100`} />}</svg>
    <Avatar type={helper.type} size={29} /><span className="quota-value">{left === null ? '—' : Math.round(left) + '%'}</span>
  </button>;
}
export function HelperPill({ helper, tasks, onUsage, onTask }: { helper: HelperView; tasks: Json[]; onUsage(): void; onTask(task: Json): void }) {
  const task = helperTask(helper, tasks), waiting = task?.state === 'waiting';
  return <div className={'helper-pill ' + (waiting ? 'waiting' : task ? 'running' : '')}>
    <QuotaAvatar helper={helper} onClick={onUsage} />
    <button className="helper-label" onClick={() => task ? onTask(task) : onUsage()}><span>{helper.label}</span><small>{waiting ? '等你确认' : task ? '在做' : helper.connected ? '空闲' : '连不上'}</small></button>
  </div>;
}
export function elapsed(value: unknown): string {
  const time = typeof value === 'number' ? value * 1000 : 0;
  if (!time) return '暂无更新时间';
  const seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
  return seconds < 60 ? '刚刚' : seconds < 3600 ? Math.floor(seconds / 60) + ' 分钟前' : seconds < 86400 ? Math.floor(seconds / 3600) + ' 小时前' : new Date(time).toLocaleDateString('zh-CN');
}
export function shortTime(value?: number): string { return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''; }
