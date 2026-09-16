import { useEffect, useRef, useState } from 'react';

import styles from './media-player-bar.module.css';

export interface MediaPlayerBarProps {
  /** What is playing. */
  title: string;
  /** Who it is by — the show for an episode, the author for a book. */
  subtitle?: string;
  artworkUrl?: string | null;
  /** The audio to play. Changing it loads the new source and resets the scrubber. */
  src: string;
  isPlaying: boolean;
  /** Known length, used until the file reports its own. */
  durationHint?: number | null;
  /** Shown when there is no artwork. */
  fallbackIcon?: string;
  ariaLabel?: string;
  onTogglePlay: () => void;
  onClose: () => void;
  onProgress?: (currentTime: number, duration: number) => void;
}

const SPEEDS = [1, 1.25, 1.5, 2];

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

/**
 * The floating transport shared by the podcast and audiobook pages.
 *
 * Both play one audio file with a title, a subtitle and a piece of artwork, so
 * they share this rather than each growing their own — the second one started
 * out as a thinner copy with no skip buttons, no speed control and no volume,
 * which is exactly the kind of drift a shared component prevents.
 *
 * Presentational: it owns the audio element and its own transport state, and
 * tells the caller nothing except progress. Whether playback continues across
 * navigation is the caller's business, not this component's.
 */
export function MediaPlayerBar({
  title,
  subtitle,
  artworkUrl,
  src,
  isPlaying,
  durationHint,
  fallbackIcon = '🎧',
  ariaLabel = 'Audio player',
  onTogglePlay,
  onClose,
  onProgress,
}: MediaPlayerBarProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [volume, setVolume] = useState(1);
  const [speed, setSpeed] = useState(1);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    // Compared against the attribute rather than audio.src: the browser
    // resolves the property to an absolute URL, so a relative src would look
    // like a change on every render and restart playback constantly.
    if (audio.getAttribute('data-src') !== src) {
      audio.setAttribute('data-src', src);
      audio.src = src;
      audio.load();
      setCurrentTime(0);
      setDuration(0);
    }

    if (isPlaying) {
      audio.play().catch((err) => {
        console.warn('Playback failed or was blocked by the autoplay policy:', err);
      });
    } else {
      audio.pause();
    }
  }, [src, isPlaying]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = speed;
  }, [speed]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.volume = volume;
  }, [volume]);

  const handleTimeUpdate = () => {
    const audio = audioRef.current;
    if (!audio) return;
    const cur = audio.currentTime;
    const dur = audio.duration || 0;
    setCurrentTime(cur);
    setDuration(dur);
    onProgress?.(cur, dur);
  };

  const seek = (value: number) => {
    if (!audioRef.current) return;
    audioRef.current.currentTime = value;
    setCurrentTime(value);
  };

  const skip = (seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + seconds));
  };

  const cycleSpeed = () => setSpeed(SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length]);

  const total = duration || durationHint || 0;

  return (
    <div className={styles.playerBar} role="region" aria-label={ariaLabel}>
      <audio
        ref={audioRef}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleTimeUpdate}
        onEnded={onTogglePlay}
      />

      <div className={styles.playerTrackInfo}>
        {artworkUrl ? (
          <img src={artworkUrl} alt="" className={styles.playerThumb} />
        ) : (
          <div
            className={styles.playerThumb}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          >
            {fallbackIcon}
          </div>
        )}
        <div className={styles.playerTrackMeta}>
          <h5 className={styles.playerTrackTitle} title={title}>
            {title}
          </h5>
          {subtitle && (
            <span className={styles.playerTrackAuthor} title={subtitle}>
              {subtitle}
            </span>
          )}
        </div>
      </div>

      <div className={styles.playerCenterControls}>
        <div className={styles.playerButtonsRow}>
          <button
            type="button"
            className={styles.controlBtn}
            onClick={() => skip(-15)}
            title="Rewind 15 seconds"
          >
            <span style={{ fontSize: 13, fontWeight: 700 }}>↺ 15</span>
          </button>

          <button
            type="button"
            className={styles.playerPlayPauseBtn}
            onClick={onTogglePlay}
            title={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="4" width="4" height="16" />
                <rect x="14" y="4" width="4" height="16" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <polygon points="6 4 20 12 6 20 6 4" />
              </svg>
            )}
          </button>

          <button
            type="button"
            className={styles.controlBtn}
            onClick={() => skip(30)}
            title="Forward 30 seconds"
          >
            <span style={{ fontSize: 13, fontWeight: 700 }}>30 ↻</span>
          </button>
        </div>

        <div className={styles.scrubRow}>
          <span className={styles.timeLabel}>{formatTime(currentTime)}</span>
          <input
            type="range"
            min={0}
            max={total || 100}
            value={currentTime}
            onChange={(event) => seek(Number(event.target.value))}
            className={styles.scrubSlider}
            aria-label="Seek"
          />
          <span className={`${styles.timeLabel} ${styles.timeLabelRight}`}>
            {formatTime(total)}
          </span>
        </div>
      </div>

      <div className={styles.playerRightControls}>
        <button
          type="button"
          className={styles.speedToggleBtn}
          onClick={cycleSpeed}
          title="Change playback speed"
        >
          {speed}x
        </button>

        <div className={styles.volumeWrapper}>
          <span className={styles.volumeIcon}>🔊</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={volume}
            onChange={(event) => setVolume(Number(event.target.value))}
            className={styles.volumeSlider}
            title={`Volume: ${Math.round(volume * 100)}%`}
            aria-label="Volume"
          />
        </div>

        <button
          type="button"
          className={styles.closePlayerBtn}
          onClick={onClose}
          title="Close player"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
