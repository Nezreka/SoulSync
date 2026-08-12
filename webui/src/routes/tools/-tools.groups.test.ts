/**
 * The inbox arithmetic.
 *
 * These are the numbers a user reads as a verdict on their library, so the
 * assertions are literal values rather than expressions recomputed from the
 * source — a formula that drifts must break a test, not follow it.
 */

import { describe, expect, it } from 'vitest';

import type { FindingGroup, FindingTypeInfo } from './-tools.groups';
import {
  contributionSegments,
  FINDING_TYPE_BLURBS,
  findingTypeBlurb,
  findingsTrend,
  groupCountForStatus,
  groupWeight,
  healthBand,
  healthBandLabel,
  libraryHealth,
  safeFixablePending,
  severityRank,
  severityWeight,
  sortInboxGroups,
  sparklinePoints,
  visibleGroups,
} from './-tools.groups';

const group = (over: Partial<FindingGroup> = {}): FindingGroup => ({
  finding_type: 'orphan_file',
  pending: 0,
  resolved: 0,
  dismissed: 0,
  total: 0,
  severity_max: 'info',
  last_seen: null,
  job_ids: [],
  ...over,
});

const info = (over: Partial<FindingTypeInfo> = {}): FindingTypeInfo => ({
  type: 'orphan_file',
  label: 'Orphan Files',
  verb: 'Review & Move',
  fixable: true,
  destructive: false,
  job_ids: [],
  ...over,
});

describe('severity', () => {
  it('ranks error above warning above info', () => {
    expect(severityRank('error')).toBe(0);
    expect(severityRank('warning')).toBe(1);
    expect(severityRank('info')).toBe(2);
  });

  it('treats an unknown severity as the quietest, never the loudest', () => {
    // An unrecognised value jumping the queue ahead of real errors would be
    // the worst possible failure mode for a triage list.
    expect(severityRank('catastrophic')).toBe(2);
    expect(severityRank(null)).toBe(2);
    expect(severityWeight('catastrophic')).toBe(0.02);
  });

  it('weights one error like fifty info rows', () => {
    expect(severityWeight('error')).toBe(1);
    expect(severityWeight('warning')).toBe(0.25);
    expect(severityWeight('info')).toBe(0.02);
  });

  it('costs a group at its worst pending severity', () => {
    expect(groupWeight(group({ pending: 4, severity_max: 'warning' }))).toBe(1);
    expect(groupWeight(group({ pending: 10, severity_max: 'error' }))).toBe(10);
    // A negative count is a server bug, not a bonus to the score.
    expect(groupWeight(group({ pending: -5, severity_max: 'error' }))).toBe(0);
  });
});

describe('libraryHealth', () => {
  it('scores an empty finding list 100', () => {
    expect(libraryHealth([], 12000)).toEqual({
      score: 100,
      band: 'healthy',
      weighted: 0,
      pending: 0,
    });
  });

  it('normalises per 1,000 tracks', () => {
    const groups = [group({ pending: 200, severity_max: 'warning' })]; // weight 50
    // 50 per 1,000 tracks in a 1,000-track library: half the library's worth
    // of trouble.
    expect(libraryHealth(groups, 1000).score).toBe(50);
    // The same 200 findings in 10,000 tracks cost a tenth as much.
    expect(libraryHealth(groups, 10000).score).toBe(95);
  });

  it('floors at zero rather than going negative', () => {
    expect(libraryHealth([group({ pending: 9000, severity_max: 'error' })], 1000).score).toBe(0);
  });

  it('treats an unknown library size as 1,000 tracks — pessimistic, not fake-perfect', () => {
    const groups = [group({ pending: 40, severity_max: 'warning' })]; // weight 10
    expect(libraryHealth(groups, null).score).toBe(90);
    expect(libraryHealth(groups, 0).score).toBe(90);
    // A tiny library must not be scored MORE leniently than the fallback.
    expect(libraryHealth(groups, 200).score).toBe(90);
  });

  it('bands at 90 and 70', () => {
    expect(healthBand(100)).toBe('healthy');
    expect(healthBand(90)).toBe('healthy');
    expect(healthBand(89)).toBe('attention');
    expect(healthBand(70)).toBe('attention');
    expect(healthBand(69)).toBe('unhealthy');
    expect(healthBandLabel('attention')).toBe('needs attention');
  });

  it('counts pending across every group', () => {
    const health = libraryHealth(
      [group({ pending: 3, severity_max: 'error' }), group({ pending: 7 })],
      1000,
    );
    expect(health.pending).toBe(10);
    expect(health.weighted).toBeCloseTo(3.14, 5);
  });
});

describe('contributionSegments', () => {
  const label = (type: string) => type.toUpperCase();

  it('sizes segments by WEIGHT, so a few errors are not buried under many info rows', () => {
    const segments = contributionSegments(
      [
        group({ finding_type: 'corrupt_audio', pending: 3, severity_max: 'error' }), // 3.0
        group({ finding_type: 'missing_cover_art', pending: 400 }), // 8.0
      ],
      label,
    );
    expect(segments.map((s) => s.findingType)).toEqual(['missing_cover_art', 'corrupt_audio']);
    // 3 broken files are more than a quarter of the bar next to 400 missing
    // covers. By raw count they would be 0.7% of it.
    expect(segments[1].percent).toBeCloseTo((3 / 11) * 100, 5);
    expect(segments[0].label).toBe('MISSING_COVER_ART');
  });

  it('drops groups with nothing pending, and returns nothing at all when the total is zero', () => {
    expect(
      contributionSegments([group({ pending: 0, resolved: 40 })], label).map((s) => s.findingType),
    ).toEqual([]);
    expect(contributionSegments([], label)).toEqual([]);
  });
});

describe('sortInboxGroups', () => {
  const destructive = (type: string) => type === 'orphan_file' || type === 'corrupt_audio';

  it('sorts worst first, destructive last within a band, then biggest', () => {
    const sorted = sortInboxGroups(
      [
        group({ finding_type: 'missing_cover_art', pending: 400 }),
        group({ finding_type: 'orphan_file', pending: 90, severity_max: 'warning' }),
        group({ finding_type: 'metadata_gap', pending: 2, severity_max: 'warning' }),
        group({ finding_type: 'corrupt_audio', pending: 1, severity_max: 'error' }),
      ],
      destructive,
    );
    expect(sorted.map((g) => g.finding_type)).toEqual([
      'corrupt_audio',
      'metadata_gap',
      'orphan_file',
      'missing_cover_art',
    ]);
  });

  it('breaks a full tie on the type name, so the order never shuffles between loads', () => {
    const sorted = sortInboxGroups(
      [
        group({ finding_type: 'zeta', pending: 5, severity_max: 'warning' }),
        group({ finding_type: 'alpha', pending: 5, severity_max: 'warning' }),
      ],
      () => false,
    );
    expect(sorted.map((g) => g.finding_type)).toEqual(['alpha', 'zeta']);
  });

  it('does not mutate the array it was given', () => {
    const groups = [group({ finding_type: 'b' }), group({ finding_type: 'a' })];
    sortInboxGroups(groups, () => false);
    expect(groups.map((g) => g.finding_type)).toEqual(['b', 'a']);
  });
});

describe('visibleGroups', () => {
  const groups = [
    group({ finding_type: 'orphan_file', pending: 3, total: 3, job_ids: ['orphan_file_detector'] }),
    group({
      finding_type: 'dead_file',
      pending: 0,
      dismissed: 4,
      total: 4,
      severity_max: 'warning',
      job_ids: ['dead_file_cleaner'],
    }),
  ];

  it('drops a group with nothing in the selected status', () => {
    expect(visibleGroups(groups, { status: 'pending' }).map((g) => g.finding_type)).toEqual([
      'orphan_file',
    ]);
    expect(visibleGroups(groups, { status: 'dismissed' }).map((g) => g.finding_type)).toEqual([
      'dead_file',
    ]);
  });

  it('filters by the job that raised it', () => {
    expect(
      visibleGroups(groups, { status: '', jobId: 'dead_file_cleaner' }).map((g) => g.finding_type),
    ).toEqual(['dead_file']);
  });

  it('filters by severity', () => {
    expect(
      visibleGroups(groups, { status: '', severity: 'warning' }).map((g) => g.finding_type),
    ).toEqual(['dead_file']);
  });

  it('an empty status means every status, using the total', () => {
    expect(visibleGroups(groups, {}).map((g) => g.finding_type)).toEqual([
      'orphan_file',
      'dead_file',
    ]);
  });
});

describe('groupCountForStatus', () => {
  it('reads the count the segmented control is showing', () => {
    const g = group({ pending: 1, resolved: 2, dismissed: 3, total: 6 });
    expect(groupCountForStatus(g, 'pending')).toBe(1);
    expect(groupCountForStatus(g, 'resolved')).toBe(2);
    expect(groupCountForStatus(g, 'dismissed')).toBe(3);
    expect(groupCountForStatus(g, '')).toBe(6);
  });
});

describe('safeFixablePending', () => {
  const catalog = new Map([
    ['missing_cover_art', info({ type: 'missing_cover_art', destructive: false })],
    ['orphan_file', info({ type: 'orphan_file', destructive: true })],
    ['fake_lossless', info({ type: 'fake_lossless', fixable: false, destructive: false })],
  ]);
  const lookup = (type: string) => catalog.get(type);

  it('counts only what a safe run would actually touch', () => {
    const total = safeFixablePending(
      [
        group({ finding_type: 'missing_cover_art', pending: 10 }),
        group({ finding_type: 'orphan_file', pending: 20 }),
        // Unfixable: counting it is how a button comes to report
        // "Fixed 0 of 1,204".
        group({ finding_type: 'fake_lossless', pending: 5 }),
        // Unknown to the catalog — no evidence it is safe.
        group({ finding_type: 'brand_new_type', pending: 99 }),
      ],
      lookup,
    );
    expect(total).toBe(10);
  });
});

describe('the blurbs', () => {
  /**
   * Every finding type the worker can emit, transcribed from
   * `FINDING_TYPE_META` in core/repair_worker.py. A pytest asserts the two
   * lists agree; this asserts each one reads like a sentence.
   */
  const SLUGS = [
    'dead_file',
    'orphan_file',
    'track_number_mismatch',
    'missing_cover_art',
    'missing_lyrics',
    'missing_replaygain',
    'replaygain_retag',
    'empty_folder',
    'expired_download',
    'metadata_gap',
    'duplicate_tracks',
    'single_album_redundant',
    'mbid_mismatch',
    'album_mbid_mismatch',
    'album_tag_inconsistency',
    'incomplete_album',
    'path_mismatch',
    'missing_lossy_copy',
    'unwanted_content',
    'unknown_artist',
    'acoustid_mismatch',
    'quality_upgrade',
    'missing_discography_track',
    'library_retag',
    'short_preview_track',
    'corrupt_audio',
    'canonical_version',
    'genre_cleanup',
    'comma_artist_split',
    'fake_lossless',
    'album_needs_enrichment',
  ];

  it('covers every finding type', () => {
    const missing = SLUGS.filter((slug) => !FINDING_TYPE_BLURBS[slug]);
    expect(missing).toEqual([]);
  });

  it('keeps each one to a single scannable line', () => {
    const tooLong = Object.entries(FINDING_TYPE_BLURBS).filter(([, text]) => text.length > 70);
    expect(tooLong).toEqual([]);
  });

  it('writes them as sentences, not slugs', () => {
    for (const [slug, text] of Object.entries(FINDING_TYPE_BLURBS)) {
      expect(text, slug).toMatch(/[.!]$/);
      expect(text, slug).not.toContain('_');
    }
  });

  it('says nothing rather than echoing the slug for a type it has never heard of', () => {
    expect(findingTypeBlurb('some_future_type')).toBe('');
  });
});

describe('the trend line', () => {
  it('reverses the newest-first history into oldest-first points', () => {
    const runs = [{ findings_created: 3 }, { findings_created: 9 }, { findings_created: 1 }];
    expect(findingsTrend(runs)).toEqual([1, 9, 3]);
  });

  it('keeps zero-finding runs as points — a flat line is the shape of a quiet library', () => {
    expect(findingsTrend([{ findings_created: 0 }, { findings_created: 4 }])).toEqual([4, 0]);
    expect(findingsTrend([{}])).toEqual([0]);
  });

  it('takes only the most recent N', () => {
    const runs = Array.from({ length: 40 }, (_, index) => ({ findings_created: index }));
    expect(findingsTrend(runs, 5)).toEqual([4, 3, 2, 1, 0]);
  });

  it('draws a flat series at mid-height instead of dividing by a zero range', () => {
    expect(sparklinePoints([5, 5, 5], 100, 20)).toBe('0.0,10.0 50.0,10.0 100.0,10.0');
    expect(sparklinePoints([7], 100, 20)).toBe('0,10 100,10');
    expect(sparklinePoints([], 100, 20)).toBe('');
  });

  it('puts the highest value at the top of the box', () => {
    // SVG y grows downward, so the max must land at y=0.
    expect(sparklinePoints([0, 10], 100, 20)).toBe('0.0,20.0 100.0,0.0');
  });
});
