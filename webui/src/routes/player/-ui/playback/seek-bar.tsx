/**
 * the seek bar, for the transport bar and the video player alike: what's
 * played and buffered, chapter marks, the time (and chapter, with its
 * picture) under the pointer, drag to scrub (the position only moves when
 * you let go), and the arrow keys as a slider (5 seconds, page up/down a
 * minute). marks (moments someone noted) sit on it as pins: near one, the
 * hint reads its note; let go on one and it lands right on it. skip markers
 * (intro, recap, credits) are quiet bands on the rail, named in the hint.
 */

import { useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

import type { Marker } from '../../-engine/timeline';
import type { Chapter } from '../../-engine/types';

import { chapterAt, clock, markerAt } from '../../-engine/timeline';
import s from './playback.module.css';

export interface SeekMark {
  id: number;
  at: number;
  /** the note, maybe empty */
  label: string;
  /** whose: a name, or 'You' */
  who: string;
  mine: boolean;
}

/** a pin within this share of the bar counts as under the pointer */
const PIN_REACH = 0.012;

export function SeekBar({
  position,
  duration,
  buffered = 0,
  chapters = [],
  onSeek,
  size = 'small',
  marks = [],
  spans = [],
}: {
  position: number;
  duration: number;
  buffered?: number;
  chapters?: readonly Chapter[];
  onSeek: (seconds: number) => void;
  size?: 'small' | 'large';
  marks?: readonly SeekMark[];
  /** skip markers, drawn as bands */
  spans?: readonly Marker[];
}) {
  const track = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const known = duration > 0;
  const shown = drag ?? position;
  const pct = (t: number) =>
    known ? `${Math.min(100, Math.max(0, (t / duration) * 100))}%` : '0%';

  const timeAt = (clientX: number) => {
    const box = track.current?.getBoundingClientRect();
    if (!box || !known) return 0;
    return Math.min(duration, Math.max(0, ((clientX - box.left) / box.width) * duration));
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (!known) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag(timeAt(e.clientX));
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const t = timeAt(e.clientX);
    setHover(t);
    if (drag !== null) setDrag(t);
  };
  const pinNear = (t: number | null) => {
    if (t === null || !known || !marks.length) return null;
    let best: SeekMark | null = null;
    for (const m of marks) {
      const d = Math.abs(m.at - t) / duration;
      if (d <= PIN_REACH && (!best || d < Math.abs(best.at - t) / duration)) best = m;
    }
    return best;
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (drag === null) return;
    const t = timeAt(e.clientX);
    onSeek(pinNear(t)?.at ?? t);
    setDrag(null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = { ArrowLeft: -5, ArrowRight: 5, PageDown: -60, PageUp: 60 }[e.key];
    if (step !== undefined) onSeek(position + step);
    else if (e.key === 'Home') onSeek(0);
    else if (e.key === 'End' && known) onSeek(duration - 1);
    else return;
    e.preventDefault();
    e.stopPropagation();
  };

  const hint = hover ?? drag;
  const hintChapter = hint !== null && chapters.length ? chapters[chapterAt(chapters, hint)] : null;
  const hintPin = pinNear(hint);
  const hintSpan = hint !== null && spans.length ? markerAt(spans, hint) : null;
  const SPAN_NAMES: Record<string, string> = {
    intro: 'Intro',
    recap: 'Recap',
    credits: 'Credits',
    preview: 'Preview',
  };

  return (
    <div className={s.seek} data-size={size}>
      <span className={s.seekTime}>{clock(shown)}</span>
      <div
        ref={track}
        className={s.seekTrack}
        role="slider"
        tabIndex={0}
        aria-label="Seek"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration) || 0}
        aria-valuenow={Math.round(shown)}
        aria-valuetext={`${clock(shown)} of ${clock(duration)}`}
        data-dragging={drag !== null ? 'true' : undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => setHover(null)}
        onKeyDown={onKeyDown}
        data-nav
      >
        <span className={s.seekRail}>
          <span className={s.seekBuffered} style={{ width: pct(buffered) }} />
          <span className={s.seekPlayed} style={{ width: pct(shown) }} />
          {known
            ? spans
                .filter((m) => m.kind !== 'commercial')
                .map((m) => (
                  <span
                    key={`${m.kind}:${m.start}`}
                    className={s.seekSpan}
                    data-kind={m.kind}
                    style={{
                      left: pct(m.start),
                      width: `${Math.max(0.2, ((Math.min(m.end, duration) - m.start) / duration) * 100)}%`,
                    }}
                  />
                ))
            : null}
          {known
            ? chapters
                .slice(1)
                .map((c) => (
                  <span key={c.start} className={s.seekMark} style={{ left: pct(c.start) }} />
                ))
            : null}
        </span>
        {known
          ? marks.map((m) => (
              <span
                key={m.id}
                className={s.seekPin}
                data-mine={m.mine ? 'true' : undefined}
                data-on={hintPin?.id === m.id ? 'true' : undefined}
                style={{ left: pct(m.at) }}
                aria-hidden="true"
              />
            ))
          : null}
        <span className={s.seekKnob} style={{ left: pct(shown) }} />
        {hint !== null && known ? (
          <span
            className={s.seekHint}
            data-pin={hintPin ? 'true' : undefined}
            style={{ left: pct(hintPin?.at ?? hint) }}
          >
            {hintPin ? (
              <span className={s.seekHintPin}>
                <span className={s.seekHintWho}>{hintPin.who}</span>
                {hintPin.label ? <span>{hintPin.label}</span> : null}
              </span>
            ) : hintSpan && SPAN_NAMES[hintSpan.kind] ? (
              <span className={s.seekHintChapter}>{SPAN_NAMES[hintSpan.kind]}</span>
            ) : hintChapter?.title ? (
              <>
                {hintChapter.thumb ? (
                  <span className={s.seekHintArt}>
                    <img src={hintChapter.thumb} alt="" decoding="async" />
                  </span>
                ) : null}
                <span className={s.seekHintChapter}>{hintChapter.title}</span>
              </>
            ) : null}
            {clock(hintPin?.at ?? hint)}
          </span>
        ) : null}
      </div>
      <span className={s.seekTime}>{known ? clock(duration) : '--:--'}</span>
    </div>
  );
}
