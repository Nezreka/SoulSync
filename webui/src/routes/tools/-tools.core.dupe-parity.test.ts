/**
 * Differential test: the UI's "Keep Best" ranking vs the backend's.
 *
 * The fixture is generated from core/library/duplicate_keep.py by
 * tests/test_duplicate_keep_ui_parity.py, which also asserts it still matches
 * the Python. So if the backend changes, the Python test fails; if the
 * TypeScript drifts, this one does. That pincer is the point — the backend
 * docstring records that these two implementations diverged once before, and
 * the consequence was the lossless copy getting deleted instead of the MP3.
 */

import { describe, expect, it } from 'vitest';

import {
  duplicateFormatRank,
  duplicateSortKey,
  pickDuplicateToKeep,
  type DuplicateTrackLike,
} from './-tools.core';
import fixture from './duplicate-keep-parity.json';

interface ParityGroup {
  name: string;
  tracks: Array<DuplicateTrackLike & { id: string }>;
  keys: number[][];
  keeper_id: string | null;
}

const rankByPath = fixture.rank_by_path as Record<string, number>;
const groups = fixture.groups as ParityGroup[];

describe('duplicate keep — parity with core/library/duplicate_keep.py', () => {
  it('the fixture actually carries cases (a silently empty file would pass everything)', () => {
    expect(Object.keys(rankByPath).length).toBeGreaterThanOrEqual(18);
    expect(groups.length).toBeGreaterThanOrEqual(14);
  });

  it.each(Object.entries(rankByPath))('ranks %j as the backend does', (path, expected) => {
    expect(duplicateFormatRank(path)).toBe(expected);
  });

  it.each(groups.map((group) => [group.name, group] as const))(
    'picks the same keeper: %s',
    (_name, group) => {
      const keeper = pickDuplicateToKeep(group.tracks);
      expect(keeper?.id ?? null).toBe(group.keeper_id);
    },
  );

  it.each(groups.map((group) => [group.name, group] as const))(
    'computes the same sort keys: %s',
    (_name, group) => {
      expect(group.tracks.map((track) => duplicateSortKey(track))).toEqual(
        group.keys.map((key) => key as [number, number, number, number]),
      );
    },
  );

  it('keeps the lossless copy even when its bitrate is missing', () => {
    // The exact regression the backend docstring describes.
    const flac = { id: 'flac', file_path: '/m/a/song.flac', duration: 210, track_number: 3 };
    const mp3 = {
      id: 'mp3',
      file_path: '/m/a/song.mp3',
      bitrate: 320,
      duration: 210,
      track_number: 3,
    };
    expect(pickDuplicateToKeep([mp3, flac])?.id).toBe('flac');
    expect(pickDuplicateToKeep([flac, mp3])?.id).toBe('flac');
  });

  it('returns null for an empty group rather than throwing', () => {
    expect(pickDuplicateToKeep([])).toBeNull();
  });

  it('breaks a total tie towards the first track, like Python max()', () => {
    const first = {
      id: 'first',
      file_path: '/m/a/x.mp3',
      bitrate: 320,
      duration: 200,
      track_number: 4,
    };
    const second = {
      id: 'second',
      file_path: '/m/a/y.mp3',
      bitrate: 320,
      duration: 200,
      track_number: 4,
    };
    expect(pickDuplicateToKeep([first, second])?.id).toBe('first');
    expect(pickDuplicateToKeep([second, first])?.id).toBe('second');
  });
});
