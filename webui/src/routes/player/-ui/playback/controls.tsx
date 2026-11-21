/**
 * the controls both the transport bar and the video player use, so a
 * button behaves the same everywhere: speed, sleep timer, chapters, volume.
 */

import type { PlayerState, Quality } from '../../-engine/engine';

import { chapterAt, clock } from '../../-engine/timeline';
import { ChaptersIcon, QualityIcon, SleepIcon, SpeedIcon, VolumeIcon } from './icons';
import { PopMenu } from './menu';
import s from './playback.module.css';
import { getEngine } from './use-player';

export const SPEEDS = [0.75, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5];

export function SpeedMenu({ rate }: { rate: number }) {
  return (
    <PopMenu
      label="Playback speed"
      title="Speed"
      icon={<SpeedIcon />}
      badge={rate !== 1 ? `${rate}x` : undefined}
      options={SPEEDS.map((r) => ({
        key: String(r),
        label: r === 1 ? 'Normal' : `${r}x`,
        selected: r === rate,
        onSelect: () => getEngine().setRate(r),
      }))}
    />
  );
}

export function SleepMenu({ state }: { state: PlayerState }) {
  const sleep = state.sleep;
  const left =
    sleep && 'until' in sleep ? Math.max(0, Math.ceil((sleep.until - Date.now()) / 60_000)) : null;
  const options = [
    ...[5, 15, 30, 45, 60, 90].map((m) => ({
      key: String(m),
      label: `${m} minutes`,
      onSelect: () => getEngine().setSleep(m),
    })),
    {
      key: 'end',
      label:
        state.kind === 'video'
          ? 'End of this'
          : state.chapters.length
            ? 'End of this book'
            : 'End of this',
      selected: !!sleep && 'endOfItem' in sleep,
      onSelect: () => getEngine().setSleep('end'),
    },
    ...(sleep
      ? [{ key: 'off', label: 'Turn off', onSelect: () => getEngine().setSleep(null) }]
      : []),
  ];
  return (
    <PopMenu
      label={sleep ? 'Sleep timer on' : 'Sleep timer'}
      title="Stop playing after"
      icon={<SleepIcon />}
      badge={left !== null ? `${left}m` : sleep ? 'end' : undefined}
      options={options}
    />
  );
}

export function ChapterMenu({ state }: { state: PlayerState }) {
  if (state.chapters.length < 2) return null;
  const at = chapterAt(state.chapters, state.position);
  return (
    <PopMenu
      label="Chapters"
      title="Chapters"
      icon={<ChaptersIcon />}
      options={state.chapters.map((c, i) => ({
        key: `${c.start}`,
        label: c.title,
        hint: clock(c.start),
        selected: i === at,
        onSelect: () => getEngine().seek(c.start),
      }))}
    />
  );
}

export function VolumeControl({ volume, muted }: { volume: number; muted: boolean }) {
  const level = muted || volume === 0 ? 0 : volume < 0.5 ? 1 : 2;
  return (
    <div className={s.volume}>
      <button
        type="button"
        className={s.ctl}
        aria-label={muted ? 'Unmute' : 'Mute'}
        title={muted ? 'Unmute (m)' : 'Mute (m)'}
        onClick={() => getEngine().setMuted(!muted)}
        data-nav
      >
        <VolumeIcon level={level} />
      </button>
      <input
        className={s.volumeRange}
        type="range"
        min={0}
        max={1}
        step={0.02}
        value={muted ? 0 : volume}
        aria-label="Volume"
        onChange={(e) => getEngine().setVolume(Number(e.target.value))}
        style={{ '--fill': `${(muted ? 0 : volume) * 100}%` } as React.CSSProperties}
      />
    </div>
  );
}

const QUALITY_LABELS: Record<Quality, { label: string; hint: string }> = {
  original: { label: 'Original', hint: 'as it is, converted only if it must be' },
  '1080p': { label: '1080p', hint: 'up to 8 Mbps' },
  '720p': { label: '720p', hint: 'up to 4 Mbps' },
  '480p': { label: '480p', hint: 'for slow connections' },
};

export function QualityMenu({ quality }: { quality: Quality }) {
  return (
    <PopMenu
      label="Quality"
      title="Quality"
      icon={<QualityIcon />}
      badge={quality !== 'original' ? quality : undefined}
      options={(Object.keys(QUALITY_LABELS) as Quality[]).map((q) => ({
        key: q,
        label: QUALITY_LABELS[q].label,
        hint: QUALITY_LABELS[q].hint,
        selected: q === quality,
        onSelect: () => getEngine().setQuality(q),
      }))}
    />
  );
}
