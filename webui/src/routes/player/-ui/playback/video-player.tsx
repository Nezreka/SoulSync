/**
 * the video player. full screen while you watch: the picture, controls that
 * fade out after a moment of stillness, an up-next card near the end. leave
 * it and the video keeps playing in a small window in the corner (and in
 * the transport bar) while you browse; click it to come back.
 *
 * the engine owns the <video>; this only moves it between the two frames.
 *
 * keys while full screen: space or k play/pause, j/l or arrows 10 seconds,
 * up/down volume, f full screen, m mute, n next, e episodes, i stats,
 * c subtitles, escape closes what's open, then leaves.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { PlayerState } from '../../-engine/engine';

import { converting } from '../../-engine/describe';
import { current, nextIndex } from '../../-engine/queue';
import { clock } from '../../-engine/timeline';
import { NextIcon, PauseIcon, PlayIcon } from '../icons';
import { inPlayer } from '../trailer';
import {
  AudioMenu,
  PictureMenu,
  QualityMenu,
  SleepMenu,
  SpeedMenu,
  SubtitleMenu,
  VolumeControl,
} from './controls';
import { EndScreen } from './end-screen';
import { PartyReactionsOverlay, setActiveWatchPartyId } from './party-reactions';
import { EpisodesPanel } from './episodes-panel';
import { FindSubtitles } from './find-subtitles';
import {
  BackSecondsIcon,
  EpisodesIcon,
  CloseIcon,
  ChevronDownBigIcon,
  ForwardSecondsIcon,
  FullscreenIcon,
  PipIcon,
  StatsIcon,
} from './icons';
import { MarkButton, useMarks } from './marks';
import s from './playback.module.css';
import { SeekBar } from './seek-bar';
import { StatsOverlay } from './stats';
import { SubtitleOverlay } from './subtitles';
import { ChannelButtons } from './tuner';
import { useLoading } from './use-loading';
import { dimOpacity, OBJECT_FIT, useBrightness, useFit } from './use-picture';
import { getEngine, usePlayer, useVideoOpen } from './use-player';

const IDLE_MS = 2600;

/** put the engine's <video> into a frame while it's on screen */
function useMountVideo(frame: React.RefObject<HTMLDivElement | null>, active: boolean) {
  useEffect(() => {
    const host = frame.current;
    if (!host || !active) return;
    const video = getEngine().element('video') as HTMLVideoElement;
    video.className = s.videoEl;
    host.appendChild(video);
    return () => {
      // leaving the frame doesn't stop the video: the other frame picks it up
      if (video.parentElement === host) host.removeChild(video);
    };
  }, [frame, active]);
}

/**
 * the picture dimmed: a black layer over the video at the right strength.
 * not a css filter: a browser drawing video in a hardware overlay (most, on
 * a real gpu) skips filters on it, so a filter dims nothing there
 */
function Dimmer({ brightness }: { brightness: number }) {
  const opacity = dimOpacity(brightness);
  if (!opacity) return null;
  return <div className={s.dimmer} style={{ opacity }} aria-hidden="true" />;
}

function FullPlayer({ state, onLeave }: { state: PlayerState; onLeave: () => void }) {
  const engine = getEngine();
  const item = current(state.queue)!;
  const root = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const [idle, setIdle] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [stats, setStats] = useState(false);
  const [episodes, setEpisodes] = useState(false);
  const marks = useMarks(item);
  const [finding, setFinding] = useState(false);
  const fit = useFit();
  useEffect(() => {
    const el = engine.element('video') as HTMLVideoElement;
    el.style.objectFit = OBJECT_FIT[fit];
  }, [engine, fit]);
  const brightness = useBrightness();
  const loading = useLoading(state);
  const timer = useRef<number | undefined>(undefined);
  useMountVideo(frame, true);

  const episodesOpen = useRef(false);
  episodesOpen.current = episodes || finding;
  const wake = useCallback(() => {
    // someone's here: no "are you still watching?"
    getEngine().activity();
    setIdle(false);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setIdle(true), IDLE_MS);
  }, []);
  useEffect(() => {
    wake();
    return () => window.clearTimeout(timer.current);
  }, [wake]);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === root.current);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void root.current?.requestFullscreen?.();
  }, []);

  // the keys, ahead of the page's own (remote) navigation
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.closest(
        'input, textarea, select, [role="menu"]',
      );
      if (typing) return;
      // a menu is open: its own keys (escape closes it, and only it)
      if (document.querySelector('[role="menu"]')) return;
      const v = engine.getState();
      const act: Record<string, () => void> = {
        ' ': () => engine.toggle(),
        k: () => engine.toggle(),
        j: () => engine.skip(-10),
        l: () => engine.skip(10),
        ArrowLeft: () => engine.skip(-10),
        ArrowRight: () => engine.skip(10),
        ArrowUp: () => engine.setVolume(v.volume + 0.05),
        ArrowDown: () => engine.setVolume(v.volume - 0.05),
        f: toggleFullscreen,
        m: () => engine.setMuted(!v.muted),
        n: () => engine.next(),
        i: () => setStats((v) => !v),
        e: () => setEpisodes((v) => !v),
        c: () => {
          const first = v.tracks?.subtitles.find((t) => t.kind !== 'unknown');
          engine.setSubtitle(v.subtitle ? null : (first?.id ?? null));
        },
        Escape: () =>
          episodesOpen.current
            ? (setEpisodes(false), setFinding(false))
            : document.fullscreenElement
              ? void document.exitFullscreen()
              : onLeave(),
      };
      const fn = act[e.key];
      if (!fn) return;
      // a focused button takes space and enter itself
      if (e.key === ' ' && (e.target as HTMLElement | null)?.closest('button')) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      wake();
      fn();
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [engine, onLeave, toggleFullscreen, wake]);

  const playing = state.status === 'playing' || state.status === 'buffering';
  const video =
    typeof document !== 'undefined' ? (engine.element('video') as HTMLVideoElement) : null;
  const canPip = !!video && 'requestPictureInPicture' in video && document.pictureInPictureEnabled;
  const hasNext = nextIndex(state.queue, false) !== null;

  return (
    <div
      ref={root}
      className={s.full}
      data-idle={idle && playing ? 'true' : undefined}
      onPointerMove={wake}
      onPointerDown={wake}
      role="dialog"
      aria-label={`Playing ${item.title}`}
    >
      <div
        ref={frame}
        className={s.fullFrame}
        onClick={() => engine.toggle()}
        onDoubleClick={toggleFullscreen}
      />
      <Dimmer brightness={brightness} />

      {state.status === 'loading' || state.status === 'buffering' ? (
        <div className={s.spinner} aria-hidden="true" />
      ) : null}
      {loading ? (
        <div className={s.waking} role="status">
          <span className={s.wakingTitle}>{loading.title}</span>
          {loading.detail ? <span className={s.wakingDetail}>{loading.detail}</span> : null}
        </div>
      ) : null}

      {state.status === 'error' ? (
        <div className={s.fullError} role="alert">
          <p>{state.error}</p>
          <button type="button" className={s.btnLight} onClick={() => engine.stop()} data-nav>
            Close
          </button>
        </div>
      ) : null}

      <PartyReactionsOverlay hudVisible={!idle} />
      <div className={s.fullTop}>
        <button
          type="button"
          className={s.minimize}
          aria-label="Minimize: keep watching in a small window while you browse"
          title="Minimize (esc)"
          onClick={onLeave}
          data-nav
        >
          <ChevronDownBigIcon />
        </button>
        <div className={s.fullTitles}>
          <span className={s.fullTitle}>{item.title}</span>
          {item.subtitle ? <span className={s.fullSub}>{item.subtitle}</span> : null}
        </div>
        {state.transcode ? (
          <span className={s.convertBadge} title={converting(state)}>
            Converted
            {state.transcode.quality && state.transcode.quality !== 'original'
              ? ` · ${state.transcode.quality}`
              : ''}
          </span>
        ) : null}
        <button
          type="button"
          className={s.ctl}
          aria-label="Stop and close"
          onClick={() => engine.stop()}
          data-nav
        >
          <CloseIcon />
        </button>
      </div>

      <SubtitleOverlay state={state} raised={!(idle && playing)} />
      {stats ? <StatsOverlay state={state} video={video} onClose={() => setStats(false)} /> : null}

      {finding ? (
        <FindSubtitles state={state} onClose={() => setFinding(false)} />
      ) : episodes && item.type === 'episode' ? (
        <EpisodesPanel state={state} onClose={() => setEpisodes(false)} />
      ) : (
        <EndScreen state={state} />
      )}

      <div className={s.fullBottom}>
        <SeekBar
          size="large"
          position={state.position}
          duration={state.duration}
          buffered={state.buffered}
          chapters={state.chapters}
          marks={marks}
          onSeek={(t) => engine.seek(t)}
        />
        <div className={s.fullRow}>
          <div className={s.fullGroup}>
            <button
              type="button"
              className={s.playBtn}
              aria-label={playing ? 'Pause' : 'Play'}
              onClick={() => engine.toggle()}
              data-nav
            >
              {playing ? <PauseIcon /> : <PlayIcon />}
            </button>
            <button
              type="button"
              className={s.ctl}
              aria-label="Back 10 seconds"
              onClick={() => engine.skip(-10)}
              data-nav
            >
              <BackSecondsIcon n={10} />
            </button>
            <button
              type="button"
              className={s.ctl}
              aria-label="Forward 10 seconds"
              onClick={() => engine.skip(10)}
              data-nav
            >
              <ForwardSecondsIcon n={10} />
            </button>
            <ChannelButtons state={state} />
            <VolumeControl volume={state.volume} muted={state.muted} />
            <span className={s.fullClock}>
              {clock(state.position)} / {clock(state.duration)}
            </span>
          </div>
          <div className={s.fullGroup}>
            {hasNext ? (
              <button
                type="button"
                className={s.ctl}
                aria-label="Next"
                title="Next (n)"
                onClick={() => engine.next()}
                data-nav
              >
                <NextIcon />
              </button>
            ) : null}
            {item.type === 'episode' && item.parentId ? (
              <button
                type="button"
                className={s.ctl}
                aria-label="Episodes"
                aria-pressed={episodes}
                title="Episodes (e)"
                onClick={() => {
                  setFinding(false);
                  setEpisodes((v) => !v);
                }}
                data-nav
              >
                <EpisodesIcon />
              </button>
            ) : null}
            <MarkButton item={item} />
            <AudioMenu state={state} />
            <SubtitleMenu
              state={state}
              onFind={
                item.type === 'movie' || item.type === 'episode'
                  ? () => {
                      setEpisodes(false);
                      setFinding(true);
                    }
                  : undefined
              }
            />
            <QualityMenu quality={state.quality} />
            <PictureMenu />
            <SpeedMenu rate={state.rate} />
            <SleepMenu state={state} />
            {canPip ? (
              <button
                type="button"
                className={s.ctl}
                aria-label="Picture in picture"
                onClick={() => void video?.requestPictureInPicture?.().catch(() => undefined)}
                data-nav
              >
                <PipIcon />
              </button>
            ) : null}
            <button
              type="button"
              className={s.ctl}
              aria-label="Stats for nerds"
              aria-pressed={stats}
              title="Stats for nerds (i)"
              onClick={() => setStats((v) => !v)}
              data-nav
            >
              <StatsIcon />
            </button>
            <button
              type="button"
              className={s.ctl}
              aria-label={fullscreen ? 'Leave full screen' : 'Full screen'}
              title="Full screen (f)"
              onClick={toggleFullscreen}
              data-nav
            >
              <FullscreenIcon on={fullscreen} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function MiniPlayer({ onOpen }: { onOpen: () => void }) {
  const frame = useRef<HTMLDivElement>(null);
  const state = usePlayer();
  const brightness = useBrightness();
  useMountVideo(frame, true);
  return (
    <div className={s.mini}>
      <div ref={frame} className={s.miniFrame} />
      <Dimmer brightness={brightness} />
      <SubtitleOverlay state={state} raised={false} />
      <button
        type="button"
        className={s.miniOpen}
        aria-label="Back to full screen"
        onClick={onOpen}
        data-nav
      />
    </div>
  );
}

/** the video, full or mini, while a video is the thing playing */
export function VideoPlayer() {
  const state = usePlayer();
  const [open, setOpen] = useVideoOpen();
  const queryClient = useQueryClient();
  // marked watched: the pages showing it (badges, continue watching) catch up
  useEffect(() => {
    if (state.watchedKey) void queryClient.invalidateQueries({ queryKey: ['player'] });
  }, [state.watchedKey, queryClient]);
  if (state.kind !== 'video' || !current(state.queue)) return null;
  return inPlayer(
    open ? (
      <FullPlayer state={state} onLeave={() => setOpen(false)} />
    ) : (
      <MiniPlayer onOpen={() => setOpen(true)} />
    ),
  );
}
