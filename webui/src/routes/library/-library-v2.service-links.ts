/**
 * "Open this on <service>" for the provider ids Library v2 already holds.
 *
 * The catalogue knows an artist's Spotify id, a release's MusicBrainz id and so
 * on; until now the page could only draw a coverage ring with them or copy one
 * to the clipboard. This turns them into the links upstream's artist-detail
 * page has (`getServiceUrl` in -artist-detail.enhanced-album.ts), which never
 * reached Library v2 because it lived on the page this branch deleted.
 *
 * Ported rather than shared: that module is part of the legacy artist-detail
 * family and importing from it would tie this page to code scheduled to go.
 */

/**
 * Discogs tags an album id's type into the id itself ('m12345' master /
 * 'r12345' release, see core/discogs_client.py._tag_discogs_album_id) because
 * release N and master N are different albums sharing one numeric namespace.
 * A bare (untagged, pre-existing) id has nothing to strip and defaults to
 * 'release' — matching _discogs_album_endpoints()'s own legacy fallback.
 */
function untagDiscogsAlbumId(id: string): { id: string; kind: 'master' | 'release' } {
  const match = /^([mr])(\d+)$/.exec(id);
  if (!match) return { id, kind: 'release' };
  return { id: match[2], kind: match[1] === 'm' ? 'master' : 'release' };
}

/** Services that store a full URL rather than an id — the value IS the link. */
const URL_VALUED = new Set(['lastfm', 'genius', 'bandcamp']);

export type ServiceEntity = 'artist' | 'album' | 'track';

/** External link for one provider id, or null when that combination has none. */
export function getServiceUrl(
  service: string,
  entityType: string,
  id: unknown,
): string | null {
  if (!id) return null;
  const key = String(service).trim().toLowerCase();
  const value = String(id).trim();
  if (!value) return null;

  if (URL_VALUED.has(key)) {
    // Genius has no album page; Last.fm and Bandcamp do. A stored value that
    // is not a link would otherwise render as an href the browser resolves
    // against SoulSync's own origin.
    if (key === 'genius' && entityType === 'album') return null;
    return /^https?:\/\//i.test(value) ? value : null;
  }

  if (key === 'discogs' && entityType === 'album') {
    const { id: bareId, kind } = untagDiscogsAlbumId(value);
    return `https://www.discogs.com/${kind}/${bareId}`;
  }

  const urls: Record<string, Partial<Record<string, string>>> = {
    spotify: {
      artist: `https://open.spotify.com/artist/${value}`,
      album: `https://open.spotify.com/album/${value}`,
      track: `https://open.spotify.com/track/${value}`,
    },
    musicbrainz: {
      artist: `https://musicbrainz.org/artist/${value}`,
      album: `https://musicbrainz.org/release/${value}`,
      track: `https://musicbrainz.org/recording/${value}`,
    },
    deezer: {
      artist: `https://www.deezer.com/artist/${value}`,
      album: `https://www.deezer.com/album/${value}`,
      track: `https://www.deezer.com/track/${value}`,
    },
    audiodb: {
      artist: `https://www.theaudiodb.com/artist/${value}`,
      album: `https://www.theaudiodb.com/album/${value}`,
      track: `https://www.theaudiodb.com/track/${value}`,
    },
    itunes: {
      artist: `https://music.apple.com/artist/${value}`,
      album: `https://music.apple.com/album/${value}`,
      track: `https://music.apple.com/song/${value}`,
    },
    tidal: {
      artist: `https://tidal.com/browse/artist/${value}`,
      album: `https://tidal.com/browse/album/${value}`,
      track: `https://tidal.com/browse/track/${value}`,
    },
    qobuz: {
      artist: `https://www.qobuz.com/artist/${value}`,
      album: `https://www.qobuz.com/album/${value}`,
      track: `https://www.qobuz.com/track/${value}`,
    },
    // Discogs has no per-track page (album is handled above — its id needs
    // master/release routing), and Amazon has no artist page.
    discogs: { artist: `https://www.discogs.com/artist/${value}` },
    amazon: {
      album: `https://music.amazon.com/albums/${value}`,
      track: `https://music.amazon.com/tracks/${value}`,
    },
  };
  return urls[key]?.[entityType] ?? null;
}
