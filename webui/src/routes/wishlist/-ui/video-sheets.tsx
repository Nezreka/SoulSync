import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { ActionMenu, type ActionMenuItem } from '@/routes/artist-detail/-ui/action-menu';

import type { VideoEpisode, VideoMovieItem, VideoSeason, VideoShowItem } from '../-video.types';

import {
  type VideoActionTarget,
  type VideoDiagKey,
  videoDiagnosticsQueryOptions,
  videoEpisodeInfoQueryOptions,
  videoShowInfoQueryOptions,
  youtubeVideoInfoQueryOptions,
} from '../-video.api';
import {
  VIDEO_STATUS,
  VIDEO_STUCK_ZONES,
  type VideoZoneKind,
  compactCount,
  describeVideoRow,
  diagModel,
  failWhy,
  fmtDate,
  fmtDuration,
  initials,
  isFailingItem,
  liveStatus,
  rowZone,
  showEpisodes,
  sized,
  videoZoneCopy,
  ytImg,
} from '../-video.helpers';
import { type VideoWishlistActions, targetKey } from '../-video.use-actions';
import { NebulaSheet } from './nebula-parts';

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** open a title, person or channel page on the video side. */
export function openVideoDetail(kind: string, id: number | string, source?: string): void {
  window.SoulSyncVideo?.openDetail?.({ kind, id, source });
}

/* ── the diagnostics drawer ────────────────────────────────────────────── */

/** "why is this stuck", in place: ids, landing folder, and every release the last search judged. */
export function VideoDiagDrawer({ diagKey }: { diagKey: VideoDiagKey }) {
  const query = useQuery(videoDiagnosticsQueryOptions(diagKey));
  if (query.isPending)
    return <div className="wlp-diag wlp-diag--note">Reading the last search…</div>;
  if (query.isError)
    return <div className="wlp-diag wlp-diag--note">Could not read the diagnostics.</div>;
  if (!query.data?.success)
    return <div className="wlp-diag wlp-diag--note">Nothing recorded for this row yet.</div>;
  const model = diagModel(query.data);
  return (
    <div className="wlp-diag" data-testid="video-diag">
      <dl className="wlp-diag-grid">
        {model.lines.map((line, i) => (
          <div
            key={`${line.k}-${i}`}
            className={`wlp-diag-line${line.tone ? ` is-${line.tone}` : ''}`}
          >
            <dt>{line.k}</dt>
            <dd>{line.v}</dd>
          </div>
        ))}
      </dl>
      {model.sources.map((src) => (
        <div key={src.head} className="wlp-diag-src">
          <div className="wlp-diag-src-head">{src.head}</div>
          {src.releases.length ? (
            src.releases.map((rel, i) => (
              <div key={`${rel.title}-${i}`} className={`wlp-diag-rel${rel.ok ? ' is-ok' : ''}`}>
                <span className="wlp-diag-rel-t">{rel.title}</span>
                <span className="wlp-diag-rel-w">{rel.verdict}</span>
              </div>
            ))
          ) : (
            <div className="wlp-diag-rel is-none">
              no releases recorded — this row has not been searched since receipts were added
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/* ── shared bits ───────────────────────────────────────────────────────── */

function StatusPill({
  zone,
  label,
  title,
}: {
  zone: VideoZoneKind;
  label: string;
  title?: string;
}) {
  const tone = VIDEO_STUCK_ZONES.has(zone)
    ? ' is-stuck'
    : zone === 'downloading'
      ? ' is-live'
      : zone === 'done'
        ? ' is-done'
        : '';
  return (
    <span className={`wlp-sheet-pill${tone}`} title={title}>
      {label}
    </span>
  );
}

function MoreMenu({ label, items }: { label: string; items: ActionMenuItem[] }) {
  if (!items.length) return null;
  return (
    <ActionMenu
      className="wlp-menu"
      items={items}
      trigger={(props) => (
        <button
          type="button"
          className="wlp-sheet-x wlp-sheet-more"
          title={label}
          aria-label={label}
          {...props}
          ref={props.ref as (el: HTMLButtonElement | null) => void}
        >
          ⋯
        </button>
      )}
    />
  );
}

function stuckCallout(
  rows: { row: VideoEpisode | VideoMovieItem; muted?: boolean; youtube?: boolean }[],
  noun: [string, string],
): { headline: string; detail: string } | null {
  const stuck = rows
    .map((r) => describeVideoRow(r.row, { muted: r.muted, youtube: r.youtube }))
    .filter((d) => VIDEO_STUCK_ZONES.has(d.zone));
  if (stuck.length === 0) return null;
  if (stuck.length === 1) return { headline: stuck[0].headline, detail: stuck[0].detail };
  const byZone = new Map<VideoZoneKind, number>();
  for (const d of stuck) byZone.set(d.zone, (byZone.get(d.zone) ?? 0) + 1);
  const parts = [...byZone.entries()].map(
    ([zone, n]) => `${n} ${videoZoneCopy(zone).title.toLowerCase()}`,
  );
  return {
    headline: `${stuck.length} ${noun[1]} stuck`,
    detail: `${parts.join(', ')}. Open one to see what the last search found.`.replace(/^./, (c) =>
      c.toUpperCase(),
    ),
  };
}

/* ── movie ─────────────────────────────────────────────────────────────── */

export function VideoMovieSheet({
  movie,
  actions,
  onClose,
}: {
  movie: VideoMovieItem;
  actions: VideoWishlistActions;
  onClose: () => void;
}) {
  const [showDiag, setShowDiag] = useState(false);
  const target: VideoActionTarget = { scope: 'movie', tmdb_id: movie.tmdb_id };
  const st = liveStatus(movie);
  const desc = describeVideoRow(movie);
  const stuck = VIDEO_STUCK_ZONES.has(desc.zone);
  const owned = movie.library_id != null;
  const meta = [
    movie.year,
    movie.upgrade_from ? `⇪ from ${movie.upgrade_from}` : owned ? 'In library' : null,
    VIDEO_STATUS[st],
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <NebulaSheet
      label={`${movie.title} on the wishlist`}
      image={sized(movie.poster_url || '', 342)}
      avatarShape="poster"
      avatarFallback={initials(movie.title)}
      name={movie.title}
      meta={<span className={stuck ? 'wlp-sheet-stuck' : undefined}>{meta}</span>}
      callout={stuck ? { headline: desc.headline, detail: desc.detail } : null}
      onClose={onClose}
      closeLabel="Close movie panel"
      actions={
        <>
          {st !== 'downloading' ? (
            <button
              type="button"
              className="wlp-btn wlp-btn--primary wlp-sheet-primary"
              disabled={actions.isBusy(targetKey('hunt', target))}
              title="Search now, past the release window"
              onClick={() => void actions.searchNow(target)}
            >
              Search now
            </button>
          ) : null}
          {st !== 'downloading' ? (
            <button
              type="button"
              className="wlp-btn wlp-sheet-ghost"
              title="Manual search — pick a release"
              onClick={() => actions.pickMovie(movie)}
            >
              Pick a release
            </button>
          ) : null}
          {isFailingItem(movie) ? (
            <button
              type="button"
              className="wlp-btn wlp-sheet-ghost"
              disabled={actions.isBusy(targetKey('retry', target))}
              title="Clear the backoff and search every source now"
              onClick={() => void actions.retry(target)}
            >
              Retry with all sources
            </button>
          ) : null}
          <button
            type="button"
            className="wlp-btn wlp-sheet-ghost"
            onClick={() =>
              openVideoDetail(
                'movie',
                owned ? (movie.library_id as number) : movie.tmdb_id,
                owned ? 'library' : 'tmdb',
              )
            }
          >
            Open movie
          </button>
        </>
      }
    >
      {(Number(movie.search_attempts) || 0) > 0 ? (
        <section className="wlp-sheet-album">
          <button
            type="button"
            className="wlp-sheet-disclose"
            aria-expanded={showDiag}
            title={failWhy(movie, Number(movie.search_attempts) || 0)}
            onClick={() => setShowDiag((v) => !v)}
          >
            {showDiag ? 'Hide the last search' : 'What the last search found'}
          </button>
          {showDiag ? (
            <VideoDiagDrawer diagKey={{ kind: 'movie', tmdb_id: movie.tmdb_id }} />
          ) : null}
        </section>
      ) : null}
      <button
        type="button"
        className="wlp-sheet-remove"
        aria-label={`Remove ${movie.title} from the wishlist`}
        disabled={actions.isBusy(targetKey('rm', target))}
        onClick={() => void actions.remove(target)}
      >
        Remove from wishlist
      </button>
    </NebulaSheet>
  );
}

/* ── tv show + youtube channel ─────────────────────────────────────────── */

/**
 * one show's or channel's panel. a youtube channel is shaped like a show
 * (year = season, video = episode), so one panel serves both; the verbs differ.
 */
export function VideoShowSheet({
  show,
  actions,
  onClose,
}: {
  show: VideoShowItem;
  actions: VideoWishlistActions;
  onClose: () => void;
}) {
  const youtube = show.source === 'youtube';
  const img = (u: string | null | undefined) => (youtube ? ytImg(u) : u);
  const poster =
    show.poster_url || (youtube && show.seasons?.[0] ? show.seasons[0].poster_url : null);
  const eps = showEpisodes(show);
  const noun: [string, string] = youtube ? ['video', 'videos'] : ['episode', 'episodes'];
  const callout = stuckCallout(
    eps.map((row) => ({ row, muted: show.muted, youtube })),
    noun,
  );
  const stuckCount = eps.filter((e) =>
    VIDEO_STUCK_ZONES.has(rowZone(e, { muted: show.muted, youtube })),
  ).length;
  const showTarget: VideoActionTarget = { scope: 'show', tmdb_id: show.tmdb_id };
  const openPage = () =>
    youtube
      ? openVideoDetail('channel', show.youtube_id || '', 'youtube')
      : openVideoDetail(
          'show',
          show.library_id != null ? show.library_id : show.tmdb_id,
          show.library_id != null ? 'library' : 'tmdb',
        );

  return (
    <NebulaSheet
      label={`${show.title} on the wishlist`}
      image={sized(img(poster) || '', 342)}
      avatarFallback={initials(show.title)}
      name={show.title}
      meta={
        <>
          {plural(show.wanted, noun[0], noun[1])} wanted
          {show.done ? ` · ${show.done} done` : ''}
          {show.muted ? ' · not followed' : ''}
          {stuckCount > 0 ? <span className="wlp-sheet-stuck"> · {stuckCount} stuck</span> : null}
        </>
      }
      callout={callout}
      onClose={onClose}
      closeLabel={youtube ? 'Close channel panel' : 'Close show panel'}
      actions={
        youtube ? (
          <button
            type="button"
            className="wlp-btn wlp-btn--primary wlp-sheet-primary"
            onClick={openPage}
          >
            Open channel
          </button>
        ) : (
          <>
            <button
              type="button"
              className="wlp-btn wlp-btn--primary wlp-sheet-primary"
              disabled={actions.isBusy(targetKey('hunt', showTarget))}
              title="Search every wanted episode now, past the release window"
              onClick={() => void actions.searchNow(showTarget)}
            >
              {show.wanted === 1 ? 'Search now' : `Search all ${show.wanted} now`}
            </button>
            {stuckCount > 0 ? (
              <button
                type="button"
                className="wlp-btn wlp-sheet-ghost"
                disabled={actions.isBusy(targetKey('retry', showTarget))}
                title="Clear the backoff on every episode and search every source now"
                onClick={() => void actions.retry(showTarget)}
              >
                Retry with all sources
              </button>
            ) : null}
            <button type="button" className="wlp-btn wlp-sheet-ghost" onClick={openPage}>
              Open show
            </button>
          </>
        )
      }
    >
      {!youtube ? <ShowAbout show={show} /> : null}
      {(show.seasons || []).map((season) => (
        <SeasonBlock
          key={season.season_number}
          show={show}
          season={season}
          youtube={youtube}
          actions={actions}
        />
      ))}
      <button
        type="button"
        className="wlp-sheet-remove"
        aria-label={`Remove ${show.title} from the wishlist`}
        disabled={actions.isBusy(
          targetKey('rm', youtube ? `channel:${show.youtube_id}` : showTarget),
        )}
        onClick={() =>
          youtube
            ? void actions.removeChannel(show)
            : void actions.remove(
                showTarget,
                `Remove all ${plural(show.wanted, 'episode')} of "${show.title}" from the wishlist?`,
              )
        }
      >
        {youtube ? 'Unfollow and remove channel' : 'Remove show from wishlist'}
      </button>
    </NebulaSheet>
  );
}

/** the show's synopsis and cast, loaded when the panel opens. */
function ShowAbout({ show }: { show: VideoShowItem }) {
  const info = useQuery(videoShowInfoQueryOptions(show));
  const overview = info.data?.overview || '';
  const cast = (info.data?.cast || []).slice(0, 8);
  if (!overview && cast.length === 0) return null;
  return (
    <section className="wlp-sheet-about">
      {overview ? <p className="wlp-sheet-overview">{overview}</p> : null}
      {cast.length ? <CastRow cast={cast} /> : null}
    </section>
  );
}

function CastRow({
  cast,
}: {
  cast: { tmdb_id?: number; name: string; character?: string | null; photo?: string | null }[];
}) {
  return (
    <div className="wlp-cast" aria-label="Cast">
      {cast.map((c) => (
        <button
          key={`${c.tmdb_id ?? c.name}`}
          type="button"
          className="wlp-cast-member"
          title={`${c.name}${c.character ? ` — ${c.character}` : ''}`}
          onClick={() => c.tmdb_id != null && openVideoDetail('person', c.tmdb_id, 'tmdb')}
        >
          <span className="wlp-cast-img">
            <span className="wlp-cast-ini">{initials(c.name)}</span>
            {c.photo ? <img src={c.photo} alt="" loading="lazy" /> : null}
          </span>
          <span className="wlp-cast-name">{c.name}</span>
        </button>
      ))}
    </div>
  );
}

function SeasonBlock({
  show,
  season,
  youtube,
  actions,
}: {
  show: VideoShowItem;
  season: VideoSeason;
  youtube: boolean;
  actions: VideoWishlistActions;
}) {
  const img = (u: string | null | undefined) => (youtube ? ytImg(u) : u);
  const posterUrl = season.poster_url || show.poster_url || null;
  const n = season.episodes.length;
  const name = youtube
    ? season.season_number
      ? String(season.season_number)
      : 'Undated'
    : `Season ${season.season_number}`;
  const seasonTarget: VideoActionTarget = {
    scope: 'season',
    tmdb_id: show.tmdb_id,
    season_number: season.season_number,
  };
  const ytIds = season.episodes.map((e) => e.source_id).filter((x): x is string => Boolean(x));

  return (
    <section className="wlp-sheet-album" data-season={season.season_number}>
      <div className="wlp-sheet-album-head">
        {posterUrl ? (
          <img
            className="wlp-sheet-cover"
            src={sized(img(posterUrl), 342) || ''}
            alt=""
            loading="lazy"
          />
        ) : (
          <div className="wlp-sheet-cover wlp-sheet-cover--ph" aria-hidden="true">
            ▶
          </div>
        )}
        <div className="wlp-sheet-album-id">
          <div className="wlp-sheet-album-name">{name}</div>
          <div className="wlp-sheet-album-count">{plural(n, youtube ? 'video' : 'episode')}</div>
        </div>
        {!youtube ? (
          <button
            type="button"
            className="wlp-sheet-chip"
            disabled={actions.isBusy(targetKey('hunt', seasonTarget))}
            title="Search this season now"
            aria-label={`Search ${name} now`}
            onClick={() => void actions.searchNow(seasonTarget)}
          >
            Search
          </button>
        ) : null}
        {!youtube ? (
          <MoreMenu
            label={`More for ${name}`}
            items={[
              {
                key: 'pick',
                label: 'Pick a release',
                title: 'Manual search — pick a release',
                onSelect: () => actions.pickEpisodes(show, season.season_number, null),
              },
            ]}
          />
        ) : null}
        <button
          type="button"
          className="wlp-sheet-x"
          title="Remove from the wishlist"
          aria-label={`Remove ${name} from the wishlist`}
          onClick={() =>
            youtube
              ? void actions.removeYoutubeVideos(
                  `year:${show.youtube_id}:${season.season_number}`,
                  ytIds,
                  `Remove ${plural(ytIds.length, 'video')} from ${name} on the wishlist?`,
                )
              : void actions.remove(
                  seasonTarget,
                  `Remove ${plural(n, 'episode')} of ${name} from the wishlist?`,
                )
          }
        >
          ✕
        </button>
      </div>
      {season.episodes.map((ep) => (
        <EpisodeRow
          key={youtube ? ep.source_id : ep.episode_number}
          show={show}
          season={season}
          ep={ep}
          youtube={youtube}
          actions={actions}
        />
      ))}
    </section>
  );
}

function EpisodeRow({
  show,
  season,
  ep,
  youtube,
  actions,
}: {
  show: VideoShowItem;
  season: VideoSeason;
  ep: VideoEpisode;
  youtube: boolean;
  actions: VideoWishlistActions;
}) {
  const [open, setOpen] = useState(false);
  const img = (u: string | null | undefined) => (youtube ? ytImg(u) : u);
  const title = ep.title || (youtube ? 'Untitled' : `Episode ${ep.episode_number}`);
  const st = liveStatus(ep, show.muted);
  const desc = describeVideoRow(ep, { muted: show.muted, youtube });
  const stuck = VIDEO_STUCK_ZONES.has(desc.zone);
  const fails = Number(ep.search_attempts) || 0;
  const date = fmtDate(ep.air_date);
  const label = youtube
    ? date || 'Video'
    : `S${season.season_number}·E${ep.episode_number}${date ? ` · ${date}` : ''}`;
  const target: VideoActionTarget = {
    scope: 'episode',
    tmdb_id: show.tmdb_id,
    season_number: season.season_number,
    episode_number: ep.episode_number,
  };
  const pill = stuck
    ? youtube && ep.unavailable
      ? 'Unavailable'
      : `${fails} tries`
    : (VIDEO_STATUS[st] ?? 'Wanted');
  const why = stuck || desc.zone === 'awaiting' ? desc.headline : desc.detail;

  const more: ActionMenuItem[] = youtube
    ? []
    : [
        ...(st !== 'downloading'
          ? [
              {
                key: 'pick',
                label: 'Pick a release',
                title: 'Manual search — pick a release',
                onSelect: () => actions.pickEpisodes(show, season.season_number, ep.episode_number),
              },
            ]
          : []),
        ...(fails >= 3 && st !== 'downloading'
          ? [
              {
                key: 'retry',
                label: 'Retry with all sources',
                onSelect: () => void actions.retry(target),
              },
            ]
          : []),
      ];

  return (
    <div
      className={`wlp-sheet-track wlp-video-ep${stuck ? ' is-stuck' : ''}${open ? ' is-open' : ''}`}
    >
      <div className="wlp-video-ep-row">
        <button
          type="button"
          className="wlp-video-ep-main"
          aria-expanded={open}
          title={stuck ? failWhy(ep, fails) : undefined}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="wlp-video-ep-thumb">
            {ep.still_url ? (
              <img src={sized(img(ep.still_url), 342) || ''} alt="" loading="lazy" />
            ) : null}
          </span>
          {/* the title gets the whole first line; the state rides on the second,
              with the episode label and the reason. */}
          <span className="wlp-sheet-track-main">
            <span className="wlp-sheet-track-name" title={title}>
              {title}
            </span>
            <span className="wlp-video-ep-sub">
              <StatusPill
                zone={desc.zone}
                label={pill}
                title={stuck ? failWhy(ep, fails) : undefined}
              />
              <span className="wlp-sheet-track-why">
                {label}
                {why ? ` · ${why}` : ''}
              </span>
            </span>
          </span>
        </button>
        {st !== 'downloading' ? (
          youtube ? (
            <button
              type="button"
              className="wlp-sheet-chip"
              disabled={actions.isBusy(targetKey('yt-now', ep.source_id || ''))}
              title="Download this video now, ignoring the retry wait"
              aria-label={`Download ${title} now`}
              onClick={() => void actions.youtubeNow(show, ep)}
            >
              Download
            </button>
          ) : (
            <button
              type="button"
              className="wlp-sheet-chip"
              disabled={actions.isBusy(targetKey('hunt', target))}
              title="Search now"
              aria-label={`Search ${label} now`}
              onClick={() => void actions.searchNow(target)}
            >
              Search
            </button>
          )
        ) : null}
        <MoreMenu label={`More for ${title}`} items={more} />
        <button
          type="button"
          className="wlp-sheet-x"
          title="Remove"
          aria-label={`Remove ${title}`}
          onClick={() =>
            youtube
              ? void actions.removeYoutubeVideos(
                  `video:${ep.source_id}`,
                  ep.source_id ? [ep.source_id] : [],
                )
              : void actions.remove(target)
          }
        >
          ✕
        </button>
      </div>
      {open ? (
        youtube ? (
          <YoutubeVideoDetail ep={ep} />
        ) : (
          <EpisodeDetail show={show} season={season} ep={ep} stuck={stuck || fails > 0} />
        )
      ) : null}
    </div>
  );
}

function EpisodeDetail({
  show,
  season,
  ep,
  stuck,
}: {
  show: VideoShowItem;
  season: VideoSeason;
  ep: VideoEpisode;
  stuck: boolean;
}) {
  const guests = useQuery(
    videoEpisodeInfoQueryOptions(show.tmdb_id, season.season_number, ep.episode_number),
  );
  const cast = (guests.data?.guest_stars || []).slice(0, 8);
  return (
    <div className="wlp-video-ep-detail">
      <p className="wlp-sheet-overview">{ep.overview || 'No synopsis for this episode.'}</p>
      {cast.length ? <CastRow cast={cast} /> : null}
      {stuck ? (
        <VideoDiagDrawer
          diagKey={{
            kind: 'episode',
            tmdb_id: show.tmdb_id,
            season_number: season.season_number,
            episode_number: ep.episode_number,
          }}
        />
      ) : null}
    </div>
  );
}

function YoutubeVideoDetail({ ep }: { ep: VideoEpisode }) {
  const info = useQuery(youtubeVideoInfoQueryOptions(ep.source_id || ''));
  const vd = info.data?.video;
  const bits = [
    fmtDate(vd?.published_at || ep.air_date),
    fmtDuration(vd?.duration_seconds),
    vd?.view_count != null && compactCount(vd.view_count)
      ? `${compactCount(vd.view_count)} views`
      : '',
  ].filter(Boolean);
  const desc = vd?.description || ep.overview || '';
  return (
    <div className="wlp-video-ep-detail">
      {bits.length ? <div className="wlp-video-eyebrow">{bits.join(' · ')}</div> : null}
      <p className="wlp-sheet-overview">
        {desc || (info.isPending ? 'Loading details…' : 'No description for this video.')}
      </p>
      {ep.last_refusal ? <p className="wlp-video-note">{ep.last_refusal}</p> : null}
      <a
        className="wlp-video-watch"
        href={`https://www.youtube.com/watch?v=${encodeURIComponent(ep.source_id || '')}`}
        target="_blank"
        rel="noopener noreferrer"
      >
        Watch on YouTube ↗
      </a>
    </div>
  );
}
