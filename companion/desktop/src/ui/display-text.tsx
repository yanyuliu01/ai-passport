import React from 'react';
import type { DisplayBlock } from '../shared';

/** Formatting is already resolved by the Gateway; never parse text for markup here. */
export function DisplayText({ block }: { block: DisplayBlock }) {
  return <p>{block.runs ? block.runs.map((run, index) => run.bold
    ? <strong key={index}>{run.text}</strong> : <React.Fragment key={index}>{run.text}</React.Fragment>) : block.text}</p>;
}
