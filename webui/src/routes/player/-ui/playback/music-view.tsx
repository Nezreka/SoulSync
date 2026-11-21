/**
 * now playing, full screen, for what you listen to. two shapes:
 *   * music: the cover big, the song's heart and add-to-playlist, lyrics
 *     that follow the song, what's next, and a moving background that
 *     follows the music (soulsync's own visuals) tinted by the cover
 *   * a book or a podcast: the cover, its chapters, speed, skip 15 / 30,
 *     the sleep timer up front
 * the transport bar stays the small form: this opens from its cover and
 * closes back to it (the chevron, escape, or the cover again), still
 * playing, so you can browse.
 *
 * keys: space play/pause, arrows 10s (shift: volume), n / p next and
 * previous, l lyrics, q queue, v next visual, escape back to the bar.
 */

import { useEffect, useState } from 'react';

import type { PlayerState } from '../../-engine/engine';

import { upcoming } from '../../-engine/queue';
import { chapterAt, clock } from '../../-engine/timeline';
import { LONG_FORM } from '../../-engine/types';
import { NextIcon, PauseIcon, PlayIcon, PrevIcon, ShuffleIcon } from '../icons';
import { PlayerLink } from '../kit';
import { SongActions } from '../music/song-actions';
import { inPlayer } from '../trailer';
import { SleepMenu, SpeedMenu, VolumeControl } from './controls';
import {
  BackSecondsIcon,
  ChaptersIcon,
  ChevronDownBigIcon,
  CloseIcon,
  ForwardSecondsIcon,
  LyricsIcon,
  QueueIcon,
  RepeatIcon,
  RepeatOneIcon,
} from './icons';
import { LyricsPanel } from './lyrics-panel';
import { MarkButton, useMarks } from './marks';
import s from './music-view.module.css';
import { SeekBar } from './seek-bar';
import { ChannelButtons } from './tuner';
import { useLoading } from './use-loading';
import { getEngine, usePlayer, useVideoOpen } from './use-player';
import { cycleVisual, Visuals, VisualsMenu } from './visuals';

type Tab = 'lyrics' | 'queue' | 'chapters';

function UpNextList({ state }: { state: PlayerState }) {
  const engine = getEngine();
  const ahead = upcoming(state.queue);
  if (!ahead.length) return <div className={s.lyricsEmpty}>Nothing after this.</div>;
  return (
    <ol className={s.upNext}>
      {ahead.slice(0, 200).map(({ item, index }) => {
        return (
          <li key={`${item.type}:${item.id}:${index}`}>
            <button type="button" className={s.upNextRow} onClick={() => engine.jump(index)}>
              {item.artwork ? <img src={item.artwork} alt="" loading="lazy" /> : <span />}
              <span className={s.upNextText}>
                <span className={s.upNextTitle}>{item.title}</span>
                {item.subtitle ? <span className={s.upNextSub}>{item.subtitle}</span> : null}
              </span>
              <span className={s.upNextTime}>{item.duration ? clock(item.duration) : ''}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function ChapterList({ state }: { state: PlayerState }) {
  const engine = getEngine();
  const now = chapterAt(state.chapters, state.position);
  if (!state.chapters.length) return <div className={s.lyricsEmpty}>No chapters in this one.</div>;
  return (
    <ol className={s.upNext}>
      {state.chapters.map((c, i) => (
        <li key={`${c.start}-${i}`}>
          <button
            type="button"
            className={s.upNextRow}
            data-now={now === i ? 'true' : undefined}
            onClick={() => engine.seek(c.start)}
          >
            <span className={s.chapterNum}>{i + 1}</span>
            <span className={s.upNextText}>
              <span className={s.upNextTitle}>{c.title || `Chapter ${i + 1}`}</span>
            </span>
            <span className={s.upNextTime}>{clock(c.start)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function View({ state, onClose }: { state: PlayerState; onClose: () => void }) {
  const engine = getEngine();
  const item = state.item!;
  const music = item.type === 'track';
  const long = LONG_FORM.has(item.type);
  const loading = useLoading(state);
  const marks = useMarks(item);
  const [tab, setTab] = useState<Tab>(
    music ? 'lyrics' : state.chapters.length ? 'chapters' : 'queue',
  );
  const playing = state.status === 'playing' || state.status === 'buffering';
  const chapterIndex = long ? chapterAt(state.chapters, state.position) : -1;
  const chapter = chapterIndex >= 0 ? state.chapters[chapterIndex] : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.closest('input, textarea, select');
      if (
        typing ||
        document.querySelector(
          '[data-player-root] [role="menu"], [data-player-root] [aria-modal="true"]',
        )
      )
        return;
      const v = engine.getState();
      const act: Record<string, () => void> = {
        ' ': () => engine.toggle(),
        k: () => engine.toggle(),
        ArrowLeft: () => (e.shiftKey ? engine.setVolume(v.volume - 0.05) : engine.skip(-10)),
        ArrowRight: () => (e.shiftKey ? engine.setVolume(v.volume + 0.05) : engine.skip(10)),
        n: () => engine.next(),
        p: () => engine.previous(),
        l: () => music && setTab('lyrics'),
        q: () => setTab('queue'),
        v: () => music && cycleVisual(),
        m: () => engine.setMuted(!v.muted),
        Escape: onClose,
      };
      const fn = act[e.key];
      if (!fn) return;
      if (e.key === ' ' && (e.target as HTMLElement | null)?.closest('button')) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      fn();
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [engine, music, onClose]);

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    ...(music ? [{ id: 'lyrics' as const, label: 'Lyrics', icon: <LyricsIcon /> }] : []),
    ...(long && state.chapters.length
      ? [{ id: 'chapters' as const, label: 'Chapters', icon: <ChaptersIcon /> }]
      : []),
    { id: 'queue', label: 'Up next', icon: <QueueIcon /> },
  ];

  const art = item.artwork ?? item.backdrop ?? null;
  return inPlayer(
    <div className={s.view} role="dialog" aria-label={`Now playing ${item.title}`}>
      <Visuals art={art} music={music} />

      <header className={s.top}>
        <button
          type="button"
          className={s.round}
          aria-label="Back to the bar (keeps playing)"
          title="Minimize (esc)"
          onClick={onClose}
          data-nav
        >
          <ChevronDownBigIcon />
        </button>
        <div className={s.from}>
          <span className={s.fromLabel}>Playing from</span>
          {item.href ? (
            <PlayerLink href={item.href} className={s.fromName} onClick={onClose} data-nav>
              {item.parentTitle ?? item.subtitle ?? 'your library'}
            </PlayerLink>
          ) : (
            <span className={s.fromName}>
              {item.parentTitle ?? item.subtitle ?? 'your library'}
            </span>
          )}
        </div>
        <div className={s.topEnd}>
          {music ? <VisualsMenu /> : null}
          <button
            type="button"
            className={s.round}
            aria-label="Stop and close"
            onClick={() => engine.stop()}
            data-nav
          >
            <CloseIcon />
          </button>
        </div>
      </header>

      <main className={s.stage}>
        <section className={s.now}>
          <div className={s.art} data-playing={playing ? 'true' : undefined}>
            {art ? <img src={art} alt="" /> : <span className={s.artBlank} />}
          </div>
          <div className={s.meta}>
            <div className={s.titles}>
              <h1 className={s.title}>{item.title}</h1>
              <div className={s.sub}>
                {chapter?.title ?? item.subtitle ?? ''}
                {item.parentTitle && music ? (
                  <>
                    {(chapter?.title ?? item.subtitle) ? ' · ' : ''}
                    {item.href ? (
                      <PlayerLink href={item.href} onClick={onClose} data-nav>
                        {item.parentTitle}
                      </PlayerLink>
                    ) : (
                      item.parentTitle
                    )}
                  </>
                ) : null}
              </div>
              {loading ? <div className={s.loading}>{loading.title}</div> : null}
              {state.status === 'error' ? <div className={s.error}>{state.error}</div> : null}
            </div>
            {music ? <SongActions item={item} /> : null}
          </div>
          <div className={s.seek}>
            <SeekBar
              size="large"
              position={state.position}
              duration={state.duration}
              buffered={state.buffered}
              chapters={state.chapters}
              marks={marks}
              onSeek={(t) => engine.seek(t)}
            />
          </div>
          <div className={s.transport}>
            {long ? (
              <button
                type="button"
                className={s.side}
                aria-label="Back 15 seconds"
                onClick={() => engine.skip(-15)}
                data-nav
              >
                <BackSecondsIcon n={15} />
              </button>
            ) : (
              <button
                type="button"
                className={s.side}
                aria-label="Shuffle"
                aria-pressed={state.queue.shuffle}
                onClick={() => engine.toggleShuffle()}
                data-nav
              >
                <ShuffleIcon />
              </button>
            )}
            <button
              type="button"
              className={s.skip}
              aria-label="Previous"
              onClick={() => engine.previous()}
              data-nav
            >
              <PrevIcon />
            </button>
            <button
              type="button"
              className={s.play}
              aria-label={playing ? 'Pause' : 'Play'}
              onClick={() => engine.toggle()}
              data-nav
            >
              {playing ? <PauseIcon /> : <PlayIcon />}
            </button>
            <button
              type="button"
              className={s.skip}
              aria-label="Next"
              onClick={() => engine.next()}
              data-nav
            >
              <NextIcon />
            </button>
            {long ? (
              <button
                type="button"
                className={s.side}
                aria-label="Forward 30 seconds"
                onClick={() => engine.skip(30)}
                data-nav
              >
                <ForwardSecondsIcon n={30} />
              </button>
            ) : (
              <button
                type="button"
                className={s.side}
                aria-label={`Repeat ${state.queue.repeat}`}
                aria-pressed={state.queue.repeat !== 'off'}
                onClick={() => engine.cycleRepeat()}
                data-nav
              >
                {state.queue.repeat === 'one' ? <RepeatOneIcon /> : <RepeatIcon />}
              </button>
            )}
          </div>
          <div className={s.extras}>
            <ChannelButtons state={state} />
            <MarkButton item={item} />
            <VolumeControl volume={state.volume} muted={state.muted} />
            {long ? <SpeedMenu rate={state.rate} /> : null}
            <SleepMenu state={state} />
          </div>
        </section>

        <section className={s.panel} aria-label="Lyrics and what's next">
          <div className={s.tabs} role="tablist">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                className={s.tab}
                onClick={() => setTab(t.id)}
                data-nav
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>
          <div className={s.panelBody}>
            {tab === 'lyrics' && music ? <LyricsPanel state={state} /> : null}
            {tab === 'chapters' ? <ChapterList state={state} /> : null}
            {tab === 'queue' ? <UpNextList state={state} /> : null}
          </div>
        </section>
      </main>
    </div>,
  );
}

/** now playing, full screen, while something you listen to plays and it's open */
export function MusicView() {
  const state = usePlayer();
  const [open, setOpen] = useVideoOpen();
  if (state.kind !== 'audio' || !state.item || !open) return null;
  return <View state={state} onClose={() => setOpen(false)} />;
}
