/**
 * the controls both the transport bar and the video player use, so a
 * button behaves the same everywhere: speed, sleep timer, chapters, volume.
 */

import { useEffect, useState } from 'react';

import type { PlayerState, Quality } from '../../-engine/engine';

import { MAX_VOLUME } from '../../-engine/engine';
import { chapterAt, clock } from '../../-engine/timeline';
import { audioLabel, subtitleLabel } from '../../-engine/tracks';
import {
  AudioIcon,
  BrightnessIcon,
  ChaptersIcon,
  QualityIcon,
  SleepIcon,
  SpeedIcon,
  SubtitlesIcon,
  VolumeIcon,
} from './icons';
import { PopMenu } from './menu';
import s from './playback.module.css';
import { BRIGHTNESS_STEPS, setBrightness, useBrightness } from './use-picture';
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
  const shown = muted ? 0 : volume;
  const pct = Math.round(shown * 100);
  return (
    <div className={s.volume} data-boost={shown > 1 ? 'true' : undefined}>
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
        max={MAX_VOLUME}
        step={0.02}
        value={shown}
        aria-label="Volume"
        aria-valuetext={`${pct}%`}
        title={`${pct}%${shown > 1 ? ' (boosted)' : ''}`}
        onChange={(e) => getEngine().setVolume(Number(e.target.value))}
        style={
          {
            '--fill': `${(shown / MAX_VOLUME) * 100}%`,
            '--full': `${(1 / MAX_VOLUME) * 100}%`,
          } as React.CSSProperties
        }
      />
      {shown > 1 ? <span className={s.volumeBoost}>{pct}%</span> : null}
    </div>
  );
}

/** audio sync: the sound earlier or later, in 50 ms steps (applied once you stop pressing) */
function useDelaySteps(applied: number): [number, (next: number) => void] {
  const [pending, setPending] = useState<number | null>(null);
  useEffect(() => {
    if (pending === null) return;
    const t = window.setTimeout(() => {
      getEngine().setAudioDelay(pending);
      setPending(null);
    }, 800);
    return () => window.clearTimeout(t);
  }, [pending]);
  return [pending ?? applied, setPending];
}

function syncWords(ms: number) {
  if (!ms) return 'In sync';
  return `Sound ${Math.abs(ms)} ms ${ms > 0 ? 'later' : 'earlier'}`;
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

/** the file's audio tracks by language, channels and codec, and audio sync */
export function AudioMenu({ state }: { state: PlayerState }) {
  const audio = state.tracks?.audio ?? [];
  const [delay, setDelay] = useDelaySteps(state.audioDelay);
  if (state.kind !== 'video') return null;
  const tracks =
    audio.length > 1
      ? audio.map((t) => {
          const words = audioLabel(t);
          return {
            key: String(t.index),
            label: words.label,
            detail: words.detail,
            selected: t.index === state.audioTrack,
            onSelect: () => getEngine().setAudioTrack(t.index),
          };
        })
      : [];
  return (
    <PopMenu
      label="Audio"
      title={`Audio · ${syncWords(delay)}`}
      icon={<AudioIcon />}
      badge={delay ? `${delay > 0 ? '+' : ''}${delay}` : undefined}
      options={[
        ...tracks,
        {
          key: 'earlier',
          label: 'Sound earlier',
          detail: 'If voices come after the lips move',
          keepOpen: true,
          onSelect: () => setDelay(delay - 50),
        },
        {
          key: 'later',
          label: 'Sound later',
          detail: 'If voices come before the lips move',
          keepOpen: true,
          onSelect: () => setDelay(delay + 50),
        },
        ...(delay
          ? [{ key: 'reset', label: 'Back in sync', keepOpen: true, onSelect: () => setDelay(0) }]
          : []),
      ]}
    />
  );
}

/** brightness: dim the picture (for watching in bed) */
export function PictureMenu() {
  const brightness = useBrightness();
  return (
    <PopMenu
      label="Brightness"
      title="Brightness"
      icon={<BrightnessIcon />}
      badge={brightness < 1 ? `${Math.round(brightness * 100)}%` : undefined}
      options={BRIGHTNESS_STEPS.map((v) => ({
        key: String(v),
        label: v === 1 ? 'Full' : `${Math.round(v * 100)}%`,
        detail: v === 0.15 ? 'Barely there' : v <= 0.45 ? 'Dim' : undefined,
        selected: Math.abs(v - brightness) < 0.01,
        onSelect: () => setBrightness(v),
      }))}
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
