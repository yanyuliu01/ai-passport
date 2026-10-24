import React from 'react';
import type { DisplayBlock, DisplayRun } from '../shared';

const SAFE_LINK = /^(https?:\/\/|mailto:)/i;

function styled(run: DisplayRun, key: React.Key): React.ReactNode {
  let node: React.ReactNode = run.text;
  if (run.code === 'inline') node = <code>{node}</code>;
  if (run.italic) node = <em>{node}</em>;
  if (run.strike) node = <s>{node}</s>;
  if (run.bold) node = <strong>{node}</strong>;
  if (run.heading) node = <span className={'run-heading h' + Math.min(6, Math.max(1, run.heading))}>{node}</span>;
  if (run.quote) node = <span className="run-quote">{node}</span>;
  if (run.href && SAFE_LINK.test(run.href)) {
    const href = run.href;
    node = <a href={href} title={href} onClick={event => { event.preventDefault(); void window.xiaoyou.openLink(href); }}>{node}</a>;
  }
  return <React.Fragment key={key}>{node}</React.Fragment>;
}

/** Formatting is already resolved by the Gateway; never parse text for markup here. */
export function DisplayText({ block }: { block: DisplayBlock }) {
  if (!block.runs) return <p>{block.text}</p>;
  const runs = block.runs, out: React.ReactNode[] = [];
  for (let index = 0; index < runs.length; index++) {
    if (runs[index].code !== 'block') { out.push(styled(runs[index], index)); continue; }
    // Consecutive code-block lines, with the line breaks between them, are one block.
    const start = index, language = runs[index].language || '';
    let text = runs[index].text;
    while (index + 2 < runs.length && runs[index + 1].text === '\n' && runs[index + 2].code === 'block') {
      text += '\n' + runs[index + 2].text;
      index += 2;
    }
    if (index + 1 < runs.length && runs[index + 1].text === '\n') index++;  // The block already ends the line.
    out.push(<code key={start} className="code-block" data-language={language || undefined}>{text}</code>);
  }
  return <p>{out}</p>;
}
