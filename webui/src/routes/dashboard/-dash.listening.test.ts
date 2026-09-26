import { describe, expect, it } from 'vitest';

import { playsCaption, sourceLabel, timeAgo, toRecentPlays } from './-dash.listening';

const NOW = new Date('2026-08-12T12:00:00Z');

describe('timeAgo', () => {
  it('parses DB-UTC stamps and buckets coarsely', () => {
    expect(timeAgo('2026-08-12 11:59:40', NOW)).toBe('just now');
    expect(timeAgo('2026-08-12 11:12:00', NOW)).toBe('48m ago');
    expect(timeAgo('2026-08-12 03:00:00', NOW)).toBe('9h ago');
    expect(timeAgo('2026-08-09 12:00:00', NOW)).toBe('3d ago');
  });

  it('is empty for absent or unparseable stamps — the row renders without a time', () => {
    expect(timeAgo(null, NOW)).toBe('');
    expect(timeAgo('garbage', NOW)).toBe('');
  });
});

describe('sourceLabel', () => {
  it('maps the known servers and passes unknowns through', () => {
    expect(sourceLabel('plex')).toBe('Plex');
    expect(sourceLabel('web_player')).toBe('SoulSync');
    expect(sourceLabel('winamp')).toBe('winamp');
    expect(sourceLabel(null)).toBe('');
  });
});

describe('toRecentPlays', () => {
  it('carries the library artist id through when the play was matched', () => {
    const plays = toRecentPlays(
      [{ title: 'T', artist: 'A', played_at: '2026-08-12 11:00:00', artist_db_id: 'art_9' }],
      NOW,
      5,
    );
    expect(plays[0].artistDbId).toBe('art_9');
  });

  it('shapes rows, drops untitled ones, and respects the limit', () => {
    const rows = [
      {
        title: 'Windowlicker',
        artist: 'Aphex Twin',
        album: 'Windowlicker EP',
        played_at: '2026-08-12 11:00:00',
        server_source: 'plex',
        image_url: '/art/1',
      },
      { title: '   ', artist: 'Nobody', played_at: '2026-08-12 10:00:00' },
      { title: 'Flim', artist: 'Aphex Twin', played_at: '2026-08-12 09:00:00', image_url: null },
      { title: 'Alberto Balsalm', artist: 'Aphex Twin', played_at: '2026-08-12 08:00:00' },
    ];
    const plays = toRecentPlays(rows, NOW, 2);
    expect(plays).toHaveLength(2);
    expect(plays[0]).toEqual({
      key: 'Windowlicker|Aphex Twin|2026-08-12 11:00:00',
      title: 'Windowlicker',
      artist: 'Aphex Twin',
      // Carried for playback: the album sharpens the streaming search when
      // the library has no copy of the track.
      album: 'Windowlicker EP',
      imageUrl: '/art/1',
      ago: '1h ago',
      source: 'Plex',
      artistDbId: null,
      plays: 1,
    });
    // The blank-titled row is dropped, so Flim is second despite the limit.
    expect(plays[1].title).toBe('Flim');
    expect(plays[1].imageUrl).toBeNull();
    // A ledger row with no album is '' — never undefined, so the playback
    // call always has a string to pass.
    expect(plays[1].album).toBe('');
  });
});

describe('back to back repeats', () => {
  const at = (h: number) => `2026-08-12 ${String(h).padStart(2, '0')}:00:00`;

  it('fold into one card that keeps the newest time and counts the plays', () => {
    const plays = toRecentPlays(
      [
        { title: 'Tunnel', artist: 'DNA', played_at: at(11) },
        { title: 'Tunnel', artist: 'DNA', played_at: at(10) },
        // the ledger's casing drifts between servers, it's still the same song
        { title: 'LÉTAL', artist: 'Théa', played_at: at(9) },
        { title: 'létal', artist: 'THÉA', played_at: at(8) },
        { title: 'LÉTAL', artist: 'Théa', played_at: at(7) },
      ],
      NOW,
      25,
    );
    expect(plays.map((p) => [p.title, p.plays, p.ago])).toEqual([
      ['Tunnel', 2, '1h ago'],
      ['LÉTAL', 3, '3h ago'],
    ]);
  });

  it('only fold when back to back, a song you came back to keeps its own card', () => {
    const plays = toRecentPlays(
      [
        { title: 'Tunnel', artist: 'DNA', played_at: at(11) },
        { title: 'Await', artist: 'DJ Dave', played_at: at(10) },
        { title: 'Tunnel', artist: 'DNA', played_at: at(9) },
      ],
      NOW,
      25,
    );
    expect(plays.map((p) => p.plays)).toEqual([1, 1, 1]);
  });

  it('a different artist with the same title is a different song', () => {
    const plays = toRecentPlays(
      [
        { title: 'Intro', artist: 'The xx', played_at: at(11) },
        { title: 'Intro', artist: 'M83', played_at: at(10) },
      ],
      NOW,
      25,
    );
    expect(plays).toHaveLength(2);
  });

  it('the limit counts cards, not rows', () => {
    const plays = toRecentPlays(
      [
        { title: 'A', artist: 'X', played_at: at(11) },
        { title: 'A', artist: 'X', played_at: at(10) },
        { title: 'B', artist: 'X', played_at: at(9) },
        { title: 'C', artist: 'X', played_at: at(8) },
      ],
      NOW,
      2,
    );
    expect(plays.map((p) => p.title)).toEqual(['A', 'B']);
  });
});

describe('playsCaption', () => {
  it('shows the count only for a folded repeat', () => {
    expect(playsCaption({ ago: '3h ago', plays: 1 })).toBe('3h ago');
    expect(playsCaption({ ago: '3h ago', plays: 2 })).toBe('3h ago · ×2');
    expect(playsCaption({ ago: '', plays: 2 })).toBe('×2');
    expect(playsCaption({ ago: '', plays: 1 })).toBe('');
  });
});
