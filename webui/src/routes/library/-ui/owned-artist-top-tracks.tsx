import { AddToPlaylistButton } from '@/features/playlists/add-to-playlist';
import { getShellBridge } from '@/platform/shell/bridge';
import { profileAsksFirst } from '@/platform/shell/download-rights';

import type { ArtistDetailTrack } from '../../artist-detail/-artist-detail.types';

import {
  playTrackByMetadata,
  topTracksBulkContext,
  trackArtistLabel,
  wishlistTrackBody,
} from '../../artist-detail/-artist-detail.top-tracks';

// dev's top-tracks row actions for the owned hero: play, add to playlist and
// a real wishlist add (or a request). nick's bookmark (monitor) stays next to
// them, and the list keeps both bulk actions: nick's Monitor All (mark them
// wanted) and dev's Download All (fetch them now, through the missing-tracks
// modal).

// nick's top-track rows and dev's share the provider track shape
type TopTrack = { name?: string | null; artists?: Array<{ name?: string }> | null };

export function OwnedTopTrackActions({
  track,
  artistName,
  artistId,
  downloadable,
}: {
  track: TopTrack;
  artistName: string;
  artistId: unknown;
  downloadable: boolean;
}) {
  const asksFirst = profileAsksFirst();
  const wishlist = async () => {
    if (window.checkMusicRequestQuota && !(await window.checkMusicRequestQuota())) return;
    try {
      const response = await fetch('/api/add-album-to-wishlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(wishlistTrackBody(track as ArtistDetailTrack, artistName, artistId)),
      });
      const data = await response.json();
      if (data?.success) {
        if (!window.announceWishlistRequest?.())
          window.showToast?.(`Added "${track.name}" to wishlist`, 'success');
      } else {
        window.showToast?.(
          `Failed to wishlist "${track.name}": ${data?.error || 'unknown'}`,
          'error',
        );
      }
    } catch {
      window.showToast?.('Failed to add track to wishlist', 'error');
    }
  };
  return (
    <>
      <button
        type="button"
        className="hero-top-track-play"
        title="Play"
        aria-label={`Play ${track.name ?? ''}`}
        onClick={(e) => {
          e.stopPropagation();
          void playTrackByMetadata(
            getShellBridge(),
            track.name ?? '',
            downloadable ? trackArtistLabel(track as ArtistDetailTrack, artistName) : artistName,
            '',
          );
        }}
      >
        ▶
      </button>
      <AddToPlaylistButton
        track={{
          track_name: track.name ?? '',
          artist_name: track.artists?.[0]?.name || artistName,
        }}
        className="hero-top-track-add"
        size={12}
      />
      {downloadable ? (
        <button
          type="button"
          className="hero-top-track-download"
          title={asksFirst ? 'Request' : 'Add to wishlist'}
          aria-label={`${asksFirst ? 'Request' : 'Add to wishlist'} ${track.name ?? ''}`}
          onClick={(e) => {
            e.stopPropagation();
            void wishlist();
          }}
        >
          ⬇
        </button>
      ) : null}
    </>
  );
}

export function OwnedTopTracksDownloadAll({
  tracks,
  source,
  artistName,
  artistId,
}: {
  tracks: TopTrack[];
  source: string;
  artistName: string;
  artistId: unknown;
}) {
  if (!tracks.length) return null;
  const asksFirst = profileAsksFirst();
  return (
    <button
      type="button"
      className="hero-top-tracks-download-all"
      onClick={(e) => {
        e.stopPropagation();
        const context = topTracksBulkContext(
          { source, title: '', tracks: tracks as ArtistDetailTrack[], downloadable: true },
          artistName,
          artistId,
        );
        void window.openDownloadMissingModalForArtistAlbum?.(
          context.virtualPlaylistId,
          context.playlistName,
          tracks,
          context.wrapperAlbum,
          context.artist,
          true,
          'playlist',
        );
      }}
    >
      {asksFirst ? 'Request All' : 'Download All'}
    </button>
  );
}
