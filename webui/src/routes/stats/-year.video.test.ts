import { describe, expect, it } from 'vitest';

import type { YearInListening } from './-year.types';

import {
  buildReelScenes,
  pickVideoFormat,
  reelDuration,
  sceneAt,
  videoFilename,
} from './-year.video';

const YEAR: YearInListening = {
  period: { start: '2026-01-01', end: '2026-12-03', label: 'Jan 2026 — Dec 2026', months: 12 },
  has_data: true,
  totals: { plays: 1200, minutes: 4000, artists: 30, albums: 20, tracks: 300, active_days: 200 },
  months: [],
  top_artists: [{ name: 'Top Act', plays: 300, months_on_top: 4, image_url: '/a.jpg' }],
  top_albums: [],
  top_tracks: [
    {
      name: 'Big Song',
      artist: 'Top Act',
      album: 'X',
      plays: 80,
      first_played: null,
      last_played: null,
      image_url: '/t.jpg',
    },
  ],
  discoveries: [],
  peak_day: { date: '2026-05-02', plays: 60, minutes: 200, top_artist: 'Top Act' },
  top_hour: { hour: 22, plays: 100 },
  days: [{ date: '2026-05-02', plays: 60, minutes: 200 }],
  streak: { days: 12, start: '2026-04-01', end: '2026-04-12' },
  top_genres: [{ genre: 'shoegaze', plays: 400, share: 0.4 }],
};

describe('the story video', () => {
  it('has a scene for each moment this year can fill, ending on the card', () => {
    const scenes = buildReelScenes(YEAR);
    expect(scenes.map((s) => s.kind)).toEqual([
      'title',
      'totals',
      'artist',
      'track',
      'genre',
      'streak',
      'day',
      'calendar',
      'card',
    ]);
    expect(scenes.find((s) => s.kind === 'genre')?.sub).toBe('40% of what you played');
    expect(scenes.find((s) => s.kind === 'day')?.sub).toContain('mostly Top Act');
    expect(scenes.find((s) => s.kind === 'artist')?.art).toBe('/a.jpg');
  });

  it('an empty year is the title and the card', () => {
    const scenes = buildReelScenes({ ...YEAR, has_data: false });
    expect(scenes.map((s) => s.kind)).toEqual(['title', 'card']);
  });

  it('leaves out what a year cached before the extras does not have', () => {
    const { days: _d, streak: _s, top_genres: _g, ...old } = YEAR;
    const kinds = buildReelScenes(old as YearInListening).map((s) => s.kind);
    expect(kinds).not.toContain('genre');
    expect(kinds).not.toContain('streak');
    expect(kinds).not.toContain('calendar');
  });

  it('finds the scene on screen at a time, and holds the last one', () => {
    const scenes = buildReelScenes(YEAR);
    expect(sceneAt(scenes, 0)).toMatchObject({ index: 0, progress: 0 });
    expect(sceneAt(scenes, scenes[0].ms + 1).index).toBe(1);
    const end = sceneAt(scenes, reelDuration(scenes) + 5000);
    expect(end.index).toBe(scenes.length - 1);
    expect(end.progress).toBe(1);
  });

  it('records mp4 where the browser can, else webm, else nothing', () => {
    expect(pickVideoFormat((m) => m.startsWith('video/mp4'))).toEqual({
      mime: 'video/mp4;codecs=avc1',
      ext: 'mp4',
    });
    expect(pickVideoFormat((m) => m === 'video/webm')).toEqual({ mime: 'video/webm', ext: 'webm' });
    expect(pickVideoFormat(() => false)).toBeNull();
    expect(
      pickVideoFormat(() => {
        throw new Error('unknown type');
      }),
    ).toBeNull();
  });

  it('names the video after the card', () => {
    expect(videoFilename('soulsync-year-jan-2026-stack.png', 'webm')).toBe(
      'soulsync-year-jan-2026-stack.webm',
    );
  });
});
