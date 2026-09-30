import { describe, expect, it } from 'vitest';

import type { InboxItem } from './-discover.inbox';

import {
  inkFor,
  paletteFor,
  pickConcert,
  pickPosterAlbum,
  poppy,
  POSTER_PALETTE,
  posterDay,
} from './-discover.posters';

describe('poppy', () => {
  it('keeps the hue and pushes it loud', () => {
    // a dull navy comes out a clear, saturated blue
    const [r, g, b] = poppy('40, 50, 90', 'x').split(', ').map(Number);
    expect(b).toBeGreaterThan(200);
    expect(b).toBeGreaterThan(r + 100);
    expect(b).toBeGreaterThan(g + 60);
  });

  it('a grey cover or no colour gets the fallback', () => {
    expect(poppy('120, 120, 124', 'fb')).toBe('fb');
    expect(poppy(null, 'fb')).toBe('fb');
    expect(poppy('nonsense', 'fb')).toBe('fb');
  });
});

describe('inkFor', () => {
  it('black type on bright colours, white on deep ones', () => {
    expect(inkFor('198, 244, 50')).toBe('dark');
    expect(inkFor('255, 210, 63')).toBe('dark');
    expect(inkFor('60, 40, 200')).toBe('light');
    expect(inkFor('bad')).toBe('light');
  });

  it('every palette colour reads with one of the two inks', () => {
    for (const c of POSTER_PALETTE) expect(['dark', 'light']).toContain(inkFor(c));
  });
});

describe('paletteFor', () => {
  it('the same name always gets the same colour, whatever the case', () => {
    expect(paletteFor('Calvin Harris')).toBe(paletteFor('calvin harris'));
    expect(POSTER_PALETTE).toContain(paletteFor('Calvin Harris'));
  });
});

describe('posterDay', () => {
  it('reads a date as a gig poster does', () => {
    expect(posterDay('2027-02-19')).toEqual({ month: 'FEB', day: '19', weekday: 'FRI' });
    expect(posterDay('soon')).toBeNull();
    expect(posterDay(undefined)).toBeNull();
  });
});

const show = (id: number, date: string, extra: Partial<InboxItem> = {}): InboxItem => ({
  id,
  kind: 'concert',
  title: 'Venue',
  artist_name: `Artist ${id}`,
  item_date: date,
  ...extra,
});

describe('pickConcert', () => {
  const today = new Date(2026, 8, 29);
  it('the soonest show still to come, and how many more', () => {
    const pick = pickConcert(
      [
        show(1, '2026-12-01'),
        show(2, '2026-10-04'),
        show(3, '2026-09-01'), // already happened
        show(4, '2026-11-01', { state: 'dismissed' }),
        { id: 5, kind: 'new_release', title: 'x', item_date: '2026-09-30' },
      ],
      today,
    );
    expect(pick?.item.id).toBe(2);
    expect(pick?.more).toBe(1);
  });

  it('today counts, and nothing is null', () => {
    expect(pickConcert([show(1, '2026-09-29')], today)?.item.id).toBe(1);
    expect(pickConcert([], today)).toBeNull();
    expect(pickConcert(undefined, today)).toBeNull();
  });
});

describe('pickPosterAlbum', () => {
  const a = (name: string, extra = {}) => ({
    album_name: name,
    artist_name: 'X',
    album_cover_url: '/c.jpg',
    ...extra,
  });
  it("skips the spotlight's release, owned ones and ones with no cover", () => {
    const albums = [
      a('spot'),
      a('owned', { in_library: true }),
      a('bare', { album_cover_url: '' }),
      a('this'),
    ];
    expect(pickPosterAlbum(albums, 0)).toEqual({ album: albums[3], index: 3 });
    expect(pickPosterAlbum(albums, null)?.index).toBe(0);
    expect(pickPosterAlbum([a('only')], 0)).toBeNull();
  });
});
