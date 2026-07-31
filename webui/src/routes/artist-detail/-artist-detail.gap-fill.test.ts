import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import {
  dedupeGaps,
  gapFillEnabled,
  gapFillUrl,
  gapNorm,
  gapReleasesFromResponse,
  gapSameRelease,
  gapSourceLabel,
  gapStreamPayload,
  gapYear,
  mergeGapReleases,
  SOURCE_LABEL_TEXT,
  setGapFillEnabled,
} from './-artist-detail.gap-fill';

afterEach(() => {
  localStorage.clear();
});

describe('the gap-fill preference', () => {
  it('is off until explicitly enabled, and persists as "1"', () => {
    expect(gapFillEnabled()).toBe(false);
    setGapFillEnabled(true);
    expect(localStorage.getItem('discog_gapfill')).toBe('1');
    expect(gapFillEnabled()).toBe(true);
  });

  it('writes "0" rather than removing the key when switched off', () => {
    setGapFillEnabled(true);
    setGapFillEnabled(false);
    expect(localStorage.getItem('discog_gapfill')).toBe('0');
    expect(gapFillEnabled()).toBe(false);
  });
});

describe('gapNorm', () => {
  it('KEEPS edition parentheses', () => {
    // "Album" and "Album (Deluxe Edition)" are different releases; collapsing
    // them would hide a real gap.
    expect(gapNorm('Album (Deluxe Edition)')).toBe('album (deluxe edition)');
    expect(gapNorm('Album')).not.toBe(gapNorm('Album (Deluxe Edition)'));
  });

  it('flattens punctuation and whitespace', () => {
    expect(gapNorm('  Selected   Ambient-Works!  ')).toBe('selected ambient works');
  });

  it('is empty for junk input', () => {
    expect(gapNorm(null)).toBe('');
    expect(gapNorm(undefined)).toBe('');
  });
});

describe('gapYear', () => {
  it('prefers the year field, then the release date prefix', () => {
    expect(gapYear({ year: 1992 })).toBe(1992);
    expect(gapYear({ release_date: '1994-11-08' })).toBe(1994);
  });

  it('rejects out-of-range and unparseable values', () => {
    expect(gapYear({ year: 12 })).toBeNull();
    expect(gapYear({ year: 9999 })).toBeNull();
    expect(gapYear({})).toBeNull();
    expect(gapYear({ year: 'soon' })).toBeNull();
  });
});

describe('gapSameRelease', () => {
  it('matches on title plus a year within one', () => {
    expect(gapSameRelease({ title: 'SAW', year: 1992 }, { title: 'saw', year: 1993 })).toBe(true);
    expect(gapSameRelease({ title: 'SAW', year: 1992 }, { title: 'SAW', year: 1995 })).toBe(false);
  });

  it('treats an UNKNOWN year on either side as a match', () => {
    // The alternative is rendering the same album twice.
    expect(gapSameRelease({ title: 'SAW' }, { title: 'SAW', year: 1992 })).toBe(true);
    expect(gapSameRelease({ title: 'SAW', year: 1992 }, { title: 'SAW' })).toBe(true);
  });

  it('reads either title or name', () => {
    expect(gapSameRelease({ name: 'SAW', year: 1992 }, { title: 'SAW', year: 1992 })).toBe(true);
  });

  it('never matches on an empty title', () => {
    expect(gapSameRelease({ title: '' }, { title: '' })).toBe(false);
  });
});

describe('gapSourceLabel', () => {
  it('names the source', () => {
    expect(gapSourceLabel('itunes')).toBe('Apple Music');
    expect(gapSourceLabel('musicbrainz')).toBe('MusicBrainz');
  });

  it('falls back to the raw key for an unknown source, as the vanilla did', () => {
    expect(gapSourceLabel('newthing')).toBe('newthing');
    expect(gapSourceLabel(undefined)).toBe('');
  });
});

describe('SOURCE_LABEL_TEXT parity with shared-helpers.js', () => {
  it('matches every label in the vanilla SOURCE_LABELS map', () => {
    // SOURCE_LABELS is a top-level `const` in a classic script — a global
    // LEXICAL binding that never lands on window, so a module cannot read it
    // and this copy is the only option. This test is what keeps the copy from
    // drifting: it parses the labels straight out of the vanilla source.
    // vitest roots at webui/, and import.meta.url is not a file URL under the
    // jsdom transform.
    const source = readFileSync(resolve(process.cwd(), 'static/shared-helpers.js'), 'utf-8');
    const start = source.indexOf('const SOURCE_LABELS = {');
    expect(start).toBeGreaterThan(-1);
    const block = source.slice(start, source.indexOf('\n};', start));

    const vanilla: Record<string, string> = {};
    let key: string | null = null;
    for (const line of block.split('\n')) {
      const keyMatch = line.match(/^\s{4}([a-z_]+):\s*\{/);
      if (keyMatch) key = keyMatch[1];
      const textMatch = line.match(/text:\s*'([^']*)'/);
      if (key && textMatch) {
        vanilla[key] = textMatch[1];
        key = null;
      }
    }

    expect(Object.keys(vanilla).length).toBeGreaterThan(5);
    expect(SOURCE_LABEL_TEXT).toEqual(vanilla);
  });
});

describe('gapReleasesFromResponse', () => {
  const RESPONSE = {
    success: true,
    gaps: {
      albums: [{ id: 'a1', title: 'Gap Album', gap_source: 'deezer', year: 2001, track_count: 12 }],
      eps: [{ id: 'e1', name: 'Gap EP', gap_source: 'itunes' }],
      singles: [],
    },
  };

  it('flattens each bucket and tags it', () => {
    const releases = gapReleasesFromResponse(RESPONSE);
    expect(releases.map((r) => [r.title, r._bucket, r._gap_source])).toEqual([
      ['Gap Album', 'albums', 'deezer'],
      ['Gap EP', 'eps', 'itunes'],
    ]);
  });

  it('starts them owned:false, never null', () => {
    // A null would render as "checking" forever if the ownership stream never
    // reached that release.
    expect(gapReleasesFromResponse(RESPONSE).every((r) => r.owned === false)).toBe(true);
  });

  it('keeps the track count OFF track_count', () => {
    // The vanilla's gap card carried no track count, so the download modal fell
    // through to its "never 0" default of 1. Setting the real field would
    // silently change what the modal opens with.
    const [album] = gapReleasesFromResponse(RESPONSE);
    expect(album.track_count).toBeUndefined();
    expect(album._gap_track_count).toBe(12);
  });

  it('falls back to the bucket for a missing album_type, and names the unknown', () => {
    const [, ep] = gapReleasesFromResponse(RESPONSE);
    expect(ep.album_type).toBe('eps');
    const [nameless] = gapReleasesFromResponse({ gaps: { albums: [{ id: 'x' }] } });
    expect(nameless.title).toBe('Unknown Release');
  });

  it('survives a response with no gaps at all', () => {
    expect(gapReleasesFromResponse({})).toEqual([]);
    expect(gapReleasesFromResponse(null)).toEqual([]);
  });
});

describe('dedupeGaps', () => {
  const gaps = gapReleasesFromResponse({
    gaps: { albums: [{ id: 'g1', title: 'SAW', year: 1992, gap_source: 'deezer' }] },
  });

  it('drops a gap the page already renders — even from another bucket', () => {
    // The library-merged view can hold owned releases the base source never
    // listed; those must not come back as "missing" gap cards.
    expect(dedupeGaps(gaps, { eps: [{ title: 'SAW', year: 1992 }] })).toEqual([]);
  });

  it('keeps a gap that is genuinely absent', () => {
    expect(dedupeGaps(gaps, { albums: [{ title: 'Drukqs', year: 2001 }] })).toHaveLength(1);
  });
});

describe('mergeGapReleases', () => {
  const gap = (id: string, year: number) =>
    gapReleasesFromResponse({ gaps: { albums: [{ id, title: id, year }] } })[0];

  it('slots a gap between the releases either side of it by year', () => {
    const base = {
      albums: [
        { id: 'new', year: 2010 },
        { id: 'old', year: 1990 },
      ],
    };
    const merged = mergeGapReleases(base, [gap('mid', 2000)]);
    expect(merged.albums?.map((r) => r.id)).toEqual(['new', 'mid', 'old']);
  });

  it('sinks an unknown year to the end', () => {
    const base = { albums: [{ id: 'a', year: 2010 }] };
    const merged = mergeGapReleases(base, [gap('unknown', NaN)]);
    expect(merged.albums?.map((r) => r.id)).toEqual(['a', 'unknown']);
  });

  it('is an insertion, not a re-sort — the base keeps its own order', () => {
    // The scan stops at the FIRST older release, so against an out-of-order
    // base the gap lands early rather than triggering a global sort. Verbatim
    // vanilla behaviour: grids render newest-first, and re-sorting would
    // rearrange releases the source deliberately ordered.
    const base = {
      albums: [
        { id: 'a', year: 1990 },
        { id: 'b', year: 2010 },
      ],
    };
    const merged = mergeGapReleases(base, [gap('c', 1995)]);
    expect(merged.albums?.map((r) => r.id)).toEqual(['c', 'a', 'b']);
  });

  it('only touches the bucket a gap belongs to', () => {
    const base = { albums: [{ id: 'a' }], eps: [{ id: 'e' }] };
    const merged = mergeGapReleases(base, [gap('g', 2000)]);
    expect(merged.eps).toBe(base.eps);
    expect(merged.albums).not.toBe(base.albums);
  });

  it('returns the SAME object when there is nothing to merge', () => {
    const base = { albums: [{ id: 'a' }] };
    expect(mergeGapReleases(base, [])).toBe(base);
  });
});

describe('gapStreamPayload', () => {
  it('gives every entry its own source and leaves the top-level source null', () => {
    // A gap id is only meaningful on the source that listed it, so these cannot
    // ride the base artist's stream.
    const gaps = gapReleasesFromResponse({
      gaps: {
        albums: [{ id: 'a', title: 'A', gap_source: 'deezer', track_count: 9 }],
        singles: [{ id: 's', title: 'S', gap_source: 'itunes' }],
      },
    });
    const payload = gapStreamPayload('Aphex Twin', gaps);

    expect(payload.source).toBeNull();
    expect(payload.albums).toEqual([
      expect.objectContaining({ id: 'a', source: 'deezer', track_count: 9 }),
    ]);
    expect(payload.singles).toEqual([expect.objectContaining({ id: 's', source: 'itunes' })]);
    expect(payload.eps).toEqual([]);
  });
});

describe('gapFillUrl', () => {
  it('passes the base source so the backend can exclude it', () => {
    expect(gapFillUrl(42, 'Aphex Twin', 'spotify')).toBe(
      '/api/artist/42/discography/gap-fill?artist_name=Aphex+Twin&base_source=spotify',
    );
  });

  it('omits absent parameters rather than sending empties', () => {
    expect(gapFillUrl(42, undefined, undefined)).toBe('/api/artist/42/discography/gap-fill?');
  });

  it('encodes an id with slashes', () => {
    expect(gapFillUrl('a/b', undefined, undefined)).toContain('/api/artist/a%2Fb/');
  });
});
