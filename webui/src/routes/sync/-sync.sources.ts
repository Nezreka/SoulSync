/**
 * The source-vertical drift catalog as DATA.
 *
 * The vanilla implements nine near-identical verticals as ~20-function clones
 * (sync-services.js); they drifted in ~35 documented ways (SYNC_PORT_AUDIT.md).
 * The port's single controller reads THIS table instead — every entry below is
 * a transcribed vanilla fact with its citation, pinned by -sync.sources.test.ts.
 *
 * Two deliberate normalizations the React controller makes ON TOP of this
 * table (documented here so the table itself stays purely descriptive):
 * - Wing-it rows: the vanilla renders '🎯 Wing It' only on transforms that
 *   handle it (see wingItInSocket/wingItInPoll) — transport-dependent
 *   rendering, live bug #4. The React transform handles wing-it for every
 *   source on every transport.
 * - Discovery transport: React consumes the ss:discovery-progress CustomEvent
 *   plus an HTTP backstop at each source's cadence, replacing the vanilla's
 *   three different poll policies (recorded below as discoveryPollPolicy).
 */

export type SyncSourceId =
  | 'tidal'
  | 'qobuz'
  | 'deezer'
  | 'youtube'
  | 'beatport'
  | 'spotify_public'
  | 'itunes_link'
  | 'listenbrainz'
  | 'mirrored';

export interface SourceVerticalConfig {
  id: SyncSourceId;
  /**
   * A human display name for the source. NOT the engine's hero 'owner' label
   * — that comes from heroSourceLabel (-sync.core.ts 101), whose vanilla
   * ladder (sync-services.js 1362-1375) has no deezer_ arm and never sees a
   * mirrored vpid, so both resolve to 'YouTube' there. These two must not be
   * conflated.
   */
  heroLabel: string;
  /**
   * API path builders. `id` is the source's own id: numeric id (tidal/qobuz/
   * deezer), url_hash (youtube/spotify_public/itunes_link), chart hash
   * (beatport), mbid (listenbrainz), `mirrored_<n>` hash (mirrored).
   */
  api: {
    discoveryStart: (id: string) => string;
    discoveryStatus: (id: string) => string;
    syncStart: (id: string) => string;
    /**
     * listenbrainz has NO cancel endpoint — the vanilla's cancel button calls
     * cancelYouTubeSync, which posts the mbid to the YOUTUBE endpoint
     * (sync-services.js 9806: 'cancelYouTubeSync' for isListenBrainz).
     */
    syncCancel: (id: string) => string;
    syncStatus: (id: string) => string;
    /**
     * Phase writes: hyphen endpoints = beatport + listenbrainz; underscore =
     * youtube/tidal/qobuz/deezer/spotify-public/itunes (closeYouTubeDiscovery
     * Modal reset blocks, sync-services.js 10237-10455).
     */
    updatePhase: (id: string) => string;
    /** Full-state fetch (discovery results etc.); null = no such endpoint. */
    state: ((id: string) => string) | null;
    /** Bulk state hydration on tab load; null = per-playlist only. */
    playlistsStates: string | null;
    /** Hard reset; null = source has no reset (documented at 9800). */
    reset: ((id: string) => string) | null;
    /**
     * What the reset POST carries. youtube + mirrored send a BARE POST
     * (resetYouTubePlaylist, 10793-10795); beatport rides update-phase and must
     * send {phase:'fresh', reset:true} (resetBeatportChart, 10851-10858).
     */
    resetBody: 'none' | 'fresh-reset';
  };
  ids: {
    /**
     * Prefix of the fake url-hash used as the youtubePlaylistStates key.
     * '' = the source's own id IS the key (beatport chart hash, LB mbid).
     */
    fakeHashPrefix: string;
    /**
     * Prefix of the virtual playlist id handed to the download engine. NOTE
     * spotify_public: fake hash 'spotifypublic_<hash>' but vpid
     * 'spotify_public_<hash>' — the inconsistent live pair (audit, SpotifyPublic
     * divergence 3). Both preserved.
     */
    vpidPrefix: string;
    /** The is_<source>_playlist boolean stamped on the fake state. */
    stateFlag: string;
  };
  discovery: {
    /** HTTP poll cadence: 1000 everywhere except beatport's 2000 (4714). */
    pollMs: number;
    /**
     * The vanilla's poll-vs-socket policy: 'always' = poll runs alongside the
     * socket (tidal/qobuz/youtube/beatport/listenbrainz); 'skip-when-connected'
     * = the poll body returns early while the socket lives (deezer 3062ff,
     * spotify_public 7033, itunes_link 8059).
     */
    pollPolicy: 'always' | 'skip-when-connected';
    /** Whether each vanilla transform mapped wing-it rows (live bug #4). */
    wingItInSocket: boolean;
    wingItInPoll: boolean;
    /**
     * Payload POSTed to discoveryStart: LB sends the whole playlist (11276),
     * beatport sends {chart_data} (4648); everyone else sends nothing.
     */
    startBody: 'none' | 'playlist' | 'chart_data';
  };
  sync: {
    /** All sync polls skip when the socket is connected (Tidal 1063 shape). */
    pollMs: number;
    /**
     * MODAL percent formula: 'processed' = (matched+failed)/total (the shared
     * painter, 10684); 'matched' = matched/total — beatport's own modal
     * painter (5574). NOTE LB's modal uses the SHARED painter (processed);
     * only its LISTING cards use matched/total — see listingPercentFormula.
     */
    percentFormula: 'processed' | 'matched';
    /**
     * The LB listing drift (11393): its tab cards compute matched/total where
     * every other listing surface shows processed. Absent = same as the modal.
     */
    listingPercentFormula?: 'matched';
  };
  ux: {
    /**
     * The #867 open-modal-immediately UX: ONLY Tidal got it (fresh click opens
     * the modal before the ~10s track fetch). Of the rest only QOBUZ actually
     * blocks on a fetch (1649-1680); deezer/spotify_public/itunes open
     * immediately too, just from already-loaded data (2856, 6774, 7800) —
     * they simply never had the #867 treatment. Port recommendation: unify to true — a
     * knowing change, decided at the vertical-UI phase, not here.
     */
    openModalImmediately: boolean;
    /**
     * Card discovery-progress rendering: 'slash-text' = "♪ T / ✓ M / ✗ F / P%"
     * (tidal 966 — slashes throughout, no parentheses); 'check-note-spans' =
     * HTML spans "✓ M / ♪ T", no failed, no percent, only when total > 0 —
     * deezer (its divergence 4) AND its two clones spotify_public (7283) +
     * itunes_link (8309).
     */
    cardProgressFormat: 'slash-text' | 'check-note-spans';
    /** isFound union variant (see isFoundRowLenient/isFoundRowQobuz). */
    foundVariant: 'lenient' | 'qobuz';
    /**
     * WHICH engine entry the download hand-off calls. The vanilla routes
     * tidal/qobuz/deezer/spotify_public/itunes_link through the generic
     * openDownloadMissingModalForTidal (sync-services.js 276/1302/1758/2426/
     * 2916/3683/7615/8641) — it takes options and hydrates the organize
     * preference at 1494 — and reserves openDownloadMissingModalForYouTube
     * (downloads.js 429, no options) for youtube, beatport, listenbrainz and
     * mirrored — the latter two reach it through startYouTubeDownloadMissing.
     * The YouTube modal alone adds the M3U export + quality-profile chrome.
     */
    downloadEntry: 'tidal' | 'youtube';
    /**
     * The toast a finished discovery raises, or null for none. Real drift, not
     * an oversight: youtube + mirrored share _discoveryCompleteToast's
     * 'Discovery complete!' (9204), listenbrainz words its own differently
     * (11076, 11171), and the other six raise NOTHING — they complete with a
     * console.log only (759 tidal, 3180 deezer, 7106 spotify_public, 8132
     * itunes_link, and the qobuz/beatport twins). Resolved through
     * discoveryCompleteToast, which also applies the #815 retry override.
     */
    discoveryCompleteToast: string | null;
    /**
     * The noun in the reset FAILURE toast: 'Error resetting playlist: ...'
     * (10833) vs 'Error resetting chart: ...' (10902). The SUCCESS line is
     * identical for both, so only this word is parameterized.
     */
    resetErrorNoun: 'playlist' | 'chart';
  };
}

const std = (base: string) => ({
  discoveryStart: (id: string) => `${base}/discovery/start/${id}`,
  discoveryStatus: (id: string) => `${base}/discovery/status/${id}`,
  syncStart: (id: string) => `${base}/sync/start/${id}`,
  syncCancel: (id: string) => `${base}/sync/cancel/${id}`,
  syncStatus: (id: string) => `${base}/sync/status/${id}`,
});

export const SYNC_SOURCES: Record<SyncSourceId, SourceVerticalConfig> = {
  tidal: {
    id: 'tidal',
    heroLabel: 'Tidal',
    api: {
      ...std('/api/tidal'),
      updatePhase: (id) => `/api/tidal/update_phase/${id}`,
      state: (id) => `/api/tidal/state/${id}`,
      playlistsStates: '/api/tidal/playlists/states',
      reset: null,
      resetBody: 'none',
    },
    ids: { fakeHashPrefix: 'tidal_', vpidPrefix: 'tidal_', stateFlag: 'is_tidal_playlist' },
    discovery: {
      pollMs: 1000,
      pollPolicy: 'always',
      wingItInSocket: true,
      wingItInPoll: false,
      startBody: 'none',
    },
    sync: { pollMs: 1000, percentFormula: 'processed' },
    ux: {
      openModalImmediately: true,
      cardProgressFormat: 'slash-text',
      foundVariant: 'lenient',
      downloadEntry: 'tidal',
      discoveryCompleteToast: null,
      resetErrorNoun: 'playlist',
    },
  },

  qobuz: {
    id: 'qobuz',
    heroLabel: 'Qobuz',
    api: {
      ...std('/api/qobuz'),
      updatePhase: (id) => `/api/qobuz/update_phase/${id}`,
      state: (id) => `/api/qobuz/state/${id}`,
      playlistsStates: '/api/qobuz/playlists/states',
      reset: null,
      resetBody: 'none',
    },
    ids: { fakeHashPrefix: 'qobuz_', vpidPrefix: 'qobuz_', stateFlag: 'is_qobuz_playlist' },
    discovery: {
      pollMs: 1000,
      pollPolicy: 'always',
      wingItInSocket: true,
      wingItInPoll: false,
      startBody: 'none',
    },
    sync: { pollMs: 1000, percentFormula: 'processed' },
    ux: {
      openModalImmediately: false,
      cardProgressFormat: 'slash-text',
      foundVariant: 'qobuz',
      downloadEntry: 'tidal',
      discoveryCompleteToast: null,
      resetErrorNoun: 'playlist',
    },
  },

  deezer: {
    id: 'deezer',
    heroLabel: 'Deezer',
    api: {
      ...std('/api/deezer'),
      updatePhase: (id) => `/api/deezer/update_phase/${id}`,
      state: (id) => `/api/deezer/state/${id}`,
      playlistsStates: '/api/deezer/playlists/states',
      reset: null,
      resetBody: 'none',
    },
    ids: { fakeHashPrefix: 'deezer_', vpidPrefix: 'deezer_', stateFlag: 'is_deezer_playlist' },
    discovery: {
      pollMs: 1000,
      pollPolicy: 'skip-when-connected',
      wingItInSocket: true,
      wingItInPoll: false,
      startBody: 'none',
    },
    sync: { pollMs: 1000, percentFormula: 'processed' },
    ux: {
      openModalImmediately: false,
      cardProgressFormat: 'check-note-spans',
      foundVariant: 'lenient',
      downloadEntry: 'tidal',
      discoveryCompleteToast: null,
      resetErrorNoun: 'playlist',
    },
  },

  youtube: {
    id: 'youtube',
    heroLabel: 'YouTube',
    api: {
      ...std('/api/youtube'),
      updatePhase: (id) => `/api/youtube/update_phase/${id}`,
      state: (id) => `/api/youtube/state/${id}`,
      playlistsStates: null, // hydrated via /api/youtube/playlists + per-hash state
      reset: (id) => `/api/youtube/reset/${id}`,
      resetBody: 'none',
    },
    // The bare url_hash IS the youtubePlaylistStates key; only the download
    // vpid carries the youtube_ prefix (10761). YouTube is the shared modal's
    // ELSE branch, so it has no resolving prefix or flag.
    ids: { fakeHashPrefix: '', vpidPrefix: 'youtube_', stateFlag: '' },
    discovery: {
      pollMs: 1000,
      pollPolicy: 'always',
      wingItInSocket: false,
      wingItInPoll: false,
      startBody: 'none',
    },
    sync: { pollMs: 1000, percentFormula: 'processed' },
    ux: {
      openModalImmediately: false,
      cardProgressFormat: 'slash-text',
      foundVariant: 'lenient',
      downloadEntry: 'youtube',
      discoveryCompleteToast: 'Discovery complete!',
      resetErrorNoun: 'playlist',
    },
  },

  beatport: {
    id: 'beatport',
    heroLabel: 'Beatport',
    api: {
      discoveryStart: (id) => `/api/beatport/discovery/start/${id}`,
      discoveryStatus: (id) => `/api/beatport/discovery/status/${id}`,
      syncStart: (id) => `/api/beatport/sync/start/${id}`,
      syncCancel: (id) => `/api/beatport/sync/cancel/${id}`,
      syncStatus: (id) => `/api/beatport/sync/status/${id}`,
      // The HYPHEN endpoint — beatport + listenbrainz drift from everyone else.
      updatePhase: (id) => `/api/beatport/charts/update-phase/${id}`,
      state: (id) => `/api/beatport/charts/status/${id}`,
      playlistsStates: '/api/beatport/charts',
      // Reset rides update-phase with {phase:'fresh', reset:true} (10837).
      resetBody: 'fresh-reset',
      reset: (id) => `/api/beatport/charts/update-phase/${id}`,
    },
    // The chart hash IS the youtubePlaylistStates key — no prefix (845-1052).
    ids: { fakeHashPrefix: '', vpidPrefix: 'beatport_', stateFlag: 'is_beatport_playlist' },
    discovery: {
      pollMs: 2000, // comment says 'like Tidal'; Tidal is 1000 — real drift (4714)
      pollPolicy: 'always',
      wingItInSocket: false,
      wingItInPoll: false,
      startBody: 'chart_data',
    },
    sync: { pollMs: 2000, percentFormula: 'matched' },
    ux: {
      openModalImmediately: false,
      cardProgressFormat: 'slash-text',
      foundVariant: 'lenient',
      downloadEntry: 'youtube',
      discoveryCompleteToast: null,
      resetErrorNoun: 'chart',
    },
  },

  spotify_public: {
    id: 'spotify_public',
    heroLabel: 'Spotify',
    api: {
      ...std('/api/spotify-public'),
      updatePhase: (id) => `/api/spotify-public/update_phase/${id}`,
      state: (id) => `/api/spotify-public/state/${id}`,
      playlistsStates: '/api/spotify-public/playlists/states',
      reset: null,
      resetBody: 'none',
    },
    // The live inconsistent pair: modal hash vs download vpid.
    ids: {
      fakeHashPrefix: 'spotifypublic_',
      vpidPrefix: 'spotify_public_',
      stateFlag: 'is_spotify_public_playlist',
    },
    discovery: {
      pollMs: 1000,
      pollPolicy: 'skip-when-connected',
      wingItInSocket: true,
      wingItInPoll: false,
      startBody: 'none',
    },
    sync: { pollMs: 1000, percentFormula: 'processed' },
    ux: {
      openModalImmediately: false,
      cardProgressFormat: 'check-note-spans',
      foundVariant: 'lenient',
      downloadEntry: 'tidal',
      discoveryCompleteToast: null,
      resetErrorNoun: 'playlist',
    },
  },

  itunes_link: {
    id: 'itunes_link',
    heroLabel: 'iTunes',
    api: {
      ...std('/api/itunes-link'),
      updatePhase: (id) => `/api/itunes-link/update_phase/${id}`,
      state: (id) => `/api/itunes-link/state/${id}`,
      playlistsStates: '/api/itunes-link/playlists/states',
      reset: null,
      resetBody: 'none',
    },
    ids: {
      fakeHashPrefix: 'ituneslink_',
      vpidPrefix: 'itunes_link_',
      stateFlag: 'is_itunes_link_playlist',
    },
    discovery: {
      pollMs: 1000,
      pollPolicy: 'skip-when-connected',
      wingItInSocket: true,
      wingItInPoll: false,
      startBody: 'none',
    },
    sync: { pollMs: 1000, percentFormula: 'processed' },
    ux: {
      openModalImmediately: false,
      cardProgressFormat: 'check-note-spans',
      foundVariant: 'lenient',
      downloadEntry: 'tidal',
      discoveryCompleteToast: null,
      resetErrorNoun: 'playlist',
    },
  },

  listenbrainz: {
    id: 'listenbrainz',
    heroLabel: 'ListenBrainz',
    api: {
      discoveryStart: (id) => `/api/listenbrainz/discovery/start/${id}`,
      discoveryStatus: (id) => `/api/listenbrainz/discovery/status/${id}`,
      syncStart: (id) => `/api/listenbrainz/sync/start/${id}`,
      // Borrowed: no LB cancel endpoint exists; the vanilla posts the mbid to
      // the YOUTUBE cancel (cancelYouTubeSync at 9806). Preserved knowingly.
      syncCancel: (id) => `/api/youtube/sync/cancel/${id}`,
      syncStatus: (id) => `/api/listenbrainz/sync/status/${id}`,
      // The other HYPHEN endpoint.
      updatePhase: (id) => `/api/listenbrainz/update-phase/${id}`,
      state: (id) => `/api/listenbrainz/state/${id}`,
      playlistsStates: '/api/listenbrainz/playlists',
      reset: null,
      resetBody: 'none',
    },
    // The mbid IS the key, in the SEPARATE listenbrainzPlaylistStates registry
    // (looked up FIRST by the shared modal, 9302).
    ids: {
      fakeHashPrefix: '',
      vpidPrefix: 'listenbrainz_',
      stateFlag: 'is_listenbrainz_playlist',
    },
    discovery: {
      pollMs: 1000,
      pollPolicy: 'always',
      // NEITHER LB transform maps wing-it (verified: the only case-insensitive
      // 'wing' in the whole LB region is the word "Showing" in a log line).
      wingItInSocket: false,
      wingItInPoll: false,
      startBody: 'playlist',
    },
    sync: { pollMs: 1000, percentFormula: 'processed', listingPercentFormula: 'matched' },
    ux: {
      openModalImmediately: false,
      cardProgressFormat: 'slash-text',
      foundVariant: 'lenient',
      downloadEntry: 'youtube',
      discoveryCompleteToast: 'ListenBrainz discovery complete!',
      resetErrorNoun: 'playlist',
    },
  },

  mirrored: {
    id: 'mirrored',
    heroLabel: 'SoulSync',
    // Mirrored rows ride the YOUTUBE vertical end to end with a
    // `mirrored_<id>` hash (renderMirroredCard, stats-automations.js 500-656;
    // the explorer port confirmed the discovery frames arrive as
    // `mirrored_<playlistId>`). Their own endpoints are the mirrored CRUD +
    // retry, not a vertical.
    api: {
      ...std('/api/youtube'),
      updatePhase: (id) => `/api/youtube/update_phase/${id}`,
      state: (id) => `/api/youtube/state/${id}`,
      playlistsStates: '/api/mirrored-playlists/discovery-states',
      reset: (id) => `/api/youtube/reset/${id}`,
      resetBody: 'none',
    },
    // The registry key IS the source id ('mirrored_<n>' — the literal prefix
    // is PART of the id, never prepended; frame ids and API paths carry the
    // same full string, stats-automations.js 2045/2084).
    ids: { fakeHashPrefix: '', vpidPrefix: 'youtube_', stateFlag: 'is_mirrored_playlist' },
    discovery: {
      pollMs: 1000,
      pollPolicy: 'always',
      wingItInSocket: false,
      wingItInPoll: false,
      startBody: 'none',
    },
    sync: { pollMs: 1000, percentFormula: 'processed' },
    ux: {
      openModalImmediately: false,
      cardProgressFormat: 'slash-text',
      foundVariant: 'lenient',
      downloadEntry: 'youtube',
      discoveryCompleteToast: 'Discovery complete!',
      resetErrorNoun: 'playlist',
    },
  },
};

/** The sources whose discovery modal state lives in youtubePlaylistStates. */
export const SOURCE_IDS: readonly SyncSourceId[] = Object.keys(SYNC_SOURCES) as SyncSourceId[];

/**
 * Resolve a source config from a fake url-hash / state-registry key, the way
 * the shared modal dispatches (LB keys are bare mbids and beatport keys are
 * bare chart hashes, so prefix resolution tries the PREFIXED sources first and
 * needs the state flags for the rest — this helper only answers for keys that
 * carry a prefix; flag-based dispatch is the controller's job).
 */
export function sourceForFakeHash(fakeHash: string): SourceVerticalConfig | null {
  // Longest prefix first so 'spotifypublic_' style additions can never be
  // shadowed by a shorter prefix.
  // mirrored keys carry their marker INSIDE the id (fakeHashPrefix is ''),
  // so resolve them explicitly before the constructed-prefix loop.
  if (fakeHash.startsWith('mirrored_')) return SYNC_SOURCES.mirrored;
  const prefixed = SOURCE_IDS.map((id) => SYNC_SOURCES[id]).filter(
    (c) => c.ids.fakeHashPrefix !== '',
  );
  prefixed.sort((a, b) => b.ids.fakeHashPrefix.length - a.ids.fakeHashPrefix.length);
  for (const config of prefixed) {
    if (fakeHash.startsWith(config.ids.fakeHashPrefix)) return config;
  }
  return null;
}

/**
 * Resolve a source config from a download-engine virtual playlist id, the
 * prefix ladder the download modal and startYouTubeDownloadMissing use.
 */
export function sourceForVpid(vpid: string): SourceVerticalConfig | null {
  const configs = SOURCE_IDS.map((id) => SYNC_SOURCES[id]).filter((c) => c.id !== 'mirrored');
  configs.sort((a, b) => b.ids.vpidPrefix.length - a.ids.vpidPrefix.length);
  for (const config of configs) {
    if (vpid.startsWith(config.ids.vpidPrefix)) return config;
  }
  return null;
}
