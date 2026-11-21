/**
 * the controls both the transport bar and the video player use, so a
 * button behaves the same everywhere: speed, sleep timer, chapters, volume.
 */

import type { PlayerState, Quality } from '../../-engine/engine';

import { chapterAt, clock } from '../../-engine/timeline';
import { audioLabel, subtitleLabel } from '../../-engine/tracks';
import {
  AudioIcon,
  ChaptersIcon,
  QualityIcon,
  SleepIcon,
  SpeedIcon,
  SubtitlesIcon,
  VolumeIcon,
} from './icons';
import { PopMenu } from './menu';
import s from './playback.module.css';
import { getEngine } from './use-player';
import { setSubtitleStyle, useSubtitleStyle } from './use-subtitle-style';

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

const QUALITY_LABELS: Record<Quality, { label: string; detail: string }> = {
  original: { label: 'Original', detail: 'Full quality' },
  '1080p': { label: '1080p', detail: 'Up to 8 Mbps' },
  '720p': { label: '720p', detail: 'Up to 4 Mbps' },
  '480p': { label: '480p', detail: 'For slow connections' },
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
        detail: QUALITY_LABELS[q].detail,
        selected: q === quality,
        onSelect: () => getEngine().setQuality(q),
      }))}
    />
  );
}

/** the file's audio tracks by language, channels and codec */
export function AudioMenu({ state }: { state: PlayerState }) {
  const audio = state.tracks?.audio ?? [];
  if (audio.length < 2) return null;
  return (
    <PopMenu
      label="Audio"
      title="Audio"
      icon={<AudioIcon />}
      options={audio.map((t) => {
        const words = audioLabel(t);
        return {
          key: String(t.index),
          label: words.label,
          detail: words.detail,
          selected: t.index === state.audioTrack,
          onSelect: () => getEngine().setAudioTrack(t.index),
        };
      })}
    />
  );
}

/** subtitles: off, each track, and how they look */
export function SubtitleMenu({ state }: { state: PlayerState }) {
  const subs = (state.tracks?.subtitles ?? []).filter((t) => t.kind !== 'unknown');
  const style = useSubtitleStyle();
  if (!subs.length) return null;
  return (
    <PopMenu
      label="Subtitles"
      title="Subtitles"
      icon={<SubtitlesIcon />}
      badge={state.subtitleLoading ? '…' : state.subtitle ? 'on' : undefined}
      options={[
        {
          key: 'off',
          label: 'Off',
          selected: !state.subtitle,
          onSelect: () => getEngine().setSubtitle(null),
        },
        ...subs.map((t) => {
          const words = subtitleLabel(t);
          return {
            key: t.id,
            label: words.label,
            detail: words.detail,
            selected: t.id === state.subtitle,
            onSelect: () => getEngine().setSubtitle(t.id),
          };
        }),
        ...(['small', 'medium', 'large'] as const).map((size) => ({
          key: `size-${size}`,
          label: `Text ${size}`,
          selected: style.size === size,
          onSelect: () => setSubtitleStyle({ size }),
        })),
        {
          key: 'box',
          label: style.box ? 'Dark box behind: on' : 'Dark box behind: off',
          selected: style.box,
          onSelect: () => setSubtitleStyle({ box: !style.box }),
        },
      ]}
    />
  );
}
