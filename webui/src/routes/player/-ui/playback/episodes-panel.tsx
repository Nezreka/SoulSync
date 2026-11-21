/**
 * a show's episodes without leaving the video: its seasons as tabs, each
 * episode with its picture, how far you got, and whether you've seen it.
 * picking one plays from there, the rest of the show queued after it.
 */

import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import type { PlayerState } from '../../-engine/engine';
import type { DetailSeason } from '../../-player.types';

import { episodeItems } from '../../-lib/play-items';
import { fraction, posterUrl } from '../../-lib/rails';
import { progressLookupQuery, showDetailQuery } from '../../-player.api';
import { CloseIcon } from './icons';
import s from './playback.module.css';
import { playItems } from './use-player';

function seasonName(x: DetailSeason) {
  return x.season_number === 0 ? 'Specials' : x.title || `Season ${x.season_number}`;
}

export function EpisodesPanel({ state, onClose }: { state: PlayerState; onClose: () => void }) {
  const item = state.item;
  const showId = Number(item?.parentId);
  const detail = useQuery({ ...showDetailQuery(showId), enabled: Number.isFinite(showId) });
  const seasons = useMemo(
    () =>
      (detail.data?.seasons ?? [])
        .filter((x) => x.episodes.some((e) => e.owned !== false))
        .sort((a, b) =>
          a.season_number === 0
            ? 1
            : b.season_number === 0
              ? -1
              : a.season_number - b.season_number,
        ),
    [detail.data],
  );
  const playingId = Number(item?.id);
  const playingSeason = seasons.find((x) => x.episodes.some((e) => e.id === playingId));
  const [picked, setPicked] = useState<number | null>(null);
  const season =
    seasons.find((x) => x.season_number === picked) ?? playingSeason ?? seasons[0] ?? null;
  const episodes = (season?.episodes ?? [])
    .slice()
    .sort((a, b) => a.episode_number - b.episode_number);
  const ids = episodes.map((e) => String(e.id));
  const progress = useQuery({ ...progressLookupQuery('episode', ids), enabled: ids.length > 0 });

  const play = (episodeId: number) => {
    const show = detail.data;
    if (!show) return;
    const queue = episodeItems({ id: showId, title: show.title }, seasons, episodeId);
    if (queue.length) playItems(queue);
    onClose();
  };

  return (
    <section className={s.episodes} aria-label="Episodes">
      <div className={s.episodesHead}>
        <span className={s.episodesTitle}>
          {detail.data?.title ?? item?.parentTitle ?? 'Episodes'}
        </span>
        <button
          type="button"
          className={s.ctl}
          aria-label="Close episodes"
          onClick={onClose}
          data-nav
        >
          <CloseIcon />
        </button>
      </div>
      {seasons.length > 1 ? (
        <div className={s.episodesSeasons} role="tablist" aria-label="Seasons">
          {seasons.map((x) => (
            <button
              key={x.id}
              type="button"
              role="tab"
              aria-selected={x === season}
              className={s.episodesSeason}
              onClick={() => setPicked(x.season_number)}
              data-nav
            >
              {seasonName(x)}
            </button>
          ))}
        </div>
      ) : null}
      {detail.isLoading ? <p className={s.episodesNote}>Loading episodes…</p> : null}
      {detail.isError ? (
        <p className={s.episodesNote}>Couldn't load this show's episodes.</p>
      ) : null}
      <ol className={s.episodesList}>
        {episodes.map((e) => {
          const owned = e.owned !== false;
          const row = progress.data?.[String(e.id)];
          const done = !!e.watched || !!row?.finished;
          const part =
            row && !row.finished ? fraction(row.position_seconds, row.duration_seconds) : undefined;
          const playing = e.id === playingId;
          return (
            <li key={e.id}>
              <button
                type="button"
                className={s.episode}
                aria-current={playing ? 'true' : undefined}
                disabled={!owned}
                title={owned ? undefined : 'Not in your library'}
                onClick={() => (playing ? onClose() : play(e.id))}
                data-nav
              >
                <span className={s.episodeArt}>
                  {e.has_still ? (
                    <img
                      src={posterUrl('episode', e.id, 300)}
                      alt=""
                      loading="lazy"
                      onError={(ev) => (ev.currentTarget.style.visibility = 'hidden')}
                    />
                  ) : null}
                  {part ? (
                    <span className={s.episodeBar}>
                      <span style={{ width: `${part * 100}%` }} />
                    </span>
                  ) : null}
                  {playing ? <span className={s.episodeNow}>Playing</span> : null}
                </span>
                <span className={s.episodeText}>
                  <span className={s.episodeName}>
                    {e.episode_number}. {e.title || `Episode ${e.episode_number}`}
                  </span>
                  <span className={s.episodeMeta}>
                    {e.runtime_minutes ? `${e.runtime_minutes}m` : null}
                    {done ? <span className={s.episodeSeen}>✓ Watched</span> : null}
                    {!owned ? <span>Not in your library</span> : null}
                  </span>
                  {e.overview ? <span className={s.episodeOverview}>{e.overview}</span> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
