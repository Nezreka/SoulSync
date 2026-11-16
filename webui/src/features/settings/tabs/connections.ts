/**
 * The Connections tab's rules (SETTINGS_PORT.md, step 10), apart from its
 * markup: which metadata sources can be picked and what the picker shows,
 * the MusicBrainz server check, and the two things other parts of the page
 * tell it (the /status frame and whether Hydrabase is connected).
 */

import type { SettingsPayload } from '../settings-store';

type Section_ = Record<string, unknown>;

// ── what the page hears from /status ──

export interface SpotifyStatus {
  authenticated?: boolean;
  free_installed?: boolean;
  rate_limited?: boolean;
  rate_limit?: unknown;
  post_ban_cooldown?: number;
}
export interface StatusFrame {
  spotify?: SpotifyStatus | null;
  metadata_source?: { source?: string } | null;
}

/** the last /status frame: core.js keeps it as `_lastStatusPayload` and
 *  sends each new one as ss:service-status (the poller and the socket both) */
function lastFrame(): StatusFrame | null {
  return (
    typeof _lastStatusPayload === 'undefined' ? null : _lastStatusPayload
  ) as StatusFrame | null;
}

let frame: StatusFrame | null | undefined;
const frameListeners = new Set<() => void>();
function heard(event: Event) {
  frame = ((event as CustomEvent<StatusFrame>).detail || null) as StatusFrame | null;
  for (const listener of frameListeners) listener();
}

export const statusFrames = {
  subscribe: (listener: () => void) => {
    if (!frameListeners.size) {
      // nothing was listening: what was heard before may be stale
      frame = undefined;
      window.addEventListener('ss:service-status', heard);
    }
    frameListeners.add(listener);
    return () => {
      frameListeners.delete(listener);
      if (!frameListeners.size) window.removeEventListener('ss:service-status', heard);
    };
  },
  get: (): StatusFrame | null => {
    if (frame === undefined) frame = lastFrame();
    return frame;
  },
};

// ── whether Hydrabase is connected (its card and the metadata picker) ──

let hydrabase = false;
const hydrabaseListeners = new Set<() => void>();
export const hydrabaseLink = {
  subscribe: (listener: () => void) => {
    hydrabaseListeners.add(listener);
    return () => {
      hydrabaseListeners.delete(listener);
    };
  },
  get: () => hydrabase,
  set: (connected: boolean) => {
    if (hydrabase === connected) return;
    hydrabase = connected;
    for (const listener of hydrabaseListeners) listener();
  },
};

// ── the metadata source picker ──

export const METADATA_SOURCES = [
  { value: 'spotify', label: 'Spotify' },
  { value: 'spotify_free', label: 'Spotify (no auth)' },
  { value: 'itunes', label: 'iTunes / Apple Music' },
  { value: 'deezer', label: 'Deezer' },
  { value: 'discogs', label: 'Discogs' },
  { value: 'musicbrainz', label: 'MusicBrainz' },
];

export interface SourceOption {
  value: string;
  label: string;
  /** what settings.js syncPrimaryMetadataSourceAvailability set, once a
   *  /status frame had arrived */
  title?: string;
  unavailable?: 'true' | 'false';
}

/** a stored value as text: a string as it is, nothing as empty */
const str = (value: unknown) =>
  typeof value === 'string' ? value : typeof value === 'number' ? String(value) : '';

const discogsToken = (settings: SettingsPayload) =>
  str((settings.discogs as Section_ | undefined)?.token).trim();

export const jiosaavnOn = (settings: SettingsPayload) =>
  (settings.experimental as Section_ | undefined)?.jiosaavn_enabled === true;

/** the picker's options: the locks show once /status has spoken, JioSaavn
 *  sits after MusicBrainz when it is switched on, Hydrabase last while it
 *  is connected */
export function sourceOptions(
  settings: SettingsPayload,
  status: StatusFrame | null,
  hydrabaseConnected: boolean,
): SourceOption[] {
  const spotify = status?.spotify;
  const out: SourceOption[] = METADATA_SOURCES.map((option) => {
    if (!spotify) return { ...option };
    if (option.value === 'spotify') {
      const ok = spotify.authenticated === true;
      return {
        ...option,
        label: ok ? 'Spotify' : '🔒 Spotify',
        title: ok
          ? 'Spotify'
          : 'Spotify authentication is required before this source can be selected.',
        unavailable: ok ? 'false' : 'true',
      };
    }
    if (option.value === 'discogs') {
      const ok = !!discogsToken(settings);
      return {
        ...option,
        label: ok ? 'Discogs' : '🔒 Discogs',
        title: ok
          ? 'Discogs'
          : 'Discogs personal access token is required before this source can be selected.',
        unavailable: ok ? 'false' : 'true',
      };
    }
    return { ...option };
  });
  if (jiosaavnOn(settings)) out.push({ value: 'jiosaavn', label: 'JioSaavn' });
  if (hydrabaseConnected) out.push({ value: 'hydrabase', label: 'Hydrabase (P2P)' });
  return out;
}

/** settings.js _isMetadataSourceSelectable */
export function selectable(
  source: string,
  settings: SettingsPayload,
  status: StatusFrame | null,
): boolean {
  if (source === 'spotify') return status?.spotify?.authenticated === true;
  // no-creds Spotify only needs SpotipyFree installed: picking it is the opt-in
  if (source === 'spotify_free') return status?.spotify?.free_installed === true;
  if (source === 'discogs') return !!discogsToken(settings);
  if (source === 'jiosaavn') return jiosaavnOn(settings);
  return true;
}

/** the picker's value from the saved settings: Spotify Free is stored as the
 *  spotify source plus a flag */
export function savedSource(settings: SettingsPayload): string {
  const meta = (settings.metadata || {}) as Section_;
  const source = str(meta.fallback_source) || 'deezer';
  return source === 'spotify' && meta.spotify_free === true ? 'spotify_free' : source;
}

/** what a picked source saves as */
export function sourceKeys(source: string) {
  return {
    fallback_source: source === 'spotify_free' ? 'spotify' : source,
    spotify_free: source === 'spotify_free',
  };
}

/** the warning settings.js gave when a source could not be picked */
export function lockedMessage(source: string) {
  return source === 'discogs'
    ? 'Discogs requires a personal access token before it can be selected as the primary metadata source.'
    : 'Spotify must be authenticated before it can be selected as the primary metadata source.';
}

// ── MusicBrainz ──

/** settings.js loadMusicBrainzServerSettings: the mode and the rate shown */
export function musicbrainzRate(settings: SettingsPayload) {
  const mb = (settings.musicbrainz || {}) as Section_;
  const interval = Number(mb.request_interval ?? 1.05);
  return {
    mode: interval === 0 ? 'adaptive' : 'fixed',
    rate: String(interval > 0 ? Number((1 / interval).toPrecision(6)) : 10),
  };
}

/** settings.js collectMusicBrainzServerSettings: what is wrong with the
 *  server fields (the page refused its whole save while anything was), or
 *  the keys they save */
export function musicbrainzKeys(
  baseUrl: string,
  mode: string,
  rawRate: string,
): { error: string } | { base_url: string; request_interval: number } {
  const base_url = baseUrl.trim();
  const adaptive = mode === 'adaptive';
  const raw = rawRate.trim();
  const rate = Number(raw);
  let host = 'musicbrainz.org';
  if (base_url) {
    let url: URL;
    try {
      url = new URL(base_url);
    } catch {
      return { error: 'MusicBrainz server URL must start with http:// or https://.' };
    }
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    ) {
      return {
        error: 'Use a MusicBrainz HTTP(S) URL without credentials, query strings or fragments.',
      };
    }
    host = url.hostname.toLowerCase().replace(/\.$/, '');
  }
  if (adaptive && (host === 'musicbrainz.org' || host.endsWith('.musicbrainz.org')))
    return { error: 'No limit (adaptive) is only for a self-hosted MusicBrainz server.' };
  if (!adaptive && (!raw || !Number.isFinite(rate) || rate <= 0))
    return { error: 'MusicBrainz requests/second must be a positive number.' };
  return { base_url, request_interval: adaptive ? 0 : 1 / rate };
}

// ── redirect URIs ──

export const REDIRECT_DEFAULTS = {
  spotify: 'http://127.0.0.1:8888/callback',
  tidal: 'http://127.0.0.1:8889/tidal/callback',
  deezer: 'http://127.0.0.1:8008/deezer/callback',
} as const;

// ── Qobuz sign-in ──

export interface QobuzAuthState {
  authenticated?: boolean;
  user?: { display_name?: string; subscription?: string };
}

/** settings.js checkQobuzAuthStatus: asks, then tells the Qobuz source panel
 *  and the Connections card, by soulsync:qobuz-auth */
export async function checkQobuzAuthStatus() {
  try {
    const resp = await fetch('/api/qobuz/auth/status');
    const data = (await resp.json()) as QobuzAuthState;
    window.dispatchEvent(new CustomEvent('soulsync:qobuz-auth', { detail: data }));
  } catch (error) {
    console.error('Qobuz auth status check failed:', error);
  }
}

/** hear each answer; returns the way to stop */
export function onQobuzAuth(heard: (auth: QobuzAuthState) => void) {
  const listener = (event: Event) => heard((event as CustomEvent<QobuzAuthState>).detail || {});
  window.addEventListener('soulsync:qobuz-auth', listener);
  return () => window.removeEventListener('soulsync:qobuz-auth', listener);
}
