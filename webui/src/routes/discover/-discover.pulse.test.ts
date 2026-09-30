import { describe, expect, it } from 'vitest';

import {
  dayLetter,
  GAP_MIN_PLAYED_PCT,
  GAP_MIN_RATIO,
  pickSpotlight,
  releaseKind,
  shortDate,
  tasteGap,
  tasteGapLine,
  weekSummary,
  type WeekStats,
} from './-discover.pulse';

// the shape of a real stats_cache_7d, trimmed
const WEEK: WeekStats = {
  success: true,
  overview: { total_plays: 2333, unique_artists: 361 },
  previous: { total_plays: 5129 },
  top_artists: [
    { name: 'Oliver Tree', play_count: 265, image_url: '/api/image-cache/abc', id: '242758' },
    { name: 'The Beatles', play_count: 224 },
  ],
  top_tracks: [
    { name: 'MANSA MUSA', artist: 'ero808', play_count: 39 },
    { name: '', artist: 'nobody' },
    { name: '600 Degrees', artist: 'HNTR', play_count: 30 },
  ],
  timeline: [
    { date: '2026-09-22', plays: 216 },
    { date: '2026-09-23', plays: 141 },
    { date: '2026-09-24', plays: 110 },
    { date: '2026-09-25', plays: 64 },
    { date: '2026-09-26', plays: 303 },
    { date: '2026-09-27', plays: 649 },
    { date: '2026-09-28', plays: 758 },
    { date: '2026-09-29', plays: 92 },
  ],
  genres: [{ genre: 'Electronic', percentage: 17.6 }],
  rhythm: { current_streak: 8 },
  own_vs_play: [
    { genre: 'Electronic', owned_pct: 5.9, played_pct: 16.7, gap: 10.8 },
    { genre: 'Psychedelic rock', owned_pct: 0.2, played_pct: 7.4, gap: 7.2 },
    { genre: 'Alternative', owned_pct: 4.1, played_pct: 10.7, gap: 6.6 },
  ],
};

describe('weekSummary', () => {
  it('reads the week off the cached stats', () => {
    const w = weekSummary(WEEK)!;
    expect(w.plays).toBe(2333);
    expect(w.artists).toBe(361);
    expect(w.streak).toBe(8);
    expect(w.topArtist?.name).toBe('Oliver Tree');
    expect(w.topGenre).toBe('Electronic');
  });

  it('compares with the week before as a whole percent', () => {
    // (2333 - 5129) / 5129 = -54.5%
    expect(weekSummary(WEEK)!.change).toBe(-55);
    expect(weekSummary({ ...WEEK, previous: { total_plays: 0 } })!.change).toBeNull();
    expect(weekSummary({ ...WEEK, previous: undefined })!.change).toBeNull();
  });

  it('keeps the last seven days, each scaled to the busiest', () => {
    const days = weekSummary(WEEK)!.days;
    expect(days.map((d) => d.date)).toEqual([
      '2026-09-23',
      '2026-09-24',
      '2026-09-25',
      '2026-09-26',
      '2026-09-27',
      '2026-09-28',
      '2026-09-29',
    ]);
    expect(days.find((d) => d.date === '2026-09-28')!.share).toBe(1);
    expect(days[0].share).toBeCloseTo(141 / 758);
  });

  it('only offers tracks it can name', () => {
    expect(weekSummary(WEEK)!.topTracks.map((t) => t.name)).toEqual(['MANSA MUSA', '600 Degrees']);
  });

  it('is null when there is no listening to talk about', () => {
    expect(weekSummary(null)).toBeNull();
    expect(weekSummary({ success: true })).toBeNull();
    expect(weekSummary({ ...WEEK, overview: { total_plays: 0 } })).toBeNull();
  });
});

describe('dayLetter', () => {
  it('reads the weekday off the date itself', () => {
    expect(dayLetter('2026-09-28')).toBe('M'); // a monday
    expect(dayLetter('2026-09-27')).toBe('S');
    expect(dayLetter('2026-10-01')).toBe('T');
    expect(dayLetter('nope')).toBe('');
  });
});

describe('tasteGap', () => {
  it('picks the genre played furthest past what you own', () => {
    const g = tasteGap(WEEK)!;
    expect(g.genre).toBe('Electronic');
    expect(g.ratio).toBe(3); // 16.7 / 5.9 = 2.8
  });

  it('ignores genres too small to matter, or owned in proportion', () => {
    expect(GAP_MIN_PLAYED_PCT).toBe(5);
    expect(GAP_MIN_RATIO).toBe(2);
    expect(
      tasteGap({ own_vs_play: [{ genre: 'Jazz', owned_pct: 0.1, played_pct: 4.9 }] }),
    ).toBeNull();
    expect(
      tasteGap({ own_vs_play: [{ genre: 'Rock', owned_pct: 6, played_pct: 11.9 }] }),
    ).toBeNull();
    expect(
      tasteGap({ own_vs_play: [{ genre: 'Rock', owned_pct: 6, played_pct: 12 }] })?.genre,
    ).toBe('Rock');
    expect(tasteGap(null)).toBeNull();
  });

  it('owning none of a genre you play is a gap with no ratio', () => {
    const g = tasteGap({ own_vs_play: [{ genre: 'Vaporwave', owned_pct: 0, played_pct: 8 }] })!;
    expect(g.ratio).toBe(0);
    expect(tasteGapLine(g)).toBe("You play it, but you don't own any of it yet");
    expect(tasteGapLine({ ...g, ratio: 3 })).toBe('You play it 3× more than you collect it');
  });
});

describe('pickSpotlight', () => {
  const albums = [
    {
      album_name: 'POUR IT OUT',
      artist_name: 'ero808',
      album_cover_url: 'a.jpg',
      album_type: 'single',
      release_date: '2026-09-25',
    },
    {
      album_name: 'I Wrote You A Letter',
      artist_name: 'M83',
      album_cover_url: 'b.jpg',
      album_type: 'album',
      release_date: '2026-09-25',
    },
    {
      album_name: 'Owned Already',
      artist_name: 'Oliver Tree',
      album_cover_url: 'c.jpg',
      album_type: 'album',
      in_library: true,
    },
    { album_name: 'No Art', artist_name: 'Oliver Tree', album_type: 'album' },
    {
      album_name: 'Older Album',
      artist_name: 'M83',
      album_cover_url: 'd.jpg',
      album_type: 'album',
      release_date: '2026-09-01',
    },
  ];

  it('spotlights the artist you played most this week', () => {
    const s = pickSpotlight(albums, [
      { name: 'ERO808', play_count: 39 },
      { name: 'M83', play_count: 3 },
    ])!;
    expect(s.album.album_name).toBe('POUR IT OUT');
    expect(s.index).toBe(0);
    expect(s.reason).toBe('You played ero808 39 times this week');
  });

  it('never spotlights something you own, or something without art', () => {
    const s = pickSpotlight(albums, [{ name: 'Oliver Tree', play_count: 265 }])!;
    expect(s.album.artist_name).not.toBe('Oliver Tree');
  });

  it('with no plays behind any of them: the newest full album', () => {
    const s = pickSpotlight(albums, [])!;
    expect(s.album.album_name).toBe('I Wrote You A Letter');
    expect(s.plays).toBe(0);
    expect(s.reason).toBe('New from M83');
  });

  it('is null with nothing to show', () => {
    expect(pickSpotlight([])).toBeNull();
  });
});

describe('labels', () => {
  it('words the release kind', () => {
    expect(releaseKind('album')).toBe('New album');
    expect(releaseKind('EP')).toBe('New EP');
    expect(releaseKind('single')).toBe('New single');
    expect(releaseKind('compilation')).toBe('New compilation');
    expect(releaseKind(undefined)).toBe('New album');
  });

  it('shortens a date without drifting across timezones', () => {
    expect(shortDate('2026-09-25')).toBe('Sep 25');
    expect(shortDate('2026-01-01T00:00:00')).toBe('Jan 1');
    expect(shortDate(undefined)).toBe('');
  });
});
