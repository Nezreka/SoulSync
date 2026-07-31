/**
 * What a result card does when you click it, ported from search.js:788-1075.
 *
 * Three flows: open an album, open a single track, stream a track. Each one ends
 * in a vanilla global that cannot move (the download modal, the media player),
 * so the shape handed over matters more than the call — every field below is
 * read by the modal, the wishlist, or the import pipeline downstream.
 *
 * The builders are separated out and pure so those shapes are testable without
 * standing up a modal.
 */

import type { SearchAlbum, SearchTrack } from './-search.types';

/** Album detail as /api/spotify/album/<id> returns it. */
export interface AlbumDetail {
  id?: string;
  name?: string;
  album_type?: string;
  release_date?: string;
  total_tracks?: number;
  images?: { url?: string }[];
  artists?: { id?: string; name?: string; image_url?: string; images?: { url?: string }[] }[];
  tracks?: Record<string, unknown>[];
}

/** The modal's synthetic playlist id. Album and track namespaces are separate. */
export function albumVirtualId(album: SearchAlbum): string {
  return `enhanced_search_album_${album.id}`;
}

export function trackVirtualId(track: SearchTrack): string {
  return `enhanced_search_track_${track.id}`;
}

/**
 * Query params for the album-detail fetch.
 *
 * `source` is omitted for spotify — it is the server's default, and sending it
 * would change nothing. The other two are escape hatches for sources whose ids
 * are not enough on their own:
 *   - Hydrabase routes by plugin origin, so the plugin name has to travel.
 *   - Bandcamp has no id lookup at all, so the release URL is the only way to
 *     fetch the exact release rather than re-searching by name.
 */
export function albumDetailParams(album: SearchAlbum, activeSource: string): URLSearchParams {
  const params = new URLSearchParams({ name: album.name ?? '', artist: album.artist ?? '' });
  if (activeSource && activeSource !== 'spotify') params.set('source', activeSource);
  const hydrabasePlugin = album.external_urls?.hydrabase_plugin;
  if (hydrabasePlugin) params.set('plugin', String(hydrabasePlugin));
  if (activeSource === 'bandcamp' && album.external_urls?.bandcamp) {
    params.set('bandcamp_url', String(album.external_urls.bandcamp));
  }
  return params;
}

/** The album object the modal renders and the wishlist stores. */
export function buildAlbumObject(detail: AlbumDetail, album: SearchAlbum): Record<string, unknown> {
  return {
    name: detail.name,
    id: detail.id,
    album_type: detail.album_type || 'album',
    images: detail.images || [],
    release_date: detail.release_date,
    total_tracks: detail.total_tracks,
    artists: detail.artists || [{ name: album.artist }],
  };
}

/**
 * The artist object, whose id the modal uses to link back to a detail page.
 *
 * The fallback is the vanilla's: some sources mint album ids of the form
 * `<artistId>_<something>`, so the leading segment is the artist. It yields
 * nonsense for sources that don't, which is why it is last.
 */
export function buildArtistObject(
  detail: AlbumDetail,
  album: SearchAlbum,
  activeSource: string,
): Record<string, unknown> {
  const first = detail.artists?.[0] ?? {};
  return {
    id: first.id || String(album.id ?? '').split('_')[0] || '',
    name: first.name || album.artist,
    image_url: first.image_url || first.images?.[0]?.url || '',
    source: activeSource || '',
  };
}

/**
 * Every track carries the whole album object.
 *
 * The modal reads it per track when it builds download jobs, and the wishlist
 * needs it to store an entry that can be retried later. `source` is threaded
 * through because the import pipeline runs source-specific logic on the file —
 * the Deezer contributors upgrade for multi-artist tags depends on it.
 */
export function enrichAlbumTracks(
  detail: AlbumDetail,
  album: SearchAlbum,
): Record<string, unknown>[] {
  const albumForTrack = {
    name: detail.name,
    id: detail.id,
    album_type: detail.album_type || 'album',
    images: detail.images || [],
    release_date: detail.release_date,
    total_tracks: detail.total_tracks,
  };
  return (detail.tracks ?? []).map((track) => ({
    ...track,
    source: (track as { source?: string }).source || album.source || null,
    album: albumForTrack,
  }));
}

/**
 * One search track, in the shape the modal expects.
 *
 * `artists` prefers the real list over the joined "A, B" display string: that
 * string is what made collab downloads land tagged with a single combined
 * artist, because resolve_track_artists saw one value.
 */
export function enrichSingleTrack(track: SearchTrack): Record<string, unknown> {
  const albumObject = {
    name: track.album,
    id: null,
    album_type: 'single',
    images: track.image_url ? [{ url: track.image_url }] : [],
    release_date: track.release_date || null,
    total_tracks: 1,
  };
  return {
    id: track.id,
    name: track.name,
    source: track.source || null,
    artists: track.artists?.length ? track.artists : [track.artist],
    album: albumObject,
    duration_ms: track.duration_ms,
    popularity: track.popularity || 0,
    preview_url: track.preview_url || null,
    external_urls: track.external_urls || null,
    image_url: track.image_url,
  };
}

/** The album object for a single track — a one-track "single". */
export function buildSingleTrackAlbum(track: SearchTrack): Record<string, unknown> {
  return {
    name: track.album,
    id: null,
    album_type: 'single',
    images: track.image_url ? [{ url: track.image_url }] : [],
    release_date: track.release_date || null,
    total_tracks: 1,
    artists: [{ name: track.artist }],
  };
}

/**
 * Sources whose "filenames" are opaque ids rather than paths.
 *
 * A format check on one of these reads the extension off an encoded string and
 * rejects a track the browser can play perfectly well.
 */
const STREAMING_USERNAMES = new Set(['youtube', 'tidal', 'qobuz', 'hifi']);

export interface StreamResult {
  username?: string;
  filename?: string;
}

/**
 * Can this stream result actually play here?
 *
 * Returns the offending format when it cannot, so the caller can name it. A
 * missing filename is allowed through: there is nothing to judge, and the
 * player is the better place to fail.
 */
export function unsupportedStreamFormat(result: StreamResult): string | null {
  if (result.username && STREAMING_USERNAMES.has(result.username)) return null;
  if (!result.filename) return null;
  if (window.isAudioFormatSupported?.(result.filename) !== false) return null;
  return (window.getFileExtension?.(result.filename) ?? '').toUpperCase();
}

/** Open an album's download modal. */
export async function openSearchAlbum(album: SearchAlbum, activeSource: string): Promise<void> {
  // Checked BEFORE the fetch, so a re-click still works when the source is down.
  if (window.reopenActiveDownloadModal?.(albumVirtualId(album))) return;

  window.showLoadingOverlay?.('Loading album...');
  try {
    const params = albumDetailParams(album, activeSource);
    const response = await fetch(`/api/spotify/album/${album.id}?${params}`);
    if (!response.ok) {
      throw new Error(
        response.status === 401
          ? 'Spotify not authenticated. Please check your API settings.'
          : `Failed to load album: ${response.status}`,
      );
    }
    const detail = (await response.json()) as AlbumDetail;

    if (!detail?.tracks?.length) {
      // Named rather than generic: a delisted or region-locked release is the
      // usual cause, and "no tracks" alone reads like a bug in SoulSync.
      window.showToast?.(
        `No tracks available for "${album.name}". This release may have been delisted or is not available in your region.`,
        'warning',
      );
      return;
    }

    const virtualId = albumVirtualId(album);
    await window.openDownloadMissingModalForArtistAlbum?.(
      virtualId,
      `[${album.artist}] ${detail.name}`,
      enrichAlbumTracks(detail, album),
      buildAlbumObject(detail, album),
      buildArtistObject(detail, album, activeSource),
      false,
    );

    window.registerSearchDownload?.(
      {
        id: album.id,
        name: detail.name,
        artist: album.artist,
        image_url: detail.images?.[0]?.url ?? null,
        images: detail.images || [],
      },
      'album',
      virtualId,
      album.artist,
    );
  } catch (error) {
    window.showToast?.(`Error opening album: ${(error as Error).message}`, 'error');
  } finally {
    window.hideLoadingOverlay?.();
  }
}

/** Open a single track's download modal. */
export async function openSearchTrack(track: SearchTrack): Promise<void> {
  const virtualId = trackVirtualId(track);
  if (window.reopenActiveDownloadModal?.(virtualId)) return;

  window.showLoadingOverlay?.('Loading track...');
  try {
    await window.openDownloadMissingModalForArtistAlbum?.(
      virtualId,
      `${track.artist} - ${track.name}`,
      [enrichSingleTrack(track)],
      buildSingleTrackAlbum(track),
      { id: null, name: track.artist },
      false,
    );

    window.registerSearchDownload?.(
      {
        id: track.id,
        name: track.name,
        artist: track.artist,
        image_url: track.image_url,
        images: track.image_url ? [{ url: track.image_url }] : [],
      },
      'track',
      virtualId,
      track.artist,
    );
  } catch (error) {
    window.showToast?.(`Error opening track: ${(error as Error).message}`, 'error');
  } finally {
    window.hideLoadingOverlay?.();
  }
}

/** Find this track on Soulseek and play it, without downloading it first. */
export async function streamSearchTrack(track: SearchTrack): Promise<void> {
  window.showLoadingOverlay?.(`Searching for ${track.name}...`);
  try {
    const response = await fetch('/api/enhanced-search/stream-track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_name: track.name,
        artist_name: track.artist,
        album_name: track.album,
        duration_ms: track.duration_ms,
      }),
    });

    if (!response.ok) {
      const failure = (await response.json().catch(() => ({}))) as { error?: string };
      throw new Error(failure.error || 'Failed to search for track');
    }

    const data = (await response.json()) as { success?: boolean; result?: StreamResult };
    if (!data.success || !data.result) throw new Error('No suitable track found');

    const format = unsupportedStreamFormat(data.result);
    if (format) {
      window.showToast?.(
        `Sorry, ${format} format is not supported in your browser. Try downloading instead.`,
        'error',
      );
      return;
    }

    // The overlay comes down BEFORE the player starts: startStream opens the
    // media player, and doing that behind a loading overlay hides it.
    window.hideLoadingOverlay?.();
    await window.SoulSyncWebShellBridge?.startStream(data.result as Record<string, unknown>);
  } catch (error) {
    window.showToast?.(`Failed to stream track: ${(error as Error).message}`, 'error');
  } finally {
    window.hideLoadingOverlay?.();
  }
}

/** Play an owned track from disk, with what the library check told us about it. */
export function playOwnedTrack(row: {
  track_id?: string | number;
  title?: string;
  file_path?: string;
  album_thumb_url?: string;
  album_title?: string;
  artist_name?: string;
}): void {
  if (!row.file_path) return;
  window.SoulSyncWebShellBridge?.playLibraryTrack(
    {
      id: row.track_id ?? '',
      title: row.title ?? '',
      file_path: row.file_path,
      _stats_image: row.album_thumb_url || null,
    },
    row.album_title || '',
    row.artist_name || '',
  );
}
