import { useEffect, useRef, useState } from 'react';

import { AudioFormatBadge } from '@/components/audio-format-badge/audio-format-badge';

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
  const localFormat = localPath.split('.').pop()?.toUpperCase() || '';
  const searchQuery = [artist, comparisonTitle || localTitle].filter(Boolean).join(' ');
  const audioRef = useRef<HTMLAudioElement>(null);
  const shouldPlayRef = useRef(false);
  const [selected, setSelected] = useState<'local' | 'comparison' | 'preview'>('local');
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playError, setPlayError] = useState('');
  const comparisonSource = comparisonPath
    ? `/stream/library-audio?path=${encodeURIComponent(comparisonPath)}`
    : '';
  const catalogSource =
    previewSource === 'deezer' && /^\d+$/.test(previewTrackId || '')
      ? `/api/repair/release-preview/deezer/${previewTrackId}`
      : previewUrl && /^https?:\/\//i.test(previewUrl)
        ? previewUrl
        : '';
  const source =
    selected === 'comparison'
      ? comparisonSource
      : selected === 'preview'
        ? catalogSource
        : `/stream/library-audio?path=${encodeURIComponent(localPath)}`;
  const knownDuration =
    selected === 'local' ? localDurationMs : selected === 'comparison' ? comparisonDurationMs : 0;
  const playbackError = () =>
    setPlayError(
      selected === 'preview'
        ? 'The catalog preview could not be played in this browser.'
        : 'This local audio could not be played.',
    );

  useEffect(() => {
    setPlaying(false);
    setPosition(0);
    setDuration(0);
    setPlayError('');
    if (shouldPlayRef.current) {
      shouldPlayRef.current = false;
      void audioRef.current?.play().catch(playbackError);
    }
  }, [source]);

  const choose = async (next: 'local' | 'comparison' | 'preview') => {
    if (selected === next) {
      const audio = audioRef.current;
      if (!audio) return;
      if (audio.paused) {
        try {
          await audio.play();
        } catch {
          playbackError();
        }
      } else audio.pause();
      return;
    }
    shouldPlayRef.current = true;
    setSelected(next);
  };

  return (
    <section className={styles.reviewAudio} aria-label="Compare audio">
      <div className={styles.reviewAudioChoices}>
        {localFormat && <AudioFormatBadge format={localFormat} />}
        <button
          type="button"
          aria-pressed={selected === 'local' && playing}
          data-active={selected === 'local' || undefined}
          onClick={() => void choose('local')}
        >
          {!(selected === 'local' && playing) && <PlayIcon size={14} />}
          {selected === 'local' && playing ? 'Pause' : 'Listen'} · Local file
          {localDurationMs ? <small>{clock(localDurationMs / 1000)}</small> : null}
        </button>
        {comparisonSource && (
          <button
            type="button"
            aria-pressed={selected === 'comparison' && playing}
            data-active={selected === 'comparison' || undefined}
            onClick={() => void choose('comparison')}
          >
            {!(selected === 'comparison' && playing) && <PlayIcon size={14} />}
            {selected === 'comparison' && playing ? 'Pause' : 'Listen'} · Other local copy
            {comparisonDurationMs ? <small>{clock(comparisonDurationMs / 1000)}</small> : null}
          </button>
        )}
        {catalogSource && (
          <button
            type="button"
            data-active={selected === 'preview' || undefined}
            onClick={() => void choose('preview')}
          >
            {!(selected === 'preview' && playing) && <PlayIcon size={14} />}
            {selected === 'preview' && playing ? 'Pause' : 'Listen'} · Release preview
          </button>
        )}
      </div>
      <div className={styles.listeningLinks} aria-label="Find this recording online">
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
      <div className={styles.reviewAudioTimeline}>
        <strong>
          {selected === 'local' ? localTitle : comparisonTitle || 'Release recording'}
        </strong>
        <input
          type="range"
          min={0}
          max={duration || knownDuration ? duration || (knownDuration || 0) / 1000 : 1}
          step={0.1}
          value={position}
          aria-label="Audio position"
          onChange={(event) => {
            if (audioRef.current) audioRef.current.currentTime = Number(event.target.value);
          }}
        />
        <span>
          {clock(position)} / {clock(duration || (knownDuration || 0) / 1000)}
        </span>
      </div>
      {playError && (
        <small role="alert">
          {playError}{' '}
          {selected === 'preview' && catalogSource && (
            <a href={catalogSource} target="_blank" rel="noopener noreferrer">
              Open the preview source
            </a>
          )}
        </small>
      )}
      <audio
        ref={audioRef}
        src={source}
        preload="metadata"
        onTimeUpdate={(event) => setPosition(event.currentTarget.currentTime)}
        onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={playbackError}
      />
    </section>
  );
}
