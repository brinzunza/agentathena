import React, { useState } from 'react';
import Markdown from './Markdown';
import { ChevronRightIcon } from './Icons';
import { splitReply, asFence, shouldFold, describeCode } from '../codeBlocks';

const CELLS = 15;     // 5 across, 3 down

// Stands in for code while it is written, and for the block once it is done. The cells lay themselves down
// one after another like a wall going up; the line count ticks as the code grows, and the code itself is never shown.
export function BuildSign({ segment, building }) {
  const what = segment.lang || 'code';
  return (
    <div className={`build-sign ${building ? 'is-building' : 'is-built'}`} role="status" aria-live="off">
      <div className="build-wall" aria-hidden="true">
        {Array.from({ length: CELLS }, (_, i) => (
          <span key={i} className="build-cell" />
        ))}
      </div>
      <div className="build-text">
        <div className="build-title">
          {building ? 'Building' : 'Built'}
          {building && <span className="build-dots" aria-hidden="true"><i /><i /><i /></span>}
        </div>
        <div className="build-meta">
          {what} · <span className="build-lines">{segment.lines}</span> {segment.lines === 1 ? 'line' : 'lines'}
        </div>
      </div>
      {building && <div className="build-tape" aria-hidden="true" />}
    </div>
  );
}

// A finished block too long to sit in the middle of a reply: a line saying what it is, and a click to read it
function FoldedCode({ segment }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="folded-code">
      <button type="button" className="sources-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={`arrow ${open ? 'expanded' : ''}`}><ChevronRightIcon /></span>
        {open ? 'hide code' : 'show code'}
        <span className="folded-what">{describeCode(segment)}</span>
      </button>
      {open && <Markdown>{asFence(segment)}</Markdown>}
    </div>
  );
}

// What an assistant reply says, with its code kept out of sight: shown as "building" while it is written,
// folded when it is long, and as ordinary code when it is a few lines.
function ReplyBody({ content, streaming }) {
  const parts = splitReply(content);
  return (
    <>
      {parts.map((part, index) => {
        if (part.type === 'text') return <Markdown key={index}>{part.text}</Markdown>;
        if (streaming) return <BuildSign key={index} segment={part} building={part.open} />;
        if (shouldFold(part)) return <FoldedCode key={index} segment={part} />;
        return <Markdown key={index}>{asFence(part)}</Markdown>;
      })}
    </>
  );
}

export default ReplyBody;
