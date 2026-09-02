import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The stuck-row diagnostics, EXECUTED.
 *
 * A wishlist row showed one attempt count and one refusal line for a search that
 * may have asked three different sources. "Stuck on 40 attempts" could equally
 * mean Prowlarr was never configured, or slskd returns nothing, or every source
 * finds it and the quality profile refuses them all — three different problems
 * with three different answers, collapsed into one number.
 *
 * The drain now stores what each source actually did. These run the real
 * readers out of video-wishlist.js against those snapshots.
 */

const SRC = readFileSync(
  resolve(process.cwd(), 'static/video/video-wishlist.js'),
  'utf8',
);

/** Pull the two diagnostic functions out of the IIFE and make them callable. */
function readers(): {
  failWhy: (row: unknown, fails: number) => string;
  sourceLines: (row: unknown) => string[];
} {
  const from = SRC.indexOf('    function failWhy(row, fails) {');
  const to = SRC.indexOf('\n    function statusPill(');
  expect(from, 'failWhy is gone from video-wishlist.js').toBeGreaterThan(-1);
  expect(to).toBeGreaterThan(from);
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${SRC.slice(from, to)}\n return { failWhy, sourceLines };`)() as ReturnType<
    typeof readers
  >;
}

const SNAP = {
  chain: ['torrent', 'soulseek'],
  sources: {
    torrent: { ran: false, reason: 'Prowlarr not configured', results: 0, accepted: 0, rejected: 0 },
    soulseek: {
      ran: true,
      results: 4,
      accepted: 0,
      rejected: 4,
      reason: "Best found: SD — SD isn't in your enabled tiers",
    },
  },
};

describe('per-source diagnostics', () => {
  it('separates "could not search" from "found nothing"', () => {
    const { sourceLines } = readers();
    const lines = sourceLines({ search_snapshot: SNAP });
    expect(lines[0]).toBe('• torrent: could not search — Prowlarr not configured');
    expect(lines[1]).toBe("• soulseek: 4 found, none accepted — Best found: SD — SD isn't in your enabled tiers");
  });

  it('reports each source in the order the chain tried them', () => {
    const { sourceLines } = readers();
    const flipped = { ...SNAP, chain: ['soulseek', 'torrent'] };
    expect(sourceLines({ search_snapshot: flipped })[0]).toContain('soulseek');
  });

  it('says so plainly when a source ran and found nothing', () => {
    const { sourceLines } = readers();
    const snap = { chain: ['soulseek'], sources: { soulseek: { ran: true, results: 0 } } };
    expect(sourceLines({ search_snapshot: snap })).toEqual(['• soulseek: found nothing']);
  });

  it('reports usable results when a source did find something', () => {
    const { sourceLines } = readers();
    const snap = { chain: ['torrent'], sources: { torrent: { ran: true, results: 6, accepted: 2 } } };
    expect(sourceLines({ search_snapshot: snap })).toEqual(['• torrent: 2 usable of 6']);
  });

  it('falls back to the chain-less key order when no chain was stored', () => {
    const { sourceLines } = readers();
    const snap = { sources: { soulseek: { ran: true, results: 0 } } };
    expect(sourceLines({ search_snapshot: snap })).toEqual(['• soulseek: found nothing']);
  });

  it('is silent for a row that has no snapshot', () => {
    const { sourceLines } = readers();
    expect(sourceLines({})).toEqual([]);
    expect(sourceLines(null)).toEqual([]);
    expect(sourceLines({ search_snapshot: {} })).toEqual([]);
  });
});

describe('the tooltip a stuck row shows', () => {
  it('leads with the count and appends the per-source breakdown', () => {
    const { failWhy } = readers();
    const text = failWhy({ search_snapshot: SNAP, last_search_at: '2026-09-01 10:00' }, 40);
    expect(text.startsWith('40 searches without a grab · last tried 2026-09-01 10:00')).toBe(true);
    expect(text).toContain('• torrent: could not search');
    expect(text).toContain('• soulseek: 4 found');
  });

  it('still shows the single refusal line when there is no snapshot', () => {
    const { failWhy } = readers();
    const text = failWhy({ last_refusal: 'Over your 4 GB size cap' }, 12);
    expect(text).toContain('Over your 4 GB size cap');
  });

  it('still advises a next step when it knows nothing at all', () => {
    const { failWhy } = readers();
    expect(failWhy({}, 3)).toContain('try Search now');
  });

  it('does not repeat the single refusal once the breakdown covers it', () => {
    const { failWhy } = readers();
    const text = failWhy({ search_snapshot: SNAP, last_refusal: 'Over your 4 GB size cap' }, 40);
    expect(text).not.toContain('Over your 4 GB size cap');
  });
});
