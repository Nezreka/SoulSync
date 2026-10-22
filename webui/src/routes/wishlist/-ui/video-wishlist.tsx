import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { ActionMenu } from '@/routes/artist-detail/-ui/action-menu';

import type { VideoMedia, VideoMovieItem, VideoShowItem, VideoSort } from '../-video.types';

import {
  VIDEO_WISHLIST_QUERY_KEY,
  backfillMovieArt,
  backfillShowArt,
  videoWishlistCountsQueryOptions,
  videoWishlistListQueryOptions,
} from '../-video.api';
import {
  VIDEO_STUCK_ZONES,
  emptyTitle,
  failingOnlyMovies,
  failingOnlyShows,
  initials,
  isFailingItem,
  movieMeta,
  movieZone,
  moviesNeedArt,
  rowZone,
  showEpisodes,
  showMeta,
  showZone,
  showsNeedArt,
  sized,
  videoZones,
  ytImg,
} from '../-video.helpers';
import { VIDEO_SORTS } from '../-video.types';
import { VIDEO_WISHLIST_CHANGED, useVideoWishlistActions } from '../-video.use-actions';
import { artistHue, orbSizeClass } from '../-wishlist.helpers';
import { NebulaOrb, NebulaPoster, NebulaStage, NebulaZone } from './nebula-parts';
import { VideoMovieSheet, VideoShowSheet, openVideoDetail } from './video-sheets';

const TABS: { media: VideoMedia; label: string }[] = [
  { media: 'movies', label: 'Movies' },
  { media: 'tv', label: 'TV' },
  { media: 'youtube', label: 'YouTube' },
];

const EYEBROW: Record<VideoMedia, string> = { movies: 'Movies', tv: 'TV', youtube: 'YouTube' };

interface Props {
  profileId: number;
  media: VideoMedia;
  /** the filter box (URL ?q=), searched server-side. */
  query: string;
  failing: boolean;
  onMedia: (media: VideoMedia) => void;
  onQuery: (q: string) => void;
  onFailing: (on: boolean) => void;
}

/**
 * The video wishlist: movies, TV and YouTube, on the music wishlist's grammar.
 * Same hero, command bar, zones by what's wrong, and the same side panel.
 * Movies are posters (a film is a poster); shows and channels are orbs.
 */
export function VideoWishlist({
  profileId,
  media,
  query,
  failing,
  onMedia,
  onQuery,
  onFailing,
}: Props) {
  const queryClient = useQueryClient();
  const actions = useVideoWishlistActions(media);

  // the video sidebar reopens the wishlist on the tab you last had (video-side.js).
  useEffect(() => {
    try {
      window.sessionStorage.setItem('soulsync.videoWishlistTab', media);
    } catch {
      /* private mode: it opens on Movies */
    }
  }, [media]);

  // the server searches, so the box is debounced like the old page (250ms).
  const [debounced, setDebounced] = useState(query);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);

  const [sort, setSort] = useState<VideoSort>('added');
  // any change to what's listed starts again at page one.
  const listKey = `${media}|${debounced}|${sort}`;
  const [paging, setPaging] = useState({ key: listKey, page: 1 });
  const page = paging.key === listKey ? paging.page : 1;
  const setPage = (p: number) => setPaging({ key: listKey, page: p });

  // the open panel belongs to the tab it was opened on.
  const [open, setOpen] = useState<{ media: VideoMedia; key: string } | null>(null);
  const openKey = open?.media === media ? open.key : null;
  const setOpenKey = (key: string | null) => setOpen(key ? { media, key } : null);

  const list = useQuery(
    videoWishlistListQueryOptions(profileId, { media, search: debounced, sort, page }),
  );
  const counts = useQuery(videoWishlistCountsQueryOptions(profileId));

  // adds and removes elsewhere (the get modal, a detail page) refresh this page.
  useEffect(() => {
    const onChanged = () =>
      void queryClient.invalidateQueries({ queryKey: VIDEO_WISHLIST_QUERY_KEY });
    document.addEventListener(VIDEO_WISHLIST_CHANGED, onChanged);
    return () => document.removeEventListener(VIDEO_WISHLIST_CHANGED, onChanged);
  }, [queryClient]);

  const rawItems = useMemo(() => list.data?.items ?? [], [list.data]);
  const isMovies = media === 'movies';
  const movies = isMovies ? (rawItems as VideoMovieItem[]) : [];
  const shows = isMovies ? [] : (rawItems as VideoShowItem[]);

  // art that wasn't captured when the row was added: fetched once, then reload.
  const backfilled = useRef<Set<VideoMedia>>(new Set());
  useEffect(() => {
    if (!list.data?.success || backfilled.current.has(media) || media === 'youtube') return;
    const needs = isMovies ? moviesNeedArt(movies) : showsNeedArt(shows);
    if (!needs) return;
    backfilled.current.add(media);
    const go = isMovies ? backfillMovieArt : backfillShowArt;
    go()
      .then((res) => {
        if (res && (res.updated ?? 0) > 0)
          void queryClient.invalidateQueries({ queryKey: VIDEO_WISHLIST_QUERY_KEY });
      })
      .catch(() => {
        /* best-effort */
      });
  }, [list.data, media, isMovies, movies, shows, queryClient]);

  // the Failing filter keeps only what keeps failing (and, for a show, only its
  // failing episodes), the way the old chip did. youtube never had it.
  const failingOn = failing && media !== 'youtube';
  const visibleMovies = failingOn ? failingOnlyMovies(movies) : movies;
  const visibleShows = failingOn ? failingOnlyShows(shows) : shows;

  const failingCount = isMovies
    ? movies.filter((m) => isFailingItem(m)).length
    : media === 'tv'
      ? shows.reduce(
          (n, sh) => n + showEpisodes(sh).filter((e) => isFailingItem(e, sh.muted)).length,
          0,
        )
      : 0;
  const stuckCount = isMovies
    ? movies.filter((m) => VIDEO_STUCK_ZONES.has(movieZone(m))).length
    : shows.reduce(
        (n, sh) =>
          n +
          showEpisodes(sh).filter((e) =>
            VIDEO_STUCK_ZONES.has(rowZone(e, { muted: sh.muted, youtube: media === 'youtube' })),
          ).length,
        0,
      );

  const movieZones = useMemo(
    () => videoZones(visibleMovies, movieZone, media),
    [visibleMovies, media],
  );
  const showZones = useMemo(() => videoZones(visibleShows, showZone, media), [visibleShows, media]);

  const keyOfMovie = (m: VideoMovieItem) => `m:${m.tmdb_id}`;
  const keyOfShow = (s: VideoShowItem) =>
    s.source === 'youtube' ? `y:${s.youtube_id}` : `s:${s.tmdb_id}`;
  const openMovie = isMovies ? visibleMovies.find((m) => keyOfMovie(m) === openKey) : undefined;
  const openShow = !isMovies ? visibleShows.find((s) => keyOfShow(s) === openKey) : undefined;
  const toggle = (key: string) => setOpenKey(openKey === key ? null : key);

  const c = counts.data;
  const tabCount: Record<VideoMedia, number> = {
    movies: c?.movie ?? 0,
    tv: c?.show ?? 0,
    youtube: c?.video ?? 0,
  };
  const pagination = list.data?.pagination;
  const total = pagination?.total_count ?? 0;
  const tabHasItems =
    media === 'youtube' ? (c?.video ?? 0) > 0 || (c?.channel ?? 0) > 0 : tabCount[media] > 0;

  return (
    <>
      <div className="wlp-media-tabs" role="tablist" aria-label="Video wishlist">
        {TABS.map((t) => (
          <button
            key={t.media}
            type="button"
            role="tab"
            aria-selected={media === t.media}
            onClick={() => onMedia(t.media)}
          >
            {t.label}
            <span className="wlp-tab-count">{tabCount[t.media]}</span>
          </button>
        ))}
      </div>

      <header className="wlp-hero">
        <div className="wlp-hero-top">
          <div>
            <div className="wlp-eyebrow">{EYEBROW[media]}</div>
            <h1 className="wlp-title">Wishlist</h1>
            <p className="wlp-sub">
              Everything you&apos;re hunting down. The automation searches this list every hour —
              triage what&apos;s stuck, grab what you want now.
            </p>
          </div>
        </div>
        {c && (c.total ?? 0) > 0 ? (
          <div className="wlp-stats">
            <div className="wlp-stat">
              <span className="wlp-stat-value">{c.movie ?? 0}</span>
              <span className="wlp-stat-label">Movies</span>
            </div>
            <div className="wlp-stat">
              <span className="wlp-stat-value">{c.show ?? 0}</span>
              <span className="wlp-stat-label">Shows</span>
            </div>
            <div className="wlp-stat">
              <span className="wlp-stat-value">{c.episode ?? 0}</span>
              <span className="wlp-stat-label">Episodes</span>
            </div>
            <div className="wlp-stat">
              <span className="wlp-stat-value">{c.video ?? 0}</span>
              <span className="wlp-stat-label">YouTube videos</span>
            </div>
            {stuckCount > 0 && (pagination?.total_pages ?? 1) <= 1 ? (
              <div className="wlp-stat">
                <span className="wlp-stat-value wlp-stat-value--warn">{stuckCount}</span>
                <span className="wlp-stat-label">Stuck</span>
              </div>
            ) : null}
          </div>
        ) : null}
      </header>

      {stuckCount > 0 ? (
        <div className="wlp-triage" role="alert">
          <span className="wlp-triage-icon" aria-hidden="true">
            ⚠
          </span>
          <span className="wlp-triage-text">
            <strong>
              {stuckCount} {stuckCount === 1 ? 'item is' : 'items are'} stuck.
            </strong>{' '}
            Searched three times or more without a grab. Open one to see what the last search found,
            then retry it or pick a release yourself.
          </span>
          {media !== 'youtube' ? (
            <div className="wlp-triage-actions">
              <button type="button" className="wlp-btn wlp-btn--sm" onClick={() => onFailing(true)}>
                Review
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="wlp-cmdbar">
        <div className="wlp-search">
          <input
            type="text"
            placeholder="Filter wishlist…"
            aria-label="Filter wishlist"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
          />
        </div>
        {media !== 'youtube' ? (
          <button
            type="button"
            className={`wlp-chip wlp-chip--warn${failingOn ? ' active' : ''}`}
            title="Show only what keeps failing to download"
            aria-pressed={failingOn}
            onClick={() => onFailing(!failing)}
          >
            <span aria-hidden="true">⚠ </span>Failing
            {failingCount > 0 && <span className="wlp-count">{failingCount}</span>}
          </button>
        ) : null}
        <label className="wlp-sort">
          Sort
          <select
            aria-label="Sort wishlist"
            value={sort}
            onChange={(event) => setSort(event.target.value as VideoSort)}
          >
            {VIDEO_SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        {tabHasItems ? (
          <ActionMenu
            className="wlp-menu"
            items={[
              {
                key: 'clear',
                label: 'Clear all',
                danger: true,
                onSelect: () => void actions.clearAll(),
              },
            ]}
            trigger={(props) => (
              <button
                type="button"
                className="wlp-btn wlp-more"
                title="Clear all"
                aria-label="More wishlist actions"
                {...props}
                ref={props.ref as (el: HTMLButtonElement | null) => void}
              >
                ⋯
              </button>
            )}
          />
        ) : null}
        {tabHasItems ? (
          <button
            type="button"
            className="wlp-btn wlp-btn--primary"
            disabled={actions.isBusy('bulk')}
            title={
              media === 'youtube'
                ? 'Queue every waiting video now instead of waiting for the automation — skips the retry wait, ignores nothing else'
                : 'Search every eligible wishlist item now instead of waiting for the hourly automation'
            }
            onClick={() => void actions.bulk()}
          >
            {media === 'youtube' ? 'Download all waiting' : 'Search all missing'}
          </button>
        ) : null}
      </div>

      {list.isPending ? (
        <div className="wl-nebula-empty">Loading wishlist…</div>
      ) : list.isError || !list.data?.success ? (
        <div className="wl-nebula-empty">
          Could not load the wishlist.{' '}
          <button type="button" className="wlp-btn wlp-btn--sm" onClick={() => void list.refetch()}>
            Try again
          </button>
        </div>
      ) : total === 0 ? (
        <div className="wlp-empty">
          <h3>{emptyTitle(media, Boolean(debounced))}</h3>
          <p>
            Add a movie or episodes from any title&rsquo;s get button — they queue up here to grab.
          </p>
        </div>
      ) : (
        <>
          <NebulaStage
            sheetOpen={Boolean(openMovie || openShow)}
            field={
              <div className="wl-nebula-field wlp-field" data-testid="video-wishlist-field">
                {(isMovies ? visibleMovies.length : visibleShows.length) === 0 ? (
                  <div className="wl-nebula-empty">Nothing on this page keeps failing.</div>
                ) : isMovies ? (
                  movieZones.map((zone) => (
                    <NebulaZone
                      key={zone.kind}
                      kind={zone.kind}
                      title={zone.title}
                      sub={zone.sub}
                      grid="posters"
                    >
                      {zone.items.map((m) => (
                        <NebulaPoster
                          key={keyOfMovie(m)}
                          title={m.title}
                          image={sized(m.poster_url || '', 342)}
                          meta={movieMeta(m)}
                          stuck={VIDEO_STUCK_ZONES.has(zone.kind)}
                          expanded={openKey === keyOfMovie(m)}
                          data={{ tmdb: String(m.tmdb_id) }}
                          onToggle={() => toggle(keyOfMovie(m))}
                        />
                      ))}
                    </NebulaZone>
                  ))
                ) : (
                  showZones.map((zone) => (
                    <NebulaZone key={zone.kind} kind={zone.kind} title={zone.title} sub={zone.sub}>
                      {zone.items.map((sh) => (
                        <ShowOrb
                          key={keyOfShow(sh)}
                          show={sh}
                          index={visibleShows.indexOf(sh)}
                          stuck={VIDEO_STUCK_ZONES.has(zone.kind)}
                          expanded={openKey === keyOfShow(sh)}
                          onToggle={() => toggle(keyOfShow(sh))}
                        />
                      ))}
                    </NebulaZone>
                  ))
                )}
              </div>
            }
            sheet={
              openMovie ? (
                <VideoMovieSheet
                  key={keyOfMovie(openMovie)}
                  movie={openMovie}
                  actions={actions}
                  onClose={() => setOpenKey(null)}
                />
              ) : openShow ? (
                <VideoShowSheet
                  key={keyOfShow(openShow)}
                  show={openShow}
                  actions={actions}
                  onClose={() => setOpenKey(null)}
                />
              ) : null
            }
          />
          {pagination && pagination.total_pages > 1 ? (
            <nav className="wlp-pager" aria-label="Wishlist pages">
              <button
                type="button"
                className="wlp-btn wlp-btn--sm"
                disabled={!pagination.has_prev}
                onClick={() => setPage(Math.max(1, page - 1))}
              >
                Previous
              </button>
              <span className="wlp-pager-info">
                Page {pagination.page} of {pagination.total_pages}
              </span>
              <button
                type="button"
                className="wlp-btn wlp-btn--sm"
                disabled={!pagination.has_next}
                onClick={() => setPage(page + 1)}
              >
                Next
              </button>
            </nav>
          ) : null}
        </>
      )}
    </>
  );
}

function ShowOrb({
  show,
  index,
  stuck,
  expanded,
  onToggle,
}: {
  show: VideoShowItem;
  index: number;
  stuck: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const youtube = show.source === 'youtube';
  const img = (u: string | null | undefined) => (youtube ? ytImg(u) : u);
  // a channel's avatar can be missing: the newest video's thumb stands in.
  const poster =
    show.poster_url || (youtube && show.seasons?.[0] ? show.seasons[0].poster_url : null);
  const ring = (show.seasons || [])
    .map((se) => se.poster_url)
    .filter((u): u is string => Boolean(u))
    .slice(0, 6)
    .map((u) => sized(img(u), 185) || '');
  const failing = showEpisodes(show).filter((e) => isFailingItem(e, show.muted)).length;
  const wanted = show.wanted || 0;
  return (
    <NebulaOrb
      name={show.title}
      image={sized(img(poster) || '', 240)}
      initials={initials(show.title)}
      hue={artistHue(show.title)}
      sizeClass={orbSizeClass(wanted)}
      meta={showMeta(show)}
      stuck={stuck}
      failingCount={stuck ? Math.max(1, failing) : 0}
      expanded={expanded}
      ringCovers={ring.length >= 3 ? ring : []}
      index={index}
      data={youtube ? { channel: show.youtube_id || '' } : { show: String(show.tmdb_id) }}
      tooltipSub={`${wanted} ${youtube ? 'video' : 'episode'}${wanted === 1 ? '' : 's'}`}
      onToggle={onToggle}
      onOpen={() =>
        youtube
          ? openVideoDetail('channel', show.youtube_id || '', 'youtube')
          : openVideoDetail(
              'show',
              show.library_id != null ? show.library_id : show.tmdb_id,
              show.library_id != null ? 'library' : 'tmdb',
            )
      }
      openTitle={youtube ? 'Open channel page' : 'Open show page'}
    />
  );
}
