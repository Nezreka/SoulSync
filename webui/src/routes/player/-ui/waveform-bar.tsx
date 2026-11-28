/**
 * Waveform seekbar for music playback (Media Analysis Phase 2).
 * Renders normalized 0..255 amplitude bars with played/unplayed accent fill
 * and click/drag seeking.
 */

import type { MouseEvent } from 'react';

export function WaveformBar({
  peaks,
  progress,
  durationS,
  onSeek,
  height = 36,
}: {
  peaks: readonly number[];
  /** 0..1 fraction played */
  progress: number;
  durationS: number;
  onSeek: (seconds: number) => void;
  height?: number;
}) {
  if (!peaks.length) return null;

  const handleClick = (e: MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || durationS <= 0) return;
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    onSeek(frac * durationS);
  };

  const clamped = Math.min(1, Math.max(0, progress));
  const playedIdx = Math.floor(clamped * peaks.length);

  return (
    <div
      role="slider"
      aria-label="Waveform seekbar"
      aria-valuemin={0}
      aria-valuemax={Math.round(durationS)}
      aria-valuenow={Math.round(clamped * durationS)}
      tabIndex={0}
      onClick={handleClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        height,
        width: '100%',
        cursor: 'pointer',
        userSelect: 'none',
      }}
    >
      {peaks.map((amp, i) => {
        const pct = Math.max(10, Math.round((Math.min(255, Math.max(0, amp)) / 255) * 100));
        const played = i <= playedIdx;
        return (
          <span
            key={i}
            style={{
              flex: 1,
              height: `${pct}%`,
              borderRadius: 2,
              background: played ? 'var(--accent, #22c55e)' : 'rgba(255,255,255,0.22)',
              transition: 'background 80ms linear',
            }}
          />
        );
      })}
    </div>
  );
}
