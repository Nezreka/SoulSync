/**
 * the guide's program card: it drops down from the program you picked, so the
 * channels keep the screen. escape or a click outside closes it and puts
 * you back on that program. on a phone it comes up from the bottom.
 * the strip above the guide says what the pointer rests on, in one line.
 */

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

import type { Picked } from './guide-grid';

import { clock } from '../-lib/format';
import { blockArtists } from '../-lib/schedule';
import p from './guide-pop.module.css';
import { Art } from './kit';

const GAP = 10;
const EDGE = 16;

export function GuidePop({
  anchor,
  onClose,
  children,
}: {
  /** the program picked; the card sits beside it */
  anchor: HTMLElement | null;
  onClose: () => void;
  children: ReactNode;
}) {
  const card = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<{ left: number; top: number; side: string } | null>(null);
  const close = useRef(onClose);
  close.current = onClose;

  // beside the program: to its right if it fits, else its left, else over it
  useLayoutEffect(() => {
    const el = card.current;
    if (!el) return;
    const place = () => {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      if (vw <= 640) {
        setAt({ left: 0, top: 0, side: 'sheet' });
        return;
      }
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      const r = anchor?.isConnected ? anchor.getBoundingClientRect() : null;
      // where the programs start: the card stays right of the channel names
      const lane =
        anchor?.closest('[role=row]')?.querySelector('[role=rowheader]')?.getBoundingClientRect()
          .right ?? 0;
      if (!r) {
        setAt({ left: (vw - w) / 2, top: Math.max(EDGE, (vh - h) / 2), side: 'center' });
        return;
      }
      // down from the program like a menu, from its left edge, or up from it
      // near the bottom. it never covers the program or the channel names
      const left = Math.min(Math.max(EDGE, Math.max(r.left, lane)), vw - EDGE - w);
      let side = 'below';
      let top = r.bottom + GAP;
      if (top + h > vh - EDGE && r.top - GAP - h >= EDGE) {
        top = r.top - GAP - h;
        side = 'above';
      }
      top = Math.min(Math.max(EDGE, top), vh - EDGE - h);
      setAt({ left, top, side });
    };
    place();
    const ro = new ResizeObserver(place);
    ro.observe(el);
    window.addEventListener('resize', place);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', place);
    };
  }, [anchor]);

  // in: its first button. out: back on the program
  useEffect(() => {
    const back = anchor;
    card.current?.querySelector<HTMLElement>('button, a[href]')?.focus({ preventScroll: true });
    return () => {
      if (back?.isConnected) back.focus({ preventScroll: true });
    };
  }, [anchor]);

  // escape, a click elsewhere, or the guide scrolling under it
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' && e.key !== 'GoBack' && e.key !== 'BrowserBack') return;
      e.preventDefault();
      e.stopImmediatePropagation();
      close.current();
    };
    const inside = (t: EventTarget | null) => t instanceof Node && !!card.current?.contains(t);
    const onDown = (e: PointerEvent) => {
      if (!inside(e.target)) close.current();
    };
    const onScroll = (e: Event) => {
      if (!inside(e.target)) close.current();
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('scroll', onScroll, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('scroll', onScroll, true);
    };
  }, []);

  const host = document.querySelector('[data-player-root]') ?? document.body;
  return createPortal(
    <div
      ref={card}
      className={p.pop}
      role="dialog"
      aria-label="Program"
      data-side={at?.side}
      style={
        !at ? { opacity: 0 } : at.side === 'sheet' ? undefined : { left: at.left, top: at.top }
      }
    >
      {children}
    </div>,
    host,
  );
}

/** one line above the guide: the program the pointer or focus rests on */
export function GuideStrip({ picked, now }: { picked: Picked; now: number }) {
  const { channel, program, block } = picked;
  const start = block?.start ?? program.start;
  const end = block?.end ?? program.end;
  const live = start <= now && end > now;
  const airing = block?.items.find((x) => x.start <= now && x.end > now) ?? program;
  const title = block ? blockArtists(block, 3) : program.title;
  const sub = block ? `${block.items.length} songs` : program.subtitle;
  return (
    <div
      className={p.strip}
      aria-live="polite"
      data-premiere={program.premiere ? 'true' : undefined}
    >
      <span className={p.stripArt}>
        <Art src={(block ? airing : program).art} alt="" />
      </span>
      <span className={p.stripChannel}>
        <span className={p.stripNum}>{channel.number}</span>
        {channel.name}
      </span>
      <span className={p.stripTitle}>
        {program.premiere ? <span className={p.stripNew}>New</span> : null}
        {title}
        {sub ? <span className={p.stripSub}> · {sub}</span> : null}
      </span>
      <span className={p.stripTime}>
        {live ? (
          <>
            <span className={p.stripLive}>On now</span>
            {Math.max(1, Math.round((end - now) / 60000))}m left
          </>
        ) : (
          `${clock(start)} – ${clock(end)}`
        )}
      </span>
      {live ? (
        <span className={p.stripBar} aria-hidden="true">
          <span style={{ width: `${((now - start) / (end - start)) * 100}%` }} />
        </span>
      ) : null}
    </div>
  );
}
