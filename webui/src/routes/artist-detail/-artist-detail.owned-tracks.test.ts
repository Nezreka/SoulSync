import { describe, expect, it } from 'vitest';

import {
  checkTracksBody,
  mergeOwnership,
  ownedCount,
} from './-artist-detail.owned-tracks';

/**
 * Why the play button needs an ownership lookup at all.
 *
 * The release rows come from /api/album/<id>/tracks, which describes what the
 * release SHOULD contain and never carries a file_path. The player treats a
 * missing file_path as "download this before playing", and auto-download for
 * queue tracks is off by default, so every row failed - including albums owned
 * in full. Merging the library's answer in first is what makes the button work.
 */

const META: Array<Record<string, unknown>> = [
  { name: 'Xtal', title: 'Xtal', track_number: 1 },
  { name: 'Tha', title: 'Tha', track_number: 2 },
];

describe('the request sent to check-tracks', () => {
  it('keys tracks on name, which is what the reply is keyed by', () => {
    const body = checkTracksBody('Aphex Twin', 'Selected Ambient Works', META);
    expect(body.artist_name).toBe('Aphex Twin');
    expect(body.album_name).toBe('Selected Ambient Works');
    expect(body.tracks).toEqual([
      { name: 'Xtal', track_number: 1 },
      { name: 'Tha', track_number: 2 },
    ]);
  });

  it('falls back to title when a row has no name', () => {
    // the metadata endpoint is not consistent about which one it fills in, and
    // sending the wrong one means every lookup misses and nothing plays
    const body = checkTracksBody('A', 'B', [{ title: 'Only A Title' }]);
    expect(body.tracks).toEqual([{ name: 'Only A Title', track_number: null }]);
  });

  it('omits album_name entirely when there is none', () => {
    // the endpoint narrows by album only when asked; sending an empty string
    // would filter against an album called ""
    expect(checkTracksBody('A', '', META)).not.toHaveProperty('album_name');
  });
});

describe('merging ownership onto the queue', () => {
  it('gives an owned track the file path that lets it play', () => {
    const merged = mergeOwnership(META, {
      Xtal: { owned: true, file_path: '/music/xtal.flac', format: 'FLAC' },
      Tha: { owned: true, file_path: '/music/tha.flac', format: 'FLAC' },
    });
    expect(merged.map((t) => t.file_path)).toEqual(['/music/xtal.flac', '/music/tha.flac']);
    // is_library is the flag that skips the download flow outright
    expect(merged.every((t) => t.is_library === true)).toBe(true);
    expect(ownedCount(merged)).toBe(2);
  });

  it('leaves an unowned track alone so it still reaches the download path', () => {
    const merged = mergeOwnership(META, { Xtal: { owned: true, file_path: '/music/xtal.flac' } });
    expect(merged[0].file_path).toBe('/music/xtal.flac');
    expect(merged[1]).not.toHaveProperty('file_path');
    expect(ownedCount(merged)).toBe(1);
  });

  it('ignores an owned flag with no path behind it', () => {
    // owned-but-unplayable is worse than unowned: it would be handed to the
    // player as ready and fail at load instead of queueing a download
    const merged = mergeOwnership(META, { Xtal: { owned: true, file_path: null } });
    expect(merged[0]).not.toHaveProperty('file_path');
    expect(ownedCount(merged)).toBe(0);
  });

  it('returns the tracks untouched when the lookup gave nothing', () => {
    expect(mergeOwnership(META, null)).toBe(META);
    expect(mergeOwnership(META, {})).toEqual(META);
  });
});
