import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { ARTMAP_LIVE_OVERFLOW_LIMIT, artMap } from './-discover.artist-map';

/**
 * The tuning constants and initial state, which the differential harness cannot
 * reach — it compares FUNCTIONS, and these are the literals the functions are
 * calibrated against. Each is checked against the vanilla text so a silent drift
 * in either direction fails here rather than showing up as a slow map.
 */
const SOURCE = readFileSync(
  resolve(process.cwd(), 'src/routes/discover/__fixtures__/-vanilla-discover.js'),
  'utf8',
);

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

  it('matches the constants still written in discover.js', () => {
    // Pinned against the source text, not a copy of my own port, so that editing
    // one side without the other is a failing test rather than a silent skew.
    expect(SOURCE).toContain('WATCHLIST_R: 320');
    expect(SOURCE).toContain('BUFFER: 8');
    expect(SOURCE).toContain('MAX_BUFFER_PX: 4096');
    expect(SOURCE).toContain('LIVE_PX: 12');
    expect(SOURCE).toContain('_panelW: 320');
    expect(SOURCE).toContain('zoom: 0.15');
    expect(SOURCE).toContain('_liveOverflow = liveN > 140');
  });
});
