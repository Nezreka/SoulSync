import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  buildEnrichmentRings,
  ENRICHMENT_SERVICES,
  RING_CIRCUMFERENCE,
  shouldShowEnrichment,
  visibleEnrichmentServices,
} from './-artist-detail.enrichment';

afterEach(() => {
  delete window.filterJiosaavnServiceEntries;
});

describe('shouldShowEnrichment', () => {
  it('hides the panel without data, or with zero tracks', () => {
    // A coverage ring over zero tracks means nothing.
    expect(shouldShowEnrichment(undefined)).toBe(false);
    expect(shouldShowEnrichment({})).toBe(false);
    expect(shouldShowEnrichment({ total_tracks: 0 })).toBe(false);
    expect(shouldShowEnrichment({ total_tracks: 12 })).toBe(true);
  });
});

describe('visibleEnrichmentServices', () => {
  it('defers to the shared helper when it exists', () => {
    // shared-helpers.js owns the experimental-source flag; do not duplicate it.
    window.filterJiosaavnServiceEntries = vi.fn((items) => items) as never;
    expect(visibleEnrichmentServices().map((s) => s.key)).toContain('jiosaavn');
    expect(window.filterJiosaavnServiceEntries).toHaveBeenCalled();
  });

  it('drops JioSaavn when the helper is unavailable — the safe default', () => {
    expect(visibleEnrichmentServices().map((s) => s.key)).not.toContain('jiosaavn');
  });

  it('keeps every other service, in declaration order', () => {
    expect(visibleEnrichmentServices().map((s) => s.key)).toEqual([
      'spotify',
      'musicbrainz',
      'deezer',
      'lastfm',
      'itunes',
      'audiodb',
      'discogs',
      'genius',
      'tidal',
      'qobuz',
      'bandcamp',
    ]);
  });
});

describe('buildEnrichmentRings', () => {
  const two = [
    { name: 'Spotify', key: 'spotify', color: '#1db954' },
    { name: 'Deezer', key: 'deezer', color: '#a238ff' },
  ];

  it('leaves a full ring at zero offset and an empty one at full circumference', () => {
    const [full] = buildEnrichmentRings({ spotify: 100 }, [two[0]]);
    expect(full.offset).toBe('0.0');
    const [empty] = buildEnrichmentRings({ spotify: 0 }, [two[0]]);
    expect(empty.offset).toBe(RING_CIRCUMFERENCE.toFixed(1));
  });

  it('offsets proportionally — half coverage leaves half the ring empty', () => {
    const [half] = buildEnrichmentRings({ spotify: 50 }, [two[0]]);
    expect(half.offset).toBe((RING_CIRCUMFERENCE / 2).toFixed(1));
  });

  it('treats a missing service as 0, not NaN', () => {
    const [ring] = buildEnrichmentRings({}, [two[0]]);
    expect(ring.pct).toBe(0);
    expect(ring.label).toBe(0);
    expect(ring.offset).not.toContain('NaN');
  });

  it('rounds the displayed number but keeps the raw pct for the geometry', () => {
    const [ring] = buildEnrichmentRings({ spotify: 66.6 }, [two[0]]);
    expect(ring.label).toBe(67);
    expect(ring.pct).toBeCloseTo(66.6);
  });

  it('staggers by 0.08s, with the percentage text 0.3s behind its ring', () => {
    const rings = buildEnrichmentRings({ spotify: 10, deezer: 20 }, two);
    expect(rings.map((r) => r.delay)).toEqual(['0.00', '0.08']);
    expect(rings.map((r) => r.pctDelay)).toEqual(['0.30', '0.38']);
  });

  it('uses one circumference for both the dash array and the offset', () => {
    const [ring] = buildEnrichmentRings({ spotify: 25 }, [two[0]]);
    expect(ring.dashArray).toBe(RING_CIRCUMFERENCE.toFixed(1));
  });

  it('covers every service the vanilla listed', () => {
    expect(ENRICHMENT_SERVICES).toHaveLength(12);
  });

  it('pins the ring geometry to absolute numbers', () => {
    // Everything else here is expressed RELATIVE to RING_CIRCUMFERENCE, so a
    // wrong constant would move both sides of those assertions together and
    // go unnoticed. r = 20 => 2*pi*20 = 125.66...
    expect(RING_CIRCUMFERENCE.toFixed(1)).toBe('125.7');
    const [quarter] = buildEnrichmentRings({ spotify: 25 }, [two[0]]);
    expect(quarter.offset).toBe('94.2');
    expect(quarter.dashArray).toBe('125.7');
  });
});
