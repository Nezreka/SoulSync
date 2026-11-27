/**
 * "Skip intro" (recap, credits) in the corner while that span plays, by the
 * profile's settings: a button (ask), a button that fills as it counts down
 * and then skips by itself (auto, with "Watch it" to undo), or nothing.
 * credits with a scene after them say so, and skipping them lands on the
 * scene. S skips from the keyboard; on a remote the button is one more
 * stop for the arrows (it never takes focus itself, so space still pauses).
 */

import type { CSSProperties } from 'react';

import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import type { PlayerState } from '../../-engine/engine';

import { nextIndex } from '../../-engine/queue';
import {
  countdown,
  creditsUpNext,
  isFirstOfSitting,
  noteStarted,
  planFor,
  skipPrefs,
  UNDO_SECONDS,
} from '../../-engine/skip';
import { skippableAt } from '../../-engine/timeline';
import { playerSettingsQuery } from '../../-player.api';
import s from './playback.module.css';
import { getEngine } from './use-player';

const WORDS: Record<string, string> = {
  intro: 'the intro',
  recap: 'the recap',
  credits: 'the credits',
  preview: 'the preview',
};

export function SkipButton({ state }: { state: PlayerState }) {
  const settingsQuery = useQuery(playerSettingsQuery());
  const prefs = skipPrefs(settingsQuery.data?.settings);
  const item = state.item;
  const itemKey = item ? `${item.type}:${item.id}` : '';
  const markers = state.tracks?.markers;
  const skip = skippableAt(state.chapters, state.position, state.duration, markers);
  const spanKey = skip ? `${itemKey}:${skip.kind}:${skip.from}` : '';

  // spans someone chose to watch (undo, or went back into one) stay put
  const watched = useRef(new Set<string>());
  const counting = useRef<{ key: string; at: number } | null>(null);
  const skipped = useRef<string | null>(null);
  const [undo, setUndo] = useState<{ kind: string; from: number; key: string } | null>(null);

  useEffect(() => {
    if (itemKey && state.kind === 'video') noteStarted(itemKey);
  }, [itemKey, state.kind]);

  // a span skipped once and then played again was chosen
  useEffect(() => {
    if (spanKey && skipped.current === spanKey && undo?.key !== spanKey) {
      watched.current.add(spanKey);
    }
  }, [spanKey, undo]);

  useEffect(() => {
    if (!undo) return;
    const t = window.setTimeout(() => setUndo(null), UNDO_SECONDS * 1000);
    return () => window.clearTimeout(t);
  }, [undo]);

  const to = nextIndex(state.queue, true);
  const hasNext = to !== null && to !== state.queue.index;
  const upNextTakesCredits = !!creditsUpNext({
    markers,
    position: state.position,
    isEpisode: item?.type === 'episode',
    hasNext,
    prefs,
  });
  let plan = planFor(skip, prefs, {
    firstOfSitting: isFirstOfSitting(itemKey),
    choseToWatch: watched.current.has(spanKey),
  });
  if (skip?.kind === 'credits' && upNextTakesCredits) plan = 'none';
  // the profile's choice first (a "never" never flashes a button); a failed read is the defaults
  if (settingsQuery.isPending) plan = 'none';
  if (state.kind !== 'video' || state.status === 'error' || state.live) plan = 'none';

  if (plan === 'auto' && spanKey && counting.current?.key !== spanKey) {
    counting.current = { key: spanKey, at: state.position };
  }
  const timer =
    plan === 'auto' && counting.current ? countdown(counting.current.at, state.position) : null;

  const doSkip = (auto: boolean) => {
    if (!skip) return;
    skipped.current = spanKey;
    getEngine().seek(skip.to);
    setUndo(auto ? { kind: skip.kind, from: skip.from, key: spanKey } : null);
  };

  // the countdown ran out
  useEffect(() => {
    if (timer && timer.left <= 0 && state.status === 'playing' && skipped.current !== spanKey) {
      doSkip(true);
    }
  });

  // S skips; a remote's OK lands on the button (it takes focus)
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (plan === 'none' || !skip) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 's' && e.key !== 'S') return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || e.metaKey || e.ctrlKey || e.altKey) return;
      // a menu or a dialog over the player keeps its own keys
      if (document.querySelector('[role="menu"], [aria-modal="true"]')) return;
      e.preventDefault();
      doSkip(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const scene = skip?.kind === 'credits' ? skip.postCredits : null;
  return (
    <>
      {undo ? (
        <div className={s.skipToast} role="status">
          <span>Skipped {WORDS[undo.kind] ?? 'it'}</span>
          <button
            type="button"
            className={s.skipUndo}
            onClick={() => {
              watched.current.add(undo.key);
              skipped.current = null;
              getEngine().seek(undo.from);
              setUndo(null);
            }}
            data-nav
          >
            Watch it
          </button>
        </div>
      ) : null}
      {plan !== 'none' && skip ? (
        <div className={s.skipWrap}>
          {scene ? (
            <span className={s.skipHint}>There&apos;s a scene after the credits</span>
          ) : null}
          <div className={s.skipRow}>
            {timer ? (
              <button
                type="button"
                className={s.skipGhost}
                onClick={() => {
                  watched.current.add(spanKey);
                  counting.current = null;
                }}
                data-nav
              >
                Watch {WORDS[skip.kind]?.replace('the ', '') ?? 'it'}
              </button>
            ) : null}
            <button
              ref={button}
              type="button"
              className={s.skipMark}
              data-auto={timer ? 'true' : undefined}
              style={
                timer ? ({ '--skip-fill': String(timer.progress) } as CSSProperties) : undefined
              }
              onClick={() => doSkip(false)}
              aria-keyshortcuts="S"
              title={`${scene ? 'Skip to the scene after the credits' : skip.label} (S)`}
              data-nav
            >
              <span className={s.skipFill} aria-hidden="true" />
              <span className={s.skipLabel}>{scene ? 'Skip to the scene' : skip.label}</span>
              {timer ? (
                <span className={s.skipCount} aria-label={`skipping in ${Math.ceil(timer.left)}`}>
                  {Math.ceil(timer.left)}
                </span>
              ) : null}
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}
