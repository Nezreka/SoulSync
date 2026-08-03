import type { EnhancedAlbum, EnhancedTrack } from './-artist-detail.enhanced';

/**
 * Missing-track management (library.js: wishlistEnhancedMissingTrack 4842,
 * openMissingTrackManageModal 4884, openHaveMissingTrackModal 4954). The pure
 * payload builders and requests; the two modals live in
 * -ui/missing-track-modals.tsx.
 */

export interface MissingTrackArtist {
  id: unknown;
  name: string;
  imageUrl: string;
}

/**
 * The wishlist payload (4848-4871): the track id prefers the metadata-source
 * ids over the synthetic row id, and the album's total_tracks prefers the
 * canonical count so a partially-owned album doesn't wishlist as complete.
 */
export function buildWishlistPayload(
  track: EnhancedTrack,
  album: EnhancedAlbum,
  artist: MissingTrackArtist,
): {
  artist: { id: unknown; name: string; image_url: string };
  albumData: Record<string, unknown>;
  wishlistTrack: Record<string, unknown>;
} {
  const record = track as Record<string, unknown>;
  const albumData = {
    id: album.id,
    name: album.title || 'Unknown Album',
    title: album.title || 'Unknown Album',
    image_url: album.thumb_url || '',
    release_date: album.year ? `${album.year}-01-01` : '',
    album_type: album.record_type || 'album',
    total_tracks: Number(
      (album as Record<string, unknown>).api_track_count ||
        (album as Record<string, unknown>).track_count ||
        album.tracks?.length ||
        1,
    ),
  };
  const wishlistTrack = {
    id:
      record.spotify_track_id ||
      record.deezer_id ||
      record.itunes_track_id ||
      record.musicbrainz_recording_id ||
      track.id,
    name: track.title || `Track ${track.track_number || ''}`,
    title: track.title || `Track ${track.track_number || ''}`,
    artists: [{ name: artist.name }],
    duration_ms: track.duration || 0,
    track_number: track.track_number || 1,
    disc_number: (record.disc_number as number) || 1,
    album: albumData,
  };
  return {
    artist: { id: artist.id, name: artist.name, image_url: artist.imageUrl },
    albumData,
    wishlistTrack,
  };
}

/** "Add to Library": the normal wishlist-add flow with this exact context (4842). */
export async function wishlistEnhancedMissingTrack(
  track: EnhancedTrack,
  album: EnhancedAlbum,
  artist: MissingTrackArtist,
  downloadNow = false,
): Promise<void> {
  if (!(track as Record<string, unknown>)._hasActionableContext) {
    window.showToast?.(
      'This missing track needs metadata context before it can be wishlisted or downloaded.',
      'error',
    );
    return;
  }
  if (typeof window.openAddToWishlistModal !== 'function') {
    window.showToast?.('Wishlist modal is not available on this page', 'error');
    return;
  }
  const payload = buildWishlistPayload(track, album, artist);
  await window.openAddToWishlistModal(
    payload.albumData,
    payload.artist,
    [payload.wishlistTrack],
    String(payload.albumData.album_type),
    { [String(payload.wishlistTrack.name)]: false },
  );
  if (downloadNow && typeof window.handleWishlistDownloadNow === 'function') {
    setTimeout(() => window.handleWishlistDownloadNow?.(), 150);
  }
}

// ---- "I Have This" import ----

export interface LibrarySearchTrack {
  id?: unknown;
  title?: string;
  artist_name?: string;
  album_title?: string;
  file_path?: string;
  duration?: number;
  bitrate?: number;
}

export async function searchLibraryTracksRequest(query: string): Promise<LibrarySearchTrack[]> {
  const response = await fetch(
    `/api/library/search-tracks?q=${encodeURIComponent(query)}&limit=12`,
  );
  const data = await response.json();
  if (!data.success) throw new Error(data.error || 'Search failed');
  return data.tracks || [];
}

/**
 * The expected-track context handed to the importer (5167-5184): the row's own
 * fields win, its canonical _sourceTrack fills the gaps.
 */
export function buildExpectedTrack(
  track: EnhancedTrack,
  artistName: string,
): Record<string, unknown> {
  const record = track as Record<string, unknown>;
  const source = (record._sourceTrack || record) as Record<string, unknown>;
  return {
    title: track.title || source.title || source.name || '',
    name: track.title || source.title || source.name || '',
    track_number: track.track_number || source.track_number,
    disc_number: record.disc_number || source.disc_number || 1,
    duration: track.duration || source.duration || source.duration_ms || 0,
    duration_ms: track.duration || source.duration_ms || source.duration || 0,
    source: record.source || source.source || '',
    track_id: record.track_id || source.track_id || source.id || '',
    id: record.track_id || source.track_id || source.id || '',
    album_id: record.album_id || source.album_id || '',
    spotify_track_id: record.spotify_track_id || source.spotify_track_id || '',
    deezer_id: record.deezer_id || source.deezer_id || '',
    itunes_track_id: record.itunes_track_id || source.itunes_track_id || '',
    musicbrainz_recording_id:
      record.musicbrainz_recording_id || source.musicbrainz_recording_id || '',
    artists: record.artists || source.artists || [artistName],
  };
}

/** The album's source id for the importer, in the vanilla's priority (5192). */
export function albumSourceId(album: EnhancedAlbum): string {
  const record = album as Record<string, unknown>;
  return String(
    record.spotify_album_id ||
      record.deezer_id ||
      record.itunes_album_id ||
      record.musicbrainz_release_id ||
      record.discogs_id ||
      record.tidal_id ||
      record.qobuz_id ||
      '',
  );
}

/** Multi-disc context: the highest disc number in the canonical list (5185, 5193). */
export function totalDiscs(album: EnhancedAlbum): number {
  const record = album as Record<string, unknown>;
  const tracks = ((record.canonical_tracks || album.tracks || []) as Record<string, unknown>[]).map(
    (t) => Number(t.disc_number || 1),
  );
  return Math.max(1, ...tracks);
}

export async function importExistingTrackRequest(
  album: EnhancedAlbum,
  track: EnhancedTrack,
  artistName: string,
  sourceTrackId: string,
): Promise<{ updatedData: Record<string, unknown> | null }> {
  const response = await fetch(`/api/library/album/${album.id}/import-existing-track`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source_track_id: sourceTrackId,
      expected_track: buildExpectedTrack(track, artistName),
      album_source_id: albumSourceId(album),
      total_discs: totalDiscs(album),
    }),
  });
  const data = await response.json();
  if (!data.success) throw new Error(data.error || 'Failed to import track');
  return {
    updatedData: data.updated_data && data.updated_data.success ? data.updated_data : null,
  };
}

/** The stage copy the import timer walks through while waiting (5082-5087). */
export const IMPORT_STAGES = [
  { after: 0, text: 'Copying selected file into staging.' },
  { after: 4, text: 'Verifying audio and writing the missing track tags.' },
  {
    after: 10,
    text: 'Post-processing can take a moment for FLAC files, lyrics, ReplayGain, and metadata.',
  },
  {
    after: 20,
    text: 'Still working. Waiting for the backend to finish and return the refreshed library row.',
  },
] as const;

export function importStageText(elapsedSeconds: number): string {
  const stage = [...IMPORT_STAGES].reverse().find((item) => elapsedSeconds >= item.after);
  return stage ? stage.text : IMPORT_STAGES[0].text;
}
