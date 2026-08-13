/**
 * URL-import helpers — the pure halves of the url-history bar and the
 * per-source id extraction, ported from sync-services.js 8680-8826 (history)
 * plus the parse-side regexes the link tabs share. localStorage/DOM stay in
 * the controller; everything here is data in, data out.
 */

export const URL_HISTORY_MAX = 10;

export interface UrlHistoryEntry {
  url: string;
  name: string;
  ts: number;
}

/** localStorage key + pill icon per source (DOM ids/load fns stay in the UI). */
export const URL_HISTORY_SOURCES: Readonly<Record<string, { key: string; icon: string }>> = {
  youtube: { key: 'soulsync-url-history-youtube', icon: '▶' },
  deezer: { key: 'soulsync-url-history-deezer', icon: '🎵' },
  'spotify-public': { key: 'soulsync-url-history-spotify-public', icon: '🎧' },
  'itunes-link': { key: 'soulsync-url-history-itunes-link', icon: '♪' },
};

/**
 * saveUrlHistory's list op (sync-services.js 8700-8706): drop the duplicate
 * url, unshift the new entry (name falls back to the url), cap at 10.
 */
export function historyWithEntry(
  history: UrlHistoryEntry[],
  url: string,
  name: string | null | undefined,
  ts: number,
): UrlHistoryEntry[] {
  let next = history.filter((h) => h.url !== url);
  next.unshift({ url, name: name || url, ts });
  if (next.length > URL_HISTORY_MAX) next = next.slice(0, URL_HISTORY_MAX);
  return next;
}

/** removeUrlHistoryEntry's list op. */
export function historyWithout(history: UrlHistoryEntry[], url: string): UrlHistoryEntry[] {
  return history.filter((h) => h.url !== url);
}

/** Pill label truncation (sync-services.js 8734): >30 chars → first 28 + '...'. */
export function pillDisplayName(name: string): string {
  return name.length > 30 ? name.substring(0, 28) + '...' : name;
}

/**
 * Deezer playlist id from a URL or a bare numeric id (the regex at
 * sync-services.js 8783-8784, shared with the Deezer-link parse head).
 */
export function extractDeezerPlaylistId(url: string): string | null {
  const match = url.match(/deezer\.com\/(?:[a-z]{2}\/)?playlist\/(\d+)/i);
  return match ? match[1] : /^\d+$/.test(url) ? url : null;
}

/**
 * Spotify id + type from an open.spotify.com playlist/album URL (the regex at
 * sync-services.js 8789, shared with parseSpotifyPublicUrl).
 */
export function extractSpotifyPublicId(url: string): { type: string; id: string } | null {
  const m = url.match(/open\.spotify\.com\/(playlist|album)\/([a-zA-Z0-9]+)/);
  return m ? { type: m[1], id: m[2] } : null;
}

/**
 * iTunes/Apple Music link id extraction (sync-services.js 8802-8820): 6 URI
 * schemes, the ?i= track param, /song and /album numeric path ids, and pl.*
 * playlist ids. Null for anything unrecognisable (including non-URLs).
 */
export function extractITunesLinkId(url: string): { type: string; id: string } | null {
  try {
    const raw = (url || '').trim();
    const uriMatch = raw.match(/^(?:itunes|applemusic):(album|track|playlist):([A-Za-z0-9._-]+)$/i);
    if (uriMatch) return { type: uriMatch[1].toLowerCase(), id: uriMatch[2] };
    const parsed = new URL(raw);
    const trackId = parsed.searchParams.get('i');
    if (trackId && /^\d+$/.test(trackId)) return { type: 'track', id: trackId };
    const songMatch = parsed.pathname.match(/\/song(?:\/[^/]+)?\/(\d+)/);
    if (songMatch) return { type: 'track', id: songMatch[1] };
    const albumMatch = parsed.pathname.match(/\/album(?:\/[^/]+)?\/(\d+)/);
    if (albumMatch) return { type: 'album', id: albumMatch[1] };
    const playlistMatch = parsed.pathname.match(/\/playlist(?:\/[^/]+)?\/(pl\.[A-Za-z0-9._-]+)/);
    if (playlistMatch) return { type: 'playlist', id: playlistMatch[1] };
  } catch {
    return null;
  }
  return null;
}
