/**
 * The dashboard header controller — pill state for the 17 worker orbs, their
 * click behaviours, the fallback polling, the JioSaavn/Hydrabase visibility
 * flags, and the watchlist/wishlist quick-nav counts.
 *
 * State model: each orb holds a MATERIALIZED pill — the last rendered strings —
 * because the reducers' `current: null` / `progress: null` mean KEEP THE
 * PREVIOUS TEXT (the vanilla's stale-tooltip no-else quirk, decided here at the
 * UI layer as P1 deferred). A frame's null fields merge over the previous
 * strings; the defaults are the markup's initial texts.
 *
 * Data sources, mirroring the vanilla's two paths per provider:
 * - `ss:enrich-status` / `ss:repair-status` window events (dispatched inside
 *   the vanilla handlers — socket frames land here).
 * - A fallback poll: the status-all bundle + Hydrabase every 10s, repair every
 *   5s (the vanilla's cadences), each tick gated on `document.hidden` like the
 *   originals. The vanilla also gated on `socketConnected`, but that flag is a
 *   script-scoped `let` no module can read — so React polls regardless. That is
 *   one cheap bundle request per 10s against the vanilla's 13 gated requests,
 *   and last-write-wins over identical payloads is harmless.
 *
 * Toggles read the pill's stateClass where the vanilla read
 * `button.classList.contains(...)` — same value, React-owned. After a
 * successful toggle the provider is refetched immediately (the vanilla's
 * `await update<X>Status()`), without the hidden/socket gates: the user just
 * clicked, the page is visible.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { EnrichEventId } from './-dash.events';
import type { PillView, ProviderStatusPayload } from './-dash.types';

import {
  fetchAllProviderStatuses,
  fetchDevMode,
  fetchHydrabaseStatus,
  fetchProviderStatus,
  fetchRepairStatus,
  fetchWatchlistCount,
  fetchWishlistCount,
  setHydrabaseRunning,
  setProviderRunning,
} from './-dash.api';
import {
  amazonPill,
  audiodbPill,
  bandcampPill,
  deezerPill,
  discogsPill,
  formatCountdownTime,
  geniusPill,
  hydrabasePill,
  itunesPill,
  jiosaavnPill,
  lastfmPill,
  musicbrainzPill,
  qobuzPill,
  repairFindingsBadge,
  repairPill,
  similarArtistsPill,
  soulidPill,
  spotifyPill,
  tidalPill,
} from './-dash.core';
import {
  useDashboardWishlistCountEvent,
  useDevModeEvent,
  useEnrichStatusEvent,
  useJiosaavnExperimentalEvent,
  useRepairStatusEvent,
  useWatchlistCountEvent,
} from './-dash.events';

export type HeaderPillId = EnrichEventId | 'repair';

/** A pill as rendered: the reducers' nullable fields resolved over the
 *  previously shown text. */
export interface HeaderPill {
  stateClass: PillView['stateClass'];
  status: string;
  current: string;
  progress: string;
  statusColor?: string;
}

const PILL_REDUCERS: Record<EnrichEventId, (data: ProviderStatusPayload) => PillView> = {
  musicbrainz: musicbrainzPill,
  audiodb: audiodbPill,
  discogs: discogsPill,
  deezer: deezerPill,
  jiosaavn: jiosaavnPill,
  spotify: spotifyPill,
  itunes: itunesPill,
  lastfm: lastfmPill,
  genius: geniusPill,
  bandcamp: bandcampPill,
  tidal: tidalPill,
  qobuz: qobuzPill,
  amazon: amazonPill,
  similar_artists: similarArtistsPill,
  hydrabase: hydrabasePill,
  soulid: soulidPill,
};

/** The markup's initial tooltip texts, per orb (index.html 2227-2624). */
export const PILL_DEFAULTS: Record<HeaderPillId, HeaderPill> = {
  musicbrainz: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  audiodb: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  deezer: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  jiosaavn: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  spotify: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  itunes: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  lastfm: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  genius: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  bandcamp: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  tidal: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  qobuz: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  discogs: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  amazon: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  similar_artists: {
    stateClass: null,
    status: 'Idle',
    current: 'No active matches',
    progress: 'Progress: 0 / 0',
  },
  // Hydrabase's tooltip has only the status line; current/progress never render.
  hydrabase: { stateClass: null, status: 'Active', current: '', progress: '' },
  repair: {
    stateClass: null,
    status: 'Idle',
    current: 'No active repairs',
    progress: 'Progress: 0 / 0',
  },
  soulid: {
    stateClass: null,
    status: 'Idle',
    current: 'No items processing',
    progress: 'Progress: 0 pending',
  },
};

/** The wording of each standard toggle's failure message, verbatim from its
 *  vanilla `throw new Error(...)`. Spotify/Discogs/Bandcamp/Hydrabase have
 *  their own toggle bodies below; Repair is a link and SoulID has no click. */
const TOGGLE_LABELS: Partial<Record<EnrichEventId, string>> = {
  musicbrainz: 'MusicBrainz',
  audiodb: 'AudioDB',
  deezer: 'Deezer',
  jiosaavn: 'JioSaavn',
  itunes: 'iTunes',
  lastfm: 'Last.fm',
  genius: 'Genius',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  amazon: 'Amazon',
  similar_artists: 'Similar Artists',
};

function materialize(previous: HeaderPill, view: PillView): HeaderPill {
  const next: HeaderPill = {
    stateClass: view.stateClass,
    status: view.status,
    // null = the vanilla skipped this write — the previous text stays.
    current: view.current ?? previous.current,
    progress: view.progress ?? previous.progress,
  };
  if (view.statusColor !== undefined) next.statusColor = view.statusColor;
  return next;
}

export interface WatchlistHero {
  count: number;
  /** Set once a countdown arrives; absent renders no title attribute, like the
   *  markup. The vanilla only overwrites the title when the countdown text is
   *  non-empty, so a later payload without one KEEPS the previous title. */
  title?: string;
  /** The bare countdown text ("2h 13m") for the hello strip — same
   *  keep-on-empty semantics as title. */
  countdown?: string;
}

export interface DashboardHeaderState {
  pills: Record<HeaderPillId, HeaderPill>;
  repairBadge: { count: number; visible: boolean };
  /** null before the first successful click/toggle refresh — no handler. */
  onOrbClick: (id: HeaderPillId) => void;
  jiosaavnVisible: boolean;
  hydrabaseVisible: boolean;
  watchlist: WatchlistHero;
  /** null before the first count payload — neither wishlist-active nor
   *  wishlist-inactive is on the button yet, like the markup. */
  wishlistCount: number | null;
}

export function useDashboardHeader(): DashboardHeaderState {
  const [pills, setPills] = useState<Record<HeaderPillId, HeaderPill>>(PILL_DEFAULTS);
  const [repairBadge, setRepairBadge] = useState({ count: 0, visible: false });
  const [jiosaavnVisible, setJiosaavnVisible] = useState<boolean>(
    () => window.isJiosaavnExperimentalEnabled?.() ?? false,
  );
  const [hydrabaseVisible, setHydrabaseVisible] = useState(false);
  const [watchlist, setWatchlist] = useState<WatchlistHero>({ count: 0 });
  const [wishlistCount, setWishlistCount] = useState<number | null>(null);

  // The toggles read the CURRENT stateClass (the vanilla read the live
  // classList); a ref avoids stale closures without re-creating handlers.
  const pillsRef = useRef(pills);
  pillsRef.current = pills;
  const mountedRef = useRef(true);

  const applyFrame = useCallback((id: HeaderPillId, data: ProviderStatusPayload) => {
    if (!mountedRef.current) return;
    const reduce = id === 'repair' ? repairPill : PILL_REDUCERS[id];
    if (!reduce) return;
    setPills((prev) => ({ ...prev, [id]: materialize(prev[id], reduce(data)) }));
    if (id === 'repair') setRepairBadge(repairFindingsBadge(data));
  }, []);

  useEnrichStatusEvent(
    useCallback(
      (frame) => {
        if (frame && frame.id && frame.data) applyFrame(frame.id, frame.data);
      },
      [applyFrame],
    ),
  );
  useRepairStatusEvent(
    useCallback((data) => applyFrame('repair', data as ProviderStatusPayload), [applyFrame]),
  );

  // ── Fallback polling (socket-down safety net; see the header comment) ──────
  useEffect(() => {
    mountedRef.current = true;

    const enrichTick = async () => {
      // Same gates as the vanilla poller twins: the socket push owns live
      // updates (window._socketConnected is core.js's mirror of its
      // script-scoped flag), and hidden tabs don't poll.
      if (window._socketConnected) return;
      if (document.hidden) return;
      const [bundle, hydrabase] = await Promise.all([
        fetchAllProviderStatuses(),
        fetchHydrabaseStatus(),
      ]);
      for (const [id, data] of Object.entries(bundle)) {
        if (data) applyFrame(id as EnrichEventId, data);
      }
      if (hydrabase) applyFrame('hydrabase', hydrabase);
    };
    // Repair has NO interval here on purpose: enrichment.js keeps an APP-WIDE
    // 5s fallback poll whose handler is dispatch-only (ss:repair-status), and
    // the tools maintenance hero consumes the same channel — a second interval
    // would double the socket-down request rate. One mount hydrate covers the
    // gap until that poll's next tick.
    const repairHydrate = async () => {
      const data = await fetchRepairStatus();
      if (data) applyFrame('repair', data as unknown as ProviderStatusPayload);
    };

    void enrichTick();
    void repairHydrate();
    const enrichTimer = setInterval(() => void enrichTick(), 10000);
    return () => {
      mountedRef.current = false;
      clearInterval(enrichTimer);
    };
  }, [applyFrame]);

  // ── Refetch-own-provider after a toggle (`await update<X>Status()`) ────────
  const refreshProvider = useCallback(
    async (id: EnrichEventId) => {
      if (id === 'hydrabase') {
        const data = await fetchHydrabaseStatus();
        if (data) applyFrame('hydrabase', data);
        return;
      }
      const data = await fetchProviderStatus(id);
      if (data) applyFrame(id, data);
    },
    [applyFrame],
  );

  const onOrbClick = useCallback(
    (id: HeaderPillId) => {
      const stateClass = pillsRef.current[id]?.stateClass;

      if (id === 'repair') {
        // The repair orb is a LINK: navigate to tools + scroll to the hero.
        // openRepairModal survived the tools flip exactly for this call.
        window.openRepairModal?.();
        return;
      }
      if (id === 'soulid') return; // display-only — the vanilla binds no click

      if (id === 'hydrabase') {
        void (async () => {
          const isRunning = stateClass === 'active';
          try {
            await setHydrabaseRunning(!isRunning);
            await refreshProvider('hydrabase');
          } catch (error) {
            console.error('Error toggling Hydrabase worker:', error);
          }
        })();
        return;
      }

      if (id === 'discogs') {
        void (async () => {
          // Inverted read: paused-or-complete resumes; anything else pauses.
          const isPaused = stateClass === 'paused' || stateClass === 'complete';
          try {
            const response = await setProviderRunning('discogs', isPaused);
            if (response.ok) {
              window.showToast?.(
                isPaused ? 'Discogs enrichment resumed' : 'Discogs enrichment paused',
                'info',
              );
            }
          } catch {
            window.showToast?.('Failed to toggle Discogs enrichment', 'error');
          }
          // No refetch — Discogs is websocket-only (no status fetcher), as in
          // the vanilla toggle.
        })();
        return;
      }

      if (id === 'spotify') {
        void (async () => {
          const isRunning = stateClass === 'active';
          try {
            const response = await setProviderRunning('spotify', !isRunning);
            if (!response.ok) {
              const data = (await response.json().catch(() => ({}))) as {
                rate_limited?: boolean;
              };
              if (data.rate_limited) {
                window.showToast?.('Cannot resume — Spotify is rate limited', 'warning');
                return;
              }
              throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} Spotify enrichment`);
            }
            await refreshProvider('spotify');
          } catch (error) {
            console.error('Error toggling Spotify enrichment:', error);
            window.showToast?.(`Error: ${(error as Error).message}`, 'error');
          }
        })();
        return;
      }

      if (id === 'bandcamp') {
        // Disabled (no-auth) — toggle via Settings instead, as the vanilla
        // refuses before any request.
        if (stateClass === 'no-auth') return;
      }

      const label = id === 'bandcamp' ? 'Bandcamp' : TOGGLE_LABELS[id];
      if (!label) return;
      void (async () => {
        const isRunning = stateClass === 'active';
        try {
          const response = await setProviderRunning(id, !isRunning);
          if (!response.ok) {
            throw new Error(`Failed to ${isRunning ? 'pause' : 'resume'} ${label} enrichment`);
          }
          await refreshProvider(id);
        } catch (error) {
          console.error(`Error toggling ${label} enrichment:`, error);
          window.showToast?.(`Error: ${(error as Error).message}`, 'error');
        }
      })();
    },
    [refreshProvider],
  );

  // ── Orb visibility (JioSaavn experimental / Hydrabase dev mode) ────────────
  useJiosaavnExperimentalEvent(
    useCallback((frame) => setJiosaavnVisible(frame.enabled === true), []),
  );
  useDevModeEvent(useCallback((frame) => setHydrabaseVisible(frame.enabled === true), []));
  useEffect(() => {
    let cancelled = false;
    void fetchDevMode().then((enabled) => {
      if (!cancelled && enabled) setHydrabaseVisible(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // ── Quick-nav counts ───────────────────────────────────────────────────────
  const applyWatchlist = useCallback(
    (data: { count?: number; next_run_in_seconds?: number | null }) => {
      if (!mountedRef.current) return;
      setWatchlist((prev) => {
        const countdownText = data.next_run_in_seconds
          ? formatCountdownTime(data.next_run_in_seconds)
          : '';
        return {
          count: data.count || 0,
          title: countdownText ? `Next auto-scan in ${countdownText}` : prev.title,
          countdown: countdownText || prev.countdown,
        };
      });
    },
    [],
  );

  useWatchlistCountEvent(
    useCallback(
      (frame) => {
        // The handler dispatches the raw payload BEFORE its own success gate —
        // apply the same gate here.
        if ((frame as { success?: boolean }).success) applyWatchlist(frame);
      },
      [applyWatchlist],
    ),
  );
  useDashboardWishlistCountEvent(
    useCallback((frame) => {
      if (mountedRef.current) setWishlistCount(frame.count || 0);
    }, []),
  );

  useEffect(() => {
    const watchlistTick = async () => {
      if (window._socketConnected) return;
      if (document.hidden) return;
      const result = await fetchWatchlistCount();
      if (result) applyWatchlist(result);
    };
    void watchlistTick();
    // updateWatchlistButtonCount's 10s fallback cadence (init.js).
    const timer = setInterval(() => void watchlistTick(), 10000);

    // Wishlist has no vanilla poller on the dashboard — mount fetch + socket.
    void fetchWishlistCount().then((count) => {
      if (mountedRef.current && count !== null) setWishlistCount(count);
    });

    return () => clearInterval(timer);
  }, [applyWatchlist]);

  return {
    pills,
    repairBadge,
    onOrbClick,
    jiosaavnVisible,
    hydrabaseVisible,
    watchlist,
    wishlistCount,
  };
}
