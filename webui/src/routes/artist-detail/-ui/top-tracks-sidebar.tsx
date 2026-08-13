import { useEffect, useState } from 'react';

import { getShellBridge } from '@/platform/shell/bridge';

import type { ArtistDetailTrack } from '../-artist-detail.types';

import {
  EMPTY_TOP_TRACKS,
  formatPlaycount,
  loadTopTracks,
  playTrackByMetadata,
  type TopTracksState,
  topTracksBulkContext,
  trackArtistLabel,
  wishlistTrackBody,
} from '../-artist-detail.top-tracks';

interface Props {
  artistId: unknown;
  artistName: string;
}

/**
 * The hero top-tracks sidebar (_loadArtistTopTracks).
 *
 * Hidden until something loads, exactly as the vanilla was: it starts
 * display:none and only reveals itself once a pass produced rows, so an artist
 * with no popularity data shows no empty panel.
 */
export function TopTracksSidebar({ artistId, artistName }: Props) {
  const [state, setState] = useState<TopTracksState>(EMPTY_TOP_TRACKS);

  useEffect(() => {
    if (!artistName) return;
    const controller = new AbortController();
    setState(EMPTY_TOP_TRACKS);

    void loadTopTracks(artistId, artistName, controller.signal).then((next) => {
      if (!controller.signal.aborted) setState(next);
    });

    // Aborted on artist change so a slow response for the previous artist
    // cannot land in the new artist's sidebar.
    return () => controller.abort();
  }, [artistId, artistName]);

  if (state.tracks.length === 0) return null;

  const play = (track: ArtistDetailTrack) => {
    // Read at click time, not render time: the vanilla shell attaches the
    // bridge asynchronously, and a sidebar that rendered before it landed would
    // otherwise hold null forever.
    void playTrackByMetadata(
      getShellBridge(),
      track.name ?? '',
      state.downloadable ? trackArtistLabel(track, artistName) : artistName,
      '',
    );
  };

  const wishlist = async (track: ArtistDetailTrack) => {
    try {
      const response = await fetch('/api/add-album-to-wishlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(wishlistTrackBody(track, artistName, artistId)),
      });
      const data = await response.json();
      if (data?.success) {
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

  const downloadAll = () => {
    const context = topTracksBulkContext(state, artistName, artistId);
    // Seven arguments on purpose: contextType 'playlist' renders the playlist
    // hero rather than an album hero, and the shell bridge's convenience
    // wrapper cannot express it.
    window.openDownloadMissingModalForArtistAlbum?.(
      context.virtualPlaylistId,
      context.playlistName,
      state.tracks,
      context.wrapperAlbum,
      context.artist,
      true,
      'playlist',
    );
  };

  return (
    <div className="artist-hero-right" id="artist-hero-sidebar">
      <div className="hero-sidebar-title" id="hero-sidebar-title">
        {state.title}
      </div>
      <div className="hero-top-tracks" id="hero-top-tracks">
        {state.tracks.map((track, index) => (
          <div
            className="hero-top-track"
            key={`${index}-${track.name ?? ''}`}
            data-index={state.downloadable ? index : undefined}
          >
            <span className="hero-top-track-num">{index + 1}</span>
            <button
              type="button"
              className="hero-top-track-play"
              title="Play"
              onClick={(e) => {
                e.stopPropagation();
                play(track);
              }}
            >
              ▶
            </button>
            <span className="hero-top-track-name" title={track.name}>
              {track.name}
            </span>
            {state.downloadable ? (
              <button
                type="button"
                className="hero-top-track-download"
                data-index={index}
                title="Add to wishlist"
                onClick={(e) => {
                  e.stopPropagation();
                  void wishlist(track);
                }}
              >
                ⬇
              </button>
            ) : (
              // Last.fm rows are display-only — a playcount, never a download.
              <span className="hero-top-track-plays">{formatPlaycount(track.playcount)}</span>
            )}
          </div>
        ))}
      </div>
      {state.downloadable ? (
        <button
          type="button"
          className="hero-top-tracks-download-all"
          id="hero-top-tracks-download-all"
          onClick={(e) => {
            e.stopPropagation();
            downloadAll();
          }}
        >
          Download All
        </button>
      ) : null}
    </div>
  );
}
