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
  /** Hero 'owner' label (must agree with heroSourceLabel in -sync.core). */
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
    /** All sync polls skip when the socket is connected (Tidal 1112 shape). */
    pollMs: number;
    /**
     * Modal/listing percent formula: 'processed' = (matched+failed)/total;
     * 'matched' = matched/total — the beatport (5481) + listenbrainz (11313)
     * alternate.
     */
    percentFormula: 'processed' | 'matched';
  };
  ux: {
    /**
     * The #867 open-modal-immediately UX: ONLY Tidal got it (fresh click opens
     * the modal before the ~10s track fetch; Qobuz/Deezer/SpotifyPublic/iTunes
     * block on the fetch first). Port recommendation: unify to true — a
     * knowing change, decided at the vertical-UI phase, not here.
     */
    openModalImmediately: boolean;
    /**
     * Card discovery-progress rendering: 'slash-text' = "♪ T / ✓ M / ✗ F (P%)"
     * (tidal 951); 'check-note-spans' = HTML spans "✓ M / ♪ T", no failed, no
     * percent (deezer divergence 4).
     */
    cardProgressFormat: 'slash-text' | 'check-note-spans';
    /** isFound union variant (see isFoundRowLenient/isFoundRowQobuz). */
    foundVariant: 'lenient' | 'qobuz';
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
    ux: { openModalImmediately: true, cardProgressFormat: 'slash-text', foundVariant: 'lenient' },
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
    ux: { openModalImmediately: false, cardProgressFormat: 'slash-text', foundVariant: 'qobuz' },
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
    ux: { openModalImmediately: false, cardProgressFormat: 'slash-text', foundVariant: 'lenient' },
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
    ux: { openModalImmediately: false, cardProgressFormat: 'slash-text', foundVariant: 'lenient' },
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
    ux: { openModalImmediately: false, cardProgressFormat: 'slash-text', foundVariant: 'lenient' },
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
    },
    ids: {
      fakeHashPrefix: 'itunes_link_',
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
    ux: { openModalImmediately: false, cardProgressFormat: 'slash-text', foundVariant: 'lenient' },
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
      wingItInSocket: true,
      wingItInPoll: false,
      startBody: 'playlist',
    },
    sync: { pollMs: 1000, percentFormula: 'matched' },
    ux: { openModalImmediately: false, cardProgressFormat: 'slash-text', foundVariant: 'lenient' },
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
    },
    ids: { fakeHashPrefix: 'mirrored_', vpidPrefix: 'youtube_', stateFlag: '' },
    discovery: {
      pollMs: 1000,
      pollPolicy: 'always',
      wingItInSocket: false,
      wingItInPoll: false,
      startBody: 'none',
    },
    sync: { pollMs: 1000, percentFormula: 'processed' },
    ux: { openModalImmediately: false, cardProgressFormat: 'slash-text', foundVariant: 'lenient' },
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
