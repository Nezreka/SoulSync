import { useEffect, useRef, useState } from 'react';

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
}) {
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
        <button
          type="button"
          data-active={selected === 'local' || undefined}
          onClick={() => void choose('local')}
        >
          {selected === 'local' && playing ? 'Pause' : '▶ Listen'} · Local file
          {localDurationMs ? <small>{clock(localDurationMs / 1000)}</small> : null}
        </button>
        {comparisonSource && (
          <button
            type="button"
            data-active={selected === 'comparison' || undefined}
            onClick={() => void choose('comparison')}
          >
            {selected === 'comparison' && playing ? 'Pause' : '▶ Listen'} · Other local copy
            {comparisonDurationMs ? <small>{clock(comparisonDurationMs / 1000)}</small> : null}
          </button>
        )}
        {catalogSource && (
          <button
            type="button"
            data-active={selected === 'preview' || undefined}
            onClick={() => void choose('preview')}
          >
            {selected === 'preview' && playing ? 'Pause' : '▶ Listen'} · Release preview
          </button>
        )}
      </div>
      <div className={styles.inlineActions} aria-label="Find this recording online">
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
          href={`https://open.spotify.com/search/${encodeURIComponent(comparisonTitle || localTitle)}`}
          target="_blank"
          rel="noreferrer"
        >
          Search Spotify ↗
        </a>
        <a
          href={`https://www.youtube.com/results?search_query=${encodeURIComponent(comparisonTitle || localTitle)}`}
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
