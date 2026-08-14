/**
 * The Tidal and Qobuz account-vertical tabs (sync-services.js 4-227 and
 * 1516-1720): click Refresh to load the account's playlists, cards render
 * instantly from metadata, tracks fetch per-playlist in the background and
 * auto-mirror, then the saved discovery states hydrate (with the P5b
 * resume-on-in-flight fix). The two differ only in their FRESH card click:
 * Tidal opens the shared modal immediately with whatever tracks are cached
 * (#867 — the backend discovery fetch is the source of truth, 152-166);
 * Qobuz fetches the track list behind a loading overlay first and refuses to
 * open without tracks (1648-1680).
 *
 * Declared divergences (the P5a/P5b pattern): card clicks open the React
 * DiscoveryModal in every phase (the vanilla's downloading branches reopened
 * the vanilla engine modal via the script-scoped registry); the states-list
 * engine-modal rehydration rides the same registry and is not reproduced;
 * the 'state not found' toasts are unreachable by construction; the progress
 * line paints for any non-fresh phase (the vanilla's hydration painted it
 * only for 'discovered', then the live writers took over); spotifyTotal comes
 * from the backend state row rather than the vanilla's playlist track_count
 * (applyTidalPlaylistState 926-929), which stops a not-yet-loaded account row
 * from rendering a negative ✗ count.
 *
 * The saved states are hydrated BEFORE the background track loop, not after
 * it (the vanilla's 61-62): the loop is sequential over every playlist and
 * takes minutes on a real account, and a states response that lands after the
 * user has already started a discovery would roll their card back.
 */

import { useCallback, useRef, useState } from 'react';

import type { SourceVerticalConfig } from '../-sync.sources';
import type { UrlTabPlaylist } from '../-sync.url-tabs';
import type { SourceVertical } from '../-sync.use-vertical';

import { fetchAccountPlaylist, fetchSourcePlaylists, postMirrorPlaylist } from '../-sync.api';
import { mapWithConcurrency } from '../-sync.core';
import { buildMirrorPayload } from '../-sync.import';
import { SYNC_SOURCES } from '../-sync.sources';
import { freshSourceState } from '../-sync.state';
import { asString, deezerMirrorTracks } from '../-sync.url-tabs';
import { fetchAndHydrateState } from '../-sync.use-vertical';
import { cardProgressLine } from './card-progress';
import { SourceCard } from './source-card';
import { hydrateStatesForLoaded } from './url-import-tab';

/**
 * How many playlists the background track crawl fetches at once.
 *
 * Three, matching the vanilla fix: one-at-a-time is what made Tidal's refresh
 * take 3-5 minutes for a large account, and unbounded would fire a request per
 * playlist simultaneously and earn a rate limit.
 */
const TRACK_CRAWL_CONCURRENCY = 3;

/** The qobuz fresh-click track projection (sync-services.js 1657-1661). */
function qobuzFreshTracks(tracks: unknown[]): Record<string, unknown>[] {
  return (tracks as Record<string, unknown>[]).map((t) => ({
    id: t.id,
    name: t.name,
    artists: t.artists || [],
    album: t.album || '',
    duration_ms: t.duration_ms || 0,
    track_number: t.track_number || 0,
  }));
}

interface AccountTabChrome {
  base: 'tidal' | 'qobuz';
  title: string;
  refreshBtnId: string;
  refreshBtnClass: string;
  containerId: string;
  initialPlaceholder: string;
  loadingPlaceholder: string;
  /** 'Tidal' | 'Qobuz' — the toast/placeholder noun. */
  noun: string;
  cardIdPrefix: string;
  cardClassName: string;
}

function AccountVerticalTab({
  config,
  chrome,
  vertical,
  onOpen,
}: {
  config: SourceVerticalConfig;
  chrome: AccountTabChrome;
  vertical: SourceVertical;
  /** Show the shared modal for this id — prep (seed/fetch) happens here first. */
  onOpen: (sourceId: string) => void;
}) {
  /** null = never loaded (the click-Refresh placeholder). */
  const [playlists, setPlaylists] = useState<UrlTabPlaylist[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const playlistsRef = useRef(playlists);
  playlistsRef.current = playlists;
  /** Invalidates the background mirror loop when Refresh re-runs. */
  const loadGeneration = useRef(0);

  const setPlaylistTracks = useCallback((id: string, tracks: unknown[]) => {
    setPlaylists((prev) =>
      // A track fetch that outlives a Refresh must NOT resurrect the cleared
      // list — that would flash 'No <source> playlists found.' mid-load.
      prev === null
        ? prev
        : prev.map((p) =>
            String(p.id) === String(id) ? { ...p, tracks, track_count: tracks.length } : p,
          ),
    );
  }, []);

  /**
   * The per-playlist track crawl that feeds auto-mirroring (27-59).
   *
   * Split out of `load` so it can run AFTER the refresh button is released —
   * see the note in `load`. Declared before `load` because `load` lists it as a
   * dependency, and a `const` referenced above its declaration is a TDZ error.
   */
  const crawlTracks = useCallback(
    async (rows: UrlTabPlaylist[], generation: number) => {
      const mirror = (p: UrlTabPlaylist, tracks: unknown[]) =>
        void postMirrorPlaylist(
          buildMirrorPayload(
            config.id,
            p.id as string | number,
            asString(p.name),
            deezerMirrorTracks(tracks),
            {
              owner: p.owner as string | undefined,
              image_url: p.image_url as string | undefined,
              description: p.description as string | undefined,
            },
          ),
        ).catch(() => undefined);

      await mapWithConcurrency(rows, TRACK_CRAWL_CONCURRENCY, async (p) => {
        // A newer refresh supersedes this one — stop crawling for the old list.
        if (generation !== loadGeneration.current) return;
        const existingTracks = Array.isArray(p.tracks) ? (p.tracks as unknown[]) : [];
        if (existingTracks.length > 0) {
          mirror(p, existingTracks);
          return;
        }
        try {
          const fullData = await fetchAccountPlaylist(chrome.base, String(p.id));
          if (generation !== loadGeneration.current) return;
          const tracks = Array.isArray(fullData.tracks) ? (fullData.tracks as unknown[]) : [];
          if (tracks.length > 0) {
            p.tracks = tracks;
            setPlaylistTracks(String(p.id), tracks);
            mirror(p, tracks);
          }
        } catch {
          // Per-playlist track fetch is best-effort (56-58).
        }
      });
    },
    [config, chrome, setPlaylistTracks],
  );

  /** loadTidalPlaylists / loadQobuzPlaylists (4-71 / 1516-1579). */
  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setErrorText(null);
    setPlaylists(null);
    try {
      const rows = (await fetchSourcePlaylists(chrome.base)) as UrlTabPlaylist[];
      if (generation !== loadGeneration.current) return;
      setPlaylists(rows);
      rows.forEach((p) => {
        if (!vertical.states[String(p.id)]) vertical.seed(String(p.id), p);
      });

      // Saved discovery states first (see the header note on ordering),
      // resuming any row the backend reports mid-flight.
      await hydrateStatesForLoaded(
        config,
        vertical,
        (pid) => rows.find((p) => String(p.id) === pid),
        () => generation === loadGeneration.current,
      );
      if (generation !== loadGeneration.current) return;

      // The cards are on screen — the button stops saying "Loading" HERE, not
      // after the crawl below.
      //
      // Specialmed (Discord, Aug 11): Tidal's refresh sat on "Loading" for 3-5
      // minutes after the playlists had visibly rendered, while Deezer snapped
      // back instantly. The crawl fetches every playlist's full track list to
      // feed auto-mirroring; awaiting it before releasing the button made the
      // button report the CRAWL rather than the list. Fixed once in the vanilla
      // page (75aa6720b) — but three days AFTER /sync flipped to React
      // (89b61d3fd), so that fix landed in a file which no longer runs, and the
      // port had already inherited the original blocking shape.
      setLoading(false);

      // Genuinely in the background now, three at a time: sequential is what
      // took minutes, unbounded would hammer Tidal for a large account.
      void crawlTracks(rows, generation);
    } catch (error) {
      if (generation !== loadGeneration.current) return;
      const message = error instanceof Error ? error.message : 'unknown error';
      setErrorText(`❌ Error: ${message}`);
      window.showToast?.(`Error loading ${chrome.noun} playlists: ${message}`, 'error');
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, [config, chrome, vertical, crawlTracks]);

  /** The shared non-fresh open (handleTidalCardClick 168-195 and the clone). */
  const openSettled = useCallback(
    async (sourceId: string, playlist: UrlTabPlaylist) => {
      const state = vertical.states[sourceId];
      if (state && state.phase === 'discovered' && state.rawResults.length === 0) {
        await fetchAndHydrateState(config, sourceId, (sid, backend) =>
          vertical.hydrate(sid, { ...backend, playlist: backend.playlist ?? playlist }),
        );
      }
      onOpen(sourceId);
    },
    [config, vertical, onOpen],
  );

  const openCard = useCallback(
    async (playlist: UrlTabPlaylist) => {
      const sourceId = String(playlist.id);
      const state = vertical.states[sourceId];
      const phase = state?.phase ?? 'fresh';
      if (phase !== 'fresh') {
        await openSettled(sourceId, playlist);
        return;
      }
      if (config.ux.openModalImmediately) {
        // #867: open IMMEDIATELY — the discovery poll fills rows in; cached
        // tracks seed instantly, missing tracks default to [] (162-166).
        const tracks = Array.isArray(playlist.tracks) ? playlist.tracks : [];
        vertical.seed(sourceId, { ...playlist, tracks });
        onOpen(sourceId);
        return;
      }
      // Qobuz: fetch the track list behind the overlay first (1649-1680).
      let tracks = Array.isArray(playlist.tracks) ? (playlist.tracks as unknown[]) : [];
      if (tracks.length === 0) {
        window.showLoadingOverlay?.(`Loading ${asString(playlist.name)}...`);
        try {
          const fullData = await fetchAccountPlaylist('qobuz', sourceId);
          const fetched = Array.isArray(fullData.tracks) ? (fullData.tracks as unknown[]) : [];
          if (fetched.length > 0) {
            tracks = qobuzFreshTracks(fetched);
            setPlaylistTracks(sourceId, tracks);
          }
        } catch {
          // Fall through to the no-tracks guard, like the vanilla's catch.
        }
      }
      window.hideLoadingOverlay?.();
      if (tracks.length === 0) {
        window.showToast?.('Could not load tracks for this playlist', 'error');
        return;
      }
      vertical.seed(sourceId, { ...playlist, tracks });
      onOpen(sourceId);
    },
    [config, chrome.base, vertical, onOpen, openSettled, setPlaylistTracks],
  );

  return (
    <div>
      <div className="playlist-header">
        <h3>{chrome.title}</h3>
        <button
          type="button"
          className={`refresh-button ${chrome.refreshBtnClass}`}
          id={chrome.refreshBtnId}
          disabled={loading}
          onClick={() => void load()}
        >
          {loading ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container" id={chrome.containerId}>
        {errorText ? (
          <div className="playlist-placeholder">{errorText}</div>
        ) : playlists === null ? (
          <div className="playlist-placeholder">
            {loading ? chrome.loadingPlaceholder : chrome.initialPlaceholder}
          </div>
        ) : playlists.length === 0 ? (
          <div className="playlist-placeholder">{`No ${chrome.noun} playlists found.`}</div>
        ) : (
          playlists.map((p) => {
            const sourceId = String(p.id);
            const state = vertical.states[sourceId] ?? freshSourceState(config, sourceId);
            return (
              <SourceCard
                key={sourceId}
                id={`${chrome.cardIdPrefix}-${sourceId}`}
                cardClassName={chrome.cardClassName}
                icon="🎵"
                name={asString(p.name)}
                countText={`${(p.track_count as number | undefined) ?? 0} tracks`}
                phase={state.phase}
                progressLine={cardProgressLine(state, config)}
                onClick={() => void openCard(p)}
              />
            );
          })
        )}
      </div>
    </div>
  );
}

export function TidalTab({
  vertical,
  onOpen,
}: {
  vertical: SourceVertical;
  onOpen: (sourceId: string) => void;
}) {
  return (
    <AccountVerticalTab
      config={SYNC_SOURCES.tidal}
      chrome={{
        base: 'tidal',
        title: 'Your Tidal Playlists',
        refreshBtnId: 'tidal-refresh-btn',
        refreshBtnClass: 'tidal',
        containerId: 'tidal-playlist-container',
        initialPlaceholder: "Click 'Refresh' to load your Tidal playlists.",
        loadingPlaceholder: '🔄 Loading Tidal playlists...',
        noun: 'Tidal',
        cardIdPrefix: 'tidal-card',
        cardClassName: 'tidal-playlist-card',
      }}
      vertical={vertical}
      onOpen={onOpen}
    />
  );
}

export function QobuzTab({
  vertical,
  onOpen,
}: {
  vertical: SourceVertical;
  onOpen: (sourceId: string) => void;
}) {
  return (
    <AccountVerticalTab
      config={SYNC_SOURCES.qobuz}
      chrome={{
        base: 'qobuz',
        title: 'Your Qobuz Playlists',
        refreshBtnId: 'qobuz-refresh-btn',
        refreshBtnClass: 'qobuz',
        containerId: 'qobuz-playlist-container',
        initialPlaceholder: "Click 'Refresh' to load your Qobuz playlists.",
        loadingPlaceholder: '🔄 Loading Qobuz playlists...',
        noun: 'Qobuz',
        cardIdPrefix: 'qobuz-card',
        cardClassName: 'qobuz-playlist-card',
      }}
      vertical={vertical}
      onOpen={onOpen}
    />
  );
}
