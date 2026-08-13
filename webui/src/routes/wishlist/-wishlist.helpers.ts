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
