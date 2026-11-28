/**
 * the end of a video. three moments:
 *   * at the credits (when a marker says where they start) or near the end
 *     with something queued: the next one counts down, and what else you
 *     were in the middle of sits beside it (continue watching)
 *   * the end with nothing queued: watch it again, or pick up something you
 *     were in the middle of
 *   * a run of episodes nobody touched: "are you still watching?" before
 *     the next one plays
 */

import type { CSSProperties } from 'react';

import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import type { PlayerState } from '../../-engine/engine';
import type { ProgressRow } from '../../-player.types';

import { nextIndex } from '../../-engine/queue';
import { countdown, creditsUpNext, skipPrefs } from '../../-engine/skip';
import { remoteImage } from '../../-lib/images';
import { fraction } from '../../-lib/rails';
import { continueQuery, playerSettingsQuery } from '../../-player.api';
import { PlayIcon } from '../icons';
import { usePlayTitle } from './play-title';
import s from './playback.module.css';
import { getEngine } from './use-player';

/** the up-next card shows this long before the end */
export const UP_NEXT_S = 25;

/** what else you were in the middle of: not this, not this show */
function useOthers(state: PlayerState, enabled: boolean): ProgressRow[] {
  const rows = useQuery({ ...continueQuery(['movie', 'episode']), enabled });
  const item = state.item;
  return (rows.data ?? [])
    .filter(
      (r) =>
        !(r.item_type === item?.type && r.item_id === item?.id) &&
        !(r.item_type === 'episode' && item?.parentId && r.meta?.parent_id === item.parentId) &&
        (r.item_type === 'movie' || !!r.meta?.parent_id),
    )
    .slice(0, 4);
}

function Others({ rows, label }: { rows: ProgressRow[]; label: string }) {
  const playTitle = usePlayTitle();
  if (!rows.length) return null;
  const play = (r: ProgressRow) =>
    r.item_type === 'movie'
      ? void playTitle('movie', Number(r.item_id))
      : void playTitle('show', Number(r.meta?.parent_id), Number(r.item_id));
  return (
    <div className={s.others}>
      <span className={s.upNextLabel}>{label}</span>
      <div className={s.othersRow}>
        {rows.map((r) => {
          const part = fraction(r.position_seconds, r.duration_seconds);
          const art = remoteImage(r.meta?.backdrop ?? r.meta?.artwork);
          return (
            <button
              key={`${r.item_type}:${r.item_id}`}
              type="button"
              className={s.other}
              onClick={() => play(r)}
              data-nav
            >
              <span className={s.otherArt}>
                {art ? <img src={art} alt="" loading="lazy" /> : null}
                {part ? (
                  <span className={s.episodeBar}>
                    <span style={{ width: `${part * 100}%` }} />
                  </span>
                ) : null}
              </span>
              <span className={s.otherTitle}>{r.meta?.title ?? 'Untitled'}</span>
              {r.meta?.subtitle ? <span className={s.otherSub}>{r.meta.subtitle}</span> : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * the credits' up next: from where the credits start (a marker says), with
 * a countdown that starts when the card shows. a scene after the credits
 * holds it: the card says so and never plays on by itself.
 */
function useCreditsUpNext(state: PlayerState, hasNext: boolean) {
  const prefs = skipPrefs(useQuery(playerSettingsQuery()).data?.settings);
  const item = state.item;
  const key = item ? `${item.type}:${item.id}` : '';
  const credits = creditsUpNext({
    markers: state.tracks?.markers,
    position: state.position,
    isEpisode: item?.type === 'episode',
    hasNext,
    prefs,
  });
  const shownAt = useRef<{ key: string; at: number } | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const active = !!credits && dismissed !== key && state.status !== 'ended';
  if (active && shownAt.current?.key !== key) shownAt.current = { key, at: state.position };
  if (!credits && shownAt.current?.key === key && state.position < (shownAt.current?.at ?? 0) - 1) {
    shownAt.current = null; // went back before the credits: count again next time
  }
  const timer =
    active && credits && shownAt.current
      ? countdown(shownAt.current.at, state.position, credits.seconds)
      : null;
  const plays = !!credits && !credits.scene && state.autoplayNext;
  useEffect(() => {
    if (active && plays && timer && timer.left <= 0 && state.status === 'playing') {
      getEngine().finishAtCredits();
    }
  });
  return {
    active,
    credits,
    timer,
    plays,
    dismiss: () => setDismissed(key),
  };
}

export function EndScreen({ state }: { state: PlayerState }) {
  const to = nextIndex(state.queue, true);
  const hasNext = to !== null && to !== state.queue.index;
  const left = state.duration - state.position;
  const ended = state.status === 'ended';
  const nearEnd = !!state.duration && left <= UP_NEXT_S && left > 0;
  const auto = state.autoplayNext;
  const atCredits = useCreditsUpNext(state, hasNext);
  const showing =
    state.stillWatching ||
    (hasNext ? (nearEnd && !ended) || (ended && !auto) || atCredits.active : ended || nearEnd);
  const others = useOthers(state, showing);
  if (!showing) return null;

  if (state.stillWatching) {
    const show = state.item?.parentTitle;
    return (
      <div className={s.endScreen} role="dialog" aria-label="Are you still watching?">
        <p className={s.endTitle}>Are you still watching{show ? ` ${show}` : ''}?</p>
        <div className={s.endButtons}>
          <button
            type="button"
            className={s.btnLight}
            onClick={() => getEngine().keepWatching()}
            data-nav
          >
            Keep watching
          </button>
          <button type="button" className={s.btnGhost} onClick={() => getEngine().stop()} data-nav>
            I'm done
          </button>
        </div>
      </div>
    );
  }

  if (hasNext) {
    const next = state.queue.items[to];
    const credits = atCredits.active && !nearEnd ? atCredits : null;
    const scene = credits?.credits?.scene ?? null;
    const label = credits
      ? scene
        ? 'Up next, after the scene'
        : credits.plays && credits.timer
          ? `Up next in ${Math.ceil(credits.timer.left)}`
          : 'Up next'
      : auto && !ended
        ? `Up next in ${Math.ceil(left)}`
        : 'Up next';
    return (
      <div className={s.upNextWrap}>
        {others.length && !credits ? <Others rows={others} label="Or keep watching" /> : null}
        {credits ? (
          <div className={s.upNextAside}>
            {scene ? (
              <span className={s.skipHint}>There&apos;s a scene after the credits</span>
            ) : null}
            {scene ? (
              <button
                type="button"
                className={s.btnGhost}
                onClick={() => getEngine().seek(scene.start)}
                data-nav
              >
                Watch the scene
              </button>
            ) : (
              <button type="button" className={s.btnGhost} onClick={credits.dismiss} data-nav>
                Watch the credits
              </button>
            )}
          </div>
        ) : null}
        <div
          className={s.upNext}
          role="status"
          data-credits={credits ? 'true' : undefined}
          style={
            credits?.plays && credits.timer
              ? ({
                  '--up-fill': String(1 - credits.timer.left / credits.credits!.seconds),
                } as CSSProperties)
              : undefined
          }
        >
          {next.backdrop || next.artwork ? (
            <img src={(next.backdrop ?? next.artwork)!} alt="" />
          ) : null}
          <div className={s.upNextText}>
            <span className={s.upNextLabel}>{label}</span>
            <span className={s.upNextTitle}>{next.title}</span>
            {next.subtitle ? <span className={s.upNextSub}>{next.subtitle}</span> : null}
          </div>
          <button
            type="button"
            className={s.upNextPlay}
            onClick={() => getEngine().next()}
            data-nav
          >
            <PlayIcon /> Play now
          </button>
        </div>
      </div>
    );
  }

  // nothing queued: near the end, a quiet row; at the end, the whole screen
  if (!ended)
    return others.length ? (
      <div className={s.upNextWrap}>
        <Others rows={others} label="Keep watching" />
      </div>
    ) : null;
  return (
    <div className={s.endScreen} role="dialog" aria-label="Finished">
      <p className={s.endTitle}>{state.item?.title}</p>
      <div className={s.endButtons}>
        <button
          type="button"
          className={s.btnLight}
          onClick={() => state.item && getEngine().playItems([state.item])}
          data-nav
        >
          Watch again
        </button>
        <button type="button" className={s.btnGhost} onClick={() => getEngine().stop()} data-nav>
          Close
        </button>
      </div>
      <Others rows={others} label="Keep watching" />
    </div>
  );
}
