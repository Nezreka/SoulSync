/**
 * the guide's channels while you watch or listen: tune one in (in at the
 * minute it's on), then channel up and down like a tv, with a banner saying
 * what you landed on. the lineup is the guide's, in its numbers; it stays
 * after you leave the guide, so channel up works from anywhere.
 *
 * keys anywhere while a channel plays: page up / page down, and a remote's
 * channel up / channel down.
 */

import { useEffect, useState, useSyncExternalStore } from 'react';

import type { PlayerState } from '../../-engine/engine';
import type { Channel } from '../../-lib/channels';

import { runtime } from '../../-lib/format';
import { stepTuning, tuneAt, type Tuning } from '../../-lib/tuner';
import { inPlayer } from '../trailer';
import { ChannelDownIcon, ChannelUpIcon } from './icons';
import s from './playback.module.css';
import { playItems, usePlayer } from './use-player';

interface Tuned {
  lineup: Channel[];
  current: Tuning | null;
  at: number;
}

let tuned: Tuned = { lineup: [], current: null, at: 0 };
const listeners = new Set<() => void>();
function emit(next: Partial<Tuned>) {
  tuned = { ...tuned, ...next };
  for (const l of listeners) l();
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
  emit({ current: t, at: Date.now() });
}

/** tune a channel in live; false: nothing on it right now */
export function tuneIn(channel: Channel): boolean {
  const t = tuneAt(channel, Date.now());
  if (!t) return false;
  play(t);
  return true;
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

  useEffect(() => {
    if (!on) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest('input, textarea, select')) return;
      const up = e.key === 'PageUp' || e.key === 'ChannelUp';
      const down = e.key === 'PageDown' || e.key === 'ChannelDown';
      if (!up && !down) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      channelStep(up ? 1 : -1);
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [on]);

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
