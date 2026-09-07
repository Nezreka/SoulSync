import { useEffect, useRef, useState } from 'react';

import type { ActivePlaybackState } from '../-podcasts.types';

import styles from './podcasts-page.module.css';

interface PodcastPlayerBarProps {
  playback: ActivePlaybackState;
  onTogglePlay: () => void;
  onClose: () => void;
  onUpdateProgress: (currentTime: number, duration: number) => void;
}

function formatTime(seconds: number): string {
  if (isNaN(seconds) || seconds < 0) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export function PodcastPlayerBar({
  playback,
  onTogglePlay,
  onClose,
  onUpdateProgress,
}: PodcastPlayerBarProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [volume, setVolume] = useState(1);
  const [speed, setSpeed] = useState(1);
  const [currentTime, setCurrentTime] = useState(playback.currentTime || 0);
  const [duration, setDuration] = useState(playback.duration || 0);

  const { episode, showTitle, showArtwork, isPlaying } = playback;
  const artwork = episode.artwork_url || showArtwork;

  // Initialize and sync audio element
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    if (audio.src !== episode.enclosure_url) {
      audio.src = episode.enclosure_url;
      audio.load();
    }

    if (isPlaying) {
      audio.play().catch((err) => {
        console.warn('Playback error or blocked by autoplay policy:', err);
      });
    } else {
      audio.pause();
    }
  }, [episode.enclosure_url, isPlaying]);

  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.playbackRate = speed;
    }
  }, [speed]);

  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = volume;
    }
  }, [volume]);

  const handleTimeUpdate = () => {
    if (!audioRef.current) return;
    const cur = audioRef.current.currentTime;
    const dur = audioRef.current.duration || 0;
    setCurrentTime(cur);
    setDuration(dur);
    onUpdateProgress(cur, dur);
  };

  const handleSeek = (val: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = val;
      setCurrentTime(val);
    }
  };

  const handleSkip = (seconds: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = Math.max(
        0,
        Math.min(audioRef.current.duration || 0, audioRef.current.currentTime + seconds),
      );
    }
  };

  const handleCycleSpeed = () => {
    const speeds = [1, 1.25, 1.5, 2];
    const nextIdx = (speeds.indexOf(speed) + 1) % speeds.length;
    setSpeed(speeds[nextIdx]);
  };

  return (
    <div className={styles.playerBar} role="region" aria-label="Podcast Audio Player">
      <audio
        ref={audioRef}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleTimeUpdate}
        onEnded={() => onTogglePlay()}
      />

      {/* Left: Info */}
      <div className={styles.playerTrackInfo}>
        {artwork ? (
          <img src={artwork} alt="" className={styles.playerThumb} />
        ) : (
          <div
            className={styles.playerThumb}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          >
            🎙️
          </div>
        )}
        <div className={styles.playerTrackMeta}>
          <h5 className={styles.playerTrackTitle} title={episode.title}>
            {episode.title}
          </h5>
          <span className={styles.playerTrackAuthor} title={showTitle}>
            {showTitle}
          </span>
        </div>
      </div>

      {/* Center: Controls & Scrubber */}
      <div className={styles.playerCenterControls}>
        <div className={styles.playerButtonsRow}>
          <button
            type="button"
            className={styles.controlBtn}
            onClick={() => handleSkip(-15)}
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
            onClick={() => handleSkip(30)}
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
            max={duration || episode.duration_seconds || 100}
            value={currentTime}
            onChange={(e) => handleSeek(Number(e.target.value))}
            className={styles.scrubSlider}
          />
          <span className={`${styles.timeLabel} ${styles.timeLabelRight}`}>
            {formatTime(duration || episode.duration_seconds || 0)}
          </span>
        </div>
      </div>

      {/* Right: Controls & Dismiss */}
      <div className={styles.playerRightControls}>
        <button
          type="button"
          className={styles.speedToggleBtn}
          onClick={handleCycleSpeed}
          title="Change playback speed"
        >
          {speed}x
        </button>

        <span style={{ color: 'rgba(255, 255, 255, 0.5)', fontSize: 14 }}>🔊</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={volume}
          onChange={(e) => setVolume(Number(e.target.value))}
          className={styles.volumeSlider}
          title={`Volume: ${Math.round(volume * 100)}%`}
        />

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
