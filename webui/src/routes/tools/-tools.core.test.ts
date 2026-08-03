/**
 * Tools pure core. Assertions are written as LITERALS rather than interpolated
 * from the module under test, so a change to a label or threshold has to be
 * re-typed here deliberately instead of silently agreeing with itself.
 */

import { describe, expect, it } from 'vitest';

import {
  FINDING_ACTION_LABELS,
  FINDING_FIXABLE_TYPES,
  FINDING_SEVERITY_ICONS,
  FINDING_TYPE_LABELS,
  MASS_ORPHAN_THRESHOLD,
  REPAIR_DEFAULT_PAGE_SIZE,
  REPAIR_PAGE_SIZE_OPTIONS,
  backupSummary,
  backupTimestamp,
  cacheHealthLabel,
  cacheHealthModalLabel,
  cacheHealthScore,
  cacheSourceBars,
  cacheSourceColor,
  cacheSourceLabel,
  findingFilePath,
  findingFixLabel,
  findingSeverityIcon,
  findingStatusBadge,
  findingTypeLabel,
  findingsPagination,
  formatCacheAge,
  formatFileSize,
  formatFreedSpace,
  isMassOrphanFix,
  isRepairJobDryRun,
  isRepairSettingSection,
  metadataCacheCardCount,
  normalizeFindingsPageSize,
  prettifyRepairSettingKey,
  repairJobBadge,
  repairJobCardClass,
  repairJobDot,
  scoreBar,
  timeAgo,
} from './-tools.core';

describe('prettifyRepairSettingKey', () => {
  it('spells out the ffmpeg cost for deep_audio_verify instead of Title Casing it', () => {
    expect(prettifyRepairSettingKey('deep_audio_verify')).toBe(
      'Deep Audio Verify (ffmpeg decode — CPU heavy)',
    );
  });

  it('title-cases a plain snake_case key', () => {
    expect(prettifyRepairSettingKey('dry_run')).toBe('Dry Run');
    expect(prettifyRepairSettingKey('max_items')).toBe('Max Items');
  });

  it('fixes up the acronyms Title Case would botch', () => {
    expect(prettifyRepairSettingKey('min_id')).toBe('Min ID');
    expect(prettifyRepairSettingKey('api_url')).toBe('API URL');
    expect(prettifyRepairSettingKey('skip_eps')).toBe('Skip EPs');
    expect(prettifyRepairSettingKey('mp3_only')).toBe('MP3 Only');
    expect(prettifyRepairSettingKey('flac_cd_os_ac_mb')).toBe('FLAC CD OS AC MB');
  });

  it('strips leading underscores so _interval_hours reads as a setting', () => {
    expect(prettifyRepairSettingKey('_interval_hours')).toBe('Interval Hours');
  });

  it('leaves an already-capitalised word alone', () => {
    expect(prettifyRepairSettingKey('Threshold')).toBe('Threshold');
  });
});

describe('isRepairSettingSection', () => {
  it('treats _section_ keys as group dividers', () => {
    expect(isRepairSettingSection('_section_scanning')).toBe(true);
  });

  it('does not mistake other underscored keys for dividers', () => {
    expect(isRepairSettingSection('_interval_hours')).toBe(false);
    expect(isRepairSettingSection('dry_run')).toBe(false);
  });
});

describe('repairJobBadge', () => {
  it('prefers the live pending count', () => {
    expect(
      repairJobBadge({ pending_findings_count: 12, last_run: { findings_created: 372 } }),
    ).toEqual({
      kind: 'pending',
      count: 12,
    });
  });

  it('falls back to the last run only when nothing is pending', () => {
    // The 372-duplicates-all-bulk-fixed case: pending is 0 but the last scan
    // did find something, so the badge says so rather than vanishing.
    expect(
      repairJobBadge({ pending_findings_count: 0, last_run: { findings_created: 372 } }),
    ).toEqual({
      kind: 'historical',
      count: 372,
    });
  });

  it('shows nothing when neither count is set', () => {
    expect(
      repairJobBadge({ pending_findings_count: 0, last_run: { findings_created: 0 } }),
    ).toEqual({
      kind: 'none',
    });
    expect(repairJobBadge({ last_run: null })).toEqual({ kind: 'none' });
  });
});

describe('repairJobDot / repairJobCardClass', () => {
  it('marks a running job as running even when it is disabled', () => {
    expect(repairJobDot({ is_running: true, enabled: false })).toBe('running');
    expect(repairJobCardClass({ is_running: true, enabled: false })).toBe('running');
  });

  it('gives an idle enabled job a dot but NO card class', () => {
    // Deliberate asymmetry in the vanilla — the two ternaries differ.
    expect(repairJobDot({ is_running: false, enabled: true })).toBe('enabled');
    expect(repairJobCardClass({ is_running: false, enabled: true })).toBe('');
  });

  it('marks an idle disabled job disabled in both', () => {
    expect(repairJobDot({ is_running: false, enabled: false })).toBe('disabled');
    expect(repairJobCardClass({ is_running: false, enabled: false })).toBe('disabled');
  });
});

describe('isRepairJobDryRun', () => {
  it('only reads a literal true, not a truthy value', () => {
    expect(isRepairJobDryRun({ settings: { dry_run: true } })).toBe(true);
    expect(isRepairJobDryRun({ settings: { dry_run: 'yes' } })).toBe(false);
    expect(isRepairJobDryRun({ settings: { dry_run: 1 } })).toBe(false);
    expect(isRepairJobDryRun({ settings: {} })).toBe(false);
    expect(isRepairJobDryRun({ settings: null })).toBe(false);
  });
});

describe('finding labels', () => {
  it('maps severities to their icons and falls back to info', () => {
    expect(findingSeverityIcon('warning')).toBe('⚠️');
    expect(findingSeverityIcon('critical')).toBe('🔴');
    expect(findingSeverityIcon('info')).toBe('ℹ️');
    expect(findingSeverityIcon('nonsense')).toBe('ℹ️');
    expect(findingSeverityIcon(null)).toBe('ℹ️');
  });

  it('carries the full severity/type/fixable/action tables', () => {
    expect(Object.keys(FINDING_SEVERITY_ICONS)).toHaveLength(3);
    expect(Object.keys(FINDING_TYPE_LABELS)).toHaveLength(22);
    expect(Object.keys(FINDING_FIXABLE_TYPES)).toHaveLength(20);
    expect(Object.keys(FINDING_ACTION_LABELS)).toHaveLength(12);
  });

  it('labels known finding types', () => {
    expect(findingTypeLabel('acoustid_mismatch')).toBe('Wrong Song');
    expect(findingTypeLabel('short_preview_track')).toBe('Preview Clip');
    expect(findingTypeLabel('comma_artist_split')).toBe('Comma Artist');
  });

  it('humanises an unknown type instead of showing a raw id', () => {
    expect(findingTypeLabel('some_new_check')).toBe('some new check');
  });

  it('gives fixable types their button label and others none', () => {
    expect(findingFixLabel('duplicate_tracks')).toBe('Keep Best');
    expect(findingFixLabel('comma_artist_split')).toBe('Split Artists');
    expect(findingFixLabel('fake_lossless')).toBeNull();
    expect(findingFixLabel('path_mismatch')).toBeNull();
  });

  it('knows missing_discography_track is fixable despite having no type label', () => {
    // Asymmetry inherited from the vanilla: it is in fixableTypes but not
    // typeLabels, so its badge shows the humanised id.
    expect(findingFixLabel('missing_discography_track')).toBe('Add to Wishlist');
    expect(findingTypeLabel('missing_discography_track')).toBe('missing discography track');
  });
});

describe('findingStatusBadge', () => {
  it('shows nothing for a pending finding', () => {
    expect(findingStatusBadge('pending', null)).toBeNull();
  });

  it('prefers the user action label', () => {
    expect(findingStatusBadge('resolved', 'removed_duplicates')).toBe('Duplicates Removed');
    expect(findingStatusBadge('resolved', 'already_gone')).toBe('Already Gone');
  });

  it('falls back to the raw status when the action has no label', () => {
    expect(findingStatusBadge('dismissed', null)).toBe('dismissed');
    expect(findingStatusBadge('resolved', 'brand_new_action')).toBe('resolved');
  });
});

describe('findingFilePath', () => {
  it('prefers the top-level path', () => {
    expect(
      findingFilePath({
        file_path: '/top.mp3',
        details: { original_path: '/orig.mp3', file_path: '/d.mp3' },
      }),
    ).toBe('/top.mp3');
  });

  it('falls back through original_path then details.file_path', () => {
    expect(findingFilePath({ details: { original_path: '/orig.mp3', file_path: '/d.mp3' } })).toBe(
      '/orig.mp3',
    );
    expect(findingFilePath({ details: { file_path: '/d.mp3' } })).toBe('/d.mp3');
  });

  it('returns an empty string when there is no path anywhere', () => {
    expect(findingFilePath({})).toBe('');
    expect(findingFilePath({ file_path: null, details: null })).toBe('');
  });
});

describe('normalizeFindingsPageSize', () => {
  it('offers exactly the three sizes and defaults to 30', () => {
    expect(REPAIR_PAGE_SIZE_OPTIONS).toEqual([30, 60, 100]);
    expect(REPAIR_DEFAULT_PAGE_SIZE).toBe(30);
  });

  it('accepts an allowed size as string or number', () => {
    expect(normalizeFindingsPageSize('60')).toBe(60);
    expect(normalizeFindingsPageSize(100)).toBe(100);
  });

  it('rejects anything else back to 30', () => {
    expect(normalizeFindingsPageSize('45')).toBe(30);
    expect(normalizeFindingsPageSize('banana')).toBe(30);
    expect(normalizeFindingsPageSize(null)).toBe(30);
    expect(normalizeFindingsPageSize(undefined)).toBe(30);
    expect(normalizeFindingsPageSize('')).toBe(30);
  });
});

describe('findingsPagination', () => {
  it('renders nothing for a single page', () => {
    const single = findingsPagination(12, 0, 30);
    expect(single.totalPages).toBe(1);
    expect(single.pages).toEqual([]);
    expect(single.showPrev).toBe(false);
    expect(single.showNext).toBe(false);
    expect(single.showFirst).toBe(false);
    expect(single.showLast).toBe(false);
    expect(single.showFirstEllipsis).toBe(false);
    expect(single.showLastEllipsis).toBe(false);
  });

  it('shows every page while they fit in the 7-wide window', () => {
    const few = findingsPagination(150, 0, 30);
    expect(few.totalPages).toBe(5);
    expect(few.pages).toEqual([0, 1, 2, 3, 4]);
    expect(few.showFirst).toBe(false);
    expect(few.showLast).toBe(false);
    expect(few.showPrev).toBe(false);
    expect(few.showNext).toBe(true);
  });

  it('anchors the window three before the current page', () => {
    const mid = findingsPagination(600, 10, 30);
    expect(mid.totalPages).toBe(20);
    expect(mid.pages).toEqual([7, 8, 9, 10, 11, 12, 13]);
    expect(mid.showFirst).toBe(true);
    expect(mid.showFirstEllipsis).toBe(true);
    expect(mid.showLast).toBe(true);
    expect(mid.showLastEllipsis).toBe(true);
  });

  it('shifts the window back rather than overrunning the last page', () => {
    const end = findingsPagination(600, 19, 30);
    expect(end.pages).toEqual([13, 14, 15, 16, 17, 18, 19]);
    expect(end.showLast).toBe(false);
    expect(end.showNext).toBe(false);
    expect(end.showPrev).toBe(true);
  });

  it('drops the first-ellipsis when only page 0 is hidden', () => {
    const near = findingsPagination(600, 4, 30);
    expect(near.pages).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(near.showFirst).toBe(true);
    expect(near.showFirstEllipsis).toBe(false);
  });

  it('tracks the page size', () => {
    expect(findingsPagination(600, 0, 100).totalPages).toBe(6);
  });
});

describe('isMassOrphanFix', () => {
  it('needs more than the threshold AND a flagged finding', () => {
    expect(MASS_ORPHAN_THRESHOLD).toBe(20);
    expect(isMassOrphanFix('orphan_file_detector', 21, true)).toBe(true);
    expect(isMassOrphanFix('orphan_file_detector', 21, false)).toBe(false);
    expect(isMassOrphanFix('orphan_file_detector', 20, true)).toBe(false);
  });

  it('applies to an unfiltered (all jobs) view too', () => {
    expect(isMassOrphanFix('', 500, true)).toBe(true);
    expect(isMassOrphanFix(null, 500, true)).toBe(true);
  });

  it('never fires for a different job', () => {
    expect(isMassOrphanFix('dead_file_cleaner', 5000, true)).toBe(false);
  });
});

describe('cache health', () => {
  it('is healthy only when both counters are zero', () => {
    expect(cacheHealthScore({ junk_entities: 0, stale_mb_nulls: 0 })).toBe('healthy');
    expect(cacheHealthScore({ junk_entities: 0, stale_mb_nulls: 1 })).toBe('fair');
    expect(cacheHealthScore({ junk_entities: 1, stale_mb_nulls: 0 })).toBe('fair');
  });

  it('turns poor above 50 junk entries', () => {
    expect(cacheHealthScore({ junk_entities: 50, stale_mb_nulls: 0 })).toBe('fair');
    expect(cacheHealthScore({ junk_entities: 51, stale_mb_nulls: 0 })).toBe('poor');
  });

  it('words the same score differently in the bar and the modal', () => {
    expect(cacheHealthLabel('healthy')).toBe('Healthy');
    expect(cacheHealthLabel('fair')).toBe('Needs Cleanup');
    expect(cacheHealthLabel('poor')).toBe('Needs Attention');
    expect(cacheHealthModalLabel('healthy')).toBe('Cache is healthy');
    expect(cacheHealthModalLabel('fair')).toBe('Minor issues detected');
    expect(cacheHealthModalLabel('poor')).toBe('Cleanup recommended');
  });

  it('colours the known sources and greys the rest', () => {
    expect(cacheSourceColor('spotify')).toBe('#1DB954');
    expect(cacheSourceColor('itunes')).toBe('#FC3C44');
    expect(cacheSourceColor('deezer')).toBe('#A238FF');
    expect(cacheSourceColor('musicbrainz')).toBe('#BA478F');
    expect(cacheSourceColor('beatport')).toBe('#666');
  });

  it('only re-cases MusicBrainz', () => {
    expect(cacheSourceLabel('musicbrainz')).toBe('MusicBrainz');
    expect(cacheSourceLabel('spotify')).toBe('spotify');
  });

  it('folds musicbrainz in and scales bars against the largest source', () => {
    const bars = cacheSourceBars({
      by_source: { spotify: 100, itunes: 25 },
      total_musicbrainz: 50,
    });
    expect(bars.map((bar) => bar.source)).toEqual(['spotify', 'itunes', 'musicbrainz']);
    expect(bars.map((bar) => bar.percent)).toEqual([100, 25, 50]);
    expect(bars[2].label).toBe('MusicBrainz');
  });

  it('does not divide by zero when every source is empty', () => {
    const bars = cacheSourceBars({ by_source: { spotify: 0 } });
    expect(bars[0].percent).toBe(0);
  });

  it('handles no sources at all', () => {
    expect(cacheSourceBars({})).toEqual([]);
  });
});

describe('formatFileSize', () => {
  it('renders a dash for nothing, including zero bytes', () => {
    expect(formatFileSize(0)).toBe('-');
    expect(formatFileSize(null)).toBe('-');
    expect(formatFileSize(undefined)).toBe('-');
  });

  it('steps B → KB → MB at the binary boundaries', () => {
    expect(formatFileSize(512)).toBe('512 B');
    expect(formatFileSize(1023)).toBe('1023 B');
    expect(formatFileSize(1024)).toBe('1.0 KB');
    expect(formatFileSize(1048575)).toBe('1024.0 KB');
    expect(formatFileSize(1048576)).toBe('1.0 MB');
    expect(formatFileSize(5 * 1048576)).toBe('5.0 MB');
  });
});

describe('formatCacheAge', () => {
  const now = Date.parse('2026-08-03T12:00:00Z');

  it('shows an em dash when there is no timestamp', () => {
    expect(formatCacheAge(null, now)).toBe('—');
    expect(formatCacheAge('', now)).toBe('—');
  });

  it('steps now → m → h → d → mo', () => {
    expect(formatCacheAge('2026-08-03T11:59:30Z', now)).toBe('now');
    expect(formatCacheAge('2026-08-03T11:30:00Z', now)).toBe('30m');
    expect(formatCacheAge('2026-08-03T09:00:00Z', now)).toBe('3h');
    expect(formatCacheAge('2026-08-01T12:00:00Z', now)).toBe('2d');
    expect(formatCacheAge('2026-05-03T12:00:00Z', now)).toBe('3mo');
  });
});

describe('timeAgo', () => {
  const now = Date.parse('2026-08-03T12:00:00Z');

  it('is empty for a missing date', () => {
    expect(timeAgo(null, now)).toBe('');
  });

  it('reads a bare timestamp as UTC rather than local time', () => {
    // Without the appended Z this would be parsed as local and could read
    // hours off — or negative, showing "just now" for an old backup.
    expect(timeAgo('2026-08-03T09:00:00', now)).toBe('3h ago');
  });

  it('leaves an explicit Z or offset alone', () => {
    expect(timeAgo('2026-08-03T09:00:00Z', now)).toBe('3h ago');
    expect(timeAgo('2026-08-03T11:00:00+00:00', now)).toBe('1h ago');
    expect(timeAgo('2026-08-03T08:00:00-01:00', now)).toBe('3h ago');
  });

  it('steps just now → s → m → h → d → mo', () => {
    expect(timeAgo('2026-08-03T11:59:58Z', now)).toBe('just now');
    expect(timeAgo('2026-08-03T11:59:30Z', now)).toBe('30s ago');
    expect(timeAgo('2026-08-03T11:30:00Z', now)).toBe('30m ago');
    expect(timeAgo('2026-08-03T06:00:00Z', now)).toBe('6h ago');
    expect(timeAgo('2026-07-29T12:00:00Z', now)).toBe('5d ago');
    expect(timeAgo('2026-04-03T12:00:00Z', now)).toBe('4mo ago');
  });
});

describe('scoreBar', () => {
  it('converts a 0..1 score to a percent and band', () => {
    expect(scoreBar(0.95)).toEqual({ percent: 95, band: 'good' });
    expect(scoreBar(0.8)).toEqual({ percent: 80, band: 'good' });
    expect(scoreBar(0.79)).toEqual({ percent: 79, band: 'warn' });
    expect(scoreBar(0.5)).toEqual({ percent: 50, band: 'warn' });
    expect(scoreBar(0.49)).toEqual({ percent: 49, band: 'bad' });
  });

  it('treats a missing score as zero', () => {
    expect(scoreBar(null)).toEqual({ percent: 0, band: 'bad' });
    expect(scoreBar(undefined)).toEqual({ percent: 0, band: 'bad' });
  });
});

describe('backup helpers', () => {
  const now = Date.parse('2026-08-03T12:00:00Z');

  it('summarises the newest backup', () => {
    expect(backupSummary([{ created: '2026-08-03T09:00:00', size_mb: 42 }], now)).toEqual({
      lastBackup: '3h ago',
      latestSize: '42 MB',
    });
  });

  it('says Never with no backups', () => {
    expect(backupSummary([], now)).toEqual({ lastBackup: 'Never', latestSize: '—' });
    expect(backupSummary(null, now)).toEqual({ lastBackup: 'Never', latestSize: '—' });
  });

  it('reads a naive backup timestamp as UTC', () => {
    expect(backupTimestamp('2026-08-03T09:00:00').toISOString()).toBe('2026-08-03T09:00:00.000Z');
    expect(backupTimestamp('2026-08-03T09:00:00Z').toISOString()).toBe('2026-08-03T09:00:00.000Z');
  });
});

describe('formatFreedSpace', () => {
  it('switches to GB at 1024 MB', () => {
    expect(formatFreedSpace(1023.5)).toBe('1023.50 MB');
    expect(formatFreedSpace(1024)).toBe('1.00 GB');
    expect(formatFreedSpace(2048)).toBe('2.00 GB');
  });

  it('uses one decimal for the completion toast and two for the stat row', () => {
    expect(formatFreedSpace(12.345, 2)).toBe('12.35 MB');
    expect(formatFreedSpace(12.345, 1)).toBe('12.3 MB');
  });
});

describe('metadataCacheCardCount', () => {
  it('sums only the four first-party sources', () => {
    expect(
      metadataCacheCardCount({
        spotify: 1,
        itunes: 2,
        deezer: 3,
        beatport: 4,
        discogs: 99,
        musicbrainz: 99,
      }),
    ).toBe(10);
  });

  it('treats missing buckets as zero', () => {
    expect(metadataCacheCardCount({ spotify: 5 })).toBe(5);
    expect(metadataCacheCardCount(null)).toBe(0);
    expect(metadataCacheCardCount(undefined)).toBe(0);
  });
});
