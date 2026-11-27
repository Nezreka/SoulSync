/**
 * a video's chapters without leaving it: a strip of pictures along the
 * bottom, each chapter's picture, name and time, the one playing marked with
 * how far into it you are. picking one jumps there.
 *
 * keys: it opens on the chapter playing, focused; left and right (or a
 * remote's) move along, home and end jump to the ends, enter plays, escape
 * closes. on a phone it's a row you swipe, each card snapping into place.
 */

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';

import type { Chapter } from '../../-engine/types';

import { chapterAt, clock } from '../../-engine/timeline';
import { CloseIcon } from './icons';
import s from './playback.module.css';

/** "12m", "1h 4m", "45s" */
export function span(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
}

/** a chapter's picture, or its number on a plain card when there's none (yet) */
export function ChapterArt({
  chapter,
  index,
  eager,
}: {
  chapter: Chapter;
  index: number;
  eager?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [chapter.thumb]);
  if (!chapter.thumb || failed)
    return (
      <span className={s.chapterNoArt} aria-hidden="true">
        {index + 1}
      </span>
    );
  return (
    <img
      className={s.chapterImg}
      src={chapter.thumb}
      alt=""
      loading={eager ? 'eager' : 'lazy'}
      decoding="async"
      draggable={false}
      onError={() => setFailed(true)}
    />
  );
}

export function ChaptersPanel({
  chapters,
  position,
  duration,
  onPick,
  onClose,
}: {
  chapters: readonly Chapter[];
  position: number;
  duration: number;
  onPick: (start: number) => void;
  onClose: () => void;
}) {
  const at = chapterAt(chapters, position);
  const strip = useRef<HTMLOListElement>(null);

  // open on the chapter playing, focused, in the middle of the strip
  useEffect(() => {
    const el = strip.current?.querySelectorAll<HTMLButtonElement>('button')[Math.max(0, at)];
    el?.focus({ preventScroll: true });
    el?.scrollIntoView?.({ block: 'nearest', inline: 'center' });
    // only when it opens: the playing chapter moving on mustn't pull focus
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const buttons = Array.from(strip.current?.querySelectorAll<HTMLButtonElement>('button') ?? []);
    const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const go = (n: number) => {
      const el = buttons[Math.max(0, Math.min(buttons.length - 1, n))];
      el?.focus({ preventScroll: true });
      el?.scrollIntoView?.({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    };
    const move: Record<string, () => void> = {
      ArrowLeft: () => go(i - 1),
      ArrowRight: () => go(i + 1),
      Home: () => go(0),
      End: () => go(buttons.length - 1),
      Escape: onClose,
    };
    const fn = move[e.key];
    if (!fn || (i < 0 && e.key !== 'Escape')) return;
    e.preventDefault();
    e.stopPropagation();
    fn();
  };

  return (
    <section className={s.chapters} aria-label="Chapters" onKeyDown={onKeyDown} data-own-keys>
      <div className={s.chaptersHead}>
        <span className={s.chaptersTitle}>
          Chapters <span className={s.chaptersCount}>{chapters.length}</span>
        </span>
        <button
          type="button"
          className={s.ctl}
          aria-label="Close chapters"
          onClick={onClose}
          data-nav
        >
          <CloseIcon />
        </button>
      </div>
      <ol className={s.chapterStrip} ref={strip}>
        {chapters.map((c, i) => {
          const end = c.end ?? chapters[i + 1]?.start ?? duration;
          const length = end - c.start;
          const playing = i === at;
          const part = playing && length > 0 ? (position - c.start) / length : 0;
          return (
            <li key={`${c.start}`}>
              <button
                type="button"
                className={s.chapterCard}
                aria-current={playing ? 'true' : undefined}
                aria-label={`${c.title}, ${clock(c.start)}${playing ? ', playing' : ''}`}
                onClick={() => onPick(c.start)}
                data-nav
              >
                <span className={s.chapterArt}>
                  <ChapterArt chapter={c} index={i} eager={Math.abs(i - Math.max(0, at)) <= 4} />
                  <span className={s.chapterTime}>{clock(c.start)}</span>
                  {playing ? (
                    <span className={s.chapterBar}>
                      <span style={{ width: `${Math.min(1, Math.max(0, part)) * 100}%` }} />
                    </span>
                  ) : null}
                </span>
                <span className={s.chapterName}>
                  <span className={s.chapterNum}>{i + 1}</span>
                  {c.title}
                </span>
                <span className={s.chapterMeta}>
                  {playing ? <span className={s.chapterNow}>Playing</span> : null}
                  {span(length)}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
