import { parseStamp } from '@/routes/active-downloads/-dl.format';

import {
  WL_FAILING_ATTEMPTS,
  type ParsedWishlistTrack,
  type WishlistArtistGroup,
  type WishlistTrackRow,
} from './-wishlist.types';

/**
 * Unpack one wishlist row.
 *
 * `spotify_data` arrives either as an object or as a JSON string depending on
 * the endpoint, and `album` is either a bare string or a full object. A row
 * that cannot be parsed is dropped rather than rendered as "Unknown/Unknown" —
 * that is what the vanilla `_parse` did by returning null.
 */
export function parseWishlistTrack(
  row: WishlistTrackRow,
  type: 'album' | 'single',
): ParsedWishlistTrack | null {
  let data: unknown = row.spotify_data;
  if (typeof data === 'string') {
    try {
      data = JSON.parse(data);
    } catch {
      return null;
    }
  }
  if (!data || typeof data !== 'object') return null;

  const sd = data as {
    name?: string;
    album?: unknown;
    artists?: unknown;
  };

  const rawAlbum = sd.album;
  const albumName =
    (typeof rawAlbum === 'string'
      ? rawAlbum
      : (rawAlbum as { name?: string } | null | undefined)?.name) || 'Unknown';
  const albumImage =
    (typeof rawAlbum === 'object' && rawAlbum !== null
      ? (rawAlbum as { images?: { url?: string }[] }).images?.[0]?.url
      : '') || '';

  let artist = 'Unknown Artist';
  const artists = sd.artists;
  if (Array.isArray(artists) && artists.length > 0) {
    const first = artists[0];
    if (
      first &&
      typeof first === 'object' &&
      typeof (first as { name?: string }).name === 'string'
    ) {
      artist = (first as { name: string }).name;
    } else if (typeof first === 'string') {
      artist = first;
    }
  }

  const retry = Number(row.retry_count) || 0;
  return {
    track: sd.name || 'Unknown',
    artist,
    album: albumName,
    image: albumImage,
    type,
    id: String(row.spotify_track_id || row.id || ''),
    retry,
    failing: retry >= WL_FAILING_ATTEMPTS,
    lastTried: row.last_attempted || '',
    failReason: row.failure_reason || '',
  };
}

/**
 * Artist name -> image URL.
 *
 * Library photos (shipped alongside the tracks, covering every wishlist artist)
 * seed the map; curated watchlist photos override where present. Order matters
 * — the watchlist photo is the better picture when it exists.
 */
export function buildArtistImageMap(
  trackResponses: { artist_images?: Record<string, string> }[],
  watchlistArtists: { artist_name?: string | null; image_url?: string | null }[],
): Map<string, string> {
  const map = new Map<string, string>();
  for (const res of trackResponses) {
    for (const [name, url] of Object.entries(res.artist_images ?? {})) {
      if (name && url) map.set(name.toLowerCase(), url);
    }
  }
  for (const artist of watchlistArtists) {
    if (artist.artist_name && artist.image_url) {
      map.set(artist.artist_name.toLowerCase(), artist.image_url);
    }
  }
  return map;
}

/**
 * Group parsed tracks into artist orbs, busiest first.
 *
 * Album tracks nest under their album; singles hang off the artist directly.
 * Insertion order is preserved inside each artist so albums appear in the order
 * the API returned them.
 */
export function groupWishlistArtists(
  albumTracks: ParsedWishlistTrack[],
  singleTracks: ParsedWishlistTrack[],
): WishlistArtistGroup[] {
  const byArtist = new Map<
    string,
    { albums: Map<string, WishlistAlbumAcc>; singles: ParsedWishlistTrack[] }
  >();

  const ensure = (artist: string) => {
    let entry = byArtist.get(artist);
    if (!entry) {
      entry = { albums: new Map(), singles: [] };
      byArtist.set(artist, entry);
    }
    return entry;
  };

  for (const track of albumTracks) {
    const entry = ensure(track.artist);
    let album = entry.albums.get(track.album);
    if (!album) {
      album = { image: track.image, tracks: [] };
      entry.albums.set(track.album, album);
    }
    album.tracks.push(track);
  }
  for (const track of singleTracks) {
    ensure(track.artist).singles.push(track);
  }

  const groups: WishlistArtistGroup[] = [...byArtist.entries()].map(([name, entry]) => {
    const albums = [...entry.albums.entries()].map(([albumName, acc]) => ({
      name: albumName,
      image: acc.image,
      tracks: acc.tracks,
    }));
    const total =
      albums.reduce((sum, album) => sum + album.tracks.length, 0) + entry.singles.length;
    const failingCount =
      albums.reduce((sum, album) => sum + album.tracks.filter((t) => t.failing).length, 0) +
      entry.singles.filter((t) => t.failing).length;
    return { name, albums, singles: entry.singles, total, failingCount };
  });

  // Busiest artist first. Stable for equal counts because Array#sort is stable,
  // so insertion order (API order) breaks ties.
  return groups.sort((a, b) => b.total - a.total);
}

interface WishlistAlbumAcc {
  image: string;
  tracks: ParsedWishlistTrack[];
}

/** Deterministic hue per artist name, so an orb keeps its colour across loads. */
export function artistHue(name: string): number {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return Math.abs(hash) % 360;
}

/** Orb size band. Thresholds are the vanilla ones: 10+ large, 4+ medium. */
export function orbSizeClass(total: number): 'orb-lg' | 'orb-md' | 'orb-sm' {
  if (total >= 10) return 'orb-lg';
  if (total >= 4) return 'orb-md';
  return 'orb-sm';
}

/**
 * The orb's picture: curated artist photo first, then any album cover, then a
 * single's cover. Empty means render the initials instead.
 */
export function orbImage(group: WishlistArtistGroup, artistImages: Map<string, string>): string {
  const curated = artistImages.get(group.name.toLowerCase());
  if (curated) return curated;
  for (const album of group.albums) {
    if (album.image) return album.image;
  }
  return group.singles.find((single) => single.image)?.image || '';
}

/**
 * Album covers orbiting the orb — up to 6, albums before singles.
 *
 * Fewer than 3 renders nothing: a ring of one or two looks like a mistake
 * rather than a ring, which is why the vanilla code gated on it.
 */
export function orbRingCovers(group: WishlistArtistGroup): string[] {
  const covers: string[] = [];
  for (const album of group.albums) {
    if (album.image && covers.length < 6) covers.push(album.image);
  }
  for (const single of group.singles) {
    if (single.image && covers.length < 6) covers.push(single.image);
  }
  return covers.length >= 3 ? covers : [];
}

/** Staggered entry animation, capped so a big wishlist still finishes promptly. */
export function orbAnimationDelay(index: number): number {
  return Math.min(index * 60, 800);
}

/** Tooltip text for a failing badge. */
export function failingTitle(track: ParsedWishlistTrack): string {
  let text = `${track.retry} failed attempt${track.retry !== 1 ? 's' : ''}`;
  if (track.lastTried) text += ` · last tried ${track.lastTried}`;
  if (track.failReason) text += `\n${track.failReason}`;
  return text;
}

/** "12 tracks" / "1 track". */
export function trackCountLabel(count: number): string {
  return `${count} track${count !== 1 ? 's' : ''}`;
}

/**
 * Apply the search box and the Failing-only chip.
 *
 * Matches the artist name OR any of its album names, which is what the vanilla
 * filter intended. It queried `.wl-satellite` for album names, but f59c56438
 * renamed that markup to `.wl-album-tile` and never updated the selector, so
 * the album branch silently matched nothing from that commit onward. Restored
 * here — searching "geogaddi" finds the artist holding it again.
 */
export function filterWishlistGroups(
  groups: WishlistArtistGroup[],
  query: string,
  failingOnly: boolean,
): WishlistArtistGroup[] {
  const needle = query.toLowerCase().trim();
  return groups.filter((group) => {
    if (failingOnly && group.failingCount === 0) return false;
    if (!needle) return true;
    if (group.name.toLowerCase().includes(needle)) return true;
    return group.albums.some((album) => album.name.toLowerCase().includes(needle));
  });
}

/**
 * what's keeping a track off the shelf. only failing tracks get a trouble
 * kind, everything else is still hunting. failure_reason also carries the
 * reason a track was ADDED (watchlist scan, quality upgrade), so anything
 * that isn't a known post-download rejection or a no-match lands in
 * 'failed' rather than being read as the cause.
 */
export type WishlistTrouble = 'rejected' | 'notfound' | 'failed' | 'hunting';

/** worst first. the order zones render in and the order an artist is filed by. */
export const TROUBLE_ORDER: WishlistTrouble[] = ['rejected', 'notfound', 'failed', 'hunting'];

const REJECTED =
  /^(file integrity check failed|acoustid verification failed|post-processing failed)|quarantin/i;

export function troubleKind(track: ParsedWishlistTrack): WishlistTrouble {
  if (!track.failing) return 'hunting';
  const reason = track.failReason.trim();
  if (REJECTED.test(reason)) return 'rejected';
  if (/^no matching track found/i.test(reason)) return 'notfound';
  return 'failed';
}

export function groupTracks(group: WishlistArtistGroup): ParsedWishlistTrack[] {
  return [...group.albums.flatMap((album) => album.tracks), ...group.singles];
}

/** the artist is filed under its worst track. */
export function groupTrouble(group: WishlistArtistGroup): WishlistTrouble {
  let worst = TROUBLE_ORDER.length - 1;
  for (const track of groupTracks(group)) {
    worst = Math.min(worst, TROUBLE_ORDER.indexOf(troubleKind(track)));
  }
  return TROUBLE_ORDER[worst];
}

export interface WishlistZone {
  kind: WishlistTrouble;
  title: string;
  /** "3 artists · the file came in bad" */
  sub: string;
  groups: WishlistArtistGroup[];
  /** every failing track id in the zone, for its retry button. */
  stuckIds: string[];
}

const ZONE_COPY: Record<WishlistTrouble, { title: string; sub: string }> = {
  rejected: { title: 'Downloaded, then rejected', sub: 'the file came in bad' },
  notfound: { title: 'No match found', sub: 'nothing on your sources yet' },
  failed: { title: 'Download keeps failing', sub: 'still retrying on the backoff' },
  hunting: { title: 'Still hunting', sub: 'in line for the next pass' },
};

/**
 * split artists into zones by what's wrong. keeps the incoming order inside
 * each zone, so the page's sort still decides who comes first. empty zones
 * are dropped.
 */
export function wishlistZones(groups: WishlistArtistGroup[]): WishlistZone[] {
  const buckets = new Map<WishlistTrouble, WishlistArtistGroup[]>();
  for (const group of groups) {
    const kind = groupTrouble(group);
    const list = buckets.get(kind) ?? [];
    list.push(group);
    buckets.set(kind, list);
  }
  return TROUBLE_ORDER.filter((kind) => buckets.has(kind)).map((kind) => {
    const zoneGroups = buckets.get(kind)!;
    const count = zoneGroups.length;
    return {
      kind,
      title: ZONE_COPY[kind].title,
      sub: `${count} artist${count === 1 ? '' : 's'} · ${ZONE_COPY[kind].sub}`,
      groups: zoneGroups,
      stuckIds: zoneGroups.flatMap((g) =>
        groupTracks(g)
          .filter((t) => t.failing)
          .map((t) => t.id),
      ),
    };
  });
}

/** sqlite stamps are bare utc. "today", "yesterday", else "Oct 17". */
export function shortStamp(value: string, now: number = Date.now()): string {
  const ms = parseStamp(value);
  if (ms === null) return '';
  const day = (t: number) => new Date(t).toDateString();
  if (day(ms) === day(now)) return 'today';
  if (day(ms) === day(now - 86_400_000)) return 'yesterday';
  const sameYear = new Date(ms).getFullYear() === new Date(now).getFullYear();
  return new Date(ms).toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  });
}

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
}

function clip(text: string, max = 160): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

/** what went wrong, in words a person would use. */
export function describeTrouble(track: ParsedWishlistTrack): {
  kind: WishlistTrouble;
  headline: string;
  detail: string;
} {
  const kind = troubleKind(track);
  const reason = track.failReason.trim();
  const after = reason.replace(/^[^:]*:\s*/, '');

  if (kind === 'hunting') {
    const last = shortStamp(track.lastTried);
    return {
      kind,
      headline: 'Waiting',
      detail:
        track.retry > 0 ? `Tried ${track.retry}×${last ? `, last ${last}` : ''}` : 'Not tried yet',
    };
  }
  if (kind === 'notfound') {
    return { kind, headline: 'No match found', detail: 'None of your sources had a match yet.' };
  }
  if (kind === 'failed') {
    return {
      kind,
      headline: 'Download keeps failing',
      detail: reason ? clip(`Last note: ${reason}`) : 'No reason was recorded.',
    };
  }

  if (/^file integrity/i.test(reason)) {
    const partial = /only ~?(\d+)s actually decodes of a (\d+)s/i.exec(reason);
    return {
      kind,
      headline: 'Downloaded, then thrown out',
      detail: partial
        ? `Only ~${partial[1]}s of ${clock(Number(partial[2]))} actually played. A preview clip or a broken download, so the integrity check threw it out.`
        : clip(`The file failed the integrity check: ${after}`),
    };
  }
  if (/^acoustid/i.test(reason)) {
    const heard = /mismatch:\s*'(.+?)' by '(.+?)'/i.exec(reason);
    return {
      kind,
      headline: 'The wrong song came back',
      detail: heard
        ? clip(`The audio fingerprint says it was "${heard[1]}" by ${heard[2]}, not this track.`)
        : "The audio fingerprint didn't match this track.",
    };
  }
  if (/^post-processing/i.test(reason)) {
    return {
      kind,
      headline: 'Downloaded, then failed to save',
      detail: /winerror 3|cannot find the path/i.test(reason)
        ? 'The file came in, but its folder could not be created on disk.'
        : clip(`The file came in, but saving it failed: ${after}`),
    };
  }
  return { kind, headline: 'Downloaded, then quarantined', detail: clip(reason) };
}

/** the quiet line under an orb. */
export function orbMeta(group: WishlistArtistGroup): string {
  const stuck = groupTracks(group).filter((t) => t.failing);
  if (stuck.length === 0) return trackCountLabel(group.total);
  const latest = stuck.reduce(
    (best, t) => ((parseStamp(t.lastTried) ?? 0) > (parseStamp(best.lastTried) ?? 0) ? t : best),
    stuck[0],
  );
  const when = shortStamp(latest.lastTried);
  const head =
    group.total === 1 ? `${stuck[0].retry} tries` : `${stuck.length} of ${group.total} stuck`;
  return when ? `${head} · ${when}` : head;
}
