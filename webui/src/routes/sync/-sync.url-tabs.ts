/**
 * The URL-import tab family's data layer — validation, dedupe, mirror track
 * projection, card presentation and url-history storage for the four
 * paste-a-link tabs (deezer-link 2706, spotify-public 6611, itunes-link 7633,
 * youtube 8806, url-history 8654 — all sync-services.js). Everything here is
 * data in, data out; fetches live in -sync.api.ts, DOM in the components.
 */

import type { UrlHistoryEntry } from './-sync.urls';

import { wrongTabError } from './-sync.url-detect';
import {
  extractDeezerPlaylistId,
  isDeezerShareUrl,
  extractITunesLinkId,
  extractSpotifyPublicId,
  URL_HISTORY_SOURCES,
} from './-sync.urls';

/** A parse-result playlist as the tabs hold it (duck-typed like the vanilla). */
export type UrlTabPlaylist = Record<string, unknown>;

/** Wire fields arrive untyped — coerce only strings/numbers, never objects. */
export function asString(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return fallback;
}

/* ── Input validation (the exact pre-parse toasts) ────────────────────────── */

/** parseYouTubePlaylist 8810-8819. */
export function youtubeUrlError(url: string): string | null {
  if (!url) return 'Please enter a YouTube playlist URL';
  if (!url.includes('youtube.com/playlist') && !url.includes('music.youtube.com/playlist')) {
    // A good link for a DIFFERENT tab gets named, not rejected as invalid.
    return wrongTabError(url, 'youtube') ?? 'Please enter a valid YouTube playlist URL';
  }
  return null;
}

/** loadDeezerPlaylist 2710-2728 — returns the id, or the error toast. */
export function deezerInputResult(
  rawUrl: string,
): { ok: true; id: string } | { ok: false; error: string } {
  if (!rawUrl) return { ok: false, error: 'Please paste a Deezer playlist URL' };
  const id = extractDeezerPlaylistId(rawUrl);
  if (!id) {
    // A share link is valid — it just hides its id behind a redirect the
    // browser cannot follow cross-origin. Say what to do instead of implying
    // Deezer's own Share button produces a malformed URL.
    if (isDeezerShareUrl(rawUrl)) {
      return {
        ok: false,
        error:
          'That is a Deezer share link, which hides the playlist id. Open it in a ' +
          'browser and paste the deezer.com/playlist/... address it lands on.',
      };
    }
    return {
      ok: false,
      error:
        wrongTabError(rawUrl, 'deezer') ??
        'Invalid Deezer playlist URL. Expected format: deezer.com/playlist/{id}',
    };
  }
  return { ok: true, id };
}

/** parseSpotifyPublicUrl 6615-6625. */
export function spotifyPublicUrlError(url: string): string | null {
  if (!url) return 'Please enter a Spotify URL';
  if (
    !url.includes('open.spotify.com/playlist') &&
    !url.includes('open.spotify.com/album') &&
    !url.startsWith('spotify:playlist:') &&
    !url.startsWith('spotify:album:')
  ) {
    return (
      wrongTabError(url, 'spotify_public') ?? 'Please enter a valid Spotify playlist or album URL'
    );
  }
  return null;
}

/** parseITunesLinkUrl 7637-7650 — including the vanilla's 'a iTunes' typo. */
export function itunesLinkUrlError(url: string): string | null {
  if (!url) return 'Please enter a iTunes URL';
  const lowerUrl = url.toLowerCase();
  if (
    !lowerUrl.includes('itunes.apple.com') &&
    !lowerUrl.includes('music.apple.com') &&
    !lowerUrl.startsWith('itunes:album:') &&
    !lowerUrl.startsWith('itunes:track:') &&
    !lowerUrl.startsWith('itunes:playlist:') &&
    !lowerUrl.startsWith('applemusic:album:') &&
    !lowerUrl.startsWith('applemusic:track:') &&
    !lowerUrl.startsWith('applemusic:playlist:')
  ) {
    return wrongTabError(url, 'itunes_link') ?? 'Please enter a valid iTunes or Apple Music URL';
  }
  return null;
}

/* ── Already-loaded dedupe (_isUrlAlreadyLoaded 8744-8774, array-backed) ──── */

/** The YouTube branch scanned card DOM data-url attributes; the React tab
 * keeps loaded + pending urls in state and checks those instead. */
export function youtubeAlreadyLoaded(loadedUrls: string[], url: string): boolean {
  return loadedUrls.includes(url);
}

export function deezerAlreadyLoaded(playlists: UrlTabPlaylist[], playlistId: string): boolean {
  return playlists.some((p) => String(p.id) === String(playlistId));
}

export function spotifyPublicAlreadyLoaded(playlists: UrlTabPlaylist[], url: string): boolean {
  const parsed = extractSpotifyPublicId(url);
  if (parsed && playlists.some((p) => p.id === parsed.id)) return true;
  return playlists.some((p) => p.url === url);
}

export function itunesLinkAlreadyLoaded(playlists: UrlTabPlaylist[], url: string): boolean {
  const parsed = extractITunesLinkId(url);
  if (parsed && playlists.some((p) => p.id === parsed.id && p.type === parsed.type)) return true;
  return playlists.some((p) => p.url === url);
}

/* ── Auto-mirror track projections (the exact per-source .map bodies) ─────── */

type RawTrack = Record<string, unknown>;

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** loadDeezerPlaylist 2756-2760. */
export function deezerMirrorTracks(tracks: unknown[]): Record<string, unknown>[] {
  return (tracks as RawTrack[]).map((t) => ({
    track_name: t.name || '',
    artist_name: Array.isArray(t.artists) ? t.artists[0] : t.artists || '',
    album_name: typeof t.album === 'string' ? t.album : '',
    duration_ms: t.duration_ms || 0,
    source_track_id: t.id || '',
  }));
}

/** parseSpotifyPublicUrl 6669-6675. */
export function spotifyPublicMirrorTracks(tracks: unknown[]): Record<string, unknown>[] {
  return (tracks as RawTrack[]).map((t) => ({
    track_name: t.name || '',
    artist_name: Array.isArray(t.artists)
      ? (t.artists as { name?: string }[]).map((a) => a.name).join(', ')
      : '',
    album_name: (t.album as { name?: string } | undefined)?.name || '',
    duration_ms: t.duration_ms || 0,
    source_track_id: t.id || '',
  }));
}

/** parseITunesLinkUrl 7694-7700 — the hardened object-or-string artist map. */
export function itunesLinkMirrorTracks(tracks: unknown[]): Record<string, unknown>[] {
  return (tracks as RawTrack[]).map((t) => ({
    track_name: t.name || '',
    artist_name: Array.isArray(t.artists)
      ? asArray(t.artists)
          .map((a) => (typeof a === 'object' && a !== null ? (a as { name?: string }).name : a))
          .filter(Boolean)
          .join(', ')
      : '',
    album_name: (t.album as { name?: string } | undefined)?.name || '',
    duration_ms: t.duration_ms || 0,
    source_track_id: t.id || '',
  }));
}

/** parseYouTubePlaylist 8861-8864 — album_name always '', artist falls to t.artist. */
export function youtubeMirrorTracks(tracks: unknown[]): Record<string, unknown>[] {
  return (tracks as RawTrack[]).map((t) => ({
    track_name: t.name || t.title || '',
    artist_name: Array.isArray(t.artists) ? t.artists[0] : t.artist || '',
    album_name: '',
    duration_ms: t.duration_ms || 0,
    source_track_id: t.id || '',
  }));
}

/* ── Card presentation ────────────────────────────────────────────────────── */

export interface CardTypeBadge {
  text: string;
  color: string;
}

/** createSpotifyPublicCard 6728-6740: 💿 Album grey vs 🎵 Playlist Spotify green. */
export function spotifyPublicCardIcon(playlist: UrlTabPlaylist): string {
  return playlist.type === 'album' ? '💿' : '🎵';
}

export function spotifyPublicTypeBadge(playlist: UrlTabPlaylist): CardTypeBadge {
  const isAlbum = playlist.type === 'album';
  return { text: isAlbum ? 'Album' : 'Playlist', color: isAlbum ? '#b3b3b3' : '#1DB954' };
}

/** createITunesLinkCard 7753-7766: album grey; playlist/track Apple pink. */
export function itunesLinkCardIcon(playlist: UrlTabPlaylist): string {
  if (playlist.type === 'album') return '💿';
  return playlist.type === 'playlist' ? '♫' : '🎵';
}

export function itunesLinkTypeBadge(playlist: UrlTabPlaylist): CardTypeBadge {
  const isAlbum = playlist.type === 'album';
  return {
    text: isAlbum ? 'Album' : playlist.type === 'playlist' ? 'Playlist' : 'Track',
    color: isAlbum ? '#b3b3b3' : '#fa586a',
  };
}

/**
 * YouTube's card runs its OWN 7-case button map (updateYouTubeCardPhase
 * 8982-9037), drifting from the shared getActionButtonText: fresh 'Start
 * Discovery' (not 'Discover'), discovered AND sync_complete 'View Details'
 * (not 'View Results'/'Download'), download_complete 'View Results' (not
 * 'Complete'). Transcribed as-is. (The vanilla's RESTORED cards briefly
 * used the shared map until their first phase update,
 * createYouTubeCardFromBackendState — the port applies this drift map
 * uniformly, picking the steady-state side of that inconsistency.)
 */
export function ytActionButtonText(phase: string): string {
  switch (phase) {
    case 'fresh':
      return 'Start Discovery';
    case 'discovering':
    case 'syncing':
      return 'View Progress';
    case 'discovered':
    case 'sync_complete':
      return 'View Details';
    case 'downloading':
      return 'View Downloads';
    case 'download_complete':
      return 'View Results';
    default:
      return 'Open';
  }
}

/**
 * YouTube also drifts on progress visibility: the shared cards hide the
 * progress element only in 'fresh' (create*Card), but YT hides it in every
 * settled phase and shows it only while work runs (8988-9036).
 */
export function ytProgressVisible(phase: string): boolean {
  return phase === 'discovering' || phase === 'syncing' || phase === 'downloading';
}

/* ── Card progress lines ──────────────────────────────────────────────────── */

export interface CardProgressCounts {
  total: number;
  matches: number;
}

/**
 * The slash-text writers' four numbers (updateYouTubeCardProgress 9120-9125).
 *
 * Split out from the string so the card renderer and the string share ONE
 * arithmetic — the card no longer prints this text, but the formula is the same
 * formula and two copies of it would be exactly the drift this page already has
 * too much of.
 */
export function slashTextCounts(progress: { spotify_total?: number; spotify_matches?: number }): {
  total: number;
  matches: number;
  failed: number;
  percentage: number;
} {
  const total = progress.spotify_total || 0;
  // clamped for the same reason as the lb card and the modal line: the
  // counter can outrun the total and nothing corrects it after discovery
  const matchesRaw = progress.spotify_matches || 0;
  const matches = total > 0 ? Math.min(matchesRaw, total) : matchesRaw;
  return {
    total,
    matches,
    failed: Math.max(0, total - matches),
    percentage: total > 0 ? Math.min(100, Math.round((matches / total) * 100)) : 0,
  };
}

/** updateYouTubeCardProgress 9120-9125 — the slash-text format. */
export function slashTextProgressLine(progress: {
  spotify_total?: number;
  spotify_matches?: number;
}): string {
  const { total, matches, failed, percentage } = slashTextCounts(progress);
  return `♪ ${total} / ✓ ${matches} / ✗ ${failed} / ${percentage}%`;
}

/**
 * updateDeezerCardProgress (and the spotify-public/itunes twins) — the
 * check-note-spans format only paints when total > 0; otherwise the element
 * stays visible but empty.
 */
export function checkNoteCounts(progress: {
  spotify_total?: number;
  spotify_matches?: number;
}): CardProgressCounts | null {
  const total = progress.spotify_total || 0;
  const matches = progress.spotify_matches || 0;
  return total > 0 ? { total, matches } : null;
}

/* ── url-history localStorage glue (getUrlHistory 8662-8669) ──────────────── */

export function readUrlHistory(source: string): UrlHistoryEntry[] {
  try {
    const cfg = URL_HISTORY_SOURCES[source];
    if (!cfg) return [];
    const raw = localStorage.getItem(cfg.key);
    return raw ? (JSON.parse(raw) as UrlHistoryEntry[]) : [];
  } catch {
    return [];
  }
}

export function writeUrlHistory(source: string, history: UrlHistoryEntry[]): void {
  const cfg = URL_HISTORY_SOURCES[source];
  if (!cfg) return;
  localStorage.setItem(cfg.key, JSON.stringify(history));
}
