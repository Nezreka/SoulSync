/**
 * the guide's channels while you watch or listen: tune one in (in at the
 * minute it's on), then channel up and down like a tv, with a banner saying
 * what you landed on. the lineup is the guide's, in its numbers; it stays
 * after you leave the guide, so channel up works from anywhere.
 *
 * keys anywhere while a channel plays: page up / page down, and a remote's
 * channel up / channel down; backspace for the last channel. typing a
 * channel's number (on the guide, or while one plays) tunes it in.
 */

import { useRouterState } from '@tanstack/react-router';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { PlayerState } from '../../-engine/engine';
import type { Channel } from '../../-lib/channels';

import { runtime } from '../../-lib/format';
import { stepTuning, tuneAt, type Tuning } from '../../-lib/tuner';
import { inPlayer } from '../trailer';
import { ChannelDownIcon, ChannelUpIcon, LastChannelIcon, StartOverIcon } from './icons';
import s from './playback.module.css';
import { playItems, usePlayer } from './use-player';

interface Tuned {
  lineup: Channel[];
  current: Tuning | null;
  /** the channel before this one: "last channel" goes back to it */
  previous: Channel | null;
  at: number;
  /** a channel number being typed */
  typing: string;
}

let tuned: Tuned = { lineup: [], current: null, previous: null, at: 0, typing: '' };
const listeners = new Set<() => void>();
function emit(next: Partial<Tuned>) {
  tuned = { ...tuned, ...next };
  for (const l of listeners) l();
}

/** the channel playing, when one is (for watching now) */
export function currentChannel(): { id: string; name: string; number: number | null } | null {
  const c = tuned.current?.channel;
  return c ? { id: c.id, name: c.name, number: c.number ?? null } : null;
}

/** the guide hands over its channels, in its order */
export function setLineup(channels: Channel[]) {
  if (
    channels.length === tuned.lineup.length &&
    channels.every((c, i) => c.id === tuned.lineup[i]?.id)
  )
    return;
  emit({ lineup: channels });
}

function play(t: Tuning) {
  playItems(t.queue, 0, { startAt: t.startAt, live: true });
  const before = tuned.current?.channel;
  emit({
    current: t,
    at: Date.now(),
    previous: before && before.id !== t.channel.id ? before : tuned.previous,
  });
}

/** back to the channel before this one */
export function lastChannel(): boolean {
  const back = tuned.previous;
  if (!back) return false;
  const fresh = tuned.lineup.find((c) => c.id === back.id) ?? back;
  return tuneIn(fresh);
}

/** the program on now, from its beginning (on the same channel) */
export function startOver(): boolean {
  const t = tuned.current;
  if (!t) return false;
  const again = tuneAt(t.channel, t.program.start);
  if (!again) return false;
  playItems(again.queue, 0, { startAt: 0, live: true });
  emit({ current: again, at: Date.now() });
  return true;
}

/** tune a channel by its number; false: there's no such channel */
export function tuneNumber(n: number): boolean {
  const channel = tuned.lineup.find((c) => c.number === n);
  return channel ? tuneIn(channel) : false;
}

/** tune a channel in live; false: nothing on it right now */
export function tuneIn(channel: Channel): boolean {
  const t = tuneAt(channel, Date.now());
  if (!t) return false;
  play(t);
  return true;
}

/** tune in a channel from the guide's lineup by id (joining someone on it); false: not known here */
export function channelByIdTune(id: string): boolean {
  const channel = tuned.lineup.find((c) => c.id === id);
  return channel ? tuneIn(channel) : false;
}

export function channelStep(delta: 1 | -1): boolean {
  const t = stepTuning(tuned.lineup, tuned.current?.channel.id ?? null, delta, Date.now());
  if (!t) return false;
  play(t);
  return true;
}

function useTuned(): Tuned {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => tuned,
    () => tuned,
  );
}

/** a channel is what's playing: it came from the tuner and is still live */
function onChannel(state: PlayerState, t: Tuned) {
  return !!t.current && state.live && t.lineup.length > 1;
}

export function ChannelButtons({ state, className }: { state: PlayerState; className?: string }) {
  const t = useTuned();
  if (!onChannel(state, t)) return null;
  return (
    <span className={s.channelBtns}>
      <button
        type="button"
        className={className ?? s.ctl}
        aria-label="Start over"
        title="Start this program over"
        onClick={() => startOver()}
        data-nav
      >
        <StartOverIcon />
      </button>
      {t.previous ? (
        <button
          type="button"
          className={className ?? s.ctl}
          aria-label={`Last channel: ${t.previous.name}`}
          title={`Last channel: ${t.previous.number ?? ''} ${t.previous.name} (backspace)`}
          onClick={() => lastChannel()}
          data-nav
        >
          <LastChannelIcon />
        </button>
      ) : null}
      <button
        type="button"
        className={className ?? s.ctl}
        aria-label="Channel up"
        title="Channel up (page up)"
        onClick={() => channelStep(1)}
        data-nav
      >
        <ChannelUpIcon />
      </button>
      <span className={s.channelNum} title={t.current!.channel.name}>
        {t.current!.channel.number ?? ''}
      </span>
      <button
        type="button"
        className={className ?? s.ctl}
        aria-label="Channel down"
        title="Channel down (page down)"
        onClick={() => channelStep(-1)}
        data-nav
      >
        <ChannelDownIcon />
      </button>
    </span>
  );
}

const BANNER_MS = 4500;

/** what you tuned to, for a few seconds; and the channel keys, anywhere */
export function ChannelBanner() {
  const state = usePlayer();
  const t = useTuned();
  const [now, setNow] = useState(() => Date.now());
  const on = onChannel(state, t);

  useEffect(() => {
    if (!t.at) return;
    setNow(Date.now());
    const timer = window.setTimeout(() => setNow(Date.now()), BANNER_MS + 50);
    return () => window.clearTimeout(timer);
  }, [t.at]);

  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const onGuide = pathname.startsWith('/player/guide');
  const typingTimer = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!on && !onGuide) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest('input, textarea, select')) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // a channel's number, typed: tuned in a moment after the last digit (or on enter)
      if (/^[0-9]$/.test(e.key) && tuned.lineup.length) {
        e.preventDefault();
        e.stopImmediatePropagation();
        const typing = (tuned.typing + e.key).slice(-4);
        emit({ typing });
        window.clearTimeout(typingTimer.current);
        typingTimer.current = window.setTimeout(() => {
          const n = Number(tuned.typing);
          emit({ typing: '' });
          if (n) tuneNumber(n);
        }, 1400);
        return;
      }
      if (e.key === 'Enter' && tuned.typing) {
        e.preventDefault();
        e.stopImmediatePropagation();
        window.clearTimeout(typingTimer.current);
        const n = Number(tuned.typing);
        emit({ typing: '' });
        if (n) tuneNumber(n);
        return;
      }
      if (!on) return;
      if (e.key === 'Backspace' && tuned.previous) {
        e.preventDefault();
        e.stopImmediatePropagation();
        lastChannel();
        return;
      }
      const up = e.key === 'PageUp' || e.key === 'ChannelUp';
      const down = e.key === 'PageDown' || e.key === 'ChannelDown';
      if (!up && !down) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      channelStep(up ? 1 : -1);
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [on, onGuide]);

  if (t.typing) {
    const n = Number(t.typing);
    const match = t.lineup.find((c) => c.number === n);
    return inPlayer(
      <div className={s.banner} role="status" aria-live="polite">
        <span className={s.bannerNum}>{t.typing}</span>
        <span className={s.bannerText}>
          <span className={s.bannerChannel}>Channel</span>
          <span className={s.bannerTitle}>
            {match ? match.name : 'No channel with that number'}
          </span>
        </span>
      </div>,
    );
  }

  if (!on || !t.current || now - t.at > BANNER_MS) return null;
  const { channel, program } = t.current;
  const left = Math.max(0, (program.end - now) / 1000);
  return inPlayer(
    <div className={s.banner} role="status" aria-live="polite" key={t.at}>
      <span className={s.bannerNum}>{channel.number ?? ''}</span>
      <span className={s.bannerText}>
        <span className={s.bannerChannel}>{channel.name}</span>
        <span className={s.bannerTitle}>{program.title}</span>
        <span className={s.bannerSub}>
          {[program.subtitle, left ? `${runtime(left)} left` : ''].filter(Boolean).join(' · ')}
        </span>
      </span>
    </div>,
  );
}
