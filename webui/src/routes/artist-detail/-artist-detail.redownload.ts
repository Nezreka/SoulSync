import type { InspectorCandidate } from '../../features/downloads/inspector';
import type { EnhancedAlbum } from './-artist-detail.enhanced';

import { streamInspection } from '../../features/downloads/inspector';
import { getAlbumCanonicalSource } from './-artist-detail.enhanced-album';

/**
 * Redownload (library.js: showTrackRedownloadModal 3348, _streamRedownloadSources
 * 3575, _pollRedownloadProgress 3813, redownloadLibraryAlbum 3890). Requests,
 * the NDJSON source stream, the progress poller, and the pure label helpers;
 * the 3-step modal UI lives in -ui/redownload-modal.tsx.
 *
 * The vanilla kept the chosen metadata in an IMPLICIT GLOBAL `selectedMeta`
 * (3497 — never declared, leaked onto window). The port holds it in component
 * state; that bug does not come along.
 */

export const REDOWNLOAD_FORMATS = ['FLAC', 'MP3', 'OPUS', 'OGG', 'M4A', 'WAV'];

/** The header format badge: known audio extensions only (3355-3356). */
export function trackFormatBadge(filePath: unknown): string {
  const ext =
    String(filePath || '')
      .split('.')
      .pop()
      ?.toUpperCase() ?? '';
  return REDOWNLOAD_FORMATS.includes(ext) ? ext : '';
}

export const METADATA_SOURCE_ICONS: Record<string, string> = {
  spotify: '🟢',
  itunes: '🍎',
  deezer: '🟣',
  hydrabase: '🔷',
};

export const METADATA_SOURCE_LABELS: Record<string, string> = {
  spotify: 'Spotify',
  itunes: 'Apple Music',
  deezer: 'Deezer',
  discogs: 'Discogs',
  hydrabase: 'Hydrabase',
};

export interface RedownloadMetadataResult {
  name?: string;
  artist?: string;
  album?: string;
  image_url?: string;
  duration_ms?: number;
  match_score?: number;
  is_current_match?: boolean;
  _source?: string;
}

export interface RedownloadMetadataResponse {
  metadata_results: Record<string, RedownloadMetadataResult[]>;
  best_match?: { source?: string } | null;
  current_track?: { thumb_url?: string } | null;
}

export async function searchRedownloadMetadata(
  trackId: unknown,
): Promise<RedownloadMetadataResponse> {
  const response = await fetch(`/api/library/track/${trackId}/redownload/search-metadata`, {
    method: 'POST',
  });
  const data = await response.json();
  if (!data.success) throw new Error(data.error);
  return data;
}

/** The redownload modal's source stream (see streamInspection). */
export function streamRedownloadSources(
  trackId: unknown,
  metadata: RedownloadMetadataResult,
  onSource: Parameters<typeof streamInspection>[2],
): Promise<InspectorCandidate[]> {
  return streamInspection(
    `/api/library/track/${trackId}/redownload/search-sources`,
    {
      metadata,
    },
    onSource,
  );
}

export async function startRedownloadRequest(
  trackId: unknown,
  metadata: RedownloadMetadataResult,
  candidate: InspectorCandidate,
  deleteOldFile: boolean,
): Promise<string> {
  // A rejected row the user chose anyway: say which rule they overrode, for
  // this one grab. The server only acts on quality overrides.
  const override =
    candidate.decision && !candidate.decision.accepted
      ? { code: candidate.decision.code, stage: candidate.decision.stage }
      : undefined;
  const response = await fetch(`/api/library/track/${trackId}/redownload/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ metadata, candidate, delete_old_file: deleteOldFile, override }),
  });
  const data = await response.json();
  if (!data.success) throw new Error(data.error);
  return String(data.task_id ?? '');
}

let progressTimer: ReturnType<typeof setInterval> | null = null;
let progressSafetyTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Poll real transfer progress every 1.5s (3813): slskd transfer % while one is
 * active, "Processing..." at 80% otherwise, done when the redownload batch
 * leaves /api/active-processes. A 5-minute safety stops polling with a
 * check-the-dashboard message.
 */
export function pollRedownloadProgress(callbacks: {
  onTick: (progress: { pct: number; text: string }) => void;
  onComplete: () => void;
  onTimeout: () => void;
}): void {
  stopRedownloadProgress();
  let completed = false;

  progressTimer = setInterval(() => {
    if (completed) return;
    void (async () => {
      try {
        const dlResponse = await fetch('/api/downloads/status');
        const dlData = await dlResponse.json();
        const transfers = (dlData.transfers || []) as Record<string, unknown>[];
        const active = transfers.find((t) => {
          const state = String(t.state || '').toLowerCase();
          return (
            state.includes('inprogress') ||
            state.includes('queued') ||
            state.includes('initializing')
          );
        });

        if (active) {
          const pct = Number(active.percentComplete) || 0;
          const transferred = Number(active.bytesTransferred) || 0;
          const total = Number(active.size) || 0;
          const text =
            total > 0
              ? `Downloading... ${Math.round(pct)}% (${(transferred / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB)`
              : `Downloading... ${Math.round(pct)}%`;
          callbacks.onTick({ pct: Math.min(95, pct), text });
        } else {
          callbacks.onTick({ pct: 80, text: 'Processing...' });
        }

        const procResponse = await fetch('/api/active-processes');
        const procData = await procResponse.json();
        const processes = (procData.active_processes || []) as { batch_id?: string }[];
        const ourBatch = processes.find(
          (p) => p.batch_id && p.batch_id.includes('redownload_batch_'),
        );
        if (!ourBatch) {
          completed = true;
          stopRedownloadProgress();
          callbacks.onTick({ pct: 100, text: 'Complete! File replaced successfully.' });
          window.showToast?.('Track redownloaded successfully', 'success');
          callbacks.onComplete();
        }
      } catch {
        /* ignore poll errors */
      }
    })();
  }, 1500);

  progressSafetyTimer = setTimeout(() => {
    if (!completed) {
      stopRedownloadProgress();
      callbacks.onTimeout();
    }
  }, 300_000);
}

export function stopRedownloadProgress(): void {
  if (progressTimer) clearInterval(progressTimer);
  if (progressSafetyTimer) clearTimeout(progressSafetyTimer);
  progressTimer = null;
  progressSafetyTimer = null;
}

// ---- Album redownload (#911) ----

/**
 * redownloadLibraryAlbum (3890): the album's CANONICAL source — the same one
 * the Enhanced view tags + displays it as — wins. Redownload must pull THAT
 * exact edition, not a fresh search that can resolve to a different one
 * (issue: matched the 66-track 'Original Soundtrack Collection', a search got
 * the 19-track 'Volume 1'). Ends in the shared Download Missing modal, which
 * stays in shared-helpers.js.
 */
export async function redownloadAlbumFlow(album: EnhancedAlbum, artistName: string): Promise<void> {
  const albumName = String(album.title || '');
  const spotifyAlbumId = String(album.spotify_album_id || '');
  const itunesAlbumId = String(album.itunes_album_id || '');
  const canonical = getAlbumCanonicalSource(album);

  if (!canonical && !spotifyAlbumId && !itunesAlbumId && !albumName) {
    window.showToast?.('No album ID or name available for redownload', 'warning');
    return;
  }

  // The Spotify/iTunes endpoints both return a Spotify-shaped payload, so
  // downstream handling is identical.
  const fetchAlbumBySource = (source: string, id: string, name?: string, artist?: string) => {
    const params = new URLSearchParams({
      name: name || albumName,
      artist: artist || artistName || '',
    });
    const base = source === 'itunes' ? '/api/itunes/album/' : '/api/spotify/album/';
    return fetch(`${base}${encodeURIComponent(id)}?${params}`);
  };

  let albumData: Record<string, unknown> | null = null;

  // 1) Primary: the canonical tagged source, via the SAME /api/album/<id>/tracks
  //    endpoint the Enhanced view uses for its canonical tracklist.
  if (canonical) {
    const params = new URLSearchParams({
      name: albumName,
      artist: artistName || '',
      source: canonical.source,
    });
    const response = await fetch(`/api/album/${encodeURIComponent(canonical.id)}/tracks?${params}`);
    if (response.ok) {
      const data = await response.json();
      if (data && data.success && Array.isArray(data.tracks) && data.tracks.length) {
        albumData = { ...data.album, tracks: data.tracks };
      }
    }
  }

  // 2) Fallback: the stored spotify/iTunes id, then a last-resort search.
  if (!albumData) {
    let response: Response | undefined;
    if (spotifyAlbumId) {
      response = await fetchAlbumBySource('spotify', spotifyAlbumId);
    } else if (itunesAlbumId) {
      response = await fetchAlbumBySource('itunes', itunesAlbumId);
    }

    if (!response || !response.ok) {
      const query = `${artistName || ''} ${albumName}`.trim();
      const searchResponse = await fetch('/api/enhanced-search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      });
      if (!searchResponse.ok) throw new Error('Album search failed');
      const searchData = await searchResponse.json();
      const spotHit = searchData.spotify_albums?.[0];
      const found = spotHit || searchData.itunes_albums?.[0];
      if (!found || !found.id) {
        window.showToast?.(
          `Could not find "${albumName}" by ${artistName || 'unknown'}`,
          'warning',
        );
        return;
      }
      // Fetch from the MATCHING source endpoint — the old fallback always hit
      // Spotify, which is wrong for an iTunes search hit.
      response = await fetchAlbumBySource(
        spotHit ? 'spotify' : 'itunes',
        found.id,
        found.name,
        found.artist,
      );
    }

    if (!response.ok) throw new Error(`Failed to load album: ${response.status}`);
    albumData = await response.json();
  }

  const tracks = (albumData?.tracks || []) as Record<string, unknown>[];
  if (!albumData || tracks.length === 0) {
    window.showToast?.(`No tracks found for "${albumName}"`, 'warning');
    return;
  }

  const resolvedId = albumData.id || spotifyAlbumId || album.id;
  const virtualPlaylistId = `library_redownload_${resolvedId}`;
  const playlistName = `[${artistName || 'Unknown'}] ${albumData.name}`;

  const enrichedTracks = tracks.map((track) => ({
    ...track,
    album: {
      name: albumData.name,
      id: albumData.id,
      album_type: albumData.album_type || 'album',
      images: albumData.images || [],
      release_date: albumData.release_date,
      total_tracks: albumData.total_tracks,
    },
  }));

  const pageState = window.artistDetailPageState as
    | { currentArtistId?: unknown; enhancedData?: { artist?: { thumb_url?: string } } }
    | undefined;
  const artistObject = {
    id: pageState?.currentArtistId || `library_${artistName || album.id}`,
    name: artistName || '',
    image_url: pageState?.enhancedData?.artist?.thumb_url || '',
  };
  const images = albumData.images as { url?: string }[] | undefined;
  const fullAlbumObject = {
    name: albumData.name,
    id: albumData.id,
    album_type: albumData.album_type || 'album',
    images: albumData.images || [],
    image_url: images?.[0]?.url || null,
    release_date: albumData.release_date,
    total_tracks: albumData.total_tracks,
    artists: albumData.artists || [{ name: artistName || '' }],
  };

  await window.openDownloadMissingModalForArtistAlbum?.(
    virtualPlaylistId,
    playlistName,
    enrichedTracks,
    fullAlbumObject,
    artistObject,
    true,
  );

  window.registerArtistDownload?.(
    artistObject,
    fullAlbumObject,
    virtualPlaylistId,
    String(fullAlbumObject.album_type || 'album'),
  );
}
