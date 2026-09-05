import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { WebLens } from '../-discover.artist-web';
import type { CacheItem } from '../-discover.cache-sections';
import type { DiscoverSectionId } from '../-discover.layout';
import type { DiscoverMix, MixAction } from '../-discover.mixes';
import type { RecentAlbum } from '../-discover.recent-releases';
import type { RecommendedArtist } from '../-discover.recommended';
import type { SeasonData, SeasonalAlbum } from '../-discover.seasonal';
import type { DiscoverHeroArtist } from '../-discover.types';
import type { YourAlbum } from '../-discover.your-albums-actions';
import type { GenreDiveData } from './genre-dive-modal';

import { fetchBecauseYouListenTo, fetchGenreDeepDive, fetchLbPlaylist } from '../-discover.api';
import { bpMetaStats } from '../-discover.build-playlist';
import { byltSections, type ByltSection } from '../-discover.bylt';
import { CACHE_SECTIONS } from '../-discover.cache-sections';
import { decadeClassicsName, decadeTrackToSpotify } from '../-discover.decade-shelf';
import { normalizeTrack } from '../-discover.helpers';
import { discoverLimiter } from '../-discover.limiter';
import {
  lbStatusBase,
  lbSyncFailedId,
  lbSyncMatchedId,
  lbSyncPercentageId,
  lbSyncTotalId,
} from '../-discover.listenbrainz';
import { beginPlayIntent, playMixNow, playTrackNow, type PlayIntent } from '../-discover.playable';
import { syncBubbleImage, toSyncTracks } from '../-discover.playlist-sync';
import { recSource, recommendedVisible } from '../-discover.recommended';
import { useAlbumOpen } from '../-discover.use-album-open';
import { useArtistMap } from '../-discover.use-artist-map';
import { useBlacklist } from '../-discover.use-blacklist';
import { useBuildPlaylist } from '../-discover.use-build-playlist';
import { useDownloadBar } from '../-discover.use-download-bar';
import { useHero } from '../-discover.use-hero';
import { useLastfmRadio } from '../-discover.use-lastfm-radio';
import { useListenBrainz } from '../-discover.use-listenbrainz';
import { defaultLazySource, useMixModal } from '../-discover.use-mix-modal';
import { useDiscoverMixes } from '../-discover.use-mixes';
import { useDiscoverPage } from '../-discover.use-page';
import { usePlaylistSync } from '../-discover.use-playlist-sync';
import { useAdventurousness, useRecommended } from '../-discover.use-recommended';
import { useYourAlbums } from '../-discover.use-your-albums';
import { useYourArtists } from '../-discover.use-your-artists';
import {
  ARTISTS_DEFAULT_SOURCES,
  savedArtistSourcesSubtitle,
} from '../-discover.your-artists-actions';
import { AdventurousnessDial } from './adventurousness-dial';
import { RecentReleasesShelf, SeasonalAlbumsShelf } from './album-shelves';
import { ArtistInfoModal } from './artist-info-modal';
import { ArtistMapAssembly } from './artist-map-assembly';
import { ArtistMapHub, ArtistWebHub } from './artist-map-hub';
import { ArtistWebAssembly } from './artist-web-assembly';
import { ArtMapExplorePrompt } from './artmap-explore-prompt';
import { BlacklistModal } from './blacklist-modal';
import { BuildPlaylistSection } from './build-playlist';
import { ByltSections } from './bylt-sections';
import { CacheShelf, GenreExplorerSection } from './cache-shelves';
import { DiscoverHero } from './discover-hero';
import { DownloadBar } from './download-bar';
import { GenreDiveModal } from './genre-dive-modal';
import { MixModal } from './mix-modal';
import { MixShelf } from './mix-shelf';
import { LastfmRadioSection, ListenBrainzSection } from './radio-sections';
import { RecommendedModal } from './recommended-modal';
import { RecommendedShelf } from './recommended-shelf';
import { YourAlbumsSourcesModal, YourArtistsSourcesModal } from './sources-modals';
import { StationsRow } from './stations-row';
import { YourAlbumsBatchModal } from './your-albums-batch-modal';
import { YourAlbumsShelf } from './your-albums-shelf';
import { YourArtistsModal } from './your-artists-modal';
import { YourArtistsShelf } from './your-artists-shelf';

/**
 * The discover page — every controller composed over DISCOVER_LAYOUT.
 *
 * Section ORDER is `buildLayoutRows(hasContent)` over the vanilla's layout
 * array; the hero and the Artist Map hub sit above the rows, exactly where
 * `_reorderDiscoverSections` leaves them (265). Data comes from
 * useDiscoverPage's tiered queries; interactions from one controller per
 * section family; the two viz overlays render INSTEAD of the container when
 * open — the vanilla's `_prevDisplay` sibling bookkeeping has nothing left to
 * do.
 */

/** buildArtistDetailPath (init.js 2964): /artist-detail/<source>/<id>. */
function detailPath(artistId: unknown, source?: string | null, name?: string | null): string {
  const normalized =
    String(source ?? '')
      .trim()
      .toLowerCase() || 'library';
  const path = `/artist-detail/${encodeURIComponent(normalized)}/${encodeURIComponent(String(artistId))}`;
  return name ? `${path}?name=${encodeURIComponent(name)}` : path;
}

/** The source-logo urls core.js holds as globals (915-922) — shared assets. */
const LOGOS = {
  spotify: '/static/img/brands/spotify.png',
  itunes: '/static/img/brands/itunes.png',
  deezer: '/static/img/brands/deezer.png',
  discogs: '/static/img/brands/discogs.svg',
};

const toast = (message: string, level = 'info') => window.showToast?.(message, level);

/**
 * progressFor hands back the REDUCED SyncProgress; SyncStatus re-reduces from
 * the wire shape, so the reduced counts ride back in under the wire keys.
 */
function toRawProgress(p: { total: number; matched: number; failed: number } | undefined) {
  return p
    ? { total_tracks: p.total, matched_tracks: p.matched, failed_tracks: p.failed }
    : undefined;
}

/**
 * The LB sync-status block, transcribed from the statusHtml at 3595-3608.
 *
 * Rendered hidden; sync-services.js flips the container's display and fills
 * the spans by id while its poll runs — the ids are the contract.
 */
function LbSyncStatus({ identifier }: { identifier: string }) {
  return (
    <div
      className="discover-sync-status"
      id={`${lbStatusBase(identifier)}-sync-status`}
      style={{ display: 'none' }}
    >
      <div className="sync-status-content">
        <div className="sync-status-label">
          <span className="sync-icon">⟳</span>
          <span>Syncing to media server...</span>
        </div>
        <div className="sync-status-stats">
          <span className="sync-stat">
            ♪ <span id={lbSyncTotalId(identifier)}>0</span>
          </span>
          <span className="sync-separator">/</span>
          <span className="sync-stat">
            ✓ <span id={lbSyncMatchedId(identifier)}>0</span>
          </span>
          <span className="sync-separator">/</span>
          <span className="sync-stat">
            ✗ <span id={lbSyncFailedId(identifier)}>0</span>
          </span>
          <span className="sync-stat">
            (<span id={lbSyncPercentageId(identifier)}>0</span>%)
          </span>
        </div>
      </div>
    </div>
  );
}

/** A section outcome's payload when it succeeded, else undefined. */
function okData<T>(outcome: unknown): T | undefined {
  const o = outcome as { kind?: string; data?: T } | undefined;
  return o?.kind === 'ok' ? o.data : undefined;
}

interface DiscoveryActionCard {
  id: string;
  eyebrow: string;
  title: string;
  detail: string;
  value: string;
  target: string;
}

interface DiscoveryInsight {
  eyebrow: string;
  title: string;
  detail: string;
  value: string;
  actionLabel: string;
  target: string;
}

function scrollToDiscoveryTarget(target: string) {
  document.getElementById(target)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function DiscoveryActionCard({ card }: { card: DiscoveryActionCard }) {
  return (
    <button
      type="button"
      className={`discover-action-card discover-action-card--${card.id}`}
      onClick={() => scrollToDiscoveryTarget(card.target)}
    >
      <span className="discover-action-card__eyebrow">{card.eyebrow}</span>
      <span className="discover-action-card__title">{card.title}</span>
      <span className="discover-action-card__detail">{card.detail}</span>
      <span className="discover-action-card__value">{card.value}</span>
    </button>
  );
}

function DiscoveryCommandPanel({
  cards,
  insight,
  onBuildPlaylist,
  onOpenMap,
  onOpenRecommended,
}: {
  cards: DiscoveryActionCard[];
  insight: DiscoveryInsight;
  onBuildPlaylist: () => void;
  onOpenMap: () => void;
  onOpenRecommended: () => void;
}) {
  return (
    <aside className="discover-command-panel" aria-label="Discovery shortcuts">
      <div className="discover-command-panel__head">
        <span className="discover-command-kicker">Discovery Queue</span>
        <h2>Start Here</h2>
        <p>The strongest moves from your library, listening history, release gaps, and builders.</p>
      </div>
      <button
        type="button"
        className="discover-next-move"
        onClick={() => scrollToDiscoveryTarget(insight.target)}
      >
        <span className="discover-next-move__eyebrow">{insight.eyebrow}</span>
        <strong>{insight.title}</strong>
        <span>{insight.detail}</span>
        <span className="discover-next-move__foot">
          <em>{insight.value}</em>
          <b>{insight.actionLabel}</b>
        </span>
      </button>
      <div className="discover-action-grid">
        {cards.map((card) => (
          <DiscoveryActionCard key={card.id} card={card} />
        ))}
      </div>
      <div className="discover-command-tools">
        <button type="button" onClick={onBuildPlaylist}>
          Build playlist
        </button>
        <button type="button" onClick={onOpenMap}>
          Artist map
        </button>
        <button type="button" onClick={onOpenRecommended}>
          Recommended
        </button>
      </div>
    </aside>
  );
}

interface DiscoveryZoneProps {
  id: string;
  title: string;
  subtitle: string;
  tone: string;
  metric: string;
  children: React.ReactNode;
}

function DiscoveryZone({ id, title, subtitle, tone, metric, children }: DiscoveryZoneProps) {
  return (
    <section className={`discovery-zone discovery-zone--${tone}`} id={id}>
      <header className="discovery-zone-head">
        <div>
          <span className="discovery-zone-kicker">{tone.replace('-', ' ')}</span>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        <span className="discovery-zone-metric">{metric}</span>
      </header>
      <div className="discovery-zone-body">{children}</div>
    </section>
  );
}

export function DiscoverPage() {
  const page = useDiscoverPage();
  const mixes = useDiscoverMixes(page.aboveFoldSettled);
  const sync = usePlaylistSync((t) => toast(t.message, t.level));
  const bar = useDownloadBar();
  const albumOpen = useAlbumOpen((t) => toast(t.message, t.level));
  const heroArtists = useMemo(
    () => (page.hero.data?.artists ?? []) as DiscoverHeroArtist[],
    [page.hero.data],
  );
  const hero = useHero(heroArtists, (t) => toast(t.message, t.level));
  const rec = useRecommended((t) => toast(t.message, t.level));
  const dial = useAdventurousness(
    okData<{ value?: number }>(page.sections.adventurousness?.data)?.value ?? 0.3,
  );
  const artists = useYourArtists((t) => toast(t.message, t.level));
  const albums = useYourAlbums((t) => toast(t.message, t.level));
  const blacklist = useBlacklist((t) => toast(t.message, t.level));
  const lastfm = useLastfmRadio((t) => toast(t.message, t.level));
  const lb = useListenBrainz((t) => toast(t.message, t.level));
  const bp = useBuildPlaylist((t) => toast(t.message, t.level));
  const map = useArtistMap((t) => toast(t.message, t.level));

  const [webRequest, setWebRequest] = useState<{ lens?: WebLens } | null>(null);
  const [dive, setDive] = useState<{
    genre: string;
    data: GenreDiveData | null;
    phase: 'loading' | 'error' | 'ready';
  } | null>(null);
  const [recModalOpen, setRecModalOpen] = useState(false);
  const [addingAll, setAddingAll] = useState(false);
  const [expandedCaches, setExpandedCaches] = useState<Record<string, boolean>>({});
  const [explorerPromptOpen, setExplorerPromptOpen] = useState(false);
  const [lbCovers, setLbCovers] = useState<Record<string, unknown[]>>({});

  // BYLT never joined use-page's tiers (its loader renders '' when empty), so
  // the page queries it directly with the same limiter/no-retry shape.
  const bylt = useQuery({
    queryKey: ['discover', 'bylt'] as const,
    queryFn: () => discoverLimiter.run(fetchBecauseYouListenTo),
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
    retry: false,
    enabled: page.aboveFoldSettled,
  });
  const byltRows: ByltSection[] = byltSections(okData(bylt.data) ?? null);

  // Obligation: resume mid-flight syncs + rehydrate yesterday's download bar.
  useEffect(() => {
    void sync.resumeActiveSyncs();
    void bar.hydrate();
    // The LB discovery states hydrate from the backend at page init
    // (discover.js 244) — without this, a restart forgets every in-flight
    // discovery/download and the resume branches find no state.
    void window.loadListenBrainzPlaylistsFromBackend?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── The mix modal: ONE registry over every section's mixes ──────────────
  const registry = useMemo(() => {
    const all: Record<string, DiscoverMix> = { ...mixes.registry };
    for (const m of [...lb.mixes, ...lastfm.mixes]) all[m.key] = m;
    // Hydrated LB tracks ride into the registry, so opening a card the
    // mosaic pass already fetched is instant — the vanilla gets the same
    // effect by mutating mix.tracks in place (4874).
    for (const [key, tracks] of Object.entries(lbCovers)) {
      if (all[key] && !all[key].tracks?.length && tracks.length) {
        all[key] = { ...all[key], tracks };
      }
    }
    return all;
  }, [mixes.registry, lb.mixes, lastfm.mixes, lbCovers]);

  // lb-* keys lazy-load from the playlist endpoint — one resolver serves
  // ListenBrainz AND Last.fm radio mixes (both key `lb-<tab>-<identifier>`).
  const lbLazy = useCallback((mix: DiscoverMix) => {
    if (!mix.key.startsWith('lb-')) return null;
    const identifier = mix.key.split('-').slice(2).join('-');
    return async () => {
      const data = (await fetchLbPlaylist(identifier)) as { tracks?: unknown[] };
      return data.tracks ?? [];
    };
  }, []);
  const modal = useMixModal(registry, lbLazy);

  // which mix key / track row is resolving. two fast taps used to queue the
  // same thing twice, and nothing on screen said anything was happening.
  const pendingPlay = useRef<{ owner: string; intent: PlayIntent } | null>(null);
  const [playingMixKey, setPlayingMixKey] = useState<string | null>(null);
  const [playingTrackIndex, setPlayingTrackIndex] = useState<number | null>(null);

  /** Play a mix straight from its card, fetching a lazy tracklist first. */
  const playMixFromCard = useCallback(
    (key: string) => {
      // Only the SAME mix is blocked while it resolves. Blocking every other
      // card too left them looking live and doing nothing, which on a slow
      // resolve is a dead control for a minute.
      if (pendingPlay.current?.owner === `mix:${key}`) return;
      const intent = beginPlayIntent();
      pendingPlay.current = { owner: `mix:${key}`, intent };
      setPlayingTrackIndex(null);
      setPlayingMixKey(key);
      void (async () => {
        try {
          const tracks = await modal.loadTracks(key);
          if (!intent.isCurrent()) return;
          if (tracks === null) {
            toast('Could not load that mix', 'error');
            return;
          }
          await playMixNow(tracks, registry[key]?.title ?? 'Mix', intent);
        } finally {
          if (pendingPlay.current?.intent === intent) {
            pendingPlay.current = null;
            setPlayingMixKey(null);
          }
        }
      })();
    },
    [playingMixKey, modal, registry],
  );

  /** Play one row of the open mix. Resolved against the rendered list. */
  const playTrackFromModal = useCallback(
    (index: number) => {
      const owner = `track:${modal.mix?.key}:${index}`;
      if (pendingPlay.current?.owner === owner) return;
      const rows = modal.tracks ?? [];
      const track = rows[index];
      if (!track) {
        toast('That track is no longer in this mix', 'error');
        return;
      }
      const intent = beginPlayIntent();
      pendingPlay.current = { owner, intent };
      setPlayingMixKey(null);
      setPlayingTrackIndex(index);
      void (async () => {
        try {
          await playTrackNow(track, normalizeTrack(track as never).name, intent);
        } finally {
          if (pendingPlay.current?.intent === intent) {
            pendingPlay.current = null;
            setPlayingTrackIndex(null);
          }
        }
      })();
    },
    [playingTrackIndex, modal.tracks],
  );

  // The cover-mosaic hydration (_hydrateMixCovers, 4870): LB cards arrive
  // track-less, so each one background-fetches its playlist once and the card
  // re-renders with a real 4-tile mosaic and an honest track count.
  const lbCoversRef = useRef(lbCovers);
  lbCoversRef.current = lbCovers;
  // Deferred: the eager version fetched EVERY LB playlist and EVERY decade
  // playlist at mount just to paint 4-tile mosaics — dozens of requests
  // before the user scrolled anywhere near those shelves. Each shelf now
  // hydrates when it actually enters the viewport. Environments without
  // IntersectionObserver (jsdom) keep the eager behaviour.
  const [mosaicsWanted, setMosaicsWanted] = useState<{ lb: boolean; decades: boolean }>(() => {
    const eager = typeof IntersectionObserver === 'undefined';
    return { lb: eager, decades: eager };
  });
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    if (mosaicsWanted.lb && mosaicsWanted.decades) return;
    const anchors: [keyof typeof mosaicsWanted, HTMLElement | null][] = [
      ['lb', document.getElementById('listenbrainz')],
      ['decades', document.getElementById('year-mixes-grid')],
    ];
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const hit = anchors.find(([, el]) => el === entry.target)?.[0];
          if (hit) {
            setMosaicsWanted((w) => (w[hit] ? w : { ...w, [hit]: true }));
            observer.unobserve(entry.target);
          }
        }
      },
      // Start the fetch a screen early so the mosaics are painted by arrival.
      { rootMargin: '600px' },
    );
    for (const [key, el] of anchors) {
      if (el && !mosaicsWanted[key]) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [mosaicsWanted, lb.mixes.length, mixes.decadeMixes.length]);
  useEffect(() => {
    const due = [
      ...(mosaicsWanted.lb ? lb.mixes : []),
      ...(mosaicsWanted.decades ? mixes.decadeMixes : []),
      // lastfm radios are few (one per generated radio) and were never
      // hydrated at all - every card wore four placeholder tiles forever
      ...lastfm.mixes,
    ];
    for (const mix of due) {
      if (mix.tracks?.length || lbCoversRef.current[mix.key]) continue;
      const lazy = defaultLazySource(mix) ?? lbLazy(mix);
      if (!lazy) continue;
      setLbCovers((prev) => ({ ...prev, [mix.key]: [] })); // in-flight marker
      lazy()
        .then((tracks) => setLbCovers((prev) => ({ ...prev, [mix.key]: tracks })))
        .catch(() => {});
    }
  }, [lb.mixes, mixes.decadeMixes, lastfm.mixes, lbLazy, mosaicsWanted]);
  const decadeMixesHydrated = useMemo(
    () =>
      mixes.decadeMixes.map((mix) =>
        !mix.tracks?.length && lbCovers[mix.key]?.length
          ? { ...mix, tracks: lbCovers[mix.key] }
          : mix,
      ),
    [mixes.decadeMixes, lbCovers],
  );
  const lbMixesHydrated = useMemo(
    () =>
      lb.mixes.map((mix) =>
        !mix.tracks?.length && lbCovers[mix.key]?.length
          ? { ...mix, tracks: lbCovers[mix.key] }
          : mix,
      ),
    [lb.mixes, lbCovers],
  );
  const lastfmMixesHydrated = useMemo(
    () =>
      lastfm.mixes.map((mix) =>
        !mix.tracks?.length && lbCovers[mix.key]?.length
          ? { ...mix, tracks: lbCovers[mix.key] }
          : mix,
      ),
    [lastfm.mixes, lbCovers],
  );

  /** Convert + hand to the SHARED downloads.js modal (11129-11160). */
  const openTracksModal = useCallback((virtualId: string, name: string, rows: unknown[]) => {
    if (!rows.length) {
      toast('No tracks available yet', 'warning');
      return false;
    }
    const spotifyTracks = toSyncTracks(rows as Record<string, unknown>[]);
    void window.openDownloadMissingModalForYouTube?.(virtualId, name, spotifyTracks);
    return true;
  }, []);

  /** A mix's action strings, built-in and lb- alike (4946-4952, 3934, 3223). */
  const runMixAction = useCallback(
    (action: MixAction) => {
      const mix = modal.mix;
      if (!mix) return;
      const [verb, ...rest] = action.onclick.split(':');
      if (verb === 'play') {
        // resolve against the library and play what's owned RIGHT NOW; the
        // rest stays a download away. LB/lastfm cards load tracks lazily -
        // with nothing loaded yet there is nothing resolvable, and
        // playMixNow's empty answer says so honestly.
        //
        // The busy state is not decoration: resolving 50 tracks against the
        // library takes a couple of seconds, and this button said nothing at
        // all for the whole wait.
        // Keep the tracklist open while listening. Completion must never close
        // another dialog opened while the request was pending.
        playMixFromCard(mix.key);
        return;
      }
      if (verb === 'lb-download') {
        // The FULL discovery flow (state check, rehydrate, seed + poll + the
        // sync-services discovery modal), verbatim in the core.js bridge —
        // Boulder's directive: exactly like vanilla, no difference. Serves
        // ListenBrainz AND Last.fm radio playlists (both are LB cards, 3378).
        modal.close();
        void (async () => {
          const identifier = rest.join(':');
          const tracks = modal.tracks?.length
            ? modal.tracks
            : (((await fetchLbPlaylist(identifier)) as { tracks?: unknown[] }).tracks ?? []);
          void window.openLbPlaylistDiscovery?.(identifier, mix.title, tracks);
        })();
        return;
      }
      if (verb === 'lb-sync') {
        // sync-services.js owns the WHOLE LB sync — fetch, virtual playlist,
        // polling — and writes into the -sync-total/-sync-matched spans the
        // modal's override renders (3592-3616). It survives PR2.
        //
        // A playlist whose state never left 'fresh' (a just-generated lastfm
        // radio) has no frontend sync state, and startListenBrainzPlaylistSync
        // console.errors and returns - a silently dead button. Say what to do.
        const lbId = rest.join(':');
        const states = window.listenbrainzPlaylistStates;
        if (states && !states[lbId]) {
          toast('Run Download first — sync needs its discovery results', 'info');
          return;
        }
        void window.startListenBrainzPlaylistSync?.(lbId);
        return;
      }
      const type = mix.syncKey ?? mix.key;
      if (action.isSync) {
        const out = sync.startMixSync(mix, modal.tracks);
        if (out) toast(out.message, out.level);
        // A sync ALSO registers a bar bubble (11344), art from the tracks.
        else {
          bar.add(
            `discover_${type}`,
            mix.title,
            type,
            syncBubbleImage(toSyncTracks((modal.tracks ?? []) as Record<string, unknown>[])),
          );
        }
      } else {
        modal.close();
        const rows = (modal.tracks ?? []) as Record<string, unknown>[];
        const decade = /^decade_(\d+)$/.exec(mix.key);
        if (!rows.length) {
          // The decade path words its refusal differently (2863).
          toast(
            decade ? 'No tracks available for this decade' : 'No tracks available yet',
            'warning',
          );
          return;
        }
        if (decade) {
          // The decade download is its OWN conversion and its OWN ids
          // (2860-2905): decade_<year>, "<year>s Classics", and artists stay
          // OBJECTS (DECADE_DOWNLOAD_KEEPS_ARTIST_OBJECTS) — toSyncTracks here
          // made every artist "Unknown Artist" in the live smoke test.
          const year = Number(decade[1]);
          void window.openDownloadMissingModalForYouTube?.(
            `decade_${year}`,
            decadeClassicsName(year),
            rows.map((t) => decadeTrackToSpotify(t, false)),
          );
        } else {
          void window.openDownloadMissingModalForYouTube?.(
            `discover_${type}`,
            mix.title,
            toSyncTracks(rows),
          );
        }
        // No bubble here: downloads.js registers it when the download actually
        // starts (1806) — a bubble at modal-open outlived a cancelled modal.
      }
    },
    [modal, sync, bar, openTracksModal, playMixFromCard],
  );

  const downloadSelection = useCallback(() => {
    const result = modal.downloadSelection();
    if (result.kind === 'ok') {
      modal.close();
      // result.tracks are ALREADY spotify-shaped (discoverTrackToSpotifyShape
      // ran inside downloadSelection). Re-converting them read track_name off
      // a shape that has `name` — every artist became "Unknown Artist" in the
      // live smoke test. Straight to the shared modal, no second conversion.
      void window.openDownloadMissingModalForYouTube?.(
        result.virtualId,
        result.name,
        result.tracks,
      );
    } else {
      toast(result.toast, result.level);
    }
  }, [modal]);

  const openDive = useCallback((genre: string) => {
    setDive({ genre, data: null, phase: 'loading' });
    fetchGenreDeepDive(genre)
      .then((data) => {
        setDive((d) =>
          d?.genre === genre
            ? { genre, data: data as unknown as GenreDiveData, phase: 'ready' }
            : d,
        );
      })
      .catch(() => {
        setDive((d) => (d?.genre === genre ? { genre, data: null, phase: 'error' } : d));
      });
  }, []);

  // ── Section payloads the render below reads more than once ─────────────
  const season = okData<SeasonData>(page.sections.seasonal?.data) ?? null;
  const recPayload = okData<{ artists?: RecommendedArtist[]; source?: string }>(
    page.sections.recommendedArtists?.data,
  );
  const recArtists = recommendedVisible(recPayload?.artists ?? []);

  // Once a shelf has rows: enrich image-less cards AND batch-confirm which
  // are already watched (checkRecommendedWatchlistStatuses, 694/801) — the
  // vanilla does both per shelf load, listening recs included.
  const listeningArtists = recommendedVisible(
    okData<{ artists?: RecommendedArtist[] }>(page.sections.listeningRecs?.data)?.artists ?? [],
  );
  useEffect(() => {
    if (recArtists.length) {
      void rec.enrichImages(recArtists, recSource(recPayload));
      void rec.checkWatching(recArtists);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recArtists.length]);
  useEffect(() => {
    if (listeningArtists.length) void rec.checkWatching(listeningArtists);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listeningArtists.length]);

  const hasContent = useCallback(
    (id: DiscoverSectionId): boolean => {
      if (id === 'lastfm-radio') return lastfm.configured === true;
      if (id === 'listenbrainz') return true; // renders its own load/error states
      if (id === 'build-a-playlist') return true; // a control, like adv-wave
      if (id === 'your-mixes-section') return mixes.mixes.length > 0;
      if (id === 'year-mixes-section') return mixes.decadeMixes.length > 0;
      if (id === 'discover-bylt-sections') return byltRows.length > 0;
      return page.hasContent(id);
    },
    [lastfm.configured, mixes, byltRows.length, page],
  );

  const cacheShelf = (id: DiscoverSectionId) => {
    const def = CACHE_SECTIONS.find((d) => d.id === id);
    if (!def) return null;
    const items = page.sectionState(id).items as CacheItem[];
    return (
      <CacheShelf
        def={def}
        items={items}
        expanded={Boolean(expandedCaches[id])}
        onToggleExpand={() => setExpandedCaches((e) => ({ ...e, [id]: !e[id] }))}
        onOpenItem={(key, index) => void albumOpen.openCacheItem(key, items[index])}
      />
    );
  };

  const renderSection = (id: DiscoverSectionId): React.ReactNode => {
    switch (id) {
      case 'cache-genre-explorer':
        return (
          <GenreExplorerSection
            genres={page.sectionState(id).items as { genre?: string }[]}
            limit={18}
            onOpenGenre={openDive}
          />
        );
      case 'your-mixes-section':
        return (
          <MixShelf
            id={id}
            title="Made For You"
            subtitle="Fresh mixes built from your listening — open one to see the tracks"
            mixes={mixes.mixes}
            loaded={true}
            gridId="your-mixes-grid"
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            playingKey={playingMixKey}
          />
        );
      case 'year-mixes-section':
        return (
          <MixShelf
            id={id}
            title="Decades"
            subtitle="The sound of every era in your collection"
            mixes={decadeMixesHydrated}
            loaded={true}
            gridId="year-mixes-grid"
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            playingKey={playingMixKey}
          />
        );
      case 'adv-wave':
        return (
          <AdventurousnessDial
            value={dial.value}
            onChange={dial.change}
            onCommit={(v) => void dial.commit(v)}
          />
        );
      case 'listening-recs-section':
      case 'recommended-artists-section': {
        const kind = id === 'listening-recs-section' ? 'listening' : 'recommended';
        const q =
          kind === 'listening' ? page.sections.listeningRecs : page.sections.recommendedArtists;
        const payload = okData<{ artists?: RecommendedArtist[]; source?: string }>(q?.data);
        const items = recommendedVisible(payload?.artists ?? []);
        return (
          <RecommendedShelf
            kind={kind}
            artists={items}
            source={recSource(payload)}
            loaded={!q?.isPending}
            watchingIds={rec.watchingIds}
            images={rec.images}
            buildDetailPath={detailPath}
            onAddToWatchlist={(artistId, artistName, source) =>
              void rec.toggleWatchlist(artistId, artistName, source)
            }
            onViewAll={kind === 'recommended' ? () => setRecModalOpen(true) : undefined}
          />
        );
      }
      case 'recent-releases': {
        const items = page.sectionState(id).items as RecentAlbum[];
        return (
          <RecentReleasesShelf
            albums={items}
            loaded={!page.sections.recentReleases?.isPending}
            onOpenAlbum={(i) => void albumOpen.openRecentAlbum(items[i])}
          />
        );
      }
      case 'seasonal-albums-section': {
        const items = page.sectionState(id).items as SeasonalAlbum[];
        return (
          <SeasonalAlbumsShelf
            season={season}
            albums={items}
            loaded={!page.sections.seasonal?.isPending}
            onOpenAlbum={(i) => void albumOpen.openSeasonalAlbum(items[i])}
          />
        );
      }
      case 'cache-genre-releases':
      case 'cache-undiscovered':
      case 'cache-label-explorer':
      case 'cache-deep-cuts':
        return cacheShelf(id);
      case 'your-albums-section':
        return (
          <YourAlbumsShelf
            albums={albums.grid.albums}
            total={albums.grid.total}
            page={albums.grid.state.page}
            loaded={albums.grid.phase !== 'loading'}
            loading={albums.grid.phase === 'loading'}
            subtitle={albums.grid.subtitle}
            query={albums.grid.state.search}
            status={albums.grid.state.status}
            sort={albums.grid.state.sort}
            canDownloadMissing={albums.grid.canDownloadMissing}
            refreshing={albums.refresh.refreshing}
            onRefresh={() => void albums.refresh.start()}
            onConfigureSources={albums.sources.openModal}
            onDownloadMissing={() => void albums.batch.openForMissing()}
            onQueryChange={(search) => albums.grid.filter({ search })}
            onStatusChange={(status) => albums.grid.filter({ status: status as never })}
            onSortChange={(sort) => albums.grid.filter({ sort })}
            onPrevPage={() => albums.grid.page(albums.grid.state.page - 1)}
            onNextPage={() => albums.grid.page(albums.grid.state.page + 1)}
            onOpenAlbum={(i) => void albumOpen.openYourAlbum(albums.grid.albums[i] as YourAlbum, i)}
          />
        );
      case 'your-artists-section':
        return (
          <YourArtistsShelf
            artists={page.sectionState(id).items as never[]}
            loaded={!page.sections.yourArtists?.isPending}
            subtitle={savedArtistSourcesSubtitle(
              artists.sources.savedEnabled ?? ARTISTS_DEFAULT_SOURCES,
            )}
            logos={LOGOS}
            refreshing={artists.refresh.refreshing}
            buildDetailPath={detailPath}
            onRefresh={() => void artists.refresh.start()}
            onConfigureSources={artists.sources.openModal}
            onViewAll={artists.browse.openModal}
            onOpenInfo={(a) => artists.info.open(a as never)}
            onToggleWatchlist={(a) => void artists.info.toggleWatch(a as never)}
          />
        );
      case 'discover-bylt-sections':
        return (
          <ByltSections
            sections={byltRows}
            // NEW behaviour at Boulder's request (the vanilla tiles are
            // inert): resolve the track's album by name+artist and open the
            // download modal — the same flow the Deep Cuts cards use. The
            // BYLT rows carry name/artist, the cache item wants
            // name/artist_name, hence the adapter.
            onOpenTrack={(track) =>
              void albumOpen.openCacheItem('genre_dive_tracks', {
                // The cache TRACK branch resolves by ALBUM name + artist; the
                // first wiring passed the TRACK name as the album and every
                // click failed with 'Failed to fetch album tracks'.
                name: track.name,
                album_name: track.album,
                artist_name: track.artist,
              })
            }
          />
        );
      case 'lastfm-radio':
        return (
          <LastfmRadioSection
            query={lastfm.query}
            results={lastfm.results}
            dropdownOpen={lastfm.dropdownOpen}
            searching={lastfm.searching}
            mixes={lastfmMixesHydrated}
            loaded={lastfm.loaded}
            generating={lastfm.generating}
            onQueryChange={lastfm.setQuery}
            onPick={(t) => void lastfm.pick(t)}
            onClear={lastfm.clear}
            onDismiss={lastfm.dismiss}
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            playingKey={playingMixKey}
          />
        );
      case 'listenbrainz':
        return (
          <ListenBrainzSection
            username={lb.username}
            activeTab={lb.activeTab}
            hasData={lb.hasData}
            mixes={lbMixesHydrated}
            loading={!lb.loaded && !lb.error}
            error={lb.error}
            loaded={lb.loaded}
            groups={lb.groups ?? undefined}
            activeGroup={lb.activeGroup}
            onSelectTab={lb.selectTab}
            onSelectGroup={lb.selectGroup}
            onRefresh={() => void lb.refresh()}
            onConnect={() => void window.openPersonalSettings?.()}
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            playingKey={playingMixKey}
          />
        );
      case 'build-a-playlist':
        return (
          <BuildPlaylistSection
            query={bp.query}
            results={bp.results}
            resultsMessage={bp.resultsMessage}
            searching={bp.searching}
            selected={bp.selected}
            generating={bp.generating}
            resultSubtitle={bp.resultSubtitle}
            hasResults={bp.tracks !== null}
            syncing={sync.syncingKeys.includes('build-playlist')}
            syncProgress={toRawProgress(sync.progressFor('build-playlist'))}
            metadata={
              bp.metadata ? (
                <div className="build-playlist-metadata">
                  {bpMetaStats(bp.metadata).map((stat) => (
                    <div className="bp-meta-stat" key={stat.label}>
                      <span className="bp-meta-value">{stat.value}</span>
                      <span className="bp-meta-label">{stat.label}</span>
                    </div>
                  ))}
                </div>
              ) : undefined
            }
            onQueryChange={bp.setQuery}
            onAdd={bp.addSeed}
            onRemove={bp.removeSeed}
            onGenerate={() => void bp.generate()}
            onDownload={() => {
              const d = bp.download();
              if (d.kind === 'ok') openTracksModal(d.virtualId, d.name, d.tracks);
              else toast(d.toast, d.level);
            }}
            onSync={() => {
              const out = sync.startMixSync(
                { key: 'build_playlist_custom', title: 'Custom Playlist' },
                bp.tracks ?? undefined,
              );
              if (out) toast(out.message, out.level);
            }}
            infoOpen={bp.infoOpen}
            onToggleInfo={bp.toggleInfo}
            loaded={true}
          />
        );
      default:
        return null;
    }
  };

  const recentAlbums = page.sectionState('recent-releases').items as RecentAlbum[];
  const genreReleaseAlbums = page.sectionState('cache-genre-releases').items as CacheItem[];
  const undiscoveredAlbums = page.sectionState('cache-undiscovered').items as CacheItem[];
  const labelAlbums = page.sectionState('cache-label-explorer').items as CacheItem[];
  const deepCuts = page.sectionState('cache-deep-cuts').items as CacheItem[];
  const genrePills = page.sectionState('cache-genre-explorer').items as { genre?: string }[];
  const personalSignalCount = mixes.mixes.length + listeningArtists.length + recArtists.length;
  const actionableAlbumCount =
    recentAlbums.length +
    genreReleaseAlbums.length +
    undiscoveredAlbums.length +
    labelAlbums.length;
  const librarySignalCount = deepCuts.length + decadeMixesHydrated.length;
  const toolSignalCount = genrePills.length + lbMixesHydrated.length + lastfm.mixes.length;
  const discoveryInsight: DiscoveryInsight =
    actionableAlbumCount > 0
      ? {
          eyebrow: 'Next best move',
          title: 'Fill the newest gaps first',
          detail: 'New releases, label finds, and missing albums are ready to open or download.',
          value: `${actionableAlbumCount} albums`,
          actionLabel: 'Review gaps',
          target: 'discover-zone-new-missing',
        }
      : personalSignalCount > 0
        ? {
            eyebrow: 'Next best move',
            title: 'Follow the taste engine',
            detail: 'Your mixes and artist recommendations are the strongest live signal today.',
            value: `${personalSignalCount} signals`,
            actionLabel: 'Open For You',
            target: 'discover-zone-for-you',
          }
        : {
            eyebrow: 'Next best move',
            title: 'Build from a seed artist',
            detail: 'Start with one artist and let SoulSync expand the discovery graph.',
            value: `${toolSignalCount} tools`,
            actionLabel: 'Build',
            target: 'build-a-playlist',
          };

  const actionCards: DiscoveryActionCard[] = [
    {
      id: 'for-you',
      eyebrow: 'Personal',
      title: 'For You',
      detail: 'Mixes, artist recs, and because-you-listen-to picks.',
      value: `${personalSignalCount} signals`,
      target: 'discover-zone-for-you',
    },
    {
      id: 'new-missing',
      eyebrow: 'Actionable',
      title: 'New & Missing',
      detail: 'Fresh releases and library gaps ready to open or download.',
      value: `${actionableAlbumCount} albums`,
      target: 'discover-zone-new-missing',
    },
    {
      id: 'library',
      eyebrow: 'Library',
      title: 'Your Taste Map',
      detail: 'Saved artists, albums, eras, and deep cuts from your collection.',
      value: `${librarySignalCount} leads`,
      target: 'discover-zone-library',
    },
    {
      id: 'tools',
      eyebrow: 'Explore',
      title: 'Browse & Build',
      detail: 'Genre explorer, stations, ListenBrainz, and custom builder.',
      value: `${toolSignalCount} tools`,
      target: 'discover-zone-tools',
    },
  ];

  const renderZoneSections = (ids: DiscoverSectionId[]) =>
    ids.filter(hasContent).map((id) => (
      <div className={`discovery-zone-section discovery-zone-section--${id}`} key={id}>
        {renderSection(id)}
      </div>
    ));

  const vizOpen = map.kind !== null || webRequest !== null;

  return (
    // NOT the vanilla page class and NOT its page id: .page is display:none
    // until the shell adds .active, which it only does for the react HOST and
    // for legacy pages — the label-detail flip hid its entire page this way.
    // The host (.page.active) already provides the page padding, and nothing
    // in CSS or shared JS targets #discover-page, so the root carries neither.
    <div>
      {/* The overlays render INSTEAD of the container — the vanilla hid every sibling. */}
      <ArtistMapAssembly
        map={map}
        onOpenInfo={(pool) => artists.info.open(pool as never)}
        buildDetailPath={detailPath}
        onToast={(t) => toast(t.message, t.level)}
      />
      <ArtistWebAssembly
        request={webRequest}
        onClose={() => setWebRequest(null)}
        onExploreInMap={(name) => void map.openExplorer(name)}
        buildDetailPath={(id, source) => detailPath(id, source)}
        onToast={(t) => toast(t.message, t.level)}
      />

      {!vizOpen && (
        <div className="discover-container">
          {(playingMixKey !== null || playingTrackIndex !== null) && (
            <div className="discover-playback-pending" role="status">
              <span>
                Preparing {playingMixKey ? (registry[playingMixKey]?.title ?? 'mix') : 'track'}…
                Checking your library and preparing audio.
              </span>
              <button
                type="button"
                onClick={() => {
                  beginPlayIntent();
                  pendingPlay.current = null;
                  setPlayingMixKey(null);
                  setPlayingTrackIndex(null);
                }}
              >
                Cancel playback
              </button>
            </div>
          )}
          <div className="discover-command-grid">
            <div className="discover-command-hero">
              <DiscoverHero
                artist={hero.artist}
                loading={page.hero.isPending}
                count={hero.artists.length}
                index={hero.index}
                watchlist={hero.watchlist}
                watchAllPhase={hero.watchAllPhase}
                discographyHref={
                  hero.artist?.artist_id != null
                    ? detailPath(hero.artist.artist_id, hero.artist.source ?? null)
                    : '#'
                }
                onNavigate={hero.navigate}
                onJump={hero.jump}
                onToggleWatchlist={() => void hero.toggleWatchlist()}
                onWatchAll={() => void hero.watchAll()}
                onViewRecommended={() => setRecModalOpen(true)}
                onOpenBlacklist={blacklist.openModal}
              />
            </div>
            <DiscoveryCommandPanel
              cards={actionCards}
              insight={discoveryInsight}
              onBuildPlaylist={() => scrollToDiscoveryTarget('build-a-playlist')}
              onOpenMap={() => void map.openWatchlist()}
              onOpenRecommended={() => setRecModalOpen(true)}
            />
          </div>
          <DiscoveryZone
            id="discover-zone-for-you"
            title="For You"
            subtitle="High-confidence mixes, artist paths, and records connected to what you already play."
            tone="for-you"
            metric={`${personalSignalCount} signals`}
          >
            {renderZoneSections(['your-mixes-section'])}
            <StationsRow />
            {renderZoneSections([
              'adv-wave',
              'listening-recs-section',
              'recommended-artists-section',
              'discover-bylt-sections',
            ])}
          </DiscoveryZone>

          <DiscoveryZone
            id="discover-zone-new-missing"
            title="New & Missing"
            subtitle="Fresh releases and collection gaps worth opening, downloading, or syncing next."
            tone="new-missing"
            metric={`${actionableAlbumCount} albums`}
          >
            {renderZoneSections([
              'recent-releases',
              'cache-genre-releases',
              'seasonal-albums-section',
              'cache-undiscovered',
              'cache-label-explorer',
              'your-albums-section',
            ])}
          </DiscoveryZone>

          <DiscoveryZone
            id="discover-zone-library"
            title="Library Signals"
            subtitle="Saved artists, eras, and deep cuts turned into useful entry points."
            tone="library"
            metric={`${librarySignalCount} leads`}
          >
            {renderZoneSections(['your-artists-section', 'year-mixes-section', 'cache-deep-cuts'])}
          </DiscoveryZone>

          <DiscoveryZone
            id="discover-zone-tools"
            title="Explore & Build"
            subtitle="The lab: maps, genres, radio, ListenBrainz, and custom playlists."
            tone="tools"
            metric={`${toolSignalCount} tools`}
          >
            <div className="discovery-zone-section discovery-zone-section--map-tools">
              <div className="discover-hub-row discover-hub-row--tools">
                <ArtistMapHub
                  onOpenWatchlist={() => void map.openWatchlist()}
                  onOpenGenre={() => void map.openGenre()}
                  // Explorer asks for an artist FIRST (9633) — the prompt resolves
                  // a real name, then the map explores it.
                  onOpenExplorer={() => setExplorerPromptOpen(true)}
                />
                <ArtistWebHub onOpenLens={(lens) => setWebRequest({ lens })} />
              </div>
            </div>
            <div className="discovery-zone-section" id="library-radio-section">
              <div className="discover-library-radio-card">
                <div className="discover-library-radio-copy">
                  <div className="discover-library-radio-title">📻 Library Radio</div>
                  <div className="discover-library-radio-sub">
                    Endless smart shuffle of your whole collection — play-count weighted, refills
                    itself by similarity.
                  </div>
                </div>
                <button
                  type="button"
                  className="modal-btn modal-btn-primary"
                  onClick={() => void window.startLibraryRadio?.()}
                >
                  ▶ Start radio
                </button>
              </div>
            </div>
            {renderZoneSections([
              'cache-genre-explorer',
              'lastfm-radio',
              'listenbrainz',
              'build-a-playlist',
            ])}
          </DiscoveryZone>
        </div>
      )}

      <DownloadBar state={bar.state} onOpen={(id) => void bar.openBubble(id)} />

      {modal.mix && (
        <MixModal
          mix={modal.mix}
          tracks={modal.tracks}
          loading={modal.loading}
          error={modal.error}
          selected={modal.selected}
          syncStatusOverride={
            modal.mix.key.startsWith('lb-') ? (
              <LbSyncStatus identifier={modal.mix.key.split('-').slice(2).join('-')} />
            ) : undefined
          }
          syncing={Boolean(modal.mix.statusBase && sync.syncingKeys.includes(modal.mix.statusBase))}
          syncProgress={
            modal.mix.statusBase ? toRawProgress(sync.progressFor(modal.mix.statusBase)) : undefined
          }
          onClose={modal.close}
          onAction={runMixAction}
          onSelectAll={modal.selectAll}
          onClearSelection={modal.clearSelection}
          onToggleTrack={modal.toggleTrack}
          onPlayTrack={playTrackFromModal}
          playingIndex={playingTrackIndex}
          playing={playingMixKey === modal.mix.key}
          onDownloadSelected={downloadSelection}
        />
      )}

      {dive && (
        <GenreDiveModal
          genre={dive.genre}
          data={dive.data}
          phase={dive.phase}
          buildDetailPath={detailPath}
          onOpenGenre={openDive}
          onFollowArtist={() => {}}
          onOpenTrack={(i) => {
            const item = (dive.data?.tracks ?? [])[i] as CacheItem | undefined;
            setDive(null); // the vanilla removes the modal before opening (10486)
            void albumOpen.openCacheItem('genre_dive_tracks', item);
          }}
          onOpenAlbum={(i) => {
            const item = (dive.data?.albums ?? [])[i] as CacheItem | undefined;
            setDive(null);
            void albumOpen.openCacheItem('genre_dive_albums', item);
          }}
          onClose={() => setDive(null)}
        />
      )}

      {recModalOpen && (
        <RecommendedModal
          artists={recArtists}
          source={recSource(recPayload)}
          cachedSource={recPayload?.source ?? null}
          watchingIds={rec.watchingIds}
          images={rec.images}
          addingAll={addingAll}
          buildDetailPath={detailPath}
          onClose={() => setRecModalOpen(false)}
          onAddToWatchlist={(artistId, artistName) =>
            void rec.toggleWatchlist(artistId, artistName)
          }
          onAddAll={() => {
            setAddingAll(true);
            void (async () => {
              for (const a of recArtists) {
                if (a.artist_id && !rec.watchingIds.has(String(a.artist_id))) {
                  await rec.toggleWatchlist(String(a.artist_id), a.artist_name ?? '');
                }
              }
              setAddingAll(false);
            })();
          }}
        />
      )}

      {artists.sources.open && (
        <YourArtistsSourcesModal
          state={artists.sources.state}
          connected={artists.sources.connected}
          onToggle={artists.sources.toggle}
          onSave={() => void artists.sources.save()}
          onClose={artists.sources.closeModal}
        />
      )}
      {artists.browse.open && (
        <YourArtistsModal
          state={artists.browse.state}
          total={artists.browse.total}
          artists={artists.browse.artists}
          phase={artists.browse.phase}
          logos={LOGOS}
          buildDetailPath={detailPath}
          onFilter={artists.browse.filter}
          onPage={artists.browse.page}
          onClose={artists.browse.closeModal}
          onOpenInfo={(a) => artists.info.open(a as never)}
          onToggleWatchlist={(a) => void artists.info.toggleWatch(a as never)}
        />
      )}
      {artists.info.pool && (
        <ArtistInfoModal
          pool={artists.info.pool}
          info={artists.info.data}
          phase={artists.info.phase}
          logos={LOGOS}
          buildDetailPath={detailPath}
          onClose={artists.info.close}
          onToggleWatchlist={() => void artists.info.toggleWatch(artists.info.pool!)}
          onExplore={() => {
            const name = artists.info.pool?.artist_name;
            artists.info.close();
            if (name) {
              setWebRequest(null);
              void map.openExplorer(name);
            }
          }}
          onOpenRelated={(related) =>
            artists.info.open({
              artist_name: related.name,
              image_url: related.image_url ?? '',
            } as never)
          }
          onViewDiscography={artists.info.close}
        />
      )}

      {albums.sources.open && (
        <YourAlbumsSourcesModal
          state={albums.sources.state}
          connected={albums.sources.connected}
          onToggle={albums.sources.toggle}
          onSave={() => void albums.sources.save()}
          onClose={albums.sources.closeModal}
        />
      )}
      {albums.batch.open && (
        <YourAlbumsBatchModal
          rows={albums.batch.rows}
          selected={albums.batch.selected}
          phase={albums.batch.phase}
          progress={albums.batch.progress}
          onToggleRow={albums.batch.toggleRow}
          onSelectAll={albums.batch.selectAll}
          onSubmit={() => void albums.batch.submit()}
          onClose={albums.batch.close}
        />
      )}

      {explorerPromptOpen && (
        <ArtMapExplorePrompt
          onPick={(name) => {
            setExplorerPromptOpen(false);
            void map.openExplorer(name);
          }}
          onClose={() => setExplorerPromptOpen(false)}
        />
      )}

      {blacklist.open && (
        <BlacklistModal
          query={blacklist.query}
          results={blacklist.results}
          entries={blacklist.entries}
          listPhase={blacklist.listPhase}
          onQueryChange={blacklist.setQuery}
          onBlock={(name) => void blacklist.block(name)}
          onUnblock={(entry) => void blacklist.unblock(entry)}
          onClose={blacklist.closeModal}
        />
      )}
    </div>
  );
}
