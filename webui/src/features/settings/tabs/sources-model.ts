/**
 * The download sources, as the Settings page knows them (SETTINGS_PORT.md,
 * step 7a). Ported from settings.js: the source registry, the three download
 * chains (music, video, audiobooks), each source's connection state (the dots
 * on the Sources tiles) and Test all sources.
 *
 * One store, read by the chain on the Downloads tab and the tiles on the
 * Sources tab, so the two cannot disagree. Until the Sources panels and their
 * modal are React (step 7b), settings.js reads and writes the connection state
 * through window.SoulSyncSources; the audiobook chain is owned by the Library
 * tab until that is React, and is read and written through
 * window.getAudiobookChain / setAudiobookChain / getAudiobookActiveSources.
 */

import { createStore } from 'zustand/vanilla';

import { type SettingsPayload, type SettingsStore, settingsStore } from '../settings-store';

export interface SourceMeta {
  id: string;
  name: string;
  icon: string | null;
  emoji: string;
}

/** dark-on-light brand marks, drawn white on the dark tiles */
export const INVERT_BRAND_MARKS = new Set(['tidal', 'qobuz', 'soundcloud']);

/** the pool's groups, in order; an id in none of them goes under "Other" */
export const DLCHAIN_GROUPS = [
  { label: 'Peer-to-peer', ids: ['soulseek'] },
  { label: 'Streaming services', ids: ['tidal', 'qobuz', 'deezer_dl', 'soundcloud', 'hifi'] },
  { label: 'Public video', ids: ['youtube'] },
  { label: 'Torrent & Usenet', ids: ['torrent', 'usenet', 'extto'] },
  { label: 'Library manager', ids: ['lidarr'] },
];

export const HYBRID_SOURCES: SourceMeta[] = [
  { id: 'soulseek', name: 'Soulseek', icon: '/static/img/brands/slskd.png', emoji: '🎵' },
  { id: 'youtube', name: 'YouTube', icon: '/static/img/brands/youtube.svg', emoji: '▶️' },
  { id: 'tidal', name: 'Tidal', icon: '/static/img/brands/tidal.svg', emoji: '🌊' },
  { id: 'qobuz', name: 'Qobuz', icon: '/static/img/brands/qobuz.svg', emoji: '🎧' },
  { id: 'hifi', name: 'HiFi', icon: null, emoji: '🎶' },
  { id: 'deezer_dl', name: 'Deezer', icon: '/static/img/brands/deezer.png', emoji: '🎧' },
  { id: 'lidarr', name: 'Lidarr', icon: null, emoji: '📦' },
  { id: 'soundcloud', name: 'SoundCloud', icon: '/static/img/brands/soundcloud.png', emoji: '☁️' },
  { id: 'torrent', name: 'Torrent', icon: null, emoji: '🧲' },
  { id: 'usenet', name: 'Usenet', icon: null, emoji: '📰' },
];

/** Prowlarr is not a link in a chain (torrent and usenet search through it),
 *  but it is configured like a source, so it has a tile */
export const EXTRA_SOURCE_TILES: SourceMeta[] = [
  { id: 'prowlarr', name: 'Indexers', icon: null, emoji: '🔎' },
];

/** sources that cannot be picked any more: amazon ran through a proxy that
 *  is gone (#1300). a saved chain that names one drops it on load */
export const RETIRED_DOWNLOAD_SOURCES = new Set(['amazon']);

/** the sources that own a config panel, so get a tile */
export const SOURCE_CONFIG_ID_BY_SRC: Record<string, string> = {
  soulseek: 'soulseek-settings-container',
  youtube: 'youtube-settings-container',
  tidal: 'tidal-download-settings-container',
  qobuz: 'qobuz-settings-container',
  hifi: 'hifi-download-settings-container',
  deezer_dl: 'deezer-download-settings-container',
  lidarr: 'lidarr-download-settings-container',
  soundcloud: 'soundcloud-download-settings-container',
  torrent: 'torrent-client-settings-container',
  usenet: 'usenet-client-settings-container',
  prowlarr: 'prowlarr-settings-container',
};

export const AUDIOBOOK_SOURCES = ['torrent', 'usenet', 'soulseek'];
const AUDIOBOOK_SOURCE_LABEL: Record<string, string> = {
  torrent: 'Torrent',
  usenet: 'Usenet',
  soulseek: 'Soulseek',
};
const AUDIOBOOK_SOURCE_EMOJI: Record<string, string> = {
  torrent: '\u{1F9F2}',
  usenet: '\u{1F4F0}',
  soulseek: '\u{1F3B5}',
};

export type ChainKind = 'music' | 'video' | 'audiobooks';

export interface ChainSpec {
  label: string;
  help: string;
  sources: () => string[];
  meta: (id: string) => SourceMeta;
}

export const CHAIN_KINDS: Record<ChainKind, ChainSpec> = {
  music: {
    label: 'Music',
    help: 'Downloads try each source in order, top first. One source means single-source mode; two or more is hybrid.',
    sources: () => HYBRID_SOURCES.map((s) => s.id),
    meta: (id) =>
      HYBRID_SOURCES.find((s) => s.id === id) || { id, name: id, emoji: '🎵', icon: null },
  },
  video: {
    label: 'Video',
    help: "Video downloads try each source in order, top first. These settings are the video side's own and save immediately.",
    sources: () => ['soulseek', 'torrent', 'usenet', 'extto'],
    meta: (id) =>
      ({
        soulseek: { id, name: 'Soulseek', emoji: '🎵', icon: '/static/img/brands/slskd.png' },
        torrent: { id, name: 'Torrent', emoji: '🧲', icon: null },
        usenet: { id, name: 'Usenet', emoji: '📰', icon: null },
        extto: { id, name: 'External', emoji: '🔗', icon: null },
      })[id] || { id, name: id, emoji: '🎬', icon: null },
  },
  audiobooks: {
    label: 'Audiobooks',
    help: 'Audiobooks are fetched as a whole folder, so the order decides which source is asked first, not which release wins.',
    sources: () => AUDIOBOOK_SOURCES.slice(),
    meta: (id) => ({
      id,
      name: AUDIOBOOK_SOURCE_LABEL[id] || id,
      emoji: AUDIOBOOK_SOURCE_EMOJI[id] || '📚',
      icon: null,
    }),
  },
};

/** the hidden selects the old page saved download_source.hybrid_primary and
 *  _secondary from had these options only; any other id saved as '' */
const LEGACY_SELECT_SOURCES = [
  'soulseek',
  'youtube',
  'tidal',
  'qobuz',
  'hifi',
  'deezer_dl',
  'soundcloud',
];

export type SourceState = 'unknown' | 'testing' | 'ok' | 'warn' | 'fail' | 'na';

export interface SourcesState {
  /** the music chain, in order (getHybridOrder) */
  music: string[];
  /** download_source.mode: changes only when the chain does */
  mode: string;
  /** what download_source.hybrid_secondary held: kept when the chain shrinks to one */
  secondary: string;
  /** in the music chain but not set up enough to run (the server's _source_status) */
  unready: Record<string, boolean>;
  status: Record<string, SourceState>;
  /** what the server said on the last test of each source or connection */
  said: Record<string, string>;
  /** passed, with something worth acting on (a message starting with ⚠) */
  warned: Record<string, boolean>;
  /** the video chain, read from video.db; null until read */
  video: string[] | null;
  /** the chain on screen in the widget */
  kind: ChainKind;
  /** the person picked a chain tab: the side no longer decides */
  kindChosen: boolean;
  /** the order the widget is editing (the open kind's) */
  order: string[];
  /** Test all is running from its button */
  testingAll: boolean;
}

type ShellWindow = Window & {
  getAudiobookChain?: () => string[];
  setAudiobookChain?: (order: string[]) => void;
  getAudiobookActiveSources?: () => string[];
  showToast?: (message: string, type?: string) => void;
};

function shell(): ShellWindow {
  return window as ShellWindow;
}

const toast = (message: string, type: string) => shell().showToast?.(message, type);

export async function fetchJson(url: string, init?: RequestInit): Promise<Record<string, unknown>> {
  const response = await fetch(url, init);
  return (await response.json()) as Record<string, unknown>;
}

/** settings.js loadHybridSourceOrder: the saved chain, without retired
 *  sources, or the legacy primary + secondary; unready sources stay in it */
export function musicFromSettings(settings: SettingsPayload) {
  const source = (settings.download_source || {}) as Record<string, unknown>;
  const saved = source.hybrid_order;
  const status = (settings._source_status || {}) as Record<string, unknown>;
  let music: string[];
  let cleaned = false;
  if (Array.isArray(saved) && saved.length > 0) {
    music = (saved as string[]).filter((id) => !RETIRED_DOWNLOAD_SOURCES.has(id));
    cleaned = music.length !== saved.length;
  } else {
    const primary = typeof source.hybrid_primary === 'string' ? source.hybrid_primary : 'soulseek';
    const second =
      typeof source.hybrid_secondary === 'string' ? source.hybrid_secondary : 'youtube';
    music = [primary || 'soulseek', second || 'youtube'].filter(
      (id) => !RETIRED_DOWNLOAD_SOURCES.has(id),
    );
    if (!music.length) music = ['soulseek'];
  }
  const unready: Record<string, boolean> = {};
  for (const src of HYBRID_SOURCES) {
    if (music.includes(src.id) && status[src.id] === false) unready[src.id] = true;
  }
  const savedMode = typeof source.mode === 'string' && source.mode ? source.mode : 'soulseek';
  const mode = RETIRED_DOWNLOAD_SOURCES.has(savedMode) ? 'soulseek' : savedMode;
  return { music, unready, mode, cleaned: cleaned || mode !== savedMode };
}

/** what the old page's hidden selects held for this chain */
export function legacyPair(order: string[], previousSecondary: string) {
  const pick = (id: string | undefined) => (id && LEGACY_SELECT_SOURCES.includes(id) ? id : '');
  return {
    primary: pick(order[0]),
    secondary: order.length > 1 ? pick(order[1]) : previousSecondary,
  };
}

/** where each source sits in every chain */
export function chainUses(id: string, chains: Array<{ label: string; order: string[] }>) {
  const uses: Array<{ label: string; pos: number }> = [];
  for (const c of chains) {
    const i = c.order.indexOf(id);
    if (i !== -1) uses.push({ label: c.label, pos: i + 1 });
  }
  return uses;
}

export interface SourcesModel {
  getState: () => SourcesState;
  subscribe: (listener: (state: SourcesState) => void) => () => void;
  /** a fresh settings payload: the music chain and what is unready */
  load: (settings: SettingsPayload) => void;
  /** the chains as the tiles read them: music, video, audiobooks */
  chains: () => Array<{ label: string; order: string[] }>;
  /** re-read the open chain (each visit, a tab switch) */
  loadChain: () => Promise<void>;
  switchChain: (kind: string) => void;
  /** the widget changed the open chain */
  commit: (order: string[]) => void;
  loadVideoChain: () => Promise<void>;
  /** one source's test, from its modal: the dot and what was said */
  testOne: (id: string) => Promise<SourceState | null>;
  /** the source has a connection test (its modal shows Test) */
  canTest: (id: string) => boolean;
  testAll: (opts?: { silent?: boolean }) => Promise<void>;
  autoTestOnce: () => void;
  setStatus: (id: string, state: SourceState) => void;
}

export function createSourcesModel({
  store = settingsStore,
  autoTestDelay = 1200,
}: { store?: SettingsStore; autoTestDelay?: number } = {}): SourcesModel {
  const model = createStore<SourcesState>(() => ({
    music: ['soulseek', 'youtube'],
    mode: 'soulseek',
    // the old hidden select's first option, until a chain of two set it
    secondary: 'soulseek',
    unready: {},
    status: {},
    said: {},
    warned: {},
    video: null,
    kind: 'music',
    kindChosen: false,
    order: [],
    testingAll: false,
  }));
  let autoTested = false;

  const set = (patch: Partial<SourcesState>) => model.setState(patch);
  const setStatus = (id: string, state: SourceState) =>
    model.setState((s) => ({ status: { ...s.status, [id]: state } }));

  const audiobookChain = () => {
    try {
      return (shell().getAudiobookChain?.() || []).slice();
    } catch {
      return [];
    }
  };
  const audiobookActive = () => {
    try {
      return shell().getAudiobookActiveSources?.() || [];
    } catch {
      return [];
    }
  };

  /** settings.js _ssTestConn: the server's test, and what it said */
  async function testConnection(service: string): Promise<boolean> {
    const j = await fetchJson('/api/test-connection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ service }),
    });
    const raw = j && (j.message || j.error);
    const msg = typeof raw === 'string' ? raw : raw ? JSON.stringify(raw) : '';
    model.setState((s) => {
      const said = { ...s.said };
      if (msg) said[service] = msg;
      else delete said[service];
      return {
        said,
        warned: { ...s.warned, [service]: !!j.success && !!msg && msg.trim().startsWith('⚠') },
      };
    });
    return !!j.success;
  }

  /** each source's test; the same endpoints its own Test button uses */
  const probes: Record<string, () => Promise<boolean>> = {
    soulseek: () => testConnection('soulseek'),
    tidal: () => testConnection('tidal'),
    qobuz: () => fetchJson('/api/qobuz/auth/status').then((j) => j.authenticated === true),
    hifi: () => fetchJson('/api/hifi/status').then((j) => j.available === true),
    // POST with an empty body tests the SAVED ARL; a GET 405s
    deezer_dl: () =>
      fetchJson('/api/deezer-download/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      }).then((j) => j.success === true),
    lidarr: () => testConnection('lidarr'),
    soundcloud: () =>
      fetchJson('/api/soundcloud/status').then((j) => j.available === true && j.reachable === true),
    torrent: () => testConnection('torrent_client'),
    usenet: () => testConnection('usenet_client'),
    prowlarr: () => testConnection('prowlarr'),
    // cookies can break it (#1126, #1233): it has a real test
    youtube: () => testConnection('youtube'),
  };

  /** the metadata and server connections Test all also checks */
  const CONNECTION_TEST_SERVICES = [
    'spotify',
    'server',
    'tidal',
    'qobuz',
    'lastfm',
    'genius',
    'listenbrainz',
    'acoustid',
    'discogs',
  ];

  const result = (id: string, good: boolean): SourceState =>
    good ? (model.getState().warned[id] ? 'warn' : 'ok') : 'fail';

  async function loadVideoChain() {
    try {
      const order = await readVideo();
      set({ video: order.slice() });
    } catch {
      // keep the last known order: a failed read must not empty the tiles
    }
  }

  async function readVideo(): Promise<string[]> {
    const cfg = (await fetchJson('/api/video/downloads/config')) || {};
    const hybrid = cfg.hybrid_order;
    if (cfg.download_mode === 'hybrid' && Array.isArray(hybrid) && hybrid.length) {
      return hybrid as string[];
    }
    return typeof cfg.download_mode === 'string' && cfg.download_mode
      ? [cfg.download_mode]
      : ['soulseek'];
  }

  function syncKindToSide() {
    if (model.getState().kindChosen) return;
    set({ kind: document.body.getAttribute('data-side') === 'video' ? 'video' : 'music' });
  }

  async function loadChain() {
    syncKindToSide();
    const kind = model.getState().kind;
    try {
      let order: string[];
      if (kind === 'video') {
        order = await readVideo();
        set({ video: order.slice() });
      } else if (kind === 'audiobooks') {
        order = audiobookChain();
      } else {
        order = model.getState().music.slice();
      }
      // the kind may have changed while the video chain was read
      if (model.getState().kind === kind) set({ order: order.slice() });
    } catch {
      if (model.getState().kind === kind) set({ order: [] });
    }
  }

  function writeMusic(order: string[]) {
    const previous = model.getState().secondary;
    const pair = legacyPair(order, previous);
    const mode = order.length > 1 ? 'hybrid' : order[0] || 'soulseek';
    set({ music: order.slice(), mode, secondary: pair.secondary });
    store.patch('download_source', {
      mode,
      hybrid_primary: pair.primary,
      hybrid_secondary: pair.secondary,
      hybrid_order: order.slice(),
    });
  }

  async function writeVideo(order: string[]) {
    const patch =
      order.length > 1
        ? { download_mode: 'hybrid', hybrid_order: order }
        : { download_mode: order[0] || 'soulseek', hybrid_order: order };
    // the video side has no page save: it saves on change
    await fetchJson('/api/video/downloads/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    // the tiles show the video chain too (#2026)
    set({ video: order.slice() });
  }

  function commit(order: string[]) {
    const kind = model.getState().kind;
    set({ order: order.slice() });
    try {
      if (kind === 'music') writeMusic(order);
      else if (kind === 'audiobooks') shell().setAudiobookChain?.(order.slice());
      else {
        writeVideo(order.slice()).catch(() => toast('Could not save the download chain', 'error'));
      }
    } catch {
      toast('Could not save the download chain', 'error');
    }
  }

  async function testOne(id: string): Promise<SourceState | null> {
    const probe = probes[id];
    if (!probe) return null;
    setStatus(id, 'testing');
    let good = false;
    try {
      good = await probe();
    } catch (error) {
      good = false;
      const msg =
        error instanceof Error ? error.message : typeof error === 'string' ? error : 'Test failed';
      model.setState((s) => ({ said: { ...s.said, [id]: msg || 'Test failed' } }));
    }
    const state = result(id, good);
    setStatus(id, state);
    return state;
  }

  async function testAll({ silent = false }: { silent?: boolean } = {}) {
    if (!silent) set({ testingAll: true });
    try {
      const state = model.getState();
      // the music chain's sources, or its one source
      const sources = new Set<string>();
      if (state.mode === 'hybrid') state.music.forEach((s) => sources.add(s));
      else if (state.mode) sources.add(state.mode);
      if (sources.size === 0) sources.add('soulseek');
      // the audiobook chain is its own setting: books over torrent drew a
      // grey torrent tile forever when only music was probed
      for (const id of audiobookActive()) sources.add(id);
      // and the video chain (#2026)
      if (model.getState().video === null) await loadVideoChain();
      for (const id of model.getState().video || []) if (probes[id]) sources.add(id);

      // torrent and usenet go through Prowlarr: test it first, and record it
      // (it is not in a chain, so the loop below never reaches it). On the
      // video side it is the main path, so it is always tested there.
      const videoSide = document.body.getAttribute('data-side') === 'video';
      if (sources.has('torrent') || sources.has('usenet') || videoSide) {
        setStatus('prowlarr', 'testing');
        try {
          setStatus('prowlarr', result('prowlarr', await testConnection('prowlarr')));
        } catch {
          setStatus('prowlarr', 'fail');
        }
      }

      for (const id of sources) setStatus(id, 'testing');

      let ok = 0;
      let fail = 0;
      const failedIds: string[] = [];
      for (const id of sources) {
        const probe = probes[id];
        if (!probe) {
          setStatus(id, 'na');
          continue;
        }
        try {
          const good = await probe();
          setStatus(id, result(id, good));
          if (good) ok++;
          else {
            fail++;
            failedIds.push(id);
          }
          // a warning is worth naming even though the source passed
          if (good && model.getState().warned[id]) failedIds.push(id);
        } catch {
          setStatus(id, 'fail');
          fail++;
          failedIds.push(id);
        }
      }

      // the configured metadata and server connections (not on the quiet
      // run when the page opens)
      let connOk = 0;
      let connFail = 0;
      if (!silent) {
        try {
          const cfg = await fetchJson('/api/settings/config-status');
          for (const svc of CONNECTION_TEST_SERVICES) {
            const entry = cfg?.[svc] as { configured?: boolean } | undefined;
            const configured = svc === 'server' ? true : !!entry?.configured;
            if (!configured) continue;
            try {
              if (await testConnection(svc)) connOk++;
              else {
                connFail++;
                failedIds.push(svc);
              }
            } catch {
              connFail++;
              failedIds.push(svc);
            }
          }
        } catch {
          // no config status: skip the connection sweep
        }
      }

      if (!silent) {
        const parts = [`sources ${ok}✓${fail ? ' / ' + fail + '✗' : ''}`];
        if (connOk || connFail) {
          parts.push(`connections ${connOk}✓${connFail ? ' / ' + connFail + '✗' : ''}`);
        }
        // a count says something broke, not what: carry the first reason (#1233)
        let detail = '';
        const said = model.getState().said;
        for (const id of failedIds) {
          if (said[id]) {
            detail = ` — ${said[id]}`;
            break;
          }
        }
        toast('Tested ' + parts.join(', ') + detail, fail || connFail ? 'error' : 'success');
      }
    } finally {
      if (!silent) set({ testingAll: false });
    }
  }

  return {
    getState: model.getState,
    subscribe: (listener) => model.subscribe(listener),
    load(settings) {
      const { music, unready, mode, cleaned } = musicFromSettings(settings);
      const pair = legacyPair(music, model.getState().secondary);
      set({ music, unready, mode, secondary: pair.secondary });
      // a retired source dropped on load: the next save writes the chain
      // that works, as the old page's next full save did. this runs inside
      // the settings load, where autosave is held, so it waits for that save
      if (cleaned) {
        store.patch('download_source', {
          mode,
          hybrid_primary: pair.primary,
          hybrid_secondary: pair.secondary,
          hybrid_order: music.slice(),
        });
      }
    },
    chains: () => [
      { label: 'Music', order: model.getState().music },
      { label: 'Video', order: model.getState().video || [] },
      { label: 'Audiobooks', order: audiobookActive() },
    ],
    loadChain,
    switchChain(kind) {
      if (!(kind in CHAIN_KINDS)) return;
      // a pick outranks the side default
      set({ kindChosen: true, kind: kind as ChainKind });
      void loadChain();
    },
    commit,
    loadVideoChain,
    testOne,
    canTest: (id) => id in probes,
    testAll,
    autoTestOnce() {
      if (autoTested) return;
      autoTested = true;
      setTimeout(() => {
        testAll({ silent: true }).catch(() => undefined);
      }, autoTestDelay);
    },
    setStatus,
  };
}

/** the page's one model */
export const sourcesModel = createSourcesModel();
