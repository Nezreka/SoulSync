/**
 * Playlist Explorer — the four endpoints (pages-extra.js:1-1141).
 *
 * The wishlist submit deliberately does NOT get its own NDJSON reader: it
 * posts to /api/artist/<id>/download-discography, the same endpoint and the
 * same line shape the discography modal already streams, so it borrows that
 * module's reader rather than growing a second copy that can drift.
 */

import {
  streamDiscographyDownload,
  type DiscogAlbumUpdate,
} from '@/routes/artist-detail/-artist-detail.discography-modal';

import type {
  ExplorerAlbum,
  ExplorerArtist,
  ExplorerMeta,
  ExplorerMode,
  ExplorerTrack,
  MirroredPlaylist,
} from './-explorer.types';

/**
 * :48-54 — the endpoint has returned both a bare array and a `{playlists: []}`
 * envelope over its life; the vanilla accepted either and so does this.
 */
export async function fetchMirroredPlaylists(): Promise<MirroredPlaylist[]> {
  const response = await fetch('/api/mirrored-playlists');
  const data = await response.json();
  if (Array.isArray(data)) return data;
  return data?.playlists || [];
}

export interface BuildTreeHandlers {
  onMeta: (meta: ExplorerMeta) => void;
  /** `index` is 1-based, matching the vanilla's artistCount (:305). */
  onArtist: (artist: ExplorerArtist, index: number) => void;
}

/**
 * :277-330 — POST, then read NDJSON: one `meta` line, then one `artist` line
 * per artist, then `complete`. A malformed line is warned about and skipped,
 * never fatal: a single bad artist must not abandon the rest of the tree.
 */
export async function streamBuildTree(
  playlistId: number,
  mode: ExplorerMode,
  handlers: BuildTreeHandlers,
): Promise<void> {
  const response = await fetch('/api/playlist-explorer/build-tree', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ playlist_id: playlistId, mode }),
  });

  if (!response.ok) {
    // :283-286 — the error body is JSON even on a failure status.
    const error = await response.json().catch(() => ({}));
    throw new Error(error?.error || 'Failed to build tree');
  }
  if (!response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let artistCount = 0;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const data = JSON.parse(line);
        if (data.type === 'meta') {
          handlers.onMeta(data as ExplorerMeta);
        } else if (data.type === 'artist') {
          artistCount += 1;
          handlers.onArtist(data as ExplorerArtist, artistCount);
        }
        // `complete` carries nothing the client needs (:322).
      } catch (error) {
        console.warn('Explorer: failed to parse NDJSON line', error);
      }
    }
  }
}

/**
 * :522-527 — null, not [], when the fetch fails or the payload is
 * unsuccessful. The vanilla left the tracklist container untouched in that
 * case, so the next double-click retried; an empty array would instead read as
 * "expanded, no tracks" and the retry would be lost.
 */
export async function fetchAlbumTracks(spotifyAlbumId: string): Promise<ExplorerTrack[] | null> {
  try {
    const response = await fetch(`/api/playlist-explorer/album-tracks/${spotifyAlbumId}`);
    const data = await response.json();
    if (!data.success || !data.tracks) return null;
    return data.tracks as ExplorerTrack[];
  } catch (error) {
    console.error('Failed to load album tracks:', error);
    return null;
  }
}

/**
 * :812-828 — one artist's albums, sorted by track count DESC so deluxe and
 * expanded editions are resolved first and the standard editions dedupe
 * against them. `source` is null per album: the explorer's tree carries no
 * per-album source, so the backend resolves each id through its own lookup.
 */
export function buildExplorerWishlistPayload(
  albums: ExplorerAlbum[],
  artistName: string,
): {
  albums: { id: unknown; name: string; artist_name: string; source: string | null }[];
  artist_name: string;
} {
  const sorted = [...albums].sort((a, b) => (b.track_count || 0) - (a.track_count || 0));
  return {
    albums: sorted.map((album) => ({
      id: album.spotify_id,
      name: album.title || '',
      artist_name: artistName,
      source: null,
    })),
    artist_name: artistName,
  };
}

/** :846-852 — the per-album line in the progress list. */
export function explorerAlbumStatusText(update: DiscogAlbumUpdate): string {
  const added = update.tracks_added || 0;
  const skipped = (update.tracks_skipped as number) || 0;
  return `Added ${added} track${added !== 1 ? 's' : ''}${skipped > 0 ? `, ${skipped} skipped` : ''}`;
}

export interface ExplorerWishlistArtist {
  artistId: unknown;
  name: string;
  albums: ExplorerAlbum[];
}

/**
 * :806-869 — one artist at a time, sequentially: the backend resolves a whole
 * artist's batch together, and the vanilla awaited each stream before starting
 * the next. A failed artist is logged and the loop continues.
 *
 * Returns the running total of tracks actually added, which is what both the
 * footer line and the success toast report.
 */
export async function submitExplorerWishlist(
  artists: ExplorerWishlistArtist[],
  onAlbum: (albumId: unknown, update: DiscogAlbumUpdate) => void,
): Promise<number> {
  let totalAdded = 0;

  for (const artist of artists) {
    const payload = buildExplorerWishlistPayload(artist.albums, artist.name);
    try {
      await streamDiscographyDownload(
        artist.artistId,
        payload,
        (update) => {
          if (update.status === 'done') totalAdded += update.tracks_added || 0;
          onAlbum(update.album_id, update);
        },
        () => {
          // The explorer ignored the summary line and totalled the per-album
          // updates itself (:833) — the per-artist summaries don't accumulate.
        },
      );
    } catch (error) {
      console.error(`Explorer wishlist: failed for ${artist.name}:`, error);
    }
  }

  return totalAdded;
}
