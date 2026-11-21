/**
 * tuning a channel, pure: what's airing on it now and the queue that plays
 * on from there (in at the minute it's on), and the channel one up or down
 * the lineup. the guide's numbers are the order; it wraps at the ends and
 * skips a channel with nothing on.
 */

import type { PlayItem } from '../-engine/types';
import type { Channel } from './channels';
import type { GuideProgram } from './schedule';

import { channelQueue } from './play-items';
import { programsInWindow } from './schedule';

const HOUR = 3_600_000;

export interface Tuning {
  channel: Channel;
  program: GuideProgram;
  queue: PlayItem[];
  /** seconds into the program it's on */
  startAt: number;
}

export function tuneAt(channel: Channel, now: number): Tuning | null {
  const programs = programsInWindow(channel.id, channel.pool, now - 6 * HOUR, now + 6 * HOUR, {
    ordered: channel.ordered,
  });
  const program = programs.find((p) => p.start <= now && p.end > now);
  if (!program) return null;
  const queue = channelQueue(programs, program);
  if (!queue.length) return null;
  return { channel, program, queue, startAt: Math.max(0, (now - program.start) / 1000) };
}

/** the next channel up (+1) or down (-1) from ``currentId`` with something on */
export function stepTuning(
  lineup: readonly Channel[],
  currentId: string | null,
  delta: 1 | -1,
  now: number,
): Tuning | null {
  if (!lineup.length) return null;
  const at = Math.max(
    -1,
    lineup.findIndex((c) => c.id === currentId),
  );
  for (let step = 1; step <= lineup.length; step++) {
    const i = (((at + delta * step) % lineup.length) + lineup.length) % lineup.length;
    if (lineup[i].id === currentId) continue;
    const tuning = tuneAt(lineup[i], now);
    if (tuning) return tuning;
  }
  return null;
}
