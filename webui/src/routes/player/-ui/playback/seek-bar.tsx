/**
 * the seek bar, for the transport bar and the video player alike: what's
 * played and buffered, chapter marks, the time (and chapter) under the
 * pointer, drag to scrub (the position only moves when you let go), and
 * the arrow keys as a slider (5 seconds, page up/down a minute).
 */

import { useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

import type { Chapter } from '../../-engine/types';

import { chapterAt, clock } from '../../-engine/timeline';
import s from './playback.module.css';

export function SeekBar({
  position,
  duration,
  buffered = 0,
  chapters = [],
  onSeek,
  size = 'small',
}: {
  position: number;
  duration: number;
  buffered?: number;
  chapters?: readonly Chapter[];
  onSeek: (seconds: number) => void;
  size?: 'small' | 'large';
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
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (drag === null) return;
    onSeek(timeAt(e.clientX));
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
            ? chapters
                .slice(1)
                .map((c) => (
                  <span key={c.start} className={s.seekMark} style={{ left: pct(c.start) }} />
                ))
            : null}
        </span>
        <span className={s.seekKnob} style={{ left: pct(shown) }} />
        {hint !== null && known ? (
          <span className={s.seekHint} style={{ left: pct(hint) }}>
            {hintChapter?.title ? (
              <span className={s.seekHintChapter}>{hintChapter.title}</span>
            ) : null}
            {clock(hint)}
          </span>
        ) : null}
      </div>
      <span className={s.seekTime}>{known ? clock(duration) : '--:--'}</span>
    </div>
  );
}
