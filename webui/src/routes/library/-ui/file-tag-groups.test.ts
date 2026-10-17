import { describe, expect, it } from 'vitest';

import { groupFileTags } from './library-v2-page';

describe('library v2 file tag grouping (§52.10)', () => {
  it('uses the quarantine inspector hierarchy and keeps lyrics in their own tab', () => {
    const grouped = groupFileTags({
      title: 'Song',
      album: 'Album',
      replaygain_track_gain: '-7.1 dB',
      spotify_track_id: 'sp-track',
      beatport_track_id: 'bp-track',
      custom_note: 'kept',
      lyrics: 'not duplicated here',
      quality: 'FLAC · 24bit/96kHz',
    });

    expect(grouped.track).toEqual([['title', 'Song']]);
    expect(grouped.album).toEqual([['album', 'Album']]);
    expect(grouped.replaygain).toEqual([['replaygain_track_gain', '-7.1 dB']]);
    expect(grouped.source.Spotify).toEqual([['spotify_track_id', 'sp-track']]);
    expect(grouped.source.Beatport).toEqual([['beatport_track_id', 'bp-track']]);
    expect(grouped.other).toEqual([['custom_note', 'kept']]);
  });

  it('sorts keys and drops empty values', () => {
    const grouped = groupFileTags({ genre: '', year: '2026', album: 'A' });

    expect(grouped.album).toEqual([
      ['album', 'A'],
      ['year', '2026'],
    ]);
  });
});
