import { describe, expect, it } from 'vitest';

import { ARTMAP_LIVE_OVERFLOW_LIMIT, artMap } from './-discover.artist-map';

/**
 * The tuning constants and initial state, which the differential harness cannot
 * reach — it compares FUNCTIONS, and these are the literals the functions are
 * calibrated against. Pin the production values directly so changes fail here
 * rather than showing up as a slow map.
 */

describe('the map singleton starts where the vanilla did', () => {
  it('keeps the geometry + performance constants', () => {
    expect(artMap.WATCHLIST_R).toBe(320);
    expect(artMap.BUFFER).toBe(8);
    expect(artMap.MAX_BUFFER_PX).toBe(4096);
    expect(artMap.LIVE_PX).toBe(12);
    expect(artMap._panelW).toBe(320);
  });

  it('starts zoomed out at 0.15 and dirty', () => {
    // `dirty` must start true or the very first draw blits an empty buffer.
    expect(artMap.zoom).toBe(0.15);
    expect(artMap.dirty).toBe(true);
    expect(artMap._fieldAlpha).toBe(1);
  });

  it('caps the live layer at 140 bubbles before the buffer takes the crowd', () => {
    expect(ARTMAP_LIVE_OVERFLOW_LIMIT).toBe(140);
  });
});
