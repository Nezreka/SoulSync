import type { ShellBridge } from '@/platform/shell/bridge';

import type { ArtistDetailTrack } from './-artist-detail.types';

/**
 * The hero top-tracks sidebar, ported from `_loadArtistTopTracks`
 * (library.js:1167) and its two download helpers.
 *
 * Two passes, in order:
 *   1. the metadata source (Spotify / Deezer) via /api/artist/<id>/top-tracks,
 *      which returns FULL track objects so each row gets a real download action
 *   2. Last.fm playcount, which is display-only — the vanilla deliberately
 *      offers no download there because the rows carry no usable metadata
 *
 * Sources that cannot rank by popularity (iTunes / Discogs / MusicBrainz)
 * answer success=false, which is what makes pass 2 reachable at all.
 */

/** Sidebar heading per source. An unknown source falls back to "Top Tracks". */
export const TOP_TRACKS_SOURCE_LABELS: Record<string, string> = {
  spotify: 'Top Tracks (Spotify)',
  deezer: 'Top Tracks (Deezer)',
  lastfm: 'Popular on Last.fm',
};

export function topTracksTitle(source: string | undefined): string {
  return (source && TOP_TRACKS_SOURCE_LABELS[source]) || 'Top Tracks';
}

export interface TopTracksState {
  /** '' means the sidebar stays hidden — no source produced anything. */
  source: string;
  title: string;
  tracks: ArtistDetailTrack[];
  /**
   * True only for the metadata-source pass. Last.fm rows show a playcount and
   * no download button, matching the legacy display-only behaviour.
   */
  downloadable: boolean;
}

export const EMPTY_TOP_TRACKS: TopTracksState = {
  source: '',
  title: '',
  tracks: [],
  downloadable: false,
};

/** The row's artist label: the track's own artists, or the page artist. */
export function trackArtistLabel(track: ArtistDetailTrack, artistName: string): string {
  const names = (track.artists ?? [])
    .map((a) => (a && a.name ? a.name : ''))
    .filter(Boolean)
    .join(', ');
  return names || artistName;
}

/** Last.fm playcounts: 1.2M / 3.4K / 999, with a trailing ".0" trimmed. */
export function formatPlaycount(n: unknown): string {
  const value = Number(n);
  if (!value || value <= 0) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}K`;
  return value.toLocaleString();
}

/**
 * Pass 1 then pass 2. Returns EMPTY_TOP_TRACKS when neither produced rows,
 * which keeps the sidebar hidden rather than showing an empty panel.
 *
 * Every failure is swallowed: the sidebar is decoration on a page that must
 * still render, and the vanilla only console.debug'd here.
 */
export async function loadTopTracks(
  artistId: unknown,
  artistName: string,
  signal?: AbortSignal,
): Promise<TopTracksState> {
  if (artistId) {
    try {
      const params = new URLSearchParams({ limit: '10' });
      const response = await fetch(
        `/api/artist/${encodeURIComponent(String(artistId))}/top-tracks?${params}`,
        { signal },
      );
      if (response.ok) {
        const data = await response.json();
        if (data?.success && Array.isArray(data.tracks) && data.tracks.length > 0) {
          return {
            source: data.source ?? '',
            title: topTracksTitle(data.source),
            tracks: data.tracks,
            downloadable: true,
          };
        }
      }
    } catch {
      // Fall through to Last.fm.
    }
  }

  try {
    // The literal `0` is the vanilla's placeholder — this endpoint keys off the
    // ?name= query, not the path id.
    const response = await fetch(
      `/api/artist/0/lastfm-top-tracks?name=${encodeURIComponent(artistName)}`,
      { signal },
    );
    const data = await response.json();
    if (!data?.success || !data.tracks?.length) return EMPTY_TOP_TRACKS;
    return {
      source: 'lastfm',
      title: TOP_TRACKS_SOURCE_LABELS.lastfm,
      tracks: data.tracks,
      downloadable: false,
    };
  } catch {
    return EMPTY_TOP_TRACKS;
  }
}

/** The body /api/add-album-to-wishlist expects for a single top track. */
export function wishlistTrackBody(track: ArtistDetailTrack, artistName: string, artistId: unknown) {
  const trackArtists = track.artists?.length ? track.artists : [{ name: artistName }];
  const album = track.album && typeof track.album === 'object' ? track.album : {};
  return {
    track: { ...track, artists: trackArtists },
    artist: { id: artistId || '', name: artistName },
    album,
    source_type: 'top_tracks',
    source_context: {
      artist_name: artistName,
      album_name: album.name || '',
      album_type: album.album_type || 'album',
    },
  };
}

/**
 * The bulk-download wrapper.
 *
 * The virtual playlist id deliberately does NOT start with `artist_album_` /
 * `enhanced_search_album_`: that prefix is what makes downloads.js set
 * is_album_download=false, so each track downloads under its own real album
 * metadata and lands in the proper per-album folder instead of one lump.
 */
export function topTracksBulkContext(state: TopTracksState, artistName: string, artistId: unknown) {
  const virtualPlaylistId = `top_tracks_${state.source}_${artistId || 'unknown'}`;
  const playlistName = `${artistName} — Top Tracks`;
  return {
    virtualPlaylistId,
    playlistName,
    wrapperAlbum: {
      id: virtualPlaylistId,
      name: playlistName,
      album_type: 'compilation',
      images: [],
      total_tracks: state.tracks.length,
      artists: [{ id: artistId || '', name: artistName }],
    },
    artist: { id: artistId || '', name: artistName, source: state.source },
  };
}

/**
 * Play one row: library first, streaming second (playTrackByMetadata,
 * library.js:1209).
 *
 * The order is the point — an owned copy is faster and better quality than a
 * stream, so /api/stats/resolve-track is always tried first and the streaming
 * fallback only runs on a genuine library miss. A resolve FAILURE also falls
 * through to streaming rather than surfacing an error.
 */
export async function playTrackByMetadata(
  bridge: ShellBridge | null,
  title: string,
  artist: string,
  album = '',
): Promise<void> {
  try {
    const response = await fetch('/api/stats/resolve-track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, artist }),
    });
    const data = await response.json();
    if (data?.success && data.track) {
      const track = data.track;
      bridge?.playLibraryTrack(
        {
          id: track.id,
          title: track.title,
          file_path: track.file_path,
          bitrate: track.bitrate,
          artist_id: track.artist_id,
          album_id: track.album_id,
          _stats_image: track.image_url || null,
        },
        track.album_title || album || '',
        track.artist_name || artist || '',
      );
      return;
    }
  } catch {
    // Library resolve failed — try streaming rather than giving up.
  }

  bridge?.showLoadingOverlay(`Searching for ${title}...`);
  try {
    const response = await fetch('/api/enhanced-search/stream-track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_name: title,
        artist_name: artist,
        album_name: album,
        duration_ms: 0,
      }),
    });
    const data = await response.json();
    bridge?.hideLoadingOverlay();

    if (data?.success && data.result) {
      if (bridge) await bridge.startStream(data.result);
      else window.showToast?.('Streaming not available', 'error');
      return;
    }
    window.showToast?.(data?.error || 'Track not found in library or any source', 'error');
  } catch {
    bridge?.hideLoadingOverlay();
    window.showToast?.('Failed to play track', 'error');
  }
}
