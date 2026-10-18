import { useEffect, useState } from 'react';

import { AudioFormatBadge } from '@/components/audio-format-badge/audio-format-badge';
import { Button } from '@/components/form/form';

import { PlayIcon } from '../../artist-detail/-ui/lib-icons';
import styles from './review-page.module.css';

function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}

export function ReviewAudio({
  localPath,
  localTitle,
  localDurationMs,
  comparisonPath,
  comparisonTitle,
  comparisonDurationMs,
  previewUrl,
  previewSource,
  previewTrackId,
  artist,
}: {
  localPath: string;
  localTitle: string;
  localDurationMs?: number;
  comparisonPath?: string;
  comparisonTitle?: string;
  comparisonDurationMs?: number;
  previewUrl?: string;
  previewSource?: string;
  previewTrackId?: string;
  artist?: string;
}) {
  const catalogSource =
    previewSource === 'deezer' && /^\d+$/.test(previewTrackId || '')
      ? `/api/repair/release-preview/deezer/${previewTrackId}`
      : previewUrl && /^https?:\/\//i.test(previewUrl)
        ? previewUrl
        : '';
  const localFormat = localPath.split('.').pop()?.toUpperCase() || '';
  const searchQuery = [artist, comparisonTitle || localTitle].filter(Boolean).join(' ');
  const [playError, setPlayError] = useState('');
  const [playingPath, setPlayingPath] = useState('');
  const [pendingPath, setPendingPath] = useState('');
  useEffect(() => {
    const audio = document.getElementById('audio-player') as HTMLAudioElement | null;
    const update = () => {
      const value = window.getCurrentTrack?.();
      const track = value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
      setPlayingPath(
        audio && !audio.paused ? String(track?.filename || track?.file_path || '') : '',
      );
    };
    update();
    window.addEventListener('soulsync:player-track', update);
    audio?.addEventListener('play', update);
    audio?.addEventListener('pause', update);
    audio?.addEventListener('ended', update);
    return () => {
      window.removeEventListener('soulsync:player-track', update);
      audio?.removeEventListener('play', update);
      audio?.removeEventListener('pause', update);
      audio?.removeEventListener('ended', update);
    };
  }, []);
  // Only submit an exact local path. A missing slot must never trigger the
  // shared player's metadata search/download fallback.
  const playable = (path?: string) => Boolean(path && /\.[a-z0-9]{2,5}$/i.test(path));
  const choose = async (path: string, title: string, durationMs?: number) => {
    setPlayError('');
    setPendingPath(path);
    try {
      if (!window.playTrackList) throw new Error('The library player is unavailable.');
      const result = await window.playTrackList(
        [
          {
            file_path: path,
            title,
            artist: artist || '',
            duration: (durationMs || 0) / 1000,
          },
        ],
        'Review Queue',
        { audition: true },
      );
      if (result && !['played', 'superseded'].includes(result.status)) {
        throw new Error(result.error || 'This local audio could not be played.');
      }
    } catch (error) {
      setPlayError((error as Error).message);
    } finally {
      setPendingPath('');
    }
  };
  return (
    <section className={styles.reviewAudio} aria-label="Compare audio">
      {(playable(localPath) || playable(comparisonPath)) && (
        <div className={styles.reviewAudioChoices}>
          {playable(localPath) && (
            <>
              {localFormat && <AudioFormatBadge format={localFormat} />}
              <Button
                type="button"
                disabled={Boolean(pendingPath)}
                onClick={() => void choose(localPath, localTitle, localDurationMs)}
              >
                <PlayIcon size={14} /> {pendingPath === localPath ? 'Starting…' : 'Play local file'}
                {localDurationMs ? <small>{clock(localDurationMs / 1000)}</small> : null}
              </Button>
            </>
          )}
          {playable(comparisonPath) && (
            <Button
              type="button"
              onClick={() =>
                void choose(
                  comparisonPath!,
                  comparisonTitle || 'Other local copy',
                  comparisonDurationMs,
                )
              }
            >
              <PlayIcon size={14} /> Play other local copy
            </Button>
          )}
        </div>
      )}
      <div className={styles.listeningLinks} aria-label="Find this recording online">
        {catalogSource && (
          <a href={catalogSource} target="_blank" rel="noreferrer">
            Open release preview ↗
          </a>
        )}
        {previewSource === 'deezer' && /^\d+$/.test(previewTrackId || '') && (
          <a
            href={`https://www.deezer.com/track/${previewTrackId}`}
            target="_blank"
            rel="noreferrer"
          >
            Listen on Deezer ↗
          </a>
        )}
        <a
          href={`https://open.spotify.com/search/${encodeURIComponent(searchQuery)}`}
          target="_blank"
          rel="noreferrer"
        >
          Search Spotify ↗
        </a>
        <a
          href={`https://music.apple.com/search?term=${encodeURIComponent(searchQuery)}`}
          target="_blank"
          rel="noreferrer"
        >
          Search Apple Music ↗
        </a>
        <a
          href={`https://www.youtube.com/results?search_query=${encodeURIComponent(searchQuery)}`}
          target="_blank"
          rel="noreferrer"
        >
          Search YouTube ↗
        </a>
      </div>
      {playingPath && [localPath, comparisonPath].includes(playingPath) && (
        <small role="status">
          Playing {playingPath === localPath ? 'this local file' : 'the other local copy'} in the
          music player.
        </small>
      )}
      {playError && <small role="alert">{playError}</small>}
    </section>
  );
}
