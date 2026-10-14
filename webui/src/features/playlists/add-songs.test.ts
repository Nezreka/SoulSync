import { describe, expect, it } from 'vitest';

import { candidatesFromRecent, candidatesFromSearch, songDuration, songKey } from './add-songs';

describe('songKey', () => {
  it('folds case, brackets and featured artists', () => {
    expect(songKey('Alright (Remastered)', 'Kendrick Lamar feat. Pharrell')).toBe(
      songKey('alright', 'kendrick lamar'),
    );
    expect(songKey('Alright', 'Kendrick Lamar')).not.toBe(songKey('Alright', 'Someone Else'));
  });
});

describe('candidatesFromSearch', () => {
  it('reads every source shape and folds the duplicates', () => {
    const rows = candidatesFromSearch([
      {
        name: 'Alright',
        artists: ['Kendrick Lamar'],
        album: 'TPAB',
        duration_ms: 219000,
        image_url: '/a.jpg',
      },
      { name: 'Alright', artist: 'Kendrick Lamar' },
      { name: 'Money Trees', artists: [{ name: 'Kendrick Lamar' }], album: { name: 'GKMC' } },
      { name: '', artist: 'x' },
    ]);
    expect(rows.map((r) => [r.track_name, r.artist_name, r.album_name])).toEqual([
      ['Alright', 'Kendrick Lamar', 'TPAB'],
      ['Money Trees', 'Kendrick Lamar', 'GKMC'],
    ]);
    expect(rows[0]).toMatchObject({ duration_ms: 219000, image_url: '/a.jpg' });
  });

  it('keeps the hit’s id and source for identify, only when it has both', () => {
    const [withId, without] = candidatesFromSearch([
      { name: 'A', artist: 'x', id: '0flx', source: 'spotify' },
      { name: 'B', artist: 'y', source: 'spotify' },
    ]);
    expect(withId).toMatchObject({ source: 'spotify', source_id: '0flx' });
    expect(without).not.toHaveProperty('source_id');
  });

  it('is empty for no results', () => {
    expect(candidatesFromSearch(undefined)).toEqual([]);
  });
});

describe('candidatesFromRecent', () => {
  it('one row per song, newest first, capped', () => {
    const rows = candidatesFromRecent(
      [
        { title: 'A', artist: 'x', image_url: '/a.jpg' },
        { title: 'A', artist: 'x' },
        { title: 'B', artist: 'y', album: 'Al' },
        { title: 'C', artist: '' },
        { title: 'D', artist: 'z' },
      ],
      2,
    );
    expect(rows.map((r) => r.track_name)).toEqual(['A', 'B']);
    expect(rows[1].album_name).toBe('Al');
  });
});

describe('songDuration', () => {
  it('m:ss, empty when unknown', () => {
    expect(songDuration(219000)).toBe('3:39');
    expect(songDuration(61000)).toBe('1:01');
    expect(songDuration(0)).toBe('');
    expect(songDuration(undefined)).toBe('');
  });
});
