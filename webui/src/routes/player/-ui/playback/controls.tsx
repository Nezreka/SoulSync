/**
 * the controls both the transport bar and the video player use, so a
 * button behaves the same everywhere: speed, sleep timer, chapters, volume.
 */

import { useEffect, useState } from 'react';

import { jobWords, useAiSubtitles } from '@/features/video-library/ai-subtitles';

import type { PlayerState, Quality } from '../../-engine/engine';

import { MAX_VOLUME } from '../../-engine/engine';
import { chapterAt, clock } from '../../-engine/timeline';
import { audioLabel, subtitleLabel } from '../../-engine/tracks';
import { languageName } from '../video-facts';
import { startLang } from './find-subtitles';
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
import {
  BRIGHTNESS_STEPS,
  FITS,
  setBrightness,
  setFit,
  useBrightness,
  useFit,
} from './use-picture';
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

export const QUALITY_LABELS: Record<Quality, { label: string; detail: string }> = {
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
            group: 'Track',
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
          group: 'Sync',
          label: 'Sound earlier',
          detail: 'If voices come after the lips move',
          keepOpen: true,
          onSelect: () => setDelay(delay - 50),
        },
        {
          key: 'later',
          group: 'Sync',
          label: 'Sound later',
          detail: 'If voices come before the lips move',
          keepOpen: true,
          onSelect: () => setDelay(delay + 50),
        },
        ...(delay
          ? [
              {
                key: 'reset',
                group: 'Sync',
                label: 'Back in sync',
                keepOpen: true,
                onSelect: () => setDelay(0),
              },
            ]
          : []),
      ]}
    />
  );
}

/** the picture: how bright (dim it for watching in bed) and how it fills the screen */
export function PictureMenu() {
  const brightness = useBrightness();
  const fit = useFit();
  return (
    <PopMenu
      label="Picture"
      title="Picture"
      icon={<BrightnessIcon />}
      badge={brightness < 1 ? `${Math.round(brightness * 100)}%` : undefined}
      options={[
        ...BRIGHTNESS_STEPS.map((v) => ({
          key: String(v),
          group: 'Brightness',
          label: v === 1 ? 'Full' : `${Math.round(v * 100)}%`,
          detail: v === 0.15 ? 'Barely there' : v === 0.3 ? 'For the dark' : undefined,
          selected: Math.abs(v - brightness) < 0.01,
          keepOpen: true,
          onSelect: () => setBrightness(v),
        })),
        ...FITS.map((f) => ({
          key: f.key,
          group: 'Shape',
          label: f.label,
          detail: f.detail,
          selected: fit === f.key,
          keepOpen: true,
          onSelect: () => setFit(f.key),
        })),
      ]}
    />
  );
}

/** subtitles: off, each track, their sync, how they look, and finding more */
export function SubtitleMenu({ state, onFind }: { state: PlayerState; onFind?: () => void }) {
  const subs = (state.tracks?.subtitles ?? []).filter((t) => t.kind !== 'unknown');
  const style = useSubtitleStyle();
  const current = subs.find((t) => t.id === state.subtitle);
  const delay = state.subtitleDelay;
  const item = state.item;
  const kind = item?.type === 'movie' || item?.type === 'episode' ? item.type : null;
  // the language ai makes: the one picked before, else the browser's
  const lang = current?.language?.slice(0, 2) || startLang();
  const ai = useAiSubtitles(
    kind,
    kind ? item?.id : null,
    lang,
    'player',
    (l) => void getEngine().refreshTracks(l),
  );
  if (!subs.length && !onFind && !kind) return null;
  const step = (ms: number) => getEngine().setSubtitleDelay(delay + ms);
  const aiWords = ai.job ? jobWords(ai.job, ai.watching) : null;
  const aiOptions = !kind
    ? []
    : ai.active && aiWords
      ? [
          {
            key: 'ai-status',
            group: 'AI subtitles',
            label: aiWords.label,
            detail: aiWords.detail,
            keepOpen: true,
            onSelect: () => undefined,
          },
          {
            key: 'ai-stop',
            group: 'AI subtitles',
            label: 'Stop making them',
            keepOpen: true,
            onSelect: () => void ai.cancel(),
          },
        ]
      : [
          {
            key: 'ai-make',
            group: 'AI subtitles',
            label: ai.asking ? 'Asking…' : `Generate ${languageName(lang)} subtitles`,
            detail:
              ai.error ||
              (!ai.available && ai.why) ||
              (aiWords && ai.job?.state !== 'done' ? `${aiWords.label}: ${aiWords.detail}` : '') ||
              'Made on your server from the audio. Real ones replace them',
            keepOpen: true,
            onSelect: () => void ai.generate(),
          },
        ];
  return (
    <PopMenu
      label="Subtitles"
      title={
        delay ? `Subtitles · ${Math.abs(delay)} ms ${delay > 0 ? 'later' : 'earlier'}` : 'Subtitles'
      }
      icon={<SubtitlesIcon />}
      badge={state.subtitleLoading ? '…' : state.subtitle ? 'on' : undefined}
      options={[
        {
          key: 'off',
          group: 'Track',
          label: 'Off',
          selected: !state.subtitle,
          onSelect: () => getEngine().setSubtitle(null),
        },
        ...subs.map((t) => {
          const words = subtitleLabel(t);
          return {
            key: t.id,
            group: 'Track',
            label: words.label,
            detail: words.detail,
            selected: t.id === state.subtitle,
            onSelect: () => getEngine().setSubtitle(t.id),
          };
        }),
        ...(onFind
          ? [
              {
                key: 'find',
                group: 'Track',
                label: 'Find subtitles…',
                detail: 'Search your subtitle providers',
                onSelect: onFind,
              },
            ]
          : []),
        ...aiOptions,
        // the player draws text subtitles itself, so their timing can move freely
        ...(current?.kind === 'text'
          ? [
              {
                key: 'earlier',
                group: 'Sync',
                label: 'Show earlier',
                detail: 'If lines come after they are spoken',
                keepOpen: true,
                onSelect: () => step(-100),
              },
              {
                key: 'later',
                group: 'Sync',
                label: 'Show later',
                detail: 'If lines come before they are spoken',
                keepOpen: true,
                onSelect: () => step(100),
              },
              {
                key: 'earlier-1s',
                group: 'Sync',
                label: 'A second earlier',
                detail: 'Saved for this subtitle file, on every screen',
                keepOpen: true,
                onSelect: () => step(-1000),
              },
              {
                key: 'later-1s',
                group: 'Sync',
                label: 'A second later',
                keepOpen: true,
                onSelect: () => step(1000),
              },
              ...(delay
                ? [
                    {
                      key: 'reset',
                      group: 'Sync',
                      label: 'Back in sync',
                      keepOpen: true,
                      onSelect: () => getEngine().setSubtitleDelay(0),
                    },
                  ]
                : []),
            ]
          : []),
        ...(subs.length
          ? [
              ...(['small', 'medium', 'large'] as const).map((size) => ({
                key: `size-${size}`,
                group: 'Look',
                label:
                  size === 'small' ? 'Small text' : size === 'large' ? 'Large text' : 'Medium text',
                selected: style.size === size,
                keepOpen: true,
                onSelect: () => setSubtitleStyle({ size }),
              })),
              {
                key: 'box',
                group: 'Look',
                label: 'Dark box behind',
                selected: style.box,
                keepOpen: true,
                onSelect: () => setSubtitleStyle({ box: !style.box }),
              },
            ]
          : []),
      ]}
    />
  );
}
