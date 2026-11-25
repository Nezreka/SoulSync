import { describe, expect, it } from 'vitest';

import type { Channel } from './channels';

import { retuneIfDrifted, stepTuning, tuneAt } from './tuner';

const movie = (id: string, seconds: number) => ({
  key: `movie:${id}`,
  title: `Movie ${id}`,
  seconds,
  kind: 'movie' as const,
});

const channel = (
  id: string,
  number: number,
  pool = [movie(`${id}a`, 5400), movie(`${id}b`, 6000), movie(`${id}c`, 4800)],
): Channel => ({
  id,
  name: id,
  kind: 'genre',
  blurb: '',
  pool,
  number,
});

describe('tuner', () => {
  const now = Date.UTC(2026, 10, 21, 20, 13);

  it('joins a channel live: what is on now, at the minute it is on, and on from there', () => {
    const t = tuneAt(channel('comedy', 1), now)!;
    expect(t.program.start).toBeLessThanOrEqual(now);
    expect(t.program.end).toBeGreaterThan(now);
    expect(t.startAt).toBeCloseTo((now - t.program.start) / 1000);
    expect(t.queue[0].id).toBe(t.program.key.split(':')[1]);
    expect(t.queue.length).toBeGreaterThan(1);
  });

  it('steps up and down the lineup, wrapping, skipping a channel with nothing on', () => {
    const lineup = [channel('a', 1), channel('empty', 2, []), channel('c', 3)];
    expect(stepTuning(lineup, 'a', 1, now)?.channel.id).toBe('c');
    expect(stepTuning(lineup, 'c', 1, now)?.channel.id).toBe('a');
    expect(stepTuning(lineup, 'a', -1, now)?.channel.id).toBe('c');
    expect(stepTuning(lineup, null, 1, now)?.channel.id).toBe('a');
    expect(stepTuning([], 'a', 1, now)).toBeNull();
  });

  describe('keeping the shared clock', () => {
    const ch = channel('comedy', 1);
    const airing = tuneAt(ch, now)!.program.key;

    it('leaves a channel alone while it plays what is airing', () => {
      expect(
        retuneIfDrifted(ch, { itemKey: airing, status: 'playing', offClock: false }, now),
      ).toBeNull();
    });

    it('tunes back in when a file ran past its slot', () => {
      const t = retuneIfDrifted(
        ch,
        { itemKey: 'movie:not-airing', status: 'playing', offClock: false },
        now,
      );
      expect(t?.program.key).toBe(airing);
      expect(t?.startAt).toBeCloseTo((now - t!.program.start) / 1000);
    });

    it('tunes back in when the queue ran out or a clip failed, even on what is airing', () => {
      for (const status of ['ended', 'error'] as const)
        expect(
          retuneIfDrifted(ch, { itemKey: airing, status, offClock: false }, now)?.program.key,
        ).toBe(airing);
    });

    it('leaves paused, loading and start over alone', () => {
      for (const status of ['paused', 'loading', 'idle'] as const)
        expect(retuneIfDrifted(ch, { itemKey: 'x:y', status, offClock: false }, now)).toBeNull();
      expect(
        retuneIfDrifted(ch, { itemKey: 'x:y', status: 'playing', offClock: true }, now),
      ).toBeNull();
      // start over that failed still comes back
      expect(
        retuneIfDrifted(ch, { itemKey: 'x:y', status: 'error', offClock: true }, now),
      ).not.toBeNull();
    });

    it('a channel with nothing on stays put', () => {
      expect(
        retuneIfDrifted(
          channel('empty', 2, []),
          { itemKey: null, status: 'ended', offClock: false },
          now,
        ),
      ).toBeNull();
    });
  });
});
