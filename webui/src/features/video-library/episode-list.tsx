/**
 * a show's season: its bar and its episodes, and everything that can be done
 * to them. this was string-built by video-detail.js with the file cards
 * grafted in; it is one React tree now, so a season, an episode and a file
 * are managed in the same place they are read.
 *
 * the page still owns the data and the acquisition flows (grab, wishlist,
 * manual search, watched state): it hands a season over through
 * window.VideoLibraryTab.mountEpisodes and this calls back through
 * window.SoulSyncVideoDetail. the class names are the page's own (vd-*), so
 * the rows look exactly as they did.
 */

import {
  Fragment,
  type MouseEvent as ReactMouseEvent,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from 'react';

import { ActionMenu, type ActionMenuItem } from '@/routes/artist-detail/-ui/action-menu';

import type {
  EpisodeDownload,
  EpisodeListProps,
  LibraryEpisode,
  LibraryFile,
  LibrarySeason,
  PageEpisode,
  ShowLibrary,
} from './types';

import {
  type EpisodeExtra,
  fetchEpisodeExtra,
  setEpisodesMonitored,
  type WishEpisode,
  wishlistEpisodes,
} from './api';
import { EpisodeFiles } from './episode-files';
import { audioLabel, codecLabel, formatBytes, pad2, rangeLabel, resolutionTone } from './format';
import { invalidate, picked, showData, togglePicked } from './library-store';
import { bridge, refreshPage } from './library-tab';
import { DeleteDialog, RenameDialog, RenumberDialog } from './manage-dialogs';
import s from './video-library.module.css';

declare global {
  interface Window {
    VideoRequests?: { cardButton?: (item: Record<string, unknown>) => string };
    VideoIssues?: unknown;
  }
}

function toast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
  window.showToast?.(message, type);
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** how many guest faces an opened episode shows before folding the rest away */
const GUEST_VISIBLE = 8;

/* ── small things the page's own script used to do ────────────────────────── */

export function minutesLabel(minutes: number | null | undefined): string {
  if (!minutes) return '';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h}h${m ? ` ${m}m` : ''}` : `${m}m`;
}

/** ask the image proxy or TMDB for a picture near the size it is drawn at */
export function sizedArt(url: string, width: number): string {
  if (!url) return url;
  if (url.includes('/api/video/poster/') || url.includes('/api/video/backdrop/')) {
    return `${url}${url.includes('?') ? '&' : '?'}w=${width}`;
  }
  if (url.includes('image.tmdb.org')) {
    const bucket = width <= 185 ? 185 : width <= 342 ? 342 : width <= 500 ? 500 : 780;
    return url.replace(/\/t\/p\/[^/]+\//, `/t/p/w${bucket}/`);
  }
  return url;
}

export function resolutionName(resolution: string | null | undefined): string {
  if (!resolution) return '';
  const r = String(resolution).toLowerCase();
  if (r.includes('2160') || r === '4k') return '4K';
  if (r.includes('1080')) return '1080p';
  if (r.includes('720')) return '720p';
  if (r.includes('480') || r.includes('576')) return 'SD';
  return r.toUpperCase();
}

function aired(ep: PageEpisode): boolean {
  return !ep.air_date || String(ep.air_date).slice(0, 10) <= new Date().toISOString().slice(0, 10);
}

function bestFile(files: LibraryFile[]): LibraryFile | null {
  return files.find((f) => f.best) ?? files[0] ?? null;
}

/* ── one ⋯ menu, in the page's own button ─────────────────────────────────── */

function Dots({
  label,
  items,
  className = 'vd-ep-getbtn vd-pop-btn',
  onOpenChange,
}: {
  label: string;
  items: ActionMenuItem[];
  className?: string;
  onOpenChange?: (open: boolean) => void;
}) {
  if (!items.length) return null;
  return (
    <ActionMenu
      items={items}
      className={s.menu}
      onOpenChange={onOpenChange}
      trigger={(props) => (
        <button
          type="button"
          className={className}
          aria-label={label}
          title="More"
          ref={props.ref}
          onClick={props.onClick}
          aria-haspopup={props['aria-haspopup']}
          aria-expanded={props['aria-expanded']}
        >
          ⋯
        </button>
      )}
    />
  );
}

/* ── the file an episode has, as one quiet line on its row ────────────────── */

function FileLine({ episode }: { episode: LibraryEpisode }) {
  const f = bestFile(episode.files);
  if (!f) return null;
  const named =
    f.quality && f.quality !== f.resolution_label ? f.quality : codecLabel(f.video_codec);
  const facts = [named, rangeLabel(f.dynamic_range), audioLabel(f), formatBytes(f.size_bytes)]
    .filter(Boolean)
    .join(' · ');
  return (
    <div className="vd-ep-file">
      <span className="vd-ep-file-res" data-tone={resolutionTone(f.resolution_label)}>
        {f.resolution_label || '—'}
      </span>
      <span className="vd-ep-file-facts">{facts}</span>
      {f.below_cutoff ? (
        <span className="vd-ep-file-chip vd-ep-file-chip--warn">Below your target</span>
      ) : null}
      {episode.files.length > 1 ? (
        <span className="vd-ep-file-chip">{episode.files.length} copies</span>
      ) : null}
    </div>
  );
}

/* ── an opened episode: guest stars, links, report ────────────────────────── */

function EpisodeExtraPanel({
  show,
  season,
  episode,
}: {
  show: EpisodeListProps['show'];
  season: number;
  episode: PageEpisode;
}) {
  const [extra, setExtra] = useState<EpisodeExtra | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => {
    if (!show.tmdbId) return;
    let live = true;
    fetchEpisodeExtra(show.tmdbId, season, episode.episode_number)
      .then((data) => live && setExtra(data))
      .catch(() => live && setExtra({}));
    return () => {
      live = false;
    };
  }, [show.tmdbId, season, episode.episode_number]);

  const report =
    window.VideoIssues && show.source !== 'tmdb' && episode.id != null ? (
      <button
        type="button"
        className="vi-ep-report"
        data-vi-report-episode={episode.id}
        data-vi-name={`${show.title} S${pad2(season)}E${pad2(episode.episode_number)}`}
        data-vi-meta={episode.title || ''}
      >
        ⚑ Report a problem
      </button>
    ) : null;

  if (!show.tmdbId) {
    return (
      <div className="vd-ep-extra">
        <div className="vd-ep-extra-empty">No extra info.</div>
        {report}
      </div>
    );
  }
  if (!extra) {
    return (
      <div className="vd-ep-extra">
        <div className="vd-ep-extra-empty">Loading…</div>
      </div>
    );
  }

  // the row above already prints this description; saying it twice doubled
  // the panel's height
  const overview =
    extra.overview && extra.overview.trim() !== (episode.overview || '').trim()
      ? extra.overview
      : '';
  const guests = extra.guest_stars ?? [];
  // a preview page keeps everything in the app
  const links =
    show.source === 'tmdb'
      ? []
      : [
          {
            label: 'TMDB',
            url: `https://www.themoviedb.org/tv/${show.tmdbId}/season/${season}/episode/${episode.episode_number}`,
          },
          ...(extra.imdb_id
            ? [{ label: 'IMDb', url: `https://www.imdb.com/title/${extra.imdb_id}/` }]
            : []),
        ];
  const hasBody = Boolean(overview) || guests.length > 0;
  return (
    <div className="vd-ep-extra">
      {extra.still_url ? (
        <img className="vd-ep-extra-still" src={extra.still_url} alt="" loading="lazy" />
      ) : null}
      <div className="vd-ep-extra-body">
        {overview ? <p className="vd-ep-extra-ov">{overview}</p> : null}
        {guests.length ? (
          <>
            <div className="vd-ep-extra-gh">Guest stars</div>
            <div className={`vd-ep-guests${all ? ' vd-ep-guests--all' : ''}`}>
              {guests.map((guest, index) => {
                const inner = (
                  <>
                    {guest.photo ? (
                      <img
                        className="vd-guest-photo"
                        src={guest.photo}
                        alt=""
                        loading="lazy"
                        onError={(e) => {
                          e.currentTarget.style.visibility = 'hidden';
                        }}
                      />
                    ) : (
                      <span className="vd-guest-photo vd-guest-photo--ph">
                        {(guest.name || '?').charAt(0)}
                      </span>
                    )}
                    <span className="vd-guest-name">{guest.name}</span>
                    {guest.character ? (
                      <span className="vd-guest-char">{guest.character}</span>
                    ) : null}
                  </>
                );
                // the page's own click handler opens a person from this link
                return guest.tmdb_id ? (
                  <a
                    key={`${guest.tmdb_id}-${index}`}
                    className="vd-guest"
                    href={`/video-detail/tmdb/person/${guest.tmdb_id}`}
                    data-vd-person={guest.tmdb_id}
                  >
                    {inner}
                  </a>
                ) : (
                  <div key={`${guest.name}-${index}`} className="vd-guest">
                    {inner}
                  </div>
                );
              })}
              {guests.length > GUEST_VISIBLE && !all ? (
                <button type="button" className="vd-guest-more" onClick={() => setAll(true)}>
                  +{guests.length - GUEST_VISIBLE} more
                </button>
              ) : null}
            </div>
          </>
        ) : null}
        {hasBody ? null : <div className="vd-ep-extra-empty">No extra info.</div>}
        {links.length || report ? (
          <div className="vd-ep-foot">
            {links.length ? (
              <div className="vd-ep-links">
                {links.map((link) => (
                  <a
                    key={link.label}
                    className="vd-ep-link"
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={`${link.label} episode`}
                  >
                    {link.label} ↗
                  </a>
                ))}
              </div>
            ) : null}
            {report}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* ── a live grab on a row ─────────────────────────────────────────────────── */

function DownloadState({ download, done }: { download?: EpisodeDownload; done: boolean }) {
  if (done) return <span className="vd-ep-dl-txt vd-ep-dl-txt--done">✓ Downloaded</span>;
  if (!download) return null;
  const pct = Math.max(0, Math.min(100, download.progress || 0));
  const label =
    download.status === 'downloading'
      ? `${pct}%`
      : download.status === 'searching'
        ? 'Searching'
        : 'Queued';
  return (
    <>
      <span className="vd-ep-dl-bar">
        <span style={{ width: `${pct}%` }} />
      </span>
      <span className="vd-ep-dl-txt">{label}</span>
    </>
  );
}

/* ── one episode ──────────────────────────────────────────────────────────── */

interface RowContext {
  show: EpisodeListProps['show'];
  season: number;
  seasonHunted: boolean;
  library: ShowLibrary | null;
  canDownload: boolean;
  nextUp: EpisodeListProps['nextUp'];
}

function EpisodeRow({
  ep,
  lib,
  ctx,
  open,
  onToggle,
  ticked,
  download,
  done,
  onHunt,
}: {
  ep: PageEpisode;
  /** the episode as the library knows it: its files, and whether it is hunted */
  lib: LibraryEpisode | null;
  ctx: RowContext;
  open: boolean;
  onToggle: () => void;
  ticked: boolean;
  download?: EpisodeDownload;
  done: boolean;
  onHunt: (episode: LibraryEpisode, on: boolean) => void;
}) {
  const { show, season, library, canDownload } = ctx;
  const b = bridge();
  // the row's actions hide until it is hovered; an open menu keeps them there
  const [menuOpen, setMenuOpen] = useState(false);
  const isLibrary = show.source === 'library';
  const hasFiles = Boolean(lib?.files.length);
  const canManage = Boolean(library?.can_manage);
  const inProgress = !ep.watched && (ep.view_offset_ms || 0) > 0 && Boolean(ep.runtime_minutes);
  const progress = inProgress
    ? Math.max(
        2,
        Math.min(
          98,
          Math.round(((ep.view_offset_ms || 0) / ((ep.runtime_minutes || 1) * 60000)) * 100),
        ),
      )
    : 0;
  const isNext =
    ctx.nextUp?.season_number === season && ctx.nextUp?.episode_number === ep.episode_number;
  const addedAt =
    ep.owned && !ep.watched && ep.added_at
      ? Date.parse(String(ep.added_at).replace(' ', 'T'))
      : Number.NaN;
  const isNew = !Number.isNaN(addedAt) && Date.now() - addedAt < 7 * 86400000;

  const meta: string[] = [];
  const runtime = minutesLabel(ep.runtime_minutes);
  if (runtime) meta.push(runtime);
  if (ep.air_date) meta.push(ep.air_date);
  // the file line says the resolution once the library data is in
  if (ep.owned && ep.resolution && !hasFiles) meta.push(resolutionName(ep.resolution));
  if (ep.rating) meta.push(`★ ${Math.round(ep.rating * 10) / 10}`);

  const stillSrc =
    show.source === 'tmdb'
      ? ep.still_url || ''
      : ep.has_still && ep.id != null
        ? `/api/video/poster/episode/${ep.id}`
        : '';
  const code = `S${pad2(season)}E${pad2(ep.episode_number)}`;
  const title = ep.title || `Episode ${ep.episode_number}`;
  // a season that is hunted with this one episode left out says so on the row
  const notHunted = Boolean(lib) && ctx.seasonHunted && lib?.monitored === false;

  const watchedItem: ActionMenuItem[] =
    isLibrary && ep.id != null && ep.owned && b.setWatched
      ? [
          {
            key: 'watched',
            label: ep.watched ? 'Mark unwatched' : 'Mark watched',
            onSelect: () => b.setWatched?.('episode', Number(ep.id), !ep.watched),
          },
        ]
      : [];
  const items: ActionMenuItem[] = [...watchedItem];
  if (canDownload) {
    if (ep.owned) {
      items.push({
        key: 'again',
        label: 'Download again (upgrade)',
        onSelect: () => b.grabEpisode?.(ep.episode_number),
      });
    }
    items.push({
      key: 'search',
      label: 'Manual search',
      onSelect: () => b.manualSearch?.('episode', season, ep.episode_number),
    });
    items.push({
      key: 'wish',
      label: ep.owned ? 'Wishlist for an upgrade' : 'Add to wishlist',
      onSelect: () => b.wishEpisode?.(ep.episode_number),
    });
    if (ep.id != null && ep.owned && isLibrary && b.subtitleSearch) {
      items.push({
        key: 'subs',
        label: 'Search subtitles',
        onSelect: () => b.subtitleSearch?.('episode', Number(ep.id), title, code),
      });
    }
  }
  if (canManage && lib) {
    items.push({
      key: 'hunt',
      label: lib.monitored ? 'Stop hunting this episode' : 'Hunt this episode',
      title: lib.monitored
        ? 'SoulSync stops looking for this episode. Nothing on disk changes.'
        : 'SoulSync looks for this episode again when it is missing',
      onSelect: () => onHunt(lib, !lib.monitored),
    });
  }

  let get = null;
  if (!canDownload) {
    // a profile that can't download asks for a missing episode instead
    const html =
      !ep.owned && show.tmdbId && window.VideoRequests?.cardButton
        ? window.VideoRequests.cardButton({
            kind: 'episode',
            tmdbId: show.tmdbId,
            season,
            episode: ep.episode_number,
            title: show.title,
            year: show.year,
            poster: show.posterUrl,
          })
        : '';
    get = html ? (
      <div
        className="vd-ep-get"
        // the request button is the requests page's own markup, built there
        dangerouslySetInnerHTML={{ __html: html }}
      />
    ) : watchedItem.length ? (
      <div className="vd-ep-get">
        <span className="vd-pop">
          <Dots
            label={`More for episode ${ep.episode_number}`}
            items={watchedItem}
            onOpenChange={setMenuOpen}
          />
        </span>
      </div>
    ) : null;
  } else {
    const busy = Boolean(download) && !done;
    get = (
      <div
        className={`vd-ep-get${done ? ' vd-ep-get--done' : busy ? ' vd-ep-get--busy' : ''}`}
        data-ep-get={ep.episode_number}
      >
        <span className="vd-ep-dl">
          <DownloadState download={download} done={done} />
        </span>
        {!ep.owned && aired(ep) ? (
          <button
            type="button"
            className="vd-ep-getbtn vd-ep-grab"
            title="Auto-search & download this episode"
            aria-label="Get episode"
            disabled={busy}
            onClick={() => b.grabEpisode?.(ep.episode_number)}
          >
            ⭳
          </button>
        ) : null}
        <span className="vd-pop">
          <Dots
            label={`More for episode ${ep.episode_number}`}
            items={items}
            onOpenChange={setMenuOpen}
          />
        </span>
      </div>
    );
  }

  const classes = [
    'vd-ep',
    ep.owned ? 'vd-ep--owned' : 'vd-ep--missing',
    ep.watched ? 'vd-ep--watched' : '',
    isNext ? 'vd-ep--next' : '',
    open ? 'vd-ep--open' : '',
    menuOpen ? 'vd-ep--menu' : '',
  ]
    .filter(Boolean)
    .join(' ');

  // the row is one big toggle; its own controls are not
  const onRowClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest('.vd-ep-pick, .vd-ep-get, a, button, input')) return;
    onToggle();
  };

  return (
    <Fragment>
      <div className={classes} data-ep-key={`${season}_${ep.episode_number}`} onClick={onRowClick}>
        <div className="vd-ep-index">
          {ep.episode_number != null ? ep.episode_number : ''}
          {hasFiles && canManage && ep.id != null ? (
            <label className="vd-ep-pick" title="Select this episode">
              <input
                type="checkbox"
                checked={ticked}
                aria-label={`Select episode ${ep.episode_number}`}
                onChange={(e) => togglePicked(Number(ep.id), e.target.checked)}
              />
            </label>
          ) : null}
        </div>
        <div className="vd-ep-thumb">
          {stillSrc ? (
            <img
              className="vd-ep-still"
              src={sizedArt(stillSrc, 342)}
              alt=""
              loading="lazy"
              onError={(e) => {
                e.currentTarget.style.display = 'none';
              }}
            />
          ) : null}
          <span className="vd-ep-thumb-ic">▶</span>
          {inProgress ? (
            <span className="vd-ep-prog">
              <span className="vd-ep-prog-fill" style={{ width: `${progress}%` }} />
            </span>
          ) : null}
        </div>
        <div className="vd-ep-info">
          <div className="vd-ep-top">
            {ep.watched ? (
              <span className="vd-ep-check" title="Watched">
                ✓
              </span>
            ) : null}
            {isNext ? (
              <span className="vd-ep-next-chip">{inProgress ? 'Resume' : 'Next up'}</span>
            ) : null}
            {isNew ? (
              <span className="vd-ep-new-chip" title="Recently added">
                New
              </span>
            ) : null}
            <span className="vd-ep-title">{title}</span>
            {meta.length ? <span className="vd-ep-rt">{meta.join(' · ')}</span> : null}
            {notHunted ? (
              <span
                className="vd-ep-file-chip"
                title="SoulSync is not looking for this episode. Its menu turns the hunt back on."
              >
                Not hunted
              </span>
            ) : null}
          </div>
          {ep.overview ? <p className="vd-ep-desc">{ep.overview}</p> : null}
          {lib && hasFiles ? <FileLine episode={lib} /> : null}
        </div>
        {/* an owned row still emits the cell, or its actions would sit one
            column left of a missing row's */}
        {ep.owned ? (
          (ep.versions || 0) > 1 ? (
            <div className="vd-ep-badge vd-ep-badge--versions">×{ep.versions}</div>
          ) : (
            <div className="vd-ep-badge vd-ep-badge--none" aria-hidden="true" />
          )
        ) : aired(ep) ? (
          <div className="vd-ep-badge">Missing</div>
        ) : (
          <div className="vd-ep-badge vd-ep-badge--soon">Upcoming</div>
        )}
        {get}
        <span className="vd-ep-chev" aria-hidden="true">
          ⌄
        </span>
      </div>
      {hasFiles && open && ep.id != null ? (
        <div className="vd-ep-files">
          <EpisodeFiles showId={show.id} season={season} episodeId={Number(ep.id)} />
        </div>
      ) : null}
      {open ? <EpisodeExtraPanel show={show} season={season} episode={ep} /> : null}
    </Fragment>
  );
}

/* ── the season ───────────────────────────────────────────────────────────── */

function wishOf(showId: number, season: LibrarySeason, ep: LibraryEpisode): WishEpisode {
  return {
    season_number: season.season_number,
    episode_number: ep.episode_number,
    title: ep.title,
    air_date: ep.air_date,
    still_url: ep.has_still ? `/api/video/poster/episode/${ep.id}` : null,
    season_poster_url:
      season.has_poster && season.id != null
        ? `/api/video/poster/season/${season.id}`
        : `/api/video/poster/show/${showId}`,
  };
}

type Dialog =
  | { kind: 'rename'; fileIds: number[]; scope: string }
  | { kind: 'delete'; fileIds: number[]; scope: string }
  | { kind: 'renumber'; fileIds?: number[] };

export function EpisodeList(props: EpisodeListProps) {
  const { show, season, missingOnly, canDownload, canGrab, downloads, finished } = props;
  const n = season.season_number;
  const b = bridge();
  const shared = useSyncExternalStore(showData.subscribe, showData.get);
  const library = show.source === 'library' && shared && shared.id === show.id ? shared : null;
  const pickedIds = useSyncExternalStore(picked.subscribe, picked.get);
  const [openKeys, setOpenKeys] = useState<ReadonlySet<string>>(() => new Set());
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const libSeason = useMemo(
    () => library?.seasons.find((x) => x.season_number === n) ?? null,
    [library, n],
  );
  const libEpisodes = useMemo(() => {
    const map = new Map<number, LibraryEpisode>();
    for (const e of libSeason?.episodes ?? []) map.set(e.id, e);
    return map;
  }, [libSeason]);

  // ticks belong to the season they were made in
  useEffect(() => {
    picked.set([]);
  }, [show.id, n]);

  const canManage = Boolean(library?.can_manage);
  const isLibrary = show.source === 'library';
  const reload = () => invalidate('show', show.id);

  const episodes = missingOnly ? season.episodes.filter((e) => !e.owned) : season.episodes;
  const seasonMissing = season.episodes.filter((e) => !e.owned);
  const canAcquire = seasonMissing.length > 0 && canDownload && canGrab;
  const hunted = libSeason
    ? libSeason.episodes.some((e) => e.monitored)
    : (Number(season.episode_monitored) || 0) > 0;
  const ownedEps = season.episodes.filter((e) => e.owned);
  const allWatched = ownedEps.length > 0 && ownedEps.every((e) => e.watched);
  const withFiles = (libSeason?.episodes ?? []).filter((e) => e.files.length);
  const seasonFileIds = withFiles.flatMap((e) => e.files.map((f) => f.id));
  const seasonName = n === 0 ? 'Specials' : `Season ${n}`;

  const run = async (key: string, work: () => Promise<unknown> | undefined) => {
    setBusy(key);
    try {
      await work();
    } finally {
      setBusy(null);
    }
  };

  const hunt = async (ids: number[], on: boolean) => {
    try {
      const res = await setEpisodesMonitored(show.id, ids, on);
      toast(
        on
          ? `Hunting ${plural(res.episodes, 'episode')} again`
          : `${plural(res.episodes, 'episode')} no longer hunted`,
      );
      reload();
    } catch (e) {
      toast((e as Error).message || 'Couldn’t change the hunt', 'error');
    }
  };

  /* the season's own menu */
  const menu: ActionMenuItem[] = [];
  if (isLibrary && season.id != null && ownedEps.length && b.setWatched) {
    menu.push({
      key: 'watched',
      label: allWatched ? 'Mark season unwatched' : 'Mark season watched',
      onSelect: () => b.setWatched?.('season', Number(season.id), !allWatched),
    });
  }
  if (canAcquire) {
    menu.push({
      key: 'search',
      label: 'Manual search',
      title: 'Pick releases for this season yourself',
      onSelect: () => b.manualSearch?.('season', n),
    });
    menu.push({
      key: 'wish',
      label: 'Wishlist season',
      title: 'Add every missing episode in this season to the wishlist',
      onSelect: () => b.wishlistSeason?.(n),
    });
  }
  if (isLibrary && show.tmdbId) {
    menu.push({
      key: 'clear',
      label: 'Clear failures',
      title: 'Clear the retry backoff on this season’s stalled wishlist rows and search again',
      onSelect: () => b.clearSeasonFailures?.(n),
    });
  }
  if (canManage && seasonFileIds.length) {
    menu.push({
      key: 'rename',
      label: 'Rename files…',
      title: 'See what each file in this season would be renamed to, then rename them',
      onSelect: () => setDialog({ kind: 'rename', fileIds: seasonFileIds, scope: seasonName }),
    });
    menu.push({
      key: 'renumber',
      label: 'Renumber episodes…',
      title: 'File this season’s files under other episodes, in order',
      onSelect: () => setDialog({ kind: 'renumber' }),
    });
    menu.push({
      key: 'delete',
      label: 'Delete files…',
      danger: true,
      title: 'See every file in this season, then remove them',
      onSelect: () => setDialog({ kind: 'delete', fileIds: seasonFileIds, scope: seasonName }),
    });
  }

  /* what is ticked */
  const pickedEpisodes = withFiles.filter((e) => pickedIds.includes(e.id));
  const pickedFiles = pickedEpisodes.flatMap((e) => e.files.map((f) => f.id));
  const pickedScope = plural(pickedEpisodes.length, 'episode');
  const allTicked = withFiles.length > 0 && pickedEpisodes.length === withFiles.length;
  const clearPicked = () => picked.set([]);
  const upgradePicked = () =>
    run('bulk', async () => {
      if (!show.tmdbId || !libSeason) return;
      try {
        await wishlistEpisodes(
          { tmdb_id: show.tmdbId, title: show.title, library_id: show.id },
          pickedEpisodes.map((e) => wishOf(show.id, libSeason, e)),
        );
        toast(
          `${pickedScope} on the wishlist and searching. A better copy replaces the file when it lands.`,
        );
        clearPicked();
      } catch (e) {
        toast((e as Error).message || 'Couldn’t start the search', 'error');
      }
    });
  const pickedHunted = pickedEpisodes.some((e) => e.monitored);

  const showBar = canAcquire || isLibrary;
  const status = canAcquire
    ? `${seasonMissing.length} missing`
    : `All ${season.episodes.length} here`;

  return (
    <div className={s.listScope}>
      {showBar ? (
        <div className="vd-season-actions">
          {canManage && withFiles.length ? (
            <label className={s.pickAll} title="Select every episode that has a file">
              <input
                type="checkbox"
                checked={allTicked}
                aria-label="Select every episode that has a file"
                onChange={(e) => picked.set(e.target.checked ? withFiles.map((x) => x.id) : [])}
              />
            </label>
          ) : null}
          <span className="vd-season-actions-count">{status}</span>
          {libSeason?.size_bytes ? (
            <span className="vd-season-lib">
              {formatBytes(libSeason.size_bytes)}
              {libSeason.res_mix.map((mix) => (
                <span
                  key={mix.label}
                  className="vd-ep-file-res"
                  data-tone={resolutionTone(mix.label)}
                >
                  {mix.label}
                  {libSeason.res_mix.length > 1 ? ` ${mix.count}` : ''}
                </span>
              ))}
            </span>
          ) : null}
          {canAcquire ? (
            <button
              type="button"
              className="discog-download-btn discog-btn-compact"
              title="Auto-search & download every missing episode in this season"
              disabled={busy === 'grab'}
              onClick={() => void run('grab', () => b.grabSeason?.(n))}
            >
              <span className="discog-btn-icon">⭳</span>
              <span className="discog-btn-text">
                {busy === 'grab' ? 'Searching…' : 'Grab season'}
              </span>
              <span className="discog-btn-shimmer" />
            </button>
          ) : null}
          {isLibrary ? (
            <button
              type="button"
              role="switch"
              aria-checked={hunted}
              className={`vd-season-switch${hunted ? ' vd-season-switch--on' : ''}`}
              disabled={busy === 'monitor'}
              title={
                hunted
                  ? 'Monitored: missing episodes are hunted. Click to stop hunting this season'
                  : 'Not monitored: SoulSync leaves this season alone. Click to hunt its missing episodes again'
              }
              onClick={() =>
                void run('monitor', async () => {
                  await b.setSeasonMonitored?.(n, !hunted);
                  reload();
                })
              }
            >
              <span className="vd-season-switch-track" aria-hidden="true" />
              <span className="vd-season-switch-label">Monitored</span>
            </button>
          ) : null}
          <span className="vd-pop">
            <Dots label="More for this season" items={menu} className="vd-pop-btn" />
          </span>
        </div>
      ) : null}

      {pickedEpisodes.length ? (
        <div className={s.selectBar} data-active="yes">
          <span className={s.selectAll}>
            {pickedScope} selected
            <button type="button" className={s.linkBtn} onClick={clearPicked}>
              Clear
            </button>
          </span>
          <div className={s.selectActions}>
            {show.tmdbId && canDownload ? (
              <button
                type="button"
                className={s.btn}
                disabled={busy === 'bulk'}
                onClick={() => void upgradePicked()}
              >
                Look for upgrades
              </button>
            ) : null}
            <button
              type="button"
              className={s.btn}
              onClick={() =>
                setDialog({ kind: 'rename', fileIds: pickedFiles, scope: pickedScope })
              }
            >
              Rename…
            </button>
            <button
              type="button"
              className={s.btn}
              onClick={() => setDialog({ kind: 'renumber', fileIds: pickedFiles })}
            >
              Renumber…
            </button>
            <button
              type="button"
              className={s.btn}
              disabled={busy === 'hunt'}
              onClick={() =>
                void run('hunt', async () => {
                  await hunt(
                    pickedEpisodes.map((e) => e.id),
                    !pickedHunted,
                  );
                  clearPicked();
                })
              }
            >
              {pickedHunted ? 'Stop hunting' : 'Hunt again'}
            </button>
            <button
              type="button"
              className={s.btnDanger}
              onClick={() =>
                setDialog({ kind: 'delete', fileIds: pickedFiles, scope: pickedScope })
              }
            >
              Delete…
            </button>
          </div>
        </div>
      ) : null}

      <div className="vd-ep-anim" key={`${show.id}:${n}:${missingOnly ? 'm' : 'a'}`}>
        {episodes.length ? (
          episodes.map((ep) => {
            const key = `${n}_${ep.episode_number}`;
            const lib = ep.id != null ? (libEpisodes.get(Number(ep.id)) ?? null) : null;
            return (
              <EpisodeRow
                key={key}
                ep={ep}
                lib={lib}
                ctx={{
                  show,
                  season: n,
                  seasonHunted: hunted,
                  library,
                  canDownload,
                  nextUp: props.nextUp,
                }}
                open={openKeys.has(key)}
                onToggle={() =>
                  setOpenKeys((prev) => {
                    const next = new Set(prev);
                    if (next.has(key)) next.delete(key);
                    else next.add(key);
                    return next;
                  })
                }
                ticked={ep.id != null && pickedIds.includes(Number(ep.id))}
                download={downloads[key]}
                done={Boolean(finished[key])}
                onHunt={(episode, on) => void hunt([episode.id], on)}
              />
            );
          })
        ) : (
          <div className="vd-ep-empty">No {missingOnly ? 'missing ' : ''}episodes here. 🎉</div>
        )}
      </div>

      {dialog?.kind === 'rename' ? (
        <RenameDialog
          kind="show"
          titleId={show.id}
          fileIds={dialog.fileIds}
          scope={dialog.scope}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            clearPicked();
            reload();
          }}
        />
      ) : null}
      {dialog?.kind === 'delete' ? (
        <DeleteDialog
          kind="show"
          titleId={show.id}
          fileIds={dialog.fileIds}
          scope={dialog.scope}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            clearPicked();
            reload();
            refreshPage();
          }}
        />
      ) : null}
      {dialog?.kind === 'renumber' ? (
        <RenumberDialog
          showId={show.id}
          season={n}
          fileIds={dialog.fileIds}
          onClose={() => setDialog(null)}
          onChanged={() => {
            clearPicked();
            reload();
            refreshPage();
          }}
        />
      ) : null}
    </div>
  );
}
