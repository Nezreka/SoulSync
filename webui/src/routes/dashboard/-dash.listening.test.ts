import { describe, expect, it } from 'vitest';

import { sourceLabel, timeAgo, toRecentPlays } from './-dash.listening';

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
  it('shapes rows, drops untitled ones, and respects the limit', () => {
    const rows = [
      { title: 'Windowlicker', artist: 'Aphex Twin', played_at: '2026-08-12 11:00:00', server_source: 'plex', image_url: '/art/1' },
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
      imageUrl: '/art/1',
      ago: '1h ago',
      source: 'Plex',
    });
    // The blank-titled row is dropped, so Flim is second despite the limit.
    expect(plays[1].title).toBe('Flim');
    expect(plays[1].imageUrl).toBeNull();
  });
});
