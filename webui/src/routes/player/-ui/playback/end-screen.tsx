/**
 * the end of a video. three moments:
 *   * near the end with something queued: the next one counts down, and
 *     what else you were in the middle of sits beside it (continue watching)
 *   * the end with nothing queued: watch it again, or pick up something you
 *     were in the middle of
 *   * a run of episodes nobody touched: "are you still watching?" before
 *     the next one plays
 */

import { useQuery } from '@tanstack/react-query';

import type { PlayerState } from '../../-engine/engine';
import type { ProgressRow } from '../../-player.types';

import { nextIndex } from '../../-engine/queue';
import { remoteImage } from '../../-lib/images';
import { fraction } from '../../-lib/rails';
import { continueQuery } from '../../-player.api';
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

export function EndScreen({ state }: { state: PlayerState }) {
  const to = nextIndex(state.queue, true);
  const hasNext = to !== null && to !== state.queue.index;
  const left = state.duration - state.position;
  const ended = state.status === 'ended';
  const nearEnd = !!state.duration && left <= UP_NEXT_S && left > 0;
  const showing = state.stillWatching || (hasNext ? nearEnd && !ended : ended || nearEnd);
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
    return (
      <div className={s.upNextWrap}>
        {others.length ? <Others rows={others} label="Or keep watching" /> : null}
        <div className={s.upNext} role="status">
          {next.backdrop || next.artwork ? (
            <img src={(next.backdrop ?? next.artwork)!} alt="" />
          ) : null}
          <div className={s.upNextText}>
            <span className={s.upNextLabel}>Up next in {Math.ceil(left)}</span>
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
