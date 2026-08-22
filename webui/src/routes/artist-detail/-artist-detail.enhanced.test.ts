import { afterEach, describe, expect, it } from 'vitest';

import {
  albumRowMeta,
  enhancedStats,
  extractFormat,
  groupAlbumsByType,
  libraryViewModeKey,
  readEnhancedViewMode,
  formatDurationMs,
  sectionCountLabel,
  sectionTrackTotal,
  showsEnhancedToggle,
  writeEnhancedViewMode,
} from './-artist-detail.enhanced';

afterEach(() => {
  localStorage.clear();
});

describe('the persisted view mode', () => {
  it('scopes the key to the profile so two admins keep different defaults', () => {
    expect(libraryViewModeKey(7)).toBe('soulsync-library-view-mode:7');
  });

  it('falls back to the UNSUFFIXED key with no profile', () => {
    // That key is what pre-multi-profile installs already have saved.
    expect(libraryViewModeKey(null)).toBe('soulsync-library-view-mode');
    expect(libraryViewModeKey(undefined)).toBe('soulsync-library-view-mode');
  });

  it('keeps profile 0 scoped rather than treating it as absent', () => {
    expect(libraryViewModeKey(0)).toBe('soulsync-library-view-mode:0');
  });

  it('round-trips both directions and writes "standard" explicitly', () => {
    writeEnhancedViewMode(7, true);
    expect(localStorage.getItem('soulsync-library-view-mode:7')).toBe('enhanced');
    expect(readEnhancedViewMode(7)).toBe(true);

    writeEnhancedViewMode(7, false);
    expect(localStorage.getItem('soulsync-library-view-mode:7')).toBe('standard');
    expect(readEnhancedViewMode(7)).toBe(false);
  });

  it("does not leak one profile's choice to another", () => {
    writeEnhancedViewMode(1, true);
    expect(readEnhancedViewMode(2)).toBe(false);
  });

  it('defaults to Standard when nothing is stored', () => {
    expect(readEnhancedViewMode(7)).toBe(false);
  });
});

describe('showsEnhancedToggle', () => {
  it('is admin-only and library-only', () => {
    expect(showsEnhancedToggle(true, false)).toBe(true);
    expect(showsEnhancedToggle(false, false)).toBe(false);
    // Forcing it on a source artist showed an empty pane and hid the
    // discography — there is no DB record behind it to edit.
    expect(showsEnhancedToggle(true, true)).toBe(false);
  });
});

describe('extractFormat', () => {
  it('maps the known extensions, m4a and aac both to AAC', () => {
    expect(extractFormat('/m/a.flac')).toBe('FLAC');
    expect(extractFormat('/m/a.mp3')).toBe('MP3');
    expect(extractFormat('/m/a.m4a')).toBe('AAC');
    expect(extractFormat('/m/a.aac')).toBe('AAC');
    expect(extractFormat('/m/a.opus')).toBe('OPUS');
  });

  it('is case-insensitive about the extension', () => {
    expect(extractFormat('/m/a.FLAC')).toBe('FLAC');
  });

  it('uppercases an unmapped extension rather than dropping it', () => {
    expect(extractFormat('/m/a.aiff')).toBe('AIFF');
  });

  it('returns a dash — not a format — when there is no path', () => {
    expect(extractFormat('')).toBe('-');
    expect(extractFormat(null)).toBe('-');
    expect(extractFormat(undefined)).toBe('-');
  });

  it('uses the LAST dot, so a dotted directory does not confuse it', () => {
    expect(extractFormat('/m/v1.0/track.flac')).toBe('FLAC');
  });
});

describe('groupAlbumsByType', () => {
  it('always returns the three known buckets, even when empty', () => {
    expect(groupAlbumsByType([])).toEqual({ album: [], ep: [], single: [] });
  });

  it('treats a missing record_type as an album', () => {
    expect(groupAlbumsByType([{ title: 'X' }]).album).toHaveLength(1);
  });

  it('LOWERCASES the type, so "EP" lands in the EPs section', () => {
    expect(groupAlbumsByType([{ record_type: 'EP' }]).ep).toHaveLength(1);
  });

  it('gives an unknown type its own bucket, and accumulates into it', () => {
    const grouped = groupAlbumsByType([{ record_type: 'live' }, { record_type: 'live' }]);
    expect(grouped.live).toHaveLength(2);
  });
});

describe('enhancedStats', () => {
  const DATA = {
    albums: [
      { record_type: 'album', tracks: [{ duration: 200000, file_path: 'a.flac' }] },
      { tracks: [{ duration: 100000, file_path: 'b.flac' }, { file_path: 'c.mp3' }] },
      { record_type: 'ep', tracks: [{ file_path: 'd.mp3' }] },
      { record_type: 'single', tracks: [] },
    ],
  };

  it('counts a MISSING record_type as an album', () => {
    expect(enhancedStats(DATA).items[0]).toEqual({ value: 2, label: 'Albums' });
  });

  it('counts EPs and singles by strict equality', () => {
    const stats = enhancedStats(DATA);
    expect(stats.items[1].value).toBe(1);
    expect(stats.items[2].value).toBe(1);
  });

  it('counts an uppercase "EP" as an EP, like the section it renders in', () => {
    // Was pinned the other way during the port: the stats bar compared strictly
    // while groupAlbumsByType lowercased, so this row rendered under EPs and
    // counted as none of the three. The number and the list under it now come
    // from one classifier (TheHomeGuy, Aug 2026).
    const stats = enhancedStats({ albums: [{ record_type: 'EP', tracks: [] }] });
    expect(stats.items.slice(0, 3).map((s) => s.value)).toEqual([0, 1, 0]);
    expect(groupAlbumsByType([{ record_type: 'EP' }]).ep).toHaveLength(1);
  });

  it('folds deezer\'s "compile" into compilation so they are one bucket', () => {
    const grouped = groupAlbumsByType([{ record_type: 'compile' }, { record_type: 'compilation' }]);
    expect(grouped.compilation).toHaveLength(2);
    expect(grouped.compile).toBeUndefined();
  });

  it('does not count a compilation as an album', () => {
    const stats = enhancedStats({ albums: [{ record_type: 'compilation', tracks: [] }] });
    expect(stats.items.slice(0, 3).map((s) => s.value)).toEqual([0, 0, 0]);
  });

  it('totals tracks across albums, tolerating an album with none', () => {
    expect(enhancedStats(DATA).items[3].value).toBe(4);
  });

  it('formats the duration, dropping the hour part below an hour', () => {
    expect(enhancedStats(DATA).items[4].value).toBe('5m');
    expect(enhancedStats({ albums: [{ tracks: [{ duration: 3_900_000 }] }] }).items[4].value).toBe(
      '1h 5m',
    );
  });

  it('counts formats and puts the commonest first', () => {
    const badges = enhancedStats(DATA).badges;
    expect(badges).toEqual([
      { format: 'FLAC', count: 2, className: 'flac' },
      { format: 'MP3', count: 2, className: 'mp3' },
    ]);
  });

  it('sorts by COUNT, not by the order the formats were first seen', () => {
    // FLAC appears first but only once; MP3 must still lead the badge row.
    const badges = enhancedStats({
      albums: [
        {
          tracks: [{ file_path: 'a.flac' }, { file_path: 'b.mp3' }, { file_path: 'c.mp3' }],
        },
      ],
    }).badges;
    expect(badges.map((b) => [b.format, b.count])).toEqual([
      ['MP3', 2],
      ['FLAC', 1],
    ]);
  });

  it('classes anything that is not FLAC or MP3 as other', () => {
    const [badge] = enhancedStats({ albums: [{ tracks: [{ file_path: 'a.opus' }] }] }).badges;
    expect(badge).toEqual({ format: 'OPUS', count: 1, className: 'other' });
  });

  it('does NOT badge a track with no file path', () => {
    // '-' is the absence of a path, not a format called "-".
    expect(enhancedStats({ albums: [{ tracks: [{ duration: 1 }] }] }).badges).toEqual([]);
  });

  it('survives a payload with no albums at all', () => {
    const stats = enhancedStats({});
    expect(stats.items.map((s) => s.value)).toEqual([0, 0, 0, 0, '0m']);
    expect(stats.badges).toEqual([]);
  });
});

describe('formatDurationMs', () => {
  it('is m:ss with a zero-padded second', () => {
    expect(formatDurationMs(65_000)).toBe('1:05');
    expect(formatDurationMs(3_600_000)).toBe('60:00');
  });

  it('is a dash — NOT 0:00 — with no duration', () => {
    expect(formatDurationMs(0)).toBe('-');
    expect(formatDurationMs(undefined)).toBe('-');
    expect(formatDurationMs(null)).toBe('-');
  });
});

describe('sectionCountLabel', () => {
  it('singularises exactly one release', () => {
    expect(sectionCountLabel(1, 12)).toBe('1 release \u00B7 12 tracks');
    expect(sectionCountLabel(3, 40)).toBe('3 releases \u00B7 40 tracks');
  });

  it('never singularises tracks, matching the vanilla', () => {
    expect(sectionCountLabel(1, 1)).toBe('1 release \u00B7 1 tracks');
  });
});

describe('sectionTrackTotal', () => {
  it('sums tracks, tolerating an album with none', () => {
    expect(sectionTrackTotal([{ tracks: [{}, {}] }, {}, { tracks: [] }])).toBe(2);
  });
});

describe('albumRowMeta', () => {
  it('builds the full meta line in order', () => {
    const meta = albumRowMeta({
      year: 1992,
      label: 'Warp',
      tracks: [
        { duration: 200_000, file_path: 'a.flac' },
        { duration: 100_000, file_path: 'b.flac' },
      ],
    });
    expect(meta.metaLine).toBe('1992 \u00B7 2 tracks \u00B7 5:00 \u00B7 Warp');
  });

  it('SKIPS the duration rather than printing the dash sentinel', () => {
    // A bare "-" between separators reads as a broken row.
    const meta = albumRowMeta({ year: 1992, tracks: [{ file_path: 'a.flac' }] });
    expect(meta.metaLine).toBe('1992 \u00B7 1 track');
  });

  it('drops the year and label when the album has none', () => {
    expect(albumRowMeta({ tracks: [] }).metaLine).toBe('0 tracks');
  });

  it('singularises a single track', () => {
    expect(albumRowMeta({ tracks: [{}] }).metaLine).toBe('1 track');
  });

  it('picks the album\u2019s commonest format for the badge', () => {
    const meta = albumRowMeta({
      tracks: [{ file_path: 'a.flac' }, { file_path: 'b.mp3' }, { file_path: 'c.mp3' }],
    });
    expect(meta.primaryFormat).toBe('MP3');
    expect(meta.formatClass).toBe('mp3');
  });

  it('has no format badge when no track has a path', () => {
    const meta = albumRowMeta({ tracks: [{ duration: 1 }] });
    expect(meta.primaryFormat).toBe('');
  });
});
