import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import { useRoutedArtists } from '@/features/audiobook-routes/audiobook-routes.queries';
import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';
import { ActionMenu } from '@/routes/artist-detail/-ui/action-menu';
import { clearAudiobookWishlist } from '@/routes/audiobooks/-audiobooks.api';

import type {
  ParsedWishlistTrack,
  WishlistAlbumGroup,
  WishlistArtistGroup,
  WishlistBulkAction,
} from '../-wishlist.types';

import { isVideoMedia } from '../-video.types';
import {
  bulkWishlistActionChunked,
  removeWishlistAlbum,
  removeWishlistTrack,
  setWishlistRetryProfile,
  WISHLIST_QUERY_KEY,
  wishlistArtistPhotosQueryOptions,
  wishlistCycleQueryOptions,
  wishlistRetryProfileQueryOptions,
  wishlistStatsQueryOptions,
  wishlistTracksQueryOptions,
} from '../-wishlist.api';
import {
  buildArtistImageMap,
  filterWishlistGroups,
  groupWishlistArtists,
  parseWishlistTrack,
  trackCountLabel,
  wishlistZones,
} from '../-wishlist.helpers';
import { useLiveWishlist } from '../-wishlist.live';
import {
  startWishlistDownload,
  stopWishlistRun,
  useWishlistRun,
  type WishlistRunScope,
} from '../-wishlist.run';
import { Route } from '../route';
import { NebulaStage, NebulaZone } from './nebula-parts';
import { VideoWishlist } from './video-wishlist';
import { WishlistArtistSheet } from './wishlist-artist-sheet';
import { WishlistAudiobooks } from './wishlist-audiobooks';
import { WishlistList } from './wishlist-list';
import { WishlistOrb } from './wishlist-orb';

type NebulaSort = 'busiest' | 'failing' | 'name';

const NEBULA_SORTS: { key: NebulaSort; label: string }[] = [
  { key: 'busiest', label: 'Busiest first' },
  { key: 'failing', label: 'Failing first' },
  { key: 'name', label: 'A–Z' },
];

const RING_CIRCUMFERENCE = 2 * Math.PI * 26;

function groupTrackIds(group: WishlistArtistGroup): string[] {
  return [...group.albums.flatMap((album) => album.tracks), ...group.singles].map(
    (track) => track.id,
  );
}

/**
 * One wishlist page for both sides. Music and audiobooks are the music side's
 * tabs; movies, TV and YouTube are the video side's (the video sidebar hands
 * /video-wishlist off to here). Both render on the same grammar: hero, command
 * bar, zones by what's wrong, and the side panel.
 */
export function WishlistPage() {
  const search = Route.useSearch();
  // the video tabs ask for the video wishlist's permission, not the music one
  useReactPageShell('wishlist', isVideoMedia(search.media) ? `?media=${search.media}` : undefined);
  const { profileId } = useProfile();
  const navigate = useNavigate({ from: Route.fullPath });

  if (isVideoMedia(search.media)) {
    return (
      <div className="page-shell wlp wlp--video">
        <VideoWishlist
          profileId={profileId}
          media={search.media}
          query={search.q}
          failing={search.failing}
          onMedia={(media) =>
            void navigate({ search: (prev) => ({ ...prev, media, q: '', failing: false }) })
          }
          onQuery={(q) => void navigate({ search: (prev) => ({ ...prev, q }), replace: true })}
          onFailing={(failing) => void navigate({ search: (prev) => ({ ...prev, failing }) })}
        />
      </div>
    );
  }
  return <MusicWishlistPage />;
}

function MusicWishlistPage() {
  const { profileId } = useProfile();
  const search = Route.useSearch();
  const media = search.media;
  const navigate = useNavigate({ from: Route.fullPath });
  const queryClient = useQueryClient();

  const [abReloadKey, setAbReloadKey] = useState(0);
  // The audiobook search box lives in WishlistAudiobooks; the audio plays listed
  // beneath it follow the same text.
  const [abQuery, setAbQuery] = useState('');

  // Only one orb open at a time, matching the vanilla accordion which cleared
  // `.expanded` from every group before setting it on the clicked one.
  const [expandedArtist, setExpandedArtist] = useState<string | null>(null);
  // Display-only view toggle: the nebula stays the default face; the list is
  // its operational twin. Persisted so the choice survives navigation.
  const [view, setView] = useState<'nebula' | 'list'>(() => {
    try {
      const stored = window.localStorage.getItem('wishlistView');
      return stored === 'list' ? 'list' : 'nebula';
    } catch {
      return 'nebula';
    }
  });
  const pickView = (next: 'nebula' | 'list') => {
    setView(next);
    try {
      window.localStorage.setItem('wishlistView', next);
    } catch {
      /* private mode — the toggle still works for the session */
    }
  };
  // The nebula keeps its own sort, independent of the list's triage sort —
  // each view remembers how you like to scan it.
  const [nebulaSort, setNebulaSort] = useState<NebulaSort>('busiest');

  const statsQuery = useQuery(wishlistStatsQueryOptions(profileId));
  const cycleQuery = useQuery(wishlistCycleQueryOptions(profileId));
  const retryProfileQuery = useQuery(wishlistRetryProfileQueryOptions(profileId));
  const albumsQuery = useQuery(wishlistTracksQueryOptions(profileId, 'albums'));
  const singlesQuery = useQuery(wishlistTracksQueryOptions(profileId, 'singles'));
  const photosQuery = useQuery(wishlistArtistPhotosQueryOptions(profileId));

  // The server counts every wanted track. Tracks by artists filed as audiobooks
  // are shown under Audiobooks instead, so they are taken out of the music
  // figures below (see routedGroups).
  const currentCycle = cycleQuery.data?.cycle || 'albums';

  const artistImages = useMemo(
    () =>
      buildArtistImageMap(
        [albumsQuery.data ?? {}, singlesQuery.data ?? {}],
        photosQuery.data ?? [],
      ),
    [albumsQuery.data, singlesQuery.data, photosQuery.data],
  );

  const allGroups = useMemo(() => {
    const parse = (rows: unknown[] | undefined, type: 'album' | 'single') =>
      (rows ?? [])
        .map((row) => parseWishlistTrack(row as never, type))
        .filter((t): t is ParsedWishlistTrack => t !== null);

    return groupWishlistArtists(
      parse(albumsQuery.data?.tracks, 'album'),
      parse(singlesQuery.data?.tracks, 'single'),
    );
  }, [albumsQuery.data?.tracks, singlesQuery.data?.tracks]);

  // Artists whose downloads are filed in the audiobook library. They are still
  // wanted through the music search, but they belong to the audiobook side, so
  // the music tab leaves them out and the audiobook tab lists them.
  const audiobookArtists = useRoutedArtists(allGroups.map((group) => group.name));
  const groups = useMemo(
    () => allGroups.filter((group) => !audiobookArtists.has(group.name)),
    [allGroups, audiobookArtists],
  );
  const routedGroups = useMemo(
    () => allGroups.filter((group) => audiobookArtists.has(group.name)),
    [allGroups, audiobookArtists],
  );
  const routedAlbumTracks = routedGroups.reduce(
    (sum, group) => sum + group.albums.reduce((n, album) => n + album.tracks.length, 0),
    0,
  );
  const routedSingleTracks = routedGroups.reduce((sum, group) => sum + group.singles.length, 0);
  const albumCount = Math.max(0, (statsQuery.data?.albums ?? 0) - routedAlbumTracks);
  const singleCount = Math.max(0, (statsQuery.data?.singles ?? 0) - routedSingleTracks);
  const total = Math.max(0, (statsQuery.data?.total ?? 0) - routedAlbumTracks - routedSingleTracks);

  const visibleGroups = useMemo(
    () => filterWishlistGroups(groups, search.q, search.failing),
    [groups, search.q, search.failing],
  );
  const visibleRoutedGroups = useMemo(
    () =>
      filterWishlistGroups(
        routedGroups,
        media === 'audiobooks' ? abQuery : search.q,
        search.failing,
      ),
    [routedGroups, media, abQuery, search.q, search.failing],
  );

  const nebulaGroups = useMemo(() => {
    const list = [...visibleGroups];
    if (nebulaSort === 'name') list.sort((a, b) => a.name.localeCompare(b.name));
    else if (nebulaSort === 'failing')
      list.sort(
        (a, b) =>
          b.failingCount - a.failingCount || b.total - a.total || a.name.localeCompare(b.name),
      );
    // 'busiest' is the helper's native order.
    return list;
  }, [visibleGroups, nebulaSort]);

  // the field splits by what's wrong; the sort still orders inside each zone.
  const nebulaZones = useMemo(() => wishlistZones(nebulaGroups), [nebulaGroups]);
  // the open artist, if it's still on screen. a filter or a removal that takes
  // it away closes the panel by itself.
  const sheetGroup = nebulaGroups.find((group) => group.name === expandedArtist) ?? null;

  const failingIds = useMemo(
    () =>
      groups
        .flatMap((group) => [...group.albums.flatMap((album) => album.tracks), ...group.singles])
        .filter((track) => track.failing)
        .map((track) => track.id),
    [groups],
  );
  const failingTotal = failingIds.length;

  const refresh = () => queryClient.invalidateQueries({ queryKey: WISHLIST_QUERY_KEY });

  const { processing: liveProcessing, batchId: liveBatchId } = useLiveWishlist(() => {
    void refresh();
  });
  // the id from our own start shows the strip at once; the live poll takes over
  // within a tick and also picks up runs the auto timer started
  const [startedBatchId, setStartedBatchId] = useState<string | null>(null);
  useEffect(() => {
    if (liveBatchId) setStartedBatchId(null);
  }, [liveBatchId]);
  const runBatchId = liveBatchId ?? startedBatchId;
  const processing = liveProcessing || startedBatchId !== null;
  const run = useWishlistRun(runBatchId);
  // our own start never confirmed by the live poll (a run that settled in
  // under a tick, or a batch already cleaned up) must not pin the strip open
  useEffect(() => {
    if (!startedBatchId) return;
    if (run?.stage === 'finished' || run?.stage === 'stopped') {
      setStartedBatchId(null);
      return;
    }
    const timer = window.setTimeout(() => setStartedBatchId(null), 15000);
    return () => window.clearTimeout(timer);
  }, [startedBatchId, run?.stage]);
  // say how it went once the run is over, the strip is gone by then
  const lastRun = useRef<typeof run>(null);
  useEffect(() => {
    if (processing) {
      if (run) lastRun.current = run;
      return;
    }
    const finished = lastRun.current;
    lastRun.current = null;
    if (finished && finished.total > 0) {
      window.showToast?.(
        `Wishlist run done: ${finished.done} downloaded${finished.failed ? `, ${finished.failed} failed` : ''}`,
        finished.failed ? 'warning' : 'success',
      );
    }
  }, [processing, run]);

  const startRun = useMutation({
    mutationFn: (scope: WishlistRunScope) => startWishlistDownload(scope),
    onSuccess: (result) => {
      if (result.alreadyRunning) {
        window.showToast?.('The wishlist is already downloading', 'info');
        return;
      }
      if (result.batchId) setStartedBatchId(result.batchId);
    },
    onError: (error: Error) => window.showToast?.(`Could not start: ${error.message}`, 'error'),
  });

  const stopRun = useMutation({
    mutationFn: (batchId: string) => stopWishlistRun(batchId),
    onSuccess: () => {
      setStartedBatchId(null);
      window.showToast?.('Stopped. Downloads already running will finish.', 'success');
    },
    onError: (error: Error) => window.showToast?.(`Could not stop: ${error.message}`, 'error'),
  });

  const onStopRun = async () => {
    if (!runBatchId) return;
    const confirmed = await window.showConfirmDialog?.({
      title: 'Stop downloading?',
      message: 'Nothing new will start. Tracks already downloading will finish.',
      confirmText: 'Stop',
      destructive: true,
    });
    if (confirmed === false) return;
    stopRun.mutate(runBatchId);
  };

  // The countdown lives in downloads.js: it is bound to wishlistCountdownInterval,
  // socketConnected and _lastWishlistStats, all module-scoped, and writes into
  // #wishlist-next-auto-timer — which is why that id is rendered below.
  const nextRunSeconds = statsQuery.data?.next_run_in_seconds ?? 0;
  useEffect(() => {
    window.startWishlistCountdownTimer?.(currentCycle, nextRunSeconds);
  }, [currentCycle, nextRunSeconds]);

  const removeAlbum = useMutation({
    mutationFn: (albumName: string) => removeWishlistAlbum(albumName),
    onSuccess: async (_data, albumName) => {
      window.showToast?.(`Removed "${albumName}"`, 'success');
      await refresh();
      window.updateWishlistCount?.();
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  const removeTrack = useMutation({
    mutationFn: (trackId: string) => removeWishlistTrack(trackId),
    onSuccess: async () => {
      window.showToast?.('Removed', 'success');
      await refresh();
      window.updateWishlistCount?.();
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  const bulkAction = useMutation({
    // Chunked: the endpoint caps a call at 200 ids, and "grab the artist"
    // on a 500-track wishlist must not 400.
    mutationFn: ({ action, ids }: { action: WishlistBulkAction; ids: string[] }) =>
      bulkWishlistActionChunked(action, ids),
    onSuccess: (response, { action, ids }) => {
      const results = response.results ?? [];
      const okCount = results.filter((r) => r.ok).length;
      const allOk = results.length > 0 && okCount === results.length;
      window.showToast?.(
        `${action[0].toUpperCase()}${action.slice(1)}: ${okCount}/${ids.length} succeeded`,
        allOk ? 'success' : 'warning',
      );
      void refresh();
      window.updateWishlistCount?.();
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  // The retry profile select lives in the hero stats. A custom ladder stays
  // API-only: it shows as the current value when active, but can't be picked.
  const retryProfileData = retryProfileQuery.data;
  const retryProfileOptions = retryProfileData?.profiles ?? [];
  const activeIsCustom =
    retryProfileData?.profile?.name === 'custom' &&
    !retryProfileOptions.some((p) => p.name === 'custom');
  const retryProfileMutation = useMutation({
    mutationFn: (name: string) => setWishlistRetryProfile(name),
    onSuccess: (response) => {
      window.showToast?.(`Retry profile: ${response.profile?.label ?? 'updated'}`, 'success');
      void queryClient.invalidateQueries({
        queryKey: [...WISHLIST_QUERY_KEY, 'retry-profile'],
      });
    },
    onError: (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error'),
  });

  const onRemoveAlbum = async (albumName: string) => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove Album',
      message: `Remove all tracks from "${albumName}"?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    removeAlbum.mutate(albumName);
  };

  /** New: one grab queues every wanted track by the artist, right now. */
  const onGrabArtist = (group: WishlistArtistGroup) => {
    const ids = groupTrackIds(group);
    if (ids.length > 0) bulkAction.mutate({ action: 'grab', ids });
  };

  /** "Search now" on the Audiobooks tab also sends the audio plays listed there
      (artists filed as audiobooks) to downloads, since only this page knows they
      belong together. Returns a sentence for the tab's notice. */
  const searchRouted = async (): Promise<string> => {
    const ids = routedGroups.flatMap(groupTrackIds);
    if (ids.length === 0) return '';
    try {
      const response = await bulkAction.mutateAsync({ action: 'grab', ids });
      const sent = (response.results ?? []).filter((result) => result.ok).length;
      return `Sent ${sent} of ${trackCountLabel(ids.length)} from the music search to downloads.`;
    } catch {
      return 'Could not send the audio plays to downloads.';
    }
  };

  /** New: queue one album's tracks without touching the rest of the artist. */
  const onGrabAlbum = (album: WishlistAlbumGroup) => {
    const ids = album.tracks.map((track) => track.id);
    if (ids.length > 0) bulkAction.mutate({ action: 'grab', ids });
  };

  /** New: clear the retry backoff on every stuck track at once. */
  const onRetryAllFailing = () => {
    if (failingIds.length > 0) bulkAction.mutate({ action: 'retry', ids: failingIds });
  };

  /** clear the backoff on just these tracks: one zone, or one artist. */
  const onRetryIds = (ids: string[]) => {
    if (ids.length > 0) bulkAction.mutate({ action: 'retry', ids });
  };

  /** New: drop a whole artist from the wishlist with a single confirm.
      Removal goes through the bulk skip endpoint with exact track ids —
      the album-name endpoint matches by name across the whole wishlist,
      so a shared album title ("Greatest Hits") could nuke another
      artist's tracks. Skip removes by id and records the same ignore
      gate a manual remove does. */
  const onRemoveArtist = async (group: WishlistArtistGroup) => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove Artist',
      message: `Remove ${trackCountLabel(group.total)} by "${group.name}" from the wishlist?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    const ids = groupTrackIds(group);
    if (ids.length === 0) return;
    try {
      const response = await bulkWishlistActionChunked('skip', ids);
      const results = response.results ?? [];
      const okCount = results.filter((result) => result.ok).length;
      if (okCount === ids.length) {
        window.showToast?.(`Removed ${trackCountLabel(okCount)} by "${group.name}"`, 'success');
      } else {
        window.showToast?.(
          `Removed ${okCount} of ${ids.length} tracks by "${group.name}" — ${ids.length - okCount} failed`,
          'warning',
        );
      }
    } catch (error) {
      window.showToast?.(
        `Error: ${error instanceof Error ? error.message : String(error)}`,
        'error',
      );
    }
    await refresh();
    window.updateWishlistCount?.();
  };

  const onClearAudiobooks = async () => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Clear Audiobook Wishlist',
      message: 'Remove all audiobooks from your wishlist? This cannot be undone.',
      confirmText: 'Clear All',
      destructive: true,
    });
    if (confirmed === false) return;
    const ok = await clearAudiobookWishlist();
    if (ok) {
      window.showToast?.('Audiobook wishlist cleared', 'success');
      setAbReloadKey((k) => k + 1);
    } else {
      window.showToast?.('Failed to clear wishlist', 'error');
    }
  };

  const showFailing = () => void navigate({ search: (prev) => ({ ...prev, failing: true }) });

  return (
    <div className="page-shell wlp">
      {/* Media tabs. Music and audiobooks are kept as separate lists, the same
          isolation the video side keeps between movies, shows and channels:
          they share a page and nothing else — different database, different
          search, different acquisition chain. */}
      <div className="wlp-media-tabs" role="tablist" aria-label="Wishlist media type">
        <button
          type="button"
          role="tab"
          aria-selected={media === 'music'}
          onClick={() => void navigate({ search: (prev) => ({ ...prev, media: 'music' }) })}
        >
          Music
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={media === 'audiobooks'}
          onClick={() => void navigate({ search: (prev) => ({ ...prev, media: 'audiobooks' }) })}
        >
          Audiobooks
        </button>
      </div>

      {media === 'audiobooks' ? (
        <>
          {/* Audiobooks share the music side's premier presentation — the
              same wlp hero, stats, triage and command bar — so the two tabs
              read as one page. Only the shared tab strip above is page-level;
              the rest of the panel lives in WishlistAudiobooks. */}
          <WishlistAudiobooks
            key={abReloadKey}
            onClearAll={() => void onClearAudiobooks()}
            onSearchAlso={searchRouted}
            onQueryChange={setAbQuery}
          />
          {routedGroups.length > 0 && (visibleRoutedGroups.length > 0 || !abQuery.trim()) && (
            <section className="wlp-routed" aria-label="Music releases filed as audiobooks">
              <h2 className="wlp-routed-title">Audio plays from the music search</h2>
              <p className="wlp-routed-note">
                Wanted through the music search, but filed in your audiobook library because the
                artist is set to <strong>Treat as audiobook</strong>.
              </p>
              <WishlistList
                groups={visibleRoutedGroups}
                artistImages={artistImages}
                filterActive={Boolean(abQuery.trim()) || search.failing}
                onRemoveAlbum={(albumName) => void onRemoveAlbum(albumName)}
                onRemoveTrack={(trackId) => removeTrack.mutate(trackId)}
                onGrabArtist={onGrabArtist}
                onBulkAction={(action, ids) => bulkAction.mutateAsync({ action, ids })}
                bulkBusy={bulkAction.isPending}
              />
            </section>
          )}
        </>
      ) : (
        <>
          <header className="wlp-hero">
            <div className="wlp-hero-top">
              <div>
                <div className="wlp-eyebrow">Music</div>
                <h1 className="wlp-title">Wishlist</h1>
                <p className="wlp-sub">
                  Everything you&apos;re hunting down. The automation works this list around the
                  clock — triage what&apos;s stuck, grab what you want now.
                </p>
              </div>
              <div className="wlp-nextrun">
                <div className="wlp-ring" aria-hidden="true">
                  <svg viewBox="0 0 64 64">
                    <circle className="wlp-ring-bg" cx="32" cy="32" r="26" />
                    <circle
                      className={`wlp-ring-fg${processing ? ' is-live' : ''}`}
                      cx="32"
                      cy="32"
                      r="26"
                      strokeDasharray={`${RING_CIRCUMFERENCE * 0.22} ${RING_CIRCUMFERENCE}`}
                      strokeDashoffset={0}
                    />
                  </svg>
                  <span className="wlp-ring-center">{processing ? '⟳' : '◷'}</span>
                </div>
                <div className="wlp-nextrun-meta">
                  <span className="wlp-nextrun-label">
                    {processing ? 'Auto-run working' : 'Next auto-run'}
                  </span>
                  <span className="wlp-nextrun-time" id="wishlist-next-auto-timer">
                    --
                  </span>
                  <span className="wlp-cycle-badge">
                    {currentCycle === 'albums' ? 'Albums/EPs' : 'Singles'}
                  </span>
                </div>
              </div>
            </div>

            {/* The stats strip hides on an empty wishlist, exactly as the old
                page did — the hero keeps its title and next-run card. */}
            {total > 0 && (
              <div className="wlp-stats">
                <div className="wlp-stat">
                  <span className="wlp-stat-value wishlist-page-count">
                    {trackCountLabel(total)}
                  </span>
                  <span className="wlp-stat-label">Wanted</span>
                </div>
                <div className="wlp-stat">
                  <span className="wlp-stat-value" id="wishlist-stat-albums">
                    {albumCount}
                  </span>
                  <span className="wlp-stat-label">Album Tracks</span>
                </div>
                <div className="wlp-stat">
                  <span className="wlp-stat-value" id="wishlist-stat-singles">
                    {singleCount}
                  </span>
                  <span className="wlp-stat-label">Singles</span>
                </div>
                {failingTotal > 0 && (
                  <div className="wlp-stat">
                    <span className="wlp-stat-value wlp-stat-value--warn">{failingTotal}</span>
                    <span className="wlp-stat-label">Failing</span>
                  </div>
                )}
                <div className="wlp-stat">
                  <span className="wlp-stat-value">
                    <select
                      className="wlp-retry-select"
                      aria-label="Retry profile"
                      title={
                        retryProfileData?.profile?.description ||
                        'How long repeatedly-failing tracks wait between scheduled retries'
                      }
                      value={retryProfileData?.profile?.name ?? 'standard'}
                      disabled={retryProfileQuery.isPending || retryProfileMutation.isPending}
                      onChange={(event) => retryProfileMutation.mutate(event.target.value)}
                    >
                      {retryProfileOptions.map((p) => (
                        <option key={p.name} value={p.name} disabled={p.name === 'custom'}>
                          {p.label}
                          {p.name === 'custom' ? ' (API only)' : ''}
                        </option>
                      ))}
                      {activeIsCustom && (
                        <option value="custom" disabled>
                          {retryProfileData?.profile?.label ?? 'Custom'} (API only)
                        </option>
                      )}
                    </select>
                  </span>
                  <span className="wlp-stat-label">Retry Profile</span>
                </div>
              </div>
            )}
          </header>

          {failingTotal > 0 && (
            <div className="wlp-triage" role="alert">
              <span className="wlp-triage-icon" aria-hidden="true">
                ⚠
              </span>
              <span className="wlp-triage-text">
                <strong>
                  {failingTotal} {failingTotal === 1 ? 'track keeps' : 'tracks keep'} failing.
                </strong>{' '}
                Stuck in the retry loop — clear their backoff to try them right now, or review
                what&apos;s wrong.
              </span>
              <div className="wlp-triage-actions">
                <button
                  type="button"
                  className="wlp-btn wlp-btn--sm wlp-btn--primary"
                  disabled={bulkAction.isPending}
                  onClick={onRetryAllFailing}
                >
                  <span aria-hidden="true">↻ </span>Retry all now
                </button>
                <button type="button" className="wlp-btn wlp-btn--sm" onClick={showFailing}>
                  Review
                </button>
              </div>
            </div>
          )}

          {total === 0 ? (
            <div className="wlp-empty">
              <div className="wlp-empty-icon" aria-hidden="true">
                <svg
                  width="40"
                  height="40"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="rgba(245,185,66,0.85)"
                  strokeWidth="1.5"
                >
                  <path d="M9 18V5l12-2v13" />
                  <circle cx="6" cy="18" r="3" />
                  <circle cx="18" cy="16" r="3" />
                </svg>
              </div>
              <h3>Your wishlist is empty</h3>
              <p>
                Failed downloads and tracks from watchlist scans will appear here automatically —
                then the hunt begins.
              </p>
            </div>
          ) : (
            <>
              <div className="wlp-cmdbar">
                <div className="wlp-search">
                  <input
                    type="text"
                    placeholder="Filter wishlist…"
                    aria-label="Filter wishlist"
                    value={search.q}
                    onChange={(event) =>
                      void navigate({
                        search: (prev) => ({ ...prev, q: event.target.value }),
                        replace: true,
                      })
                    }
                  />
                </div>
                <button
                  type="button"
                  className={`wlp-chip wlp-chip--warn${search.failing ? ' active' : ''}`}
                  title="Show only artists with tracks that keep failing to download"
                  aria-pressed={search.failing}
                  onClick={() =>
                    void navigate({ search: (prev) => ({ ...prev, failing: !prev.failing }) })
                  }
                >
                  <span aria-hidden="true">⚠ </span>Failing
                  {failingTotal > 0 && <span className="wlp-count">{failingTotal}</span>}
                </button>
                {view === 'nebula' && (
                  <label className="wlp-sort">
                    Sort
                    <select
                      aria-label="Sort nebula"
                      value={nebulaSort}
                      onChange={(event) => setNebulaSort(event.target.value as NebulaSort)}
                    >
                      {NEBULA_SORTS.map((s) => (
                        <option key={s.key} value={s.key}>
                          {s.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <div className="wlp-segmented" role="tablist" aria-label="Wishlist view">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === 'nebula'}
                    title="The orbital view"
                    onClick={() => pickView('nebula')}
                  >
                    <span aria-hidden="true">✦ </span>Nebula
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === 'list'}
                    title="Dense list with retry status — sortable"
                    onClick={() => pickView('list')}
                  >
                    <span aria-hidden="true">☰ </span>List
                  </button>
                </div>
                {/* All three open modals owned by downloads.js and shared with the
                    wishlist hero button, so they are invoked rather than reimplemented.
                    they fold into one menu: rare jobs, and one of them wipes the list. */}
                <ActionMenu
                  className="wlp-menu"
                  items={[
                    {
                      key: 'ignored',
                      label: 'Ignored',
                      title:
                        'Tracks you removed or cancelled — auto-skipped until they expire. Un-ignore to allow auto-download again.',
                      onSelect: () => window.openWishlistIgnoreModal?.(),
                    },
                    {
                      key: 'cleanup',
                      label: 'Cleanup',
                      title: 'Drop wanted tracks you already own',
                      onSelect: () => window.cleanupWishlistOverview?.(),
                    },
                    {
                      key: 'clear',
                      label: 'Clear All',
                      danger: true,
                      onSelect: () => window.clearEntireWishlist?.(),
                    },
                  ]}
                  trigger={(props) => (
                    <button
                      type="button"
                      className="wlp-btn wlp-more"
                      title="Ignored, cleanup, clear all"
                      aria-label="More wishlist actions"
                      {...props}
                      ref={props.ref as (el: HTMLButtonElement | null) => void}
                    >
                      ⋯
                    </button>
                  )}
                />
                <div className="wlp-split" role="group" aria-label="Download the wishlist">
                  <button
                    type="button"
                    className="wlp-btn wlp-btn--primary wlp-split-main"
                    disabled={processing || startRun.isPending || total === 0}
                    title="Download every wanted track now"
                    onClick={() => startRun.mutate('all')}
                  >
                    <span aria-hidden="true">⬇ </span>
                    {processing
                      ? 'Downloading…'
                      : startRun.isPending
                        ? 'Starting…'
                        : 'Download now'}
                  </button>
                  <ActionMenu
                    className="wlp-menu"
                    items={[
                      {
                        key: 'all',
                        label: `Everything (${total})`,
                        disabled: total === 0,
                        onSelect: () => startRun.mutate('all'),
                      },
                      {
                        key: 'albums',
                        label: `Albums & EPs only (${albumCount})`,
                        disabled: albumCount === 0,
                        onSelect: () => startRun.mutate('albums'),
                      },
                      {
                        key: 'singles',
                        label: `Singles only (${singleCount})`,
                        disabled: singleCount === 0,
                        onSelect: () => startRun.mutate('singles'),
                      },
                    ]}
                    trigger={(props) => (
                      <button
                        type="button"
                        className="wlp-btn wlp-btn--primary wlp-split-more"
                        aria-label="Choose what to download"
                        disabled={processing || startRun.isPending}
                        {...props}
                        ref={props.ref as (el: HTMLButtonElement | null) => void}
                      >
                        ▾
                      </button>
                    )}
                  />
                </div>
              </div>

              {processing ? (
                <div className="wlp-run" role="status" aria-live="polite">
                  <div className="wlp-run-head">
                    <span className="wlp-run-label">{run?.label ?? 'Starting…'}</span>
                    <span className="wlp-run-actions">
                      <button
                        type="button"
                        className="wlp-btn"
                        // the old modal, opened on the run that's already going
                        onClick={() => void window._nebulaDownload?.()}
                      >
                        View details
                      </button>
                      <button
                        type="button"
                        className="wlp-btn"
                        disabled={!runBatchId || stopRun.isPending}
                        onClick={() => void onStopRun()}
                      >
                        {stopRun.isPending ? 'Stopping…' : 'Stop'}
                      </button>
                    </span>
                  </div>
                  <div className="wlp-run-bar">
                    <i style={{ width: `${Math.round((run?.fraction ?? 0) * 100)}%` }} />
                  </div>
                </div>
              ) : null}

              {view === 'list' ? (
                <WishlistList
                  groups={visibleGroups}
                  artistImages={artistImages}
                  filterActive={Boolean(search.q?.trim()) || search.failing}
                  onRemoveAlbum={(albumName) => void onRemoveAlbum(albumName)}
                  onRemoveTrack={(trackId) => removeTrack.mutate(trackId)}
                  onGrabArtist={onGrabArtist}
                  onBulkAction={(action, ids) => bulkAction.mutateAsync({ action, ids })}
                  bulkBusy={bulkAction.isPending}
                  zoned
                  onRetryIds={onRetryIds}
                />
              ) : (
                <NebulaStage
                  sheetOpen={sheetGroup !== null}
                  field={
                    <div
                      className={`wl-nebula-field wlp-field${processing ? ' nebula-processing' : ''}`}
                    >
                      {nebulaGroups.length === 0 ? (
                        <div className="wl-nebula-empty">
                          Nothing matches — clear the filter to see the whole hunt.
                        </div>
                      ) : (
                        nebulaZones.map((zone) => (
                          <NebulaZone
                            key={zone.kind}
                            kind={zone.kind}
                            title={zone.title}
                            sub={zone.sub}
                            action={
                              zone.stuckIds.length > 0
                                ? {
                                    label:
                                      zone.stuckIds.length === 1
                                        ? 'Retry now'
                                        : `Retry all ${zone.stuckIds.length}`,
                                    title:
                                      'Clear the retry backoff so the next pass tries these again',
                                    disabled: bulkAction.isPending,
                                    onClick: () => onRetryIds(zone.stuckIds),
                                  }
                                : null
                            }
                          >
                            {zone.groups.map((group) => (
                              <WishlistOrb
                                key={group.name}
                                group={group}
                                index={nebulaGroups.indexOf(group)}
                                artistImages={artistImages}
                                currentCycle={currentCycle}
                                processing={processing}
                                expanded={expandedArtist === group.name}
                                onToggleExpand={() =>
                                  setExpandedArtist((current) =>
                                    current === group.name ? null : group.name,
                                  )
                                }
                              />
                            ))}
                          </NebulaZone>
                        ))
                      )}
                    </div>
                  }
                  sheet={
                    sheetGroup ? (
                      <WishlistArtistSheet
                        key={sheetGroup.name}
                        group={sheetGroup}
                        artistImages={artistImages}
                        onClose={() => setExpandedArtist(null)}
                        onGrabArtist={() => onGrabArtist(sheetGroup)}
                        onGrabAlbum={onGrabAlbum}
                        onRetry={onRetryIds}
                        onRemoveAlbum={(albumName) => void onRemoveAlbum(albumName)}
                        onRemoveTrack={(trackId) => removeTrack.mutate(trackId)}
                        onRemoveArtist={() => void onRemoveArtist(sheetGroup)}
                        actionBusy={bulkAction.isPending}
                      />
                    ) : null
                  }
                />
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
