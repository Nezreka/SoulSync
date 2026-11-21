import { describe as group, expect, it } from 'vitest';

import type { PlayerState } from './engine';

import { describe } from './describe';

const base = {
  phase: null,
  phaseSince: 0,
  position: 0,
  buffered: 0,
  source: null,
} as unknown as PlayerState;
const at = (patch: Partial<PlayerState>) => ({ ...base, ...patch }) as PlayerState;

group('what loading says', () => {
  it('looks, then says why it is taking a while', () => {
    expect(describe(at({ phase: 'locating', phaseSince: 0 }), 1000)?.title).toBe(
      'Finding the file',
    );
    const slow = describe(at({ phase: 'locating', phaseSince: 0 }), 12_000);
    expect(slow?.title).toBe('Still looking for the file');
    expect(slow?.detail).toMatch(/^12s\. The drive it's on may be waking up/);
  });

  it('says a drive was asleep only once the server timed it', () => {
    const fast = at({ phase: 'opening', source: { via: 'disk', tookMs: 300 } });
    expect(describe(fast, 0)).toEqual({ title: 'Opening the file', detail: undefined });
    const asleep = at({ phase: 'opening', source: { via: 'disk', tookMs: 24_400 } });
    expect(describe(asleep, 0)?.detail).toBe('Found it in 24 seconds. The drive was asleep.');
  });

  it('names the media server it streams from', () => {
    expect(describe(at({ phase: 'opening', source: { via: 'plex', tookMs: 100 } }), 0)?.title).toBe(
      'Connecting to Plex',
    );
    const resume = describe(
      at({ phase: 'resuming', position: 2530, source: { via: 'jellyfin', tookMs: 100 } }),
      0,
    );
    expect(resume).toEqual({ title: 'Picking up at 42:10', detail: 'From Jellyfin' });
  });

  it('seeking and buffering', () => {
    expect(describe(at({ phase: 'seeking', position: 3723 }), 0)?.title).toBe('Jumping to 1:02:03');
    const buffering = at({
      phase: 'buffering',
      phaseSince: 0,
      position: 10,
      buffered: 14.5,
      source: { via: 'disk', tookMs: 1 },
    });
    expect(describe(buffering, 1000)?.detail).toBeUndefined();
    expect(describe(buffering, 5000)?.detail).toBe('Waiting on the drive, 4s ready');
    expect(describe(at({ phase: null }), 0)).toBeNull();
  });
});
