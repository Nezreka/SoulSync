import { describe, expect, it } from 'vitest';

import type { LevelPrefs, Loudness } from './loudness';
import type { PlayItem } from './types';

import {
  DEFAULT_LEVEL_PREFS,
  dbToLinear,
  inAlbumContext,
  levelPrefsFrom,
  loudnessFromHeaders,
  peakCeiling,
  songLevelDb,
  videoLevelDb,
} from './loudness';

const loud = (over: Partial<Loudness> = {}): Loudness => ({
  track_gain: -7,
  track_peak: 0.5,
  album_gain: -9,
  album_peak: 0.6,
  reference_lufs: -18,
  ...over,
});
const prefs = (over: Partial<LevelPrefs> = {}): LevelPrefs => ({ ...DEFAULT_LEVEL_PREFS, ...over });
const song = (id: string, album?: string, n?: number, disc?: number): PlayItem => ({
  type: 'track',
  id,
  title: id,
  parentId: album,
  trackNumber: n,
  discNumber: disc,
});
const q = (items: PlayItem[], index: number, shuffle = false) => ({ items, index, shuffle });

describe('song levelling', () => {
  it('track, album and off use the right gain', () => {
    expect(songLevelDb(loud(), prefs({ mode: 'track' }), true)).toBe(-7);
    expect(songLevelDb(loud(), prefs({ mode: 'album' }), false)).toBe(-9);
    expect(songLevelDb(loud(), prefs({ mode: 'off' }), true)).toBe(0);
  });

  it('smart takes the album gain only inside an album', () => {
    expect(songLevelDb(loud(), prefs({ mode: 'smart' }), true)).toBe(-9);
    expect(songLevelDb(loud(), prefs({ mode: 'smart' }), false)).toBe(-7);
  });

  it('target and pre-amp move every gain, and a library written hotter is brought back', () => {
    expect(songLevelDb(loud(), prefs({ mode: 'track', target: -14 }), false)).toBe(-3);
    expect(songLevelDb(loud(), prefs({ mode: 'track', preamp: 2.5 }), false)).toBe(-4.5);
    // gains written against -14 (#1060): the player still lands on its own -18
    expect(songLevelDb(loud({ reference_lufs: -14 }), prefs({ mode: 'track' }), false)).toBe(-11);
  });

  it('a missing album gain falls back to the track gain and the other way round', () => {
    expect(songLevelDb(loud({ album_gain: null }), prefs({ mode: 'album' }), true)).toBe(-7);
    expect(songLevelDb(loud({ track_gain: null }), prefs({ mode: 'track' }), false)).toBe(-9);
  });

  it('clipping prevention caps a boost at the peak', () => {
    // +8 dB asked, the peak (0.5 = -6 dBFS) allows 6.02
    const quiet = loud({ track_gain: 8, track_peak: 0.5 });
    expect(songLevelDb(quiet, prefs({ mode: 'track' }), false)).toBeCloseTo(6.02, 2);
    expect(songLevelDb(quiet, prefs({ mode: 'track', preventClipping: false }), false)).toBe(8);
    // the album's peak guards the album gain
    const album = loud({ album_gain: 5, album_peak: 0.9 });
    expect(songLevelDb(album, prefs({ mode: 'album' }), true)).toBeCloseTo(0.92, 2);
  });

  it('no peak known: never turned up, still turned down', () => {
    expect(
      songLevelDb(loud({ track_gain: 4, track_peak: null }), prefs({ mode: 'track' }), false),
    ).toBe(0);
    expect(
      songLevelDb(loud({ track_gain: -4, track_peak: null }), prefs({ mode: 'track' }), false),
    ).toBe(-4);
  });

  it('a song not measured yet is turned down a little, never up', () => {
    expect(songLevelDb(null, prefs(), false)).toBe(-6);
    expect(songLevelDb(null, prefs({ target: -14 }), false)).toBe(-2);
    expect(songLevelDb(null, prefs({ target: -10, preamp: 6 }), false)).toBe(0);
  });

  it('peak ceiling and linear gain', () => {
    expect(peakCeiling(1)).toBe(0);
    expect(peakCeiling(null)).toBeNull();
    expect(peakCeiling(0.5)).toBeCloseTo(6.02, 2);
    expect(dbToLinear(-6.0206)).toBeCloseTo(0.5, 3);
  });
});

describe('album context (smart)', () => {
  const album = [song('1', 'a', 1), song('2', 'a', 2), song('3', 'a', 3)];

  it('an album in order is album levelled, first to last', () => {
    expect(inAlbumContext(q(album, 0))).toBe(true);
    expect(inAlbumContext(q(album, 1))).toBe(true);
    expect(inAlbumContext(q(album, 2))).toBe(true);
  });

  it('shuffle, a lone song, and a playlist of mixed albums are song levelled', () => {
    expect(inAlbumContext(q(album, 1, true))).toBe(false);
    expect(inAlbumContext(q([song('1', 'a', 1)], 0))).toBe(false);
    expect(inAlbumContext(q([song('1', 'a', 1), song('9', 'b', 4), song('2', 'c', 2)], 1))).toBe(
      false,
    );
  });

  it('songs of one album out of order are not an album playing', () => {
    expect(inAlbumContext(q([song('3', 'a', 3), song('1', 'a', 1)], 0))).toBe(false);
    expect(inAlbumContext(q([song('3', 'a', 3), song('1', 'a', 1)], 1))).toBe(false);
  });

  it('disc two follows disc one, and no track numbers means the page order counts', () => {
    expect(inAlbumContext(q([song('9', 'a', 12, 1), song('10', 'a', 1, 2)], 1))).toBe(true);
    expect(inAlbumContext(q([song('1', 'a'), song('2', 'a')], 0))).toBe(true);
  });

  it('an album run inside a playlist is levelled as an album', () => {
    const list = [song('x', 'z', 5), song('1', 'a', 1), song('2', 'a', 2), song('y', 'q', 1)];
    expect(inAlbumContext(q(list, 1))).toBe(true);
    expect(inAlbumContext(q(list, 2))).toBe(true);
    expect(inAlbumContext(q(list, 3))).toBe(false);
  });

  it('the same song twice is not an album', () => {
    expect(inAlbumContext(q([song('1', 'a', 1), song('1', 'a', 1)], 0))).toBe(false);
  });
});

describe('video levelling', () => {
  const film = { integrated_lufs: -31, true_peak_db: -8, audio_stream: 0 };

  it('off unless asked for, and only for the track that was measured', () => {
    expect(videoLevelDb(film, prefs(), 0)).toBe(0);
    expect(videoLevelDb(film, prefs({ video: true }), 1)).toBe(0);
    expect(videoLevelDb(null, prefs({ video: true }), 0)).toBe(0);
  });

  it('lifts a quiet film toward -23, capped by its peak and by the most it may be lifted', () => {
    expect(videoLevelDb(film, prefs({ video: true }), 0)).toBe(8);
    expect(videoLevelDb({ ...film, true_peak_db: -3 }, prefs({ video: true }), 0)).toBe(3);
    expect(
      videoLevelDb({ ...film, integrated_lufs: -45, true_peak_db: -30 }, prefs({ video: true }), 0),
    ).toBe(10);
  });

  it('turns a loud video down', () => {
    expect(
      videoLevelDb({ integrated_lufs: -11, true_peak_db: 0.5 }, prefs({ video: true }), 0),
    ).toBe(-12);
  });
});

describe('settings and headers', () => {
  it('reads the profile settings, junk as the default', () => {
    expect(levelPrefsFrom(null)).toEqual(DEFAULT_LEVEL_PREFS);
    expect(
      levelPrefsFrom({
        loudness_mode: 'album',
        loudness_target: -14,
        loudness_preamp: 1.5,
        loudness_prevent_clipping: false,
        video_loudness: true,
      }),
    ).toEqual({ mode: 'album', target: -14, preamp: 1.5, preventClipping: false, video: true });
    expect(levelPrefsFrom({ loudness_mode: 'loud' }).mode).toBe('smart');
  });

  it('reads loudness off a pre-cached song', () => {
    const h = new Headers({
      'X-Loudness-Reference': '-18.00',
      'X-ReplayGain-Track-Gain': '-7.31',
      'X-ReplayGain-Track-Peak': '0.988000',
    });
    expect(loudnessFromHeaders(h)).toEqual({
      track_gain: -7.31,
      track_peak: 0.988,
      album_gain: null,
      album_peak: null,
      reference_lufs: -18,
    });
    expect(loudnessFromHeaders(new Headers())).toBeNull();
  });
});
