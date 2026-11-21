/**
 * the transport bar: what's playing, its controls, and the seek bar, along
 * the bottom of every page while something plays. music gets shuffle and
 * repeat; books, podcasts and videos get skip back and forward, speed, a
 * sleep timer and chapters. a video playing in the bar has a button back
 * to full screen.
 */

import { useState } from 'react';

import { current } from '../../-engine/queue';
import { chapterAt } from '../../-engine/timeline';
import { LONG_FORM } from '../../-engine/types';
import { NextIcon, PauseIcon, PlayIcon, PrevIcon, ShuffleIcon } from '../icons';
import { PlayerLink } from '../kit';
import { SongActions } from '../music/song-actions';
import { RetryImg } from '../retry-img';
import { ChapterMenu, SleepMenu, SpeedMenu, VolumeControl } from './controls';
import {
  BackSecondsIcon,
  CloseIcon,
  ExpandIcon,
  ForwardSecondsIcon,
  QueueIcon,
  RepeatIcon,
  RepeatOneIcon,
} from './icons';
import { useMarks } from './marks';
import s from './playback.module.css';
import { QueuePanel } from './queue-panel';
import { SeekBar } from './seek-bar';
import { ChannelButtons } from './tuner';
import { useLoading } from './use-loading';
import { getEngine, usePlayer, useVideoOpen } from './use-player';

export function TransportBar() {
  const state = usePlayer();
  const [, setVideoOpen] = useVideoOpen();
  const [queueOpen, setQueueOpen] = useState(false);
  const loading = useLoading(state);
  const item = current(state.queue);
  const marks = useMarks(item);
  if (!item) return null;
  const engine = getEngine();
  const long = LONG_FORM.has(item.type);
  const playing = state.status === 'playing' || state.status === 'buffering';
  const chapter = state.chapters.length
    ? state.chapters[chapterAt(state.chapters, state.position)]
    : null;
  const sub =
    state.status === 'error'
      ? state.error
      : loading
        ? [loading.title, loading.detail].filter(Boolean).join(' · ')
        : (chapter?.title ?? item.subtitle);

  return (
    <section className={s.bar} aria-label="Now playing">
      <div className={s.now}>
        <button
          type="button"
          className={s.nowArt}
          onClick={() => setVideoOpen(true)}
          aria-label={state.kind === 'video' ? 'Back to the video' : 'Open now playing'}
          title={
            state.kind === 'video' ? 'Back to the video' : 'Now playing: lyrics, up next, visuals'
          }
          data-nav
        >
          {item.artwork || item.backdrop ? (
            <RetryImg src={(item.artwork ?? item.backdrop)!} alt="" />
          ) : null}
          <span className={s.nowArtExpand} aria-hidden="true">
            <ExpandIcon />
          </span>
        </button>
        <div className={s.nowText}>
          {item.href ? (
            <PlayerLink href={item.href} className={s.nowTitle} data-nav>
              {item.title}
            </PlayerLink>
          ) : (
            <span className={s.nowTitle}>{item.title}</span>
          )}
          {sub ? (
            <span className={s.nowSub} data-error={state.status === 'error' ? 'true' : undefined}>
              {sub}
            </span>
          ) : null}
        </div>
        {item.type === 'track' ? <SongActions item={item} /> : null}
      </div>

      <div className={s.center}>
        <div className={s.controls}>
          {long ? (
            <button
              type="button"
              className={s.ctl}
              aria-label="Back 15 seconds"
              onClick={() => engine.skip(-15)}
              data-nav
            >
              <BackSecondsIcon n={15} />
            </button>
          ) : (
            <button
              type="button"
              className={s.ctl}
              aria-label={state.queue.shuffle ? 'Shuffle on' : 'Shuffle'}
              aria-pressed={state.queue.shuffle}
              onClick={() => engine.toggleShuffle()}
              data-nav
            >
              <ShuffleIcon />
            </button>
          )}
          <button
            type="button"
            className={s.ctl}
            aria-label="Previous"
            onClick={() => engine.previous()}
            data-nav
          >
            <PrevIcon />
          </button>
          <button
            type="button"
            className={s.playBtn}
            aria-label={playing ? 'Pause' : 'Play'}
            onClick={() => engine.toggle()}
            data-busy={
              state.status === 'loading' || state.status === 'buffering' ? 'true' : undefined
            }
            data-nav
          >
            {playing ? <PauseIcon /> : <PlayIcon />}
          </button>
          <button
            type="button"
            className={s.ctl}
            aria-label="Next"
            onClick={() => engine.next()}
            data-nav
          >
            <NextIcon />
          </button>
          {long ? (
            <button
              type="button"
              className={s.ctl}
              aria-label="Forward 30 seconds"
              onClick={() => engine.skip(30)}
              data-nav
            >
              <ForwardSecondsIcon n={30} />
            </button>
          ) : (
            <button
              type="button"
              className={s.ctl}
              aria-label={`Repeat ${state.queue.repeat}`}
              aria-pressed={state.queue.repeat !== 'off'}
              onClick={() => engine.cycleRepeat()}
              data-nav
            >
              {state.queue.repeat === 'one' ? <RepeatOneIcon /> : <RepeatIcon />}
            </button>
          )}
        </div>
        <SeekBar
          position={state.position}
          duration={state.duration}
          buffered={state.buffered}
          chapters={state.chapters}
          marks={marks}
          onSeek={(t) => engine.seek(t)}
        />
      </div>

      <div className={s.end}>
        {long ? <ChapterMenu state={state} /> : null}
        {long ? <SpeedMenu rate={state.rate} /> : null}
        <ChannelButtons state={state} />
        <SleepMenu state={state} />
        <button
          type="button"
          className={s.ctl}
          aria-label="Queue"
          aria-pressed={queueOpen}
          onClick={() => setQueueOpen((v) => !v)}
          data-nav
        >
          <QueueIcon />
        </button>
        <VolumeControl volume={state.volume} muted={state.muted} />
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
      {queueOpen ? <QueuePanel state={state} onClose={() => setQueueOpen(false)} /> : null}
    </section>
  );
}
