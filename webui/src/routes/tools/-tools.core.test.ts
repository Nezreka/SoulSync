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
  findingRowFixLabel,
  findingSeverityClass,
  findingSeverityIcon,
  findingStatusBadge,
  findingTypeLabel,
  findingsBulkBarState,
  findingsPagination,
  bulkFixLoopMessage,
  bulkFixRunMessage,
  commaSplitChips,
  fakeLosslessSpectrum,
  genericDetailRows,
  incompleteAlbumCompletion,
  libraryRetagDetail,
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

  it('maps the emitted severities onto the stylesheet classes', () => {
    // The CSS has .critical and no .error, so `error` renders through it
    // rather than churning the stylesheet (and losing styling for rows
    // already stored under either word).
    expect(findingSeverityClass('error')).toBe('critical');
    expect(findingSeverityClass('critical')).toBe('critical');
    expect(findingSeverityClass('warning')).toBe('warning');
    expect(findingSeverityClass('info')).toBe('info');
    expect(findingSeverityClass(null)).toBe('info');
    expect(findingSeverityIcon('error')).toBe('🔴');
  });
  it('carries the full severity/type/fixable/action tables', () => {
    // 4, not 3: the jobs emit `error` (corrupt audio) and the table used to
    // know only `critical`, which nothing has ever emitted. Both map to the
    // same icon and the same CSS class while old rows exist.
    expect(Object.keys(FINDING_SEVERITY_ICONS)).toHaveLength(4);
    expect(Object.keys(FINDING_TYPE_LABELS)).toHaveLength(23);
    expect(Object.keys(FINDING_FIXABLE_TYPES)).toHaveLength(21);
    expect(Object.keys(FINDING_ACTION_LABELS)).toHaveLength(13);
  });

  it('labels known finding types', () => {
    expect(findingTypeLabel('acoustid_mismatch')).toBe('Wrong Song');
    expect(findingTypeLabel('short_preview_track')).toBe('Preview Clip');
    expect(findingTypeLabel('genre_enrichment')).toBe('Genre Enrichment');
    expect(findingTypeLabel('comma_artist_split')).toBe('Comma Artist');
  });

  it('humanises an unknown type instead of showing a raw id', () => {
    expect(findingTypeLabel('some_new_check')).toBe('some new check');
  });

  it('gives fixable types their button label and others none', () => {
    expect(findingFixLabel('duplicate_tracks')).toBe('Keep Best');
    expect(findingFixLabel('genre_enrichment')).toBe('Apply Genres');
    expect(findingFixLabel('comma_artist_split')).toBe('Split Artists');
    expect(findingFixLabel('fake_lossless')).toBeNull();
    expect(findingFixLabel('path_mismatch')).toBeNull();
  });

  it('names the delete for a finding with no track behind it', () => {
    // The corruption detector walks the library folders too, so it raises rows
    // with `entity_type: 'file'` and no id. There is no track to re-request,
    // and the button used to promise one anyway — over a fix that could only
    // answer "No track ID associated with this finding".
    const stray = { finding_type: 'corrupt_audio', entity_type: 'file', entity_id: null };
    expect(findingRowFixLabel(stray)).toBe('Delete File');
    expect(findingRowFixLabel({ ...stray, finding_type: 'short_preview_track' })).toBe(
      'Delete File',
    );
  });

  it('leaves a finding that DOES name a track on the re-download wording', () => {
    expect(
      findingRowFixLabel({
        finding_type: 'short_preview_track',
        entity_type: 'track',
        entity_id: 'lib2:7',
      }),
    ).toBe('Re-download');
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

// ── P5: finding detail + bulk fix ────────────────────────────────────────────

describe('fakeLosslessSpectrum', () => {
  it('scales both markers against the nyquist frequency', () => {
    expect(
      fakeLosslessSpectrum({ detected_cutoff_khz: 16, expected_min_khz: 20, nyquist_khz: 22.05 }),
    ).toEqual({ cutoff: 16, expectedMin: 20, cutoffPct: 73, expectedPct: 91 });
  });

  it('falls back to sample_rate / 2000 when nyquist_khz is absent', () => {
    expect(
      fakeLosslessSpectrum({ detected_cutoff_khz: 22, expected_min_khz: 44, sample_rate: 176400 }),
    ).toEqual({ cutoff: 22, expectedMin: 44, cutoffPct: 25, expectedPct: 50 });
  });

  it('clamps a cutoff above nyquist to 100%', () => {
    expect(fakeLosslessSpectrum({ detected_cutoff_khz: 40, expected_min_khz: 20 })?.cutoffPct).toBe(
      100,
    );
  });

  it('renders no bar unless BOTH the cutoff and the expected minimum are present', () => {
    expect(fakeLosslessSpectrum({ detected_cutoff_khz: 16 })).toBeNull();
    expect(fakeLosslessSpectrum({ expected_min_khz: 20 })).toBeNull();
    expect(fakeLosslessSpectrum({ detected_cutoff_khz: 0, expected_min_khz: 20 })).toBeNull();
    expect(fakeLosslessSpectrum(null)).toBeNull();
  });
});

describe('incompleteAlbumCompletion', () => {
  it('reports the percentage of expected tracks present', () => {
    expect(incompleteAlbumCompletion({ actual_tracks: 9, expected_tracks: 12 })).toEqual({
      actual: 9,
      expected: 12,
      percent: 75,
    });
  });

  it('skips the bar entirely when the expected count is missing or zero', () => {
    expect(incompleteAlbumCompletion({ actual_tracks: 9 })).toBeNull();
    expect(incompleteAlbumCompletion({ actual_tracks: 9, expected_tracks: 0 })).toBeNull();
  });

  it('treats a missing actual count as zero rather than NaN', () => {
    expect(incompleteAlbumCompletion({ expected_tracks: 10 })).toEqual({
      actual: 0,
      expected: 10,
      percent: 0,
    });
  });
});

describe('libraryRetagDetail', () => {
  it('joins the meta line with a spaced middot and only the present fields', () => {
    expect(libraryRetagDetail({ source: 'spotify', mode: 'safe' }).meta).toBe(
      'Source: spotify  ·  Mode: safe',
    );
    expect(libraryRetagDetail({ cover_action: 'refresh' }).meta).toBe('Cover: refresh');
  });

  it('renders an empty old value as ∅ so a filled-in blank tag is visible', () => {
    const detail = libraryRetagDetail({
      tracks: [{ title: 'Song', changes: { album_artist: { old: '', new: 'Nine Inch Nails' } } }],
    });
    expect(detail.tracks[0].rows).toEqual([
      ['album artist', '∅   →   Nine Inch Nails', 'highlight'],
    ]);
  });

  it('shows the filename only when it differs from the label', () => {
    const withTitle = libraryRetagDetail({
      tracks: [
        { title: 'Song', file_path: '/m/a/01 Song.flac', changes: { year: { old: 1, new: 2 } } },
      ],
    });
    expect(withTitle.tracks[0]).toMatchObject({ label: 'Song', filename: '01 Song.flac' });

    const noTitle = libraryRetagDetail({
      tracks: [{ file_path: '/m/a/01 Song.flac', changes: { year: { old: 1, new: 2 } } }],
    });
    expect(noTitle.tracks[0]).toMatchObject({ label: '01 Song.flac', filename: null });
  });

  it('drops tracks with no changes and caps the rest at 40', () => {
    const tracks = Array.from({ length: 45 }, (_, index) => ({
      title: `T${index}`,
      changes: { year: { old: 1, new: 2 } },
    }));
    tracks.push({ title: 'unchanged', changes: {} } as (typeof tracks)[number]);
    const detail = libraryRetagDetail({ tracks });
    expect(detail.tracks).toHaveLength(40);
    expect(detail.overflow).toBe(5);
  });

  it('flags cover-only when nothing changes but a cover action is set', () => {
    expect(libraryRetagDetail({ cover_action: 'refresh', tracks: [] }).coverOnly).toBe(true);
    expect(libraryRetagDetail({ tracks: [] }).coverOnly).toBe(false);
    // BOTH halves matter: a cover action alongside real tag changes is an
    // ordinary retag, not a cover-only refresh.
    expect(
      libraryRetagDetail({
        cover_action: 'refresh',
        tracks: [{ title: 'T', changes: { year: { old: 1, new: 2 } } }],
      }).coverOnly,
    ).toBe(false);
  });
});

describe('commaSplitChips', () => {
  it('marks library members and source-verified parts differently', () => {
    expect(
      commaSplitChips({
        parts_resolution: [
          { name: 'A', in_library: true, library_artist_id: 7 },
          { name: 'B', verified_via: 'MusicBrainz' },
          { name: 'C' },
        ],
      }),
    ).toEqual([
      { name: 'A', mark: ' ✓ in your library', inLibrary: true, libraryArtistId: '7' },
      { name: 'B', mark: ' ✓ MusicBrainz', inLibrary: false, libraryArtistId: null },
      { name: 'C', mark: '', inLibrary: false, libraryArtistId: null },
    ]);
  });

  it('falls back to the bare split names, all unverified', () => {
    expect(commaSplitChips({ split_artists: ['A', 'B'] })).toEqual([
      { name: 'A', mark: '', inLibrary: false, libraryArtistId: null },
      { name: 'B', mark: '', inLibrary: false, libraryArtistId: null },
    ]);
  });

  it('keeps an alphanumeric artist id as a string rather than coercing it', () => {
    expect(
      commaSplitChips({
        parts_resolution: [{ name: 'A', in_library: true, library_artist_id: 'ab12' }],
      })[0].libraryArtistId,
    ).toBe('ab12');
  });
});

describe('genericDetailRows', () => {
  it('title-cases the key and skips objects and thumb urls', () => {
    expect(
      genericDetailRows({
        some_key: 'value',
        count: 3,
        nested: { a: 1 },
        album_thumb_url: 'http://x',
      }),
    ).toEqual([
      ['Some Key', 'value'],
      ['Count', '3'],
    ]);
  });

  it('returns nothing for an empty payload', () => {
    expect(genericDetailRows(null)).toEqual([]);
  });
});

describe('findingsBulkBarState', () => {
  it('hides the bar until something is selected', () => {
    expect(findingsBulkBarState(0, 30, 100)).toMatchObject({ showBar: false, countLabel: '' });
    expect(findingsBulkBarState(2, 30, 100)).toMatchObject({
      showBar: true,
      countLabel: '2 selected',
    });
  });

  it('offers Fix All only once the whole page is selected AND more pages exist', () => {
    expect(findingsBulkBarState(30, 30, 100)).toMatchObject({
      showFixAll: true,
      fixAllLabel: 'Fix All 100',
    });
    expect(findingsBulkBarState(29, 30, 100).showFixAll).toBe(false);
    expect(findingsBulkBarState(30, 30, 30).showFixAll).toBe(false);
  });

  it('drives the select-all tri-state', () => {
    expect(findingsBulkBarState(0, 30, 100)).toMatchObject({
      selectAllChecked: false,
      selectAllIndeterminate: false,
    });
    expect(findingsBulkBarState(5, 30, 100)).toMatchObject({
      selectAllChecked: false,
      selectAllIndeterminate: true,
    });
    expect(findingsBulkBarState(30, 30, 100)).toMatchObject({
      selectAllChecked: true,
      selectAllIndeterminate: false,
    });
    expect(findingsBulkBarState(0, 0, 0).selectAllChecked).toBe(false);
  });
});

describe('bulkFixRunMessage', () => {
  it('reports fixed of total', () => {
    expect(bulkFixRunMessage({ fixed: 8, failed: 0, total: 8 })).toEqual({
      message: 'Fixed 8 of 8',
      type: 'success',
    });
  });

  it('appends the failure count and the first error', () => {
    expect(
      bulkFixRunMessage({ fixed: 6, failed: 2, total: 8, errors: [{ error: 'disk full' }] }),
    ).toEqual({ message: 'Fixed 6, 2 failed of 8: disk full', type: 'success' });
  });

  it('lowercases the first letter behind the stopped prefix', () => {
    expect(bulkFixRunMessage({ fixed: 3, failed: 0, total: 9, stopped: true }).message).toBe(
      'Bulk fix stopped — fixed 3 of 9',
    );
  });

  it('is an error when nothing was fixed, even with no failures', () => {
    expect(bulkFixRunMessage({ fixed: 0, failed: 0, total: 4 }).type).toBe('error');
  });
});

describe('bulkFixLoopMessage', () => {
  it('omits the "of N" the background run reports', () => {
    expect(bulkFixLoopMessage(5, 0, '')).toEqual({ message: 'Fixed 5', type: 'success' });
  });

  it('appends the last error only when something failed', () => {
    expect(bulkFixLoopMessage(4, 1, 'nope').message).toBe('Fixed 4, 1 failed: nope');
    expect(bulkFixLoopMessage(4, 1, '').message).toBe('Fixed 4, 1 failed');
    expect(bulkFixLoopMessage(0, 2, 'nope').type).toBe('error');
    // The `failed` half of the guard is load-bearing even though the caller only
    // ever sets lastError alongside a failure — a stale error must not surface
    // on a clean run.
    expect(bulkFixLoopMessage(5, 0, 'stale').message).toBe('Fixed 5');
  });
});
