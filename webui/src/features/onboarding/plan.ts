/**
 * Pure rules for the setup flow: which steps apply, which folders a purpose
 * needs, whether a step can move on, and the exact settings the ready step
 * saves. No fetches, no DOM, so every decision here is unit tested.
 */

import type { Draft, Environment, FolderKey, Purpose, SourceId, StepId } from './types';

export const PROFILE_COLORS = ['#1db954', '#8b5cf6', '#0ea5e9', '#f59e0b', '#f43f5e', '#e4e4e7'];

export const SOURCE_INFO: Record<
  SourceId,
  { name: string; desc: string; need: string; free: boolean }
> = {
  soulseek: {
    name: 'Soulseek',
    desc: 'Best selection. Whole albums from one person.',
    need: 'Needs slskd',
    free: false,
  },
  hifi: { name: 'HiFi', desc: 'Free lossless, no account.', need: 'No account', free: true },
  youtube: { name: 'YouTube', desc: 'Almost everything, lossy.', need: 'No account', free: true },
  tidal: { name: 'Tidal', desc: 'Up to 24-bit FLAC.', need: 'Sign in with Tidal', free: false },
  qobuz: { name: 'Qobuz', desc: 'Up to 24-bit 192 kHz.', need: 'Sign in with Qobuz', free: false },
  deezer_dl: {
    name: 'Deezer',
    desc: 'FLAC with a paid account.',
    need: 'Paste an ARL token',
    free: false,
  },
  soundcloud: { name: 'SoundCloud', desc: 'Mixes and uploads.', need: 'No account', free: true },
};

export const ALL_SOURCES = Object.keys(SOURCE_INFO) as SourceId[];

export const STEP_LABELS: Record<StepId, string> = {
  welcome: 'Welcome',
  account: 'Your account',
  purpose: "What it's for",
  server: 'Media server',
  folders: 'Folders',
  sources: 'Download sources',
  metadata: 'Metadata and accounts',
  video: 'Movies and TV',
  library: 'Make it yours',
  ready: 'Ready',
};

export const FOLDER_INFO: Record<FolderKey, { name: string; hint: string }> = {
  download: { name: 'Downloads', hint: 'Where downloads land first' },
  library: { name: 'Music library', hint: 'Finished, tagged music' },
  staging: { name: 'Import inbox', hint: 'Music you already have' },
  movies: { name: 'Movies', hint: 'Finished films' },
  tv: { name: 'TV', hint: 'Finished episodes' },
  youtube: { name: 'YouTube', hint: 'Followed channels' },
  podcasts: { name: 'Podcasts', hint: 'Downloaded episodes' },
  audiobooks: { name: 'Audiobooks', hint: 'Downloaded books' },
};

export function hasMusic(purposes: Purpose[]): boolean {
  return purposes.includes('music');
}

export function hasVideo(purposes: Purpose[]): boolean {
  return purposes.includes('video');
}

/** the steps this install walks through, in order */
export function visibleSteps(purposes: Purpose[]): StepId[] {
  const steps: StepId[] = ['account', 'purpose', 'server', 'folders'];
  if (hasMusic(purposes)) steps.push('sources', 'metadata');
  if (hasVideo(purposes)) steps.push('video');
  if (hasMusic(purposes)) steps.push('library');
  steps.push('ready');
  return steps;
}

export function folderRows(purposes: Purpose[]): FolderKey[] {
  const rows: FolderKey[] = [];
  const any = (...p: Purpose[]) => p.some((x) => purposes.includes(x));
  if (any('music', 'video', 'youtube')) rows.push('download');
  if (any('music')) rows.push('library', 'staging');
  if (any('video')) rows.push('movies', 'tv');
  if (any('youtube')) rows.push('youtube');
  if (any('podcasts')) rows.push('podcasts');
  if (any('audiobooks')) rows.push('audiobooks');
  return rows;
}

export function emptyDraft(env?: Partial<Environment>): Draft {
  const folders = {
    download: '',
    library: '',
    staging: '',
    movies: '',
    tv: '',
    youtube: '',
    podcasts: '',
    audiobooks: '',
    ...env?.folders,
  };
  return {
    account: { name: '', color: PROFILE_COLORS[0], lock: 'pin', pin: '', password: '' },
    purposes: ['music'],
    server: {
      kind: null,
      url: '',
      token: '',
      apiKey: '',
      username: '',
      password: '',
      serverName: '',
      connected: false,
      musicLibrary: null,
      moviesLibrary: null,
      tvLibrary: null,
      libraryPaths: {},
    },
    folders,
    chain: ['hifi', 'youtube'],
    slskd: { url: '', apiKey: '', connected: false },
    deezerArl: '',
    signedIn: {},
    preset: 'balanced',
    catalogue: 'deezer',
    spotify: { clientId: '', clientSecret: '' },
    listenbrainz: { token: '', user: '' },
    lastfm: { apiKey: '', username: '' },
    video: {
      tmdbKey: '',
      prowlarr: { url: '', apiKey: '' },
      client: { kind: 'qbittorrent', url: '', username: '', password: '', apiKey: '' },
    },
    follows: [],
    playlistUrl: '',
  };
}

export interface Gate {
  ok: boolean;
  reason?: string;
}

/**
 * Can this step move on. Only things that would leave the install broken
 * block; everything optional is just left empty.
 */
export function canContinue(step: StepId, d: Draft): Gate {
  switch (step) {
    case 'account': {
      if (!d.account.name.trim()) return { ok: false, reason: 'Add your name' };
      if (d.account.lock === 'pin' && !/^\d{4,8}$/.test(d.account.pin))
        return { ok: false, reason: 'A PIN is 4 to 8 digits' };
      if (d.account.lock === 'password' && d.account.password.length < 8)
        return { ok: false, reason: 'Use at least 8 characters' };
      return { ok: true };
    }
    case 'purpose':
      return d.purposes.length ? { ok: true } : { ok: false, reason: 'Pick at least one' };
    case 'server': {
      const s = d.server;
      if (!s.kind) return { ok: false, reason: 'Pick a server, or No server' };
      if (s.kind !== 'soulsync' && !s.connected)
        return { ok: false, reason: `Connect ${serverLabel(s.kind)} first` };
      return { ok: true };
    }
    case 'sources': {
      if (!d.chain.length) return { ok: false, reason: 'Add at least one source' };
      if (d.chain.includes('soulseek') && !d.slskd.connected)
        return { ok: false, reason: 'Connect slskd, or remove Soulseek' };
      if (d.chain.includes('deezer_dl') && !d.deezerArl.trim())
        return { ok: false, reason: 'Paste your Deezer ARL, or remove Deezer' };
      return { ok: true };
    }
    case 'metadata':
      if (
        d.catalogue === 'spotify' &&
        !(d.spotify.clientId.trim() && d.spotify.clientSecret.trim())
      )
        return { ok: false, reason: 'Spotify as the catalogue needs its app keys' };
      return { ok: true };
    default:
      return { ok: true };
  }
}

export function serverLabel(kind: Draft['server']['kind']): string {
  switch (kind) {
    case 'plex':
      return 'Plex';
    case 'jellyfin':
      return 'Jellyfin';
    case 'navidrome':
      return 'Navidrome';
    case 'soulsync':
      return 'SoulSync';
    default:
      return 'a server';
  }
}

/** the values a server's own config section takes */
export function serverConfig(s: Draft['server']): Record<string, string> {
  switch (s.kind) {
    case 'plex':
      return { base_url: s.url.trim(), token: s.token.trim() };
    case 'jellyfin':
      return { base_url: s.url.trim(), api_key: s.apiKey.trim() };
    case 'navidrome':
      return { base_url: s.url.trim(), username: s.username.trim(), password: s.password };
    default:
      return {};
  }
}

export function downloadSourceConfig(chain: SourceId[]): Record<string, unknown> {
  if (chain.length === 1) return { mode: chain[0] };
  return { mode: 'hybrid', hybrid_order: [...chain] };
}

/**
 * The one /api/settings payload the ready step saves. Only what the user
 * chose is in it, so a skipped step leaves the install's own defaults alone.
 */
export function buildSettingsPatch(d: Draft): Record<string, Record<string, unknown> | string> {
  const patch: Record<string, Record<string, unknown> | string> = {};
  const music = hasMusic(d.purposes);
  const rows = folderRows(d.purposes);
  const set = (section: string, values: Record<string, unknown>) => {
    const prev = (patch[section] as Record<string, unknown> | undefined) ?? {};
    patch[section] = { ...prev, ...values };
  };

  if (d.server.kind) {
    patch.active_media_server = d.server.kind;
    if (d.server.kind !== 'soulsync') set(d.server.kind, serverConfig(d.server));
  }

  if (rows.includes('download') && d.folders.download.trim())
    set('soulseek', { download_path: d.folders.download.trim() });
  if (music && d.folders.library.trim())
    set('soulseek', { transfer_path: d.folders.library.trim() });
  if (music && d.folders.staging.trim()) set('import', { staging_path: d.folders.staging.trim() });
  if (rows.includes('podcasts') && d.folders.podcasts.trim())
    set('podcasts', { download_path: d.folders.podcasts.trim() });
  if (rows.includes('audiobooks') && d.folders.audiobooks.trim())
    set('audiobooks', { download_path: d.folders.audiobooks.trim() });

  if (music) {
    if (d.chain.length) set('download_source', downloadSourceConfig(d.chain));
    if (d.chain.includes('soulseek'))
      set('soulseek', { slskd_url: d.slskd.url.trim(), api_key: d.slskd.apiKey.trim() });
    if (d.chain.includes('deezer_dl') && d.deezerArl.trim())
      set('deezer_download', { arl: d.deezerArl.trim() });
    set('metadata', { fallback_source: d.catalogue });
    if (d.spotify.clientId.trim() && d.spotify.clientSecret.trim())
      set('spotify', {
        client_id: d.spotify.clientId.trim(),
        client_secret: d.spotify.clientSecret.trim(),
      });
    if (d.listenbrainz.token.trim()) set('listenbrainz', { token: d.listenbrainz.token.trim() });
    if (d.lastfm.apiKey.trim())
      set('lastfm', { api_key: d.lastfm.apiKey.trim(), username: d.lastfm.username.trim() });
  }

  if (hasVideo(d.purposes)) {
    const p = d.video.prowlarr;
    if (p.url.trim() && p.apiKey.trim())
      set('prowlarr', { url: p.url.trim(), api_key: p.apiKey.trim() });
    const c = d.video.client;
    if (c.url.trim()) {
      if (c.kind === 'qbittorrent')
        set('torrent_client', {
          type: 'qbittorrent',
          url: c.url.trim(),
          username: c.username.trim(),
          password: c.password,
        });
      else set('usenet_client', { type: 'sabnzbd', url: c.url.trim(), api_key: c.apiKey.trim() });
    }
  }
  return patch;
}

/** POST /api/video/downloads/config, or null when the video side wasn't picked */
export function buildVideoDownloadConfig(d: Draft): Record<string, unknown> | null {
  const video = hasVideo(d.purposes);
  const youtube = d.purposes.includes('youtube');
  if (!video && !youtube) return null;
  const out: Record<string, unknown> = {};
  if (video) {
    if (d.folders.movies.trim()) out.movies_path = d.folders.movies.trim();
    if (d.folders.tv.trim()) out.tv_path = d.folders.tv.trim();
    if (d.video.client.url.trim())
      out.download_mode = d.video.client.kind === 'qbittorrent' ? 'torrent' : 'usenet';
  }
  if (youtube && d.folders.youtube.trim()) out.youtube_path = d.folders.youtube.trim();
  return Object.keys(out).length ? out : null;
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}
