import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { DiscographyRelease } from '../../artist-detail/-artist-detail.types';

import { libraryV2AlbumQueryOptions } from '../-library-v2.api';
import { albumQueueRows, queueableRows } from '../-library-v2.play';
import {
  albumTracksParams,
  lockSectionType,
  reconcileAlbumWithTracksResponse,
  releasePlaylistName,
  releaseToAlbumData,
  releaseVirtualPlaylistId,
  type OpenReleaseArtist,
} from '../../artist-detail/-artist-detail.open-release';
import { checkTracksBody, mergeOwnership } from '../../artist-detail/-artist-detail.owned-tracks';

// dev's release-card behaviour for the owned discovery cards: a provider
// card (the "+ other sources" ones) opens dev's download modal with a track
// picker and plays from the provider, every card gets a play button, and a
// release with a musicbrainz id links there.

export interface ReleaseCardTarget {
  /** a catalogue release has one; a provider-only release does not. */
  albumId?: number;
  providerId?: string;
  /** the source that lists a "+ other sources" card. */
  gapSource?: string;
  title: string;
  albumType: string;
  imageUrl: string;
  releaseDate?: string | null;
  totalTracks: number;
  ownedTracks: number;
  musicbrainzId?: string | null;
}

export interface ReleaseCardArtist {
  /** the provider artist id the release belongs to */
  id: string | number | null;
  name: string;
  imageUrl: string;
  source: string | null;
}

function asRelease(card: ReleaseCardTarget): DiscographyRelease {
  return {
    id: card.providerId,
    title: card.title,
    name: card.title,
    album_type: card.albumType,
    image_url: card.imageUrl,
    release_date: card.releaseDate ?? undefined,
    track_count: card.totalTracks || undefined,
    _gap_source: card.gapSource,
  } as DiscographyRelease;
}

function asArtist(artist: ReleaseCardArtist, card: ReleaseCardTarget): OpenReleaseArtist {
  return {
    id: artist.id ?? undefined,
    name: artist.name,
    image_url: artist.imageUrl,
    source: card.gapSource || artist.source,
  };
}

async function fetchReleaseTracks(release: DiscographyRelease, artist: OpenReleaseArtist) {
  const album = releaseToAlbumData(release);
  const params = new URLSearchParams(albumTracksParams(release, artist));
  const response = await fetch(
    `/api/album/${encodeURIComponent(String(album.id))}/tracks?${params}`,
  );
  if (!response.ok) throw new Error(`Failed to load album tracks: ${response.status}`);
  const data = await response.json();
  if (!data.success || !data.tracks?.length) throw new Error('No tracks found for this release');
  return { album, data };
}

/** dev's openRelease: the download modal with a per-track picker. */
export async function openProviderRelease(card: ReleaseCardTarget, artistInfo: ReleaseCardArtist) {
  if (!card.providerId) return;
  const release = asRelease(card);
  const artist = asArtist(artistInfo, card);
  const virtualId = releaseVirtualPlaylistId(artist, releaseToAlbumData(release));
  if (window.reopenActiveDownloadModal?.(virtualId, { runningOnly: true })) return;
  window.showLoadingOverlay?.('Loading album...');
  try {
    const { album: cardAlbum, data } = await fetchReleaseTracks(release, artist);
    const album = lockSectionType(reconcileAlbumWithTracksResponse(cardAlbum, data), release);
    await window.openDownloadMissingModalForArtistAlbum?.(
      virtualId,
      releasePlaylistName(artist, album),
      data.tracks,
      album,
      artist,
      false,
    );
    window.registerArtistDownload?.(artist, album, virtualId, album.album_type);
  } catch (error) {
    window.showToast?.(`Error opening album: ${(error as Error).message}`, 'error');
  } finally {
    window.hideLoadingOverlay?.();
  }
}

/** dev's playRelease for a provider card: owned copies first, the rest streams. */
export async function playProviderRelease(card: ReleaseCardTarget, artistInfo: ReleaseCardArtist) {
  if (!card.providerId) return;
  const release = asRelease(card);
  const artist = asArtist(artistInfo, card);
  window.showLoadingOverlay?.('Loading album...');
  try {
    const { album, data } = await fetchReleaseTracks(release, artist);
    const tracks = (data.tracks as Record<string, unknown>[]).map((track) => ({
      ...track,
      title: track.title || track.name || 'Unknown Track',
      name: track.name || track.title || 'Unknown Track',
      artist: track.artist || track.artist_name || artist.name,
      artists:
        Array.isArray(track.artists) && track.artists.length
          ? track.artists
          : [{ name: artist.name }],
      album: track.album || track.album_title || album.name,
      image_url: track.image_url || album.image_url || artist.image_url,
    }));
    let playable = tracks;
    try {
      const owned = await fetch('/api/library/check-tracks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(checkTracksBody(artist.name, String(album.name || ''), tracks)),
      });
      if (owned.ok) {
        const ownedData = await owned.json();
        if (ownedData?.success) playable = mergeOwnership(tracks, ownedData.owned_tracks);
      }
    } catch {
      // a failed lookup is not fatal, the player downloads what it lacks
    }
    window.hideLoadingOverlay?.();
    await window.playTrackList?.(playable, String(album.name || 'Album'));
  } catch (error) {
    window.hideLoadingOverlay?.();
    window.showToast?.(`Error playing album: ${(error as Error).message}`, 'error');
  }
}

/** a catalogue card plays its files, the way the album play button does. */
export async function playCatalogueRelease(
  queryClient: QueryClient,
  albumId: number,
  title: string,
  artistName: string,
) {
  try {
    const album = await queryClient.fetchQuery(libraryV2AlbumQueryOptions(albumId));
    const rows = queueableRows(albumQueueRows(album, artistName));
    if (!rows.length) {
      window.showToast?.('Nothing on this release is on disk yet', 'info');
      return;
    }
    await window.playTrackList?.(rows, title || 'Album');
  } catch (error) {
    window.showToast?.(`Could not play ${title}: ${(error as Error).message}`, 'error');
  }
}

export function ReleaseCardExtras({
  card,
  artist,
}: {
  card: ReleaseCardTarget;
  artist: ReleaseCardArtist;
}) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const canPlay = card.albumId ? card.ownedTracks > 0 : Boolean(card.providerId);
  const mbUrl = card.musicbrainzId ? `https://musicbrainz.org/release/${card.musicbrainzId}` : null;
  return (
    <>
      {canPlay ? (
        <button
          type="button"
          className="release-card-play-btn"
          aria-label={`Play ${card.title || 'album'}`}
          title={`Play ${card.title || 'album'}`}
          disabled={pending}
          onClick={(e) => {
            e.stopPropagation();
            setPending(true);
            const run = card.albumId
              ? playCatalogueRelease(queryClient, card.albumId, card.title, artist.name)
              : playProviderRelease(card, artist);
            void run.finally(() => setPending(false));
          }}
        >
          ▶
        </button>
      ) : null}
      {mbUrl ? (
        <div
          className="mb-card-icon"
          title="View on MusicBrainz"
          role="link"
          onClick={(e) => {
            e.stopPropagation();
            window.open(mbUrl, '_blank');
          }}
        >
          <img
            src="/static/img/brands/musicbrainz.png"
            style={{ width: 20, height: 'auto', display: 'block' }}
            alt="MusicBrainz"
          />
        </div>
      ) : null}
    </>
  );
}
