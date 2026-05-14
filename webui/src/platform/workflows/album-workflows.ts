import { apiClient } from '@/app/api-client';

export interface AlbumWorkflowLaunchInput {
  spotifyAlbumId?: string;
  artistName?: string;
  albumName?: string;
  source?: string;
}

export interface DownloadMissingAlbumWorkflowInput {
  virtualPlaylistId: string;
  playlistName: string;
  tracks: Array<Record<string, unknown>>;
  album: Record<string, unknown>;
  artist: Record<string, unknown>;
  albumType: string;
  forceDownload: boolean;
  registerDownload?: boolean;
}

export interface WishlistAlbumWorkflowInput {
  tracks: Array<Record<string, unknown>>;
  album: Record<string, unknown>;
  artist: Record<string, unknown>;
  albumType: string;
}

interface AlbumSearchResult {
  id?: string;
  name?: string;
  title?: string;
  artist?: string;
}

interface AlbumApiResponse {
  id?: string;
  name?: string;
  album_type?: string;
  images?: Array<{ url?: string }>;
  image_url?: string | null;
  release_date?: string;
  total_tracks?: number;
  artists?: Array<{ id?: string | null; name?: string }>;
  tracks?: Array<Record<string, unknown>>;
}

interface EnhancedSearchResponse {
  spotify_albums?: AlbumSearchResult[];
  itunes_albums?: AlbumSearchResult[];
}

function getWorkflowBridge() {
  const bridge = window.SoulSyncWorkflowActions;
  if (!bridge) {
    throw new Error('Album workflow host is not ready yet');
  }
  return bridge;
}

function notify(message: string, type: 'success' | 'error' | 'warning' | 'info' = 'info') {
  if (window.SoulSyncWorkflowActions?.notify) {
    window.SoulSyncWorkflowActions.notify(message, type);
    return;
  }
  window.showToast?.(message, type);
}

async function searchAlbum(input: AlbumWorkflowLaunchInput): Promise<AlbumSearchResult> {
  const query = `${input.artistName || ''} ${input.albumName || ''}`.trim();
  if (!query) {
    throw new Error('No album ID or artist/album info available');
  }

  const searchData =
    (await apiClient
      .post('enhanced-search', {
        json: { query },
      })
      .json<EnhancedSearchResponse>()) ?? {};
  const foundAlbum = searchData.spotify_albums?.[0] ?? searchData.itunes_albums?.[0];
  if (!foundAlbum?.id) {
    throw new Error(
      `Could not find "${input.albumName || 'album'}" by ${input.artistName || 'unknown artist'}`,
    );
  }
  return foundAlbum;
}

async function fetchAlbum(input: AlbumWorkflowLaunchInput): Promise<AlbumApiResponse> {
  let albumId = input.spotifyAlbumId || '';
  let albumName = input.albumName || '';
  let artistName = input.artistName || '';

  if (!albumId) {
    const foundAlbum = await searchAlbum(input);
    albumId = foundAlbum.id || '';
    albumName = foundAlbum.name || foundAlbum.title || albumName;
    artistName = foundAlbum.artist || artistName;
  }

  const searchParams = new URLSearchParams({ name: albumName, artist: artistName });

  try {
    return (
      (await apiClient
        .get(`spotify/album/${encodeURIComponent(albumId)}`, { searchParams })
        .json<AlbumApiResponse>()) ?? {}
    );
  } catch (error) {
    if (!input.spotifyAlbumId || (!input.artistName && !input.albumName)) {
      throw error;
    }

    const foundAlbum = await searchAlbum(input);
    const fallbackParams = new URLSearchParams({
      name: foundAlbum.name || foundAlbum.title || albumName,
      artist: foundAlbum.artist || artistName,
    });
    return (
      (await apiClient
        .get(`spotify/album/${encodeURIComponent(foundAlbum.id || '')}`, {
          searchParams: fallbackParams,
        })
        .json<AlbumApiResponse>()) ?? {}
    );
  }
}

async function resolveAlbumWorkflowData(input: AlbumWorkflowLaunchInput) {
  const albumData = await fetchAlbum(input);
  if (!albumData.tracks?.length) {
    throw new Error(`No tracks available for "${input.albumName || albumData.name || 'album'}"`);
  }

  const albumArtists = albumData.artists?.length
    ? albumData.artists
    : [{ name: input.artistName || 'Unknown Artist' }];
  const artistName = input.artistName || albumArtists[0]?.name || 'Unknown Artist';
  const albumType = albumData.album_type || 'album';
  const album = {
    name: albumData.name || input.albumName || 'Unknown Album',
    id: albumData.id || input.spotifyAlbumId || '',
    album_type: albumType,
    images: albumData.images || [],
    image_url: albumData.image_url || albumData.images?.[0]?.url || null,
    release_date: albumData.release_date,
    total_tracks: albumData.total_tracks,
    artists: albumArtists,
  };
  const tracks = albumData.tracks.map((track) => ({
    ...track,
    artists: albumArtists,
    album,
  }));

  return {
    album,
    albumType,
    artist: { id: `workflow_${artistName}`, name: artistName, image_url: '' },
    artistName,
    tracks,
  };
}

export async function launchAlbumDownloadWorkflow(input: AlbumWorkflowLaunchInput) {
  const bridge = getWorkflowBridge();
  const { album, albumType, artist, artistName, tracks } = await resolveAlbumWorkflowData(input);
  const resolvedAlbumId = String(album.id || input.spotifyAlbumId || Date.now());
  const source = input.source || 'album';

  await bridge.openDownloadMissingAlbum({
    virtualPlaylistId: `${source}_download_${resolvedAlbumId}`,
    playlistName: `[${artistName}] ${String(album.name || 'Unknown Album')}`,
    tracks,
    album,
    artist,
    albumType,
    forceDownload: true,
    registerDownload: true,
  });
}

export async function launchAlbumWishlistWorkflow(input: AlbumWorkflowLaunchInput) {
  const bridge = getWorkflowBridge();
  const { album, albumType, artist, tracks } = await resolveAlbumWorkflowData(input);

  await bridge.openAddToWishlistAlbum({
    album,
    artist,
    tracks,
    albumType,
  });
  notify('Wishlist workflow opened', 'success');
}
