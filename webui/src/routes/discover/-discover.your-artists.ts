/**
 * Your Artists — card metadata, subtitle, and the stale/poll behaviour.
 *
 * Transcribed from `loadYourArtists` (5217), `_pollYourArtists` (5275),
 * `_renderYourArtistCard` (5295) and `_pickArtistDetailSource` (5192), read end
 * to end first.
 *
 * ── This section's stale handling is NOT your-albums' ───────────────────────
 *
 * your-albums declares `isStale`/`renderStale`/`onStale`, so the controller
 * puts it in the dedicated `stale` phase. your-artists does NOT: it branches
 * inside `renderItems` and starts its poller from `onRendered`, so the
 * controller considers it RENDERED the whole time. Same visible outcome —
 * spinner plus a message, then a poller — reached by a different route.
 *
 * The pollers also differ and it is not cosmetic: your-albums gives up after
 * 12 attempts (1 minute), your-artists after 60 (5 minutes), because matching
 * artists across connected services takes far longer than reading an album
 * cache.
 */

/** Poll cadence — `setInterval(..., 5000)` in both sections. */
export const YOUR_ARTISTS_POLL_MS = 5000;
/** `if (attempts > 60)` — five minutes, five times your-albums' budget. */
export const YOUR_ARTISTS_POLL_MAX_ATTEMPTS = 60;

/** Copy, verbatim. */
export const YOUR_ARTISTS_STALE_SUBTITLE = 'Discovering your artists across connected services...';
export const YOUR_ARTISTS_STALE_BODY = 'Fetching and matching artists from your services...';
export const YOUR_ARTISTS_UPDATING_SUFFIX = ' (updating...)';
export const YOUR_ARTISTS_NO_SOURCES = 'your music services';

/** Display names for the services an artist can come from (5248). */
export const SOURCE_NAMES: Record<string, string> = {
  spotify: 'Spotify',
  lastfm: 'Last.fm',
  tidal: 'Tidal',
  deezer: 'Deezer',
};

/** Origin-dot colours (5311). Anything unrecognised falls back to grey. */
export const SOURCE_COLORS: Record<string, string> = {
  spotify: '#1DB954',
  lastfm: '#D51007',
  tidal: '#00FFFF',
  deezer: '#A238FF',
};
export const SOURCE_COLOR_FALLBACK = '#666';

export function sourceColor(service: string): string {
  return SOURCE_COLORS[service] || SOURCE_COLOR_FALLBACK;
}

export interface YourArtist {
  id?: number | string;
  artist_name?: string;
  image_url?: string;
  on_watchlist?: boolean;
  source_services?: string[];
  active_source?: string;
  active_source_id?: string;
  source?: string;
  spotify_artist_id?: string;
  itunes_artist_id?: string;
  deezer_artist_id?: string;
  discogs_artist_id?: string;
  amazon_artist_id?: string;
  musicbrainz_artist_id?: string;
  soul_id?: string;
  [key: string]: unknown;
}

/** `items.length === 0 && !data.stale` (5229) — empty ONLY when not still discovering. */
export function yourArtistsIsEmpty(items: unknown[], data: { stale?: boolean } | null): boolean {
  return items.length === 0 && !data?.stale;
}

/** The in-renderer stale branch: nothing yet, but upstream is still working (5235). */
export function yourArtistsIsStaleEmpty(
  items: unknown[],
  data: { stale?: boolean } | null,
): boolean {
  return items.length === 0 && Boolean(data?.stale);
}

/**
 * Subtitle (5245-5253).
 *
 * Built from the DISTINCT services across all loaded artists, joined with
 * " and " — not commas. With no services at all it says "your music services"
 * rather than leaving a dangling "on ". A trailing "(updating...)" is appended
 * while the upstream is still discovering.
 */
export function yourArtistsSubtitle(
  artists: YourArtist[],
  data: { stale?: boolean } | null,
): string {
  const sources = new Set<string>();
  for (const a of artists) for (const s of a.source_services || []) sources.add(s);
  const list = [...sources].map((s) => SOURCE_NAMES[s] || s).join(' and ');
  let text = `Artists you follow on ${list || YOUR_ARTISTS_NO_SOURCES}`;
  if (data?.stale) text += YOUR_ARTISTS_UPDATING_SUFFIX;
  return text;
}

export interface SourceBadge {
  key: string;
  fallback: string;
  title: string;
}

/**
 * Metadata-source badges (5300-5304), in the vanilla's fixed order.
 *
 * Order is Spotify, Apple Music, Deezer, Discogs regardless of which source is
 * active — the badges say "we have an id here", not "this is the active one".
 */
export function artistSourceBadges(artist: YourArtist | null | undefined): SourceBadge[] {
  if (!artist) return [];
  const badges: SourceBadge[] = [];
  if (artist.spotify_artist_id) badges.push({ key: 'spotify', fallback: 'SP', title: 'Spotify' });
  if (artist.itunes_artist_id) badges.push({ key: 'itunes', fallback: 'IT', title: 'Apple Music' });
  if (artist.deezer_artist_id) badges.push({ key: 'deezer', fallback: 'Dz', title: 'Deezer' });
  if (artist.discogs_artist_id) badges.push({ key: 'discogs', fallback: 'DC', title: 'Discogs' });
  return badges;
}

export interface DetailSource {
  id: string;
  source: string;
}

/**
 * Which id opens the artist-detail page, and under which source.
 *
 * Argument-pure, so this one IS differentially tested against the real vanilla.
 * Order matters (5192-5215):
 *
 *   1. the ACTIVE source's own field, if populated
 *   2. otherwise the first populated field in declaration order —
 *      spotify, deezer, itunes, discogs, amazon, musicbrainz, hydrabase
 *   3. otherwise `active_source_id`, but ONLY when the active source is one of
 *      the known fields (that guard is easy to drop and would hand back an id
 *      with a source the detail page cannot resolve)
 *   4. otherwise nothing, and the card is not clickable
 */
export const DETAIL_SOURCE_FIELDS: Record<string, keyof YourArtist> = {
  spotify: 'spotify_artist_id',
  deezer: 'deezer_artist_id',
  itunes: 'itunes_artist_id',
  discogs: 'discogs_artist_id',
  amazon: 'amazon_artist_id',
  musicbrainz: 'musicbrainz_artist_id',
  hydrabase: 'soul_id',
};

export function pickArtistDetailSource(artist: YourArtist | null | undefined): DetailSource {
  if (!artist) return { id: '', source: '' };
  const active = String(artist.active_source || artist.source || '').toLowerCase();
  const activeField = DETAIL_SOURCE_FIELDS[active];
  if (activeField && artist[activeField]) {
    return { id: String(artist[activeField]), source: active };
  }
  for (const [source, field] of Object.entries(DETAIL_SOURCE_FIELDS)) {
    if (artist[field]) return { id: String(artist[field]), source };
  }
  if (artist.active_source_id && activeField) {
    return { id: String(artist.active_source_id), source: active };
  }
  return { id: '', source: '' };
}

/** A card is clickable only with a resolvable id (5318). */
export function artistCardIsClickable(artist: YourArtist | null | undefined): boolean {
  return pickArtistDetailSource(artist).id !== '';
}

/** Watchlist button state (5316, 5338). */
export function watchlistButtonTitle(onWatchlist: boolean | undefined): string {
  return onWatchlist ? 'On watchlist' : 'Add to watchlist';
}
