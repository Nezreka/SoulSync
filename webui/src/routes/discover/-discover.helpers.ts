/**
 * Pure helpers ported from webui/static/discover.js.
 *
 * Every function here is a pure function of its arguments — no DOM, no fetch,
 * no module state — which is what lets `-discover.helpers.differential.test.ts`
 * lift the REAL vanilla functions out of discover.js and assert this port is
 * byte-identical to them.
 *
 * ── The escapeHtml difference, which is deliberate ──────────────────────────
 *
 * The vanilla reason-strings call `escapeHtml()` internally, because the
 * vanilla drops them straight into `innerHTML`. React escapes on render, so a
 * 1:1 port would DOUBLE-escape: a library containing `AC/DC & Friends` would
 * literally show `AC/DC &amp; Friends` on screen.
 *
 * So the ported versions return RAW text and let React do the escaping — the
 * correct React translation of the same intent. The differential test proves
 * they still match by supplying `escapeHtml = (s) => s` to the vanilla, which
 * makes the vanilla return raw text too. Identical logic, escaping moved to the
 * one place that should own it.
 */

/** A track row as the discover endpoints return it (several shapes, see below). */
export interface DiscoverTrackLike {
  track_data_json?: Record<string, unknown> | null;
  track_name?: string;
  artist_name?: string;
  album_name?: string;
  album_cover_url?: string;
  duration_ms?: number;
  spotify_track_id?: string;
  name?: string;
  [key: string]: unknown;
}

export interface NormalizedTrack {
  name: string;
  artist: string;
  album: string;
  cover: string;
  durationMs: number;
}

/** An artist recommendation carrying the "because you have X" provenance. */
export interface RecommendedArtistLike {
  because?: string[];
  occurrence_count?: number;
  [key: string]: unknown;
}

/**
 * Strip featured-artist noise from an artist name.
 *
 * Note the falsy passthrough: the vanilla returns the ARGUMENT unchanged when
 * it is falsy, so `null` stays `null` and `''` stays `''` — it does not
 * normalise to a string. Callers rely on that, so the port preserves it.
 */
export function cleanArtistName<T extends string | null | undefined>(artistName: T): T | string {
  if (!artistName) return artistName;

  const patterns = [
    /\s+feat\.?\s+.*/i, //     "feat." or "feat"
    /\s+featuring\s+.*/i, //   "featuring"
    /\s+ft\.?\s+.*/i, //       "ft." or "ft"
    /\s+with\s+.*/i, //        "with"
    /\s+x\s+.*/i, //           " x " (common in collaborations)
  ];

  let cleaned: string = artistName;
  for (const pattern of patterns) {
    cleaned = cleaned.replace(pattern, '');
  }

  return cleaned.trim();
}

/**
 * Flatten a discover track row to the fields the UI actually renders.
 *
 * Rows arrive in two shapes: enriched rows carry a nested `track_data_json`
 * (Spotify-shaped), while decade/Spotify rows carry name/artists[]/album at the
 * top level. The `||` chain handles both — the same fallback the vanilla's
 * `_renderTabbedTrackList` uses.
 *
 * `album` and `cover` deliberately fall back to '' rather than a placeholder:
 * ListenBrainz recording playlists carry neither, and the vanilla chose a blank
 * over printing "Unknown Album" / "0:00".
 */
export function normalizeTrack(track: DiscoverTrackLike): NormalizedTrack {
  const td = ((track && track.track_data_json) || track || {}) as Record<string, unknown>;
  const artists = td.artists as { name?: string }[] | string[] | undefined;
  const a0 = artists && artists[0];
  const album = td.album as { name?: string; images?: { url?: string }[] } | undefined;

  return {
    name: (td.name as string) || (td.track_name as string) || track.track_name || 'Unknown Track',
    artist:
      (a0 && ((a0 as { name?: string }).name || (a0 as unknown as string))) ||
      (td.artist_name as string) ||
      track.artist_name ||
      'Unknown Artist',
    album: (album && album.name) || (td.album_name as string) || track.album_name || '',
    cover:
      (album && album.images && album.images[0] && album.images[0].url) ||
      track.album_cover_url ||
      '',
    durationMs: (td.duration_ms as number) || track.duration_ms || 0,
  };
}

/**
 * Re-shape a discover row into the Spotify track shape the download modals and
 * the media player both expect.
 *
 * The artists array is flattened to plain names on the way out — rows reach
 * here with either `[{name}]` or `['name']` depending on the source.
 */
export function discoverTrackToSpotifyShape(track: DiscoverTrackLike): Record<string, unknown> {
  const s: Record<string, unknown> = track.track_data_json
    ? { ...track.track_data_json }
    : {
        id: track.spotify_track_id,
        name: track.track_name || track.name,
        artists: [{ name: track.artist_name }],
        album: {
          name: track.album_name || '',
          images: track.album_cover_url ? [{ url: track.album_cover_url }] : [],
        },
        duration_ms: track.duration_ms || 0,
      };

  if (s.artists && Array.isArray(s.artists)) {
    s.artists = (s.artists as ({ name?: string } | string)[]).map(
      (a) => (a && (a as { name?: string }).name) || a,
    );
  }
  return s;
}

/**
 * "Because you have X" — why a similar-artist recommendation surfaced.
 *
 * Returns RAW text; React escapes it. See the file header for why this differs
 * from the vanilla, which escaped inline for innerHTML.
 */
export function recommendationReason(artist: RecommendedArtistLike | null | undefined): string {
  const names = (artist && artist.because) || [];
  if (names.length === 1) return `Because you have ${names[0]}`;
  if (names.length === 2) return `Because you have ${names[0]} & ${names[1]}`;
  if (names.length >= 3) {
    const shown = names.slice(0, 2).join(', ');
    return `Because you have ${shown} +${names.length - 2} more`;
  }
  const n = (artist && artist.occurrence_count) || 0;
  return n > 1 ? `Similar to ${n} artists in your library` : 'Similar to an artist in your library';
}

/** The full provenance list, for the tooltip. Never escaped in the vanilla either. */
export function recommendationReasonTitle(
  artist: RecommendedArtistLike | null | undefined,
): string {
  const names = (artist && artist.because) || [];
  return names.length ? `In your library: ${names.join(', ')}` : '';
}

/** Glyph for a recommendation's "why" category. */
export function whyIcon(type: string | null | undefined): string {
  return type === 'genre'
    ? '🎯'
    : type === 'obscure'
      ? '💎'
      : type === 'consensus'
        ? '👥'
        : type === 'explore'
          ? '🧭'
          : '✨';
}

/**
 * "Because you listen to X" — the play-weighted sibling of recommendationReason
 * (#913 listening recommendations). Same shape, different copy, and a different
 * zero-case: play data implies artists you play, not artists you merely own.
 *
 * Returns RAW text; React escapes it.
 */
export function listeningRecommendationReason(
  artist: RecommendedArtistLike | null | undefined,
): string {
  const names = (artist && artist.because) || [];
  if (names.length === 1) return `Because you listen to ${names[0]}`;
  if (names.length === 2) return `Because you listen to ${names[0]} & ${names[1]}`;
  if (names.length >= 3) {
    const shown = names.slice(0, 2).join(', ');
    return `Because you listen to ${shown} +${names.length - 2} more`;
  }
  return 'From artists you play often';
}

/** Tooltip counterpart for the listening recommendations. */
export function listeningRecommendationReasonTitle(
  artist: RecommendedArtistLike | null | undefined,
): string {
  const names = (artist && artist.because) || [];
  return names.length ? `You listen to: ${names.join(', ')}` : '';
}
