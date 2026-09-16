import { useCallback, useEffect, useId, useState } from 'react';

import { useAccessibleModal } from '@/components/dialog';
import { CompactPlaylist } from '@/routes/discover/-ui/mix-modal';
import {
  fetchDeezerLinkPlaylist,
  fetchSpotifyPlaylistTracks,
  postMirrorPlaylist,
} from '@/routes/sync/-sync.api';
import { buildMirrorPayload } from '@/routes/sync/-sync.import';

import type { SearchPlaylist } from '../-search.types';

import { streamSearchTrack } from '../-search.actions';

export interface PlaylistPreviewModalProps {
  playlist: SearchPlaylist;
  onClose: () => void;
  onPlayTrack?: (track: unknown) => void;
}

export function PlaylistPreviewModal({
  playlist,
  onClose,
  onPlayTrack,
}: PlaylistPreviewModalProps) {
  const [tracks, setTracks] = useState<unknown[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mirroring, setMirroring] = useState(false);
  const [mirrored, setMirrored] = useState(false);
  const [playingIndex, setPlayingIndex] = useState<number | null>(null);

  const titleId = useId();
  const { ref, onBackdropClick } = useAccessibleModal<HTMLDivElement>(onClose);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setTracks(null);
    setMirrored(false);

    const playlistId = String(playlist.id ?? '');
    if (!playlistId) {
      setLoading(false);
      setError('Playlist ID missing');
      return;
    }

    const source = playlist.source || 'deezer';
    const load =
      source === 'deezer'
        ? fetchDeezerLinkPlaylist
        : source === 'spotify'
          ? fetchSpotifyPlaylistTracks
          : null;
    if (!load) {
      setLoading(false);
      setError(`Playlist preview is not supported for ${source}`);
      return;
    }

    load(playlistId)
      .then((data) => {
        if (cancelled) return;
        if (data.error)
          throw new Error(
            typeof data.error === 'string' ? data.error : 'Could not load playlist tracks',
          );
        const list = (data.tracks as unknown[]) || [];
        setTracks(list);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Could not load playlist tracks');
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [playlist.id, playlist.source]);

  const handleMirror = useCallback(async () => {
    if (mirroring || mirrored || !tracks || tracks.length === 0) return;
    setMirroring(true);
    try {
      const payload = buildMirrorPayload(
        playlist.source || 'deezer',
        playlist.id ?? '',
        playlist.name ?? 'Untitled Playlist',
        tracks as never,
        {
          owner: playlist.creator,
          image_url: playlist.image_url,
          description: playlist.link,
        },
      );
      const res = await postMirrorPlaylist(payload);
      if (res.success === false) {
        window.showToast?.(res.error || 'Failed to mirror playlist', 'error');
      } else {
        setMirrored(true);
        window.showToast?.(`Added "${playlist.name}" to Playlists!`, 'success');
      }
    } catch (err) {
      window.showToast?.(err instanceof Error ? err.message : 'Failed to mirror playlist', 'error');
    } finally {
      setMirroring(false);
    }
  }, [mirroring, mirrored, tracks, playlist]);

  const handlePlay = useCallback(
    (index: number) => {
      if (!tracks || !tracks[index]) return;
      setPlayingIndex(index);
      if (onPlayTrack) {
        onPlayTrack(tracks[index]);
      } else {
        const t = tracks[index] as Record<string, unknown>;
        const textName = (value: unknown): string => {
          if (typeof value === 'string') return value;
          if (!value || typeof value !== 'object') return '';
          const record = value as Record<string, unknown>;
          return typeof record.name === 'string'
            ? record.name
            : typeof record.title === 'string'
              ? record.title
              : '';
        };
        const trackTitle = textName(t.title || t.name);
        const credits = Array.isArray(t.artists) ? t.artists : [t.artist || t.artist_name];
        const artistName = credits.map(textName).filter(Boolean).join(', ');
        if (trackTitle && artistName) {
          void streamSearchTrack({
            name: trackTitle,
            artist: artistName,
            album: textName(t.album),
            duration_ms: Number(t.duration_ms || 0),
          });
        } else {
          window.showToast?.('Track title or artist is missing', 'error');
        }
      }
      setTimeout(() => setPlayingIndex(null), 1500);
    },
    [tracks, onPlayTrack],
  );

  const trackCountLabel = tracks
    ? `${tracks.length} tracks`
    : playlist.track_count
      ? `${playlist.track_count} tracks`
      : '';

  return (
    <div className="modal-overlay" id="mix-modal-overlay" onClick={onBackdropClick}>
      <div
        className="mix-modal"
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="mix-modal-header">
          <div>
            <div className="mix-modal-subtitle">
              Playlist · {playlist.source ? playlist.source.toUpperCase() : 'DEEZER'}
            </div>
            <h2 className="mix-modal-title" id={titleId}>
              {playlist.name}
            </h2>
            <div className="mix-modal-meta">
              {playlist.creator ? `by ${playlist.creator} · ` : ''}
              {trackCountLabel}
            </div>
          </div>
          <div className="mix-modal-actions">
            <button
              type="button"
              className="btn btn--sm btn--primary"
              disabled={mirroring || mirrored || loading || !tracks || tracks.length === 0}
              onClick={() => void handleMirror()}
              title="Add as a mirrored playlist in Playlists"
            >
              {mirroring ? 'Mirroring…' : mirrored ? 'Added ✓' : '+ Add to Playlists'}
            </button>
            <button type="button" className="mix-modal-close" aria-label="Close" onClick={onClose}>
              ✕
            </button>
          </div>
        </div>

        <div className="mix-modal-body" id="mix-modal-tracks">
          {loading ? (
            <div className="discover-empty">
              <p>Loading tracks…</p>
            </div>
          ) : error ? (
            <div className="discover-empty">
              <p>{error}</p>
            </div>
          ) : (
            tracks && (
              <CompactPlaylist
                tracks={tracks}
                selectable={false}
                onPlay={handlePlay}
                playingIndex={playingIndex}
              />
            )
          )}
        </div>
      </div>
    </div>
  );
}
