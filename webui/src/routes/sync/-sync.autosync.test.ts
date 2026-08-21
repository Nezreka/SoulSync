/**
 * Auto-Sync pure core — differential against the live auto-sync.js.
 *
 * Every ported function is executable outside the DOM, so this file lifts the
 * REAL vanilla bodies and runs them beside the port over matrices that include
 * the awkward inputs. The vanilla's module constants (AUTO_SYNC_BUCKETS, the
 * weekday tables) are supplied as a transcribed preamble; raw source anchors
 * pin the originals so a silent vanilla edit fails here.
 *
 * One deliberate divergence: the port null-guards `getMirroredSourceRef` and
 * friends with optional chaining where the vanilla would throw on null rows.
 * Differentials therefore only run over non-null inputs (the only ones any
 * caller produces).
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from '../../test/vanilla-extract';
import {
  AUTO_SYNC_TABS,
  autoSyncBuildLanes,
  autoSyncNormalizeTab,
  autoSyncParseCustomInterval,
  autoSyncSavedToast,
  autoSyncSchedulePayload,
  autoSyncSummary,
  isValidTimezone,
  autoSyncAutomationCardFields,
  autoSyncFormatTrigger,
  autoSyncHumanizeType,
  autoSyncMonitorSummary,
  autoSyncDelta,
  autoSyncDeltaClass,
  autoSyncDeltaLabel,
  autoSyncDurationLabel,
  autoSyncFormatDateTime,
  autoSyncHistoryLogLines,
  autoSyncHistoryMatchesFilter,
  autoSyncHistoryResultPills,
  autoSyncHistoryStats,
  autoSyncHistoryStatusClass,
  autoSyncHistoryStatusLabel,
  autoSyncHistoryTabs,
  autoSyncNextHistoryLimit,
  autoSyncNormalizeHistoryEntry,
  autoSyncParseHistoryObject,
  autoSyncTimeAgo,
  autoSyncValueLabel,
  autoSyncNormalizeHistoryFilter,
  autoSyncPipelineLatestLog,
  autoSyncPipelineProgress,
  autoSyncPipelineStatusClass,
  autoSyncPipelineStatusLabel,
  type PipelineState,
  autoSyncGroupBySource,
  autoSyncMatchesFilter,
  autoSyncNextRunLabel,
  autoSyncParseUTC,
  autoSyncPlaylistHealth,
  buildAutoSyncScheduleState,
  getAutoSyncPipelinePlaylists,
  AUTO_SYNC_BUCKETS,
  AUTO_SYNC_WEEKDAYS,
  AUTO_SYNC_WEEKDAY_LABELS,
  autoSyncActionForPlaylist,
  autoSyncBucketLabel,
  autoSyncCanSchedulePlaylist,
  autoSyncEnrichDiscoveryRows,
  autoSyncExpandPersonalizedRows,
  autoSyncGeneratedCountMap,
  autoSyncGroupSidebarRows,
  autoSyncHoursFromTrigger,
  autoSyncIntervalLabel,
  autoSyncIsPersonalizedAutomation,
  autoSyncIsPipelineAutomation,
  autoSyncIsScheduleOwned,
  autoSyncKindLabel,
  autoSyncLaneCadence,
  autoSyncPersonalizedEntry,
  autoSyncPlaylistIdFromAutomation,
  autoSyncRowIdForPersonalized,
  autoSyncSourceLabel,
  autoSyncTriggerForHours,
  autoSyncWeeklyFromTrigger,
  autoSyncWeeklyLabel,
  autoSyncWeeklyTrigger,
  detectBrowserTimezone,
  getMirroredSourceRef,
} from './-sync.autosync';

const AUTO_SYNC_SRC = readFileSync(resolve(process.cwd(), 'static/auto-sync.js'), 'utf8');

/**
 * The vanilla functions close over module constants; supply them transcribed.
 * The source anchors below keep the transcription honest.
 */
const PREAMBLE = `
const AUTO_SYNC_BUCKETS = [1, 2, 4, 8, 12, 16, 24, 48, 72, 168];
const AUTO_SYNC_WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const AUTO_SYNC_WEEKDAY_LABELS = {
    mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu',
    fri: 'Fri', sat: 'Sat', sun: 'Sun',
};
`;

const NAMES = [
  'getMirroredSourceRef',
  'autoSyncTriggerForHours',
  'autoSyncHoursFromTrigger',
  'autoSyncBucketLabel',
  'autoSyncIntervalLabel',
  'autoSyncLaneCadence',
  'detectBrowserTimezone',
  'autoSyncWeeklyTrigger',
  'autoSyncWeeklyFromTrigger',
  'autoSyncWeeklyLabel',
  'autoSyncSourceLabel',
  'autoSyncCanSchedulePlaylist',
  'autoSyncIsPipelineAutomation',
  'autoSyncPlaylistIdFromAutomation',
  'autoSyncIsScheduleOwned',
  'autoSyncKindLabel',
  'autoSyncEnrichDiscoveryRows',
  'autoSyncGeneratedCountMap',
  'autoSyncExpandPersonalizedRows',
  'autoSyncActionForPlaylist',
  'autoSyncIsPersonalizedAutomation',
  'autoSyncPersonalizedEntry',
  'autoSyncRowIdForPersonalized',
  'autoSyncGroupSidebarRows',
] as const;

type Vanilla = Record<(typeof NAMES)[number], (...args: never[]) => unknown>;

const body = NAMES.map((n) => extractFunction(n, AUTO_SYNC_SRC)).join('\n');
// eslint-disable-next-line @typescript-eslint/no-implied-eval
const vanilla = new Function(`${PREAMBLE}\n${body}\nreturn { ${NAMES.join(', ')} };`)() as Vanilla;

describe('constants stay anchored to the vanilla source', () => {
  it('transcribed preamble matches the live declarations', () => {
    expect(AUTO_SYNC_SRC).toContain(
      'const AUTO_SYNC_BUCKETS = [1, 2, 4, 8, 12, 16, 24, 48, 72, 168];',
    );
    expect(AUTO_SYNC_SRC).toContain(
      "const AUTO_SYNC_WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];",
    );
  });

  it('port constants match as literals', () => {
    expect(AUTO_SYNC_BUCKETS).toEqual([1, 2, 4, 8, 12, 16, 24, 48, 72, 168]);
    expect(AUTO_SYNC_WEEKDAYS).toEqual(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']);
    expect(AUTO_SYNC_WEEKDAY_LABELS.mon).toBe('Mon');
    expect(AUTO_SYNC_WEEKDAY_LABELS.sun).toBe('Sun');
  });
});

describe('getMirroredSourceRef (differential)', () => {
  const ROWS = [
    {
      source_ref: 'ref-1',
      description: 'https://x',
      source: 'spotify_public',
      source_playlist_id: 9,
    },
    {
      source: 'spotify_public',
      description: 'https://open.spotify.com/playlist/abc',
      source_playlist_id: 9,
    },
    { source: 'youtube', description: 'HTTPS://youtu.be/x', source_playlist_id: 'yt1' },
    { source: 'spotify', description: 'https://x', source_playlist_id: 'sp1' }, // desc ignored for other sources
    { source: 'spotify_public', description: 'not a url', source_playlist_id: 7 },
    { source: 'tidal', source_playlist_id: 12345 },
    { source: 'tidal' },
  ];

  it('matches the vanilla row for row', () => {
    for (const row of ROWS) {
      expect(getMirroredSourceRef(row), JSON.stringify(row)).toBe(
        vanilla.getMirroredSourceRef(row as never),
      );
    }
  });

  it('pins the URL-in-description trick as literals', () => {
    expect(
      getMirroredSourceRef({
        source: 'spotify_public',
        description: 'https://open.spotify.com/playlist/abc',
        source_playlist_id: 9,
      }),
    ).toBe('https://open.spotify.com/playlist/abc');
    expect(
      getMirroredSourceRef({
        source: 'spotify',
        description: 'https://x',
        source_playlist_id: 'sp1',
      }),
    ).toBe('sp1');
  });
});

describe('trigger codecs (differential)', () => {
  const HOURS = [0, 1, 2, 12, 23, 24, 25, 36, 48, 167, 168, 169, NaN, '24' as const];

  it('autoSyncTriggerForHours matches the vanilla', () => {
    for (const h of HOURS) {
      expect(autoSyncTriggerForHours(h as never), String(h)).toEqual(
        vanilla.autoSyncTriggerForHours(h as never),
      );
    }
  });

  it('pins the surprising arms', () => {
    // 0 and NaN both fall to the || 24 default.
    expect(autoSyncTriggerForHours(0)).toEqual({ interval: 1, unit: 'days' });
    expect(autoSyncTriggerForHours(25)).toEqual({ interval: 25, unit: 'hours' });
    expect(autoSyncTriggerForHours(168)).toEqual({ interval: 7, unit: 'days' });
  });

  const CONFIGS = [
    { interval: 90, unit: 'minutes' },
    { interval: 30, unit: 'minutes' },
    { interval: 2, unit: 'days' },
    { interval: 1, unit: 'weeks' },
    { interval: 5, unit: 'hours' },
    { interval: 5 },
    { interval: '3', unit: 'hours' },
    { interval: 0, unit: 'days' },
    {},
    null,
    undefined,
  ];

  it('autoSyncHoursFromTrigger matches the vanilla', () => {
    for (const c of CONFIGS) {
      expect(autoSyncHoursFromTrigger(c as never), JSON.stringify(c)).toBe(
        vanilla.autoSyncHoursFromTrigger(c as never),
      );
    }
  });

  it('pins minutes rounding and the null contract', () => {
    expect(autoSyncHoursFromTrigger({ interval: 90, unit: 'minutes' })).toBe(2);
    expect(autoSyncHoursFromTrigger({ interval: 30, unit: 'minutes' })).toBe(1);
    expect(autoSyncHoursFromTrigger({ interval: 1, unit: 'weeks' })).toBe(168);
    expect(autoSyncHoursFromTrigger(null)).toBe(null);
  });
});

describe('cadence labels (differential)', () => {
  const HOURS = [1, 2, 3, 12, 16, 23, 24, 36, 48, 72, 168];

  it('all three label maps match the vanilla', () => {
    for (const h of HOURS) {
      expect(autoSyncBucketLabel(h), `bucket ${h}`).toBe(vanilla.autoSyncBucketLabel(h as never));
      expect(autoSyncIntervalLabel(h), `interval ${h}`).toBe(
        vanilla.autoSyncIntervalLabel(h as never),
      );
      expect(autoSyncLaneCadence(h), `lane ${h}`).toBe(vanilla.autoSyncLaneCadence(h as never));
    }
  });

  it('pins the words as literals', () => {
    expect(autoSyncBucketLabel(168)).toBe('Weekly');
    expect(autoSyncBucketLabel(36)).toBe('1.5d'); // non-integer days render as-is
    expect(autoSyncIntervalLabel(24)).toBe('Every 1 day');
    expect(autoSyncIntervalLabel(48)).toBe('Every 2 days');
    expect(autoSyncLaneCadence(12)).toBe('Twice a day');
    expect(autoSyncLaneCadence(3)).toBe('Every 3h');
  });
});

describe('weekly codecs (differential)', () => {
  it('detectBrowserTimezone agrees with the vanilla in this environment', () => {
    expect(detectBrowserTimezone()).toBe(vanilla.detectBrowserTimezone());
  });

  const TRIGGER_INPUTS = [
    { time: '08:30', days: ['mon', 'fri'], tz: 'Europe/Berlin' },
    { time: '8:30', days: ['mon'], tz: 'UTC' },
    { time: 'garbage', days: ['mon', 'nope', 'sun'], tz: 'UTC' },
    { time: '10:00', days: 'not-an-array', tz: 'UTC' },
    { time: '10:00', days: ['tue'] }, // tz omitted → browser tz (same env both sides)
    {},
    undefined,
  ];

  it('autoSyncWeeklyTrigger matches the vanilla', () => {
    for (const input of TRIGGER_INPUTS) {
      expect(autoSyncWeeklyTrigger(input as never), JSON.stringify(input)).toEqual(
        vanilla.autoSyncWeeklyTrigger(input as never),
      );
    }
  });

  const PARSE_INPUTS = [
    { time: '08:30', days: ['mon', 'fri'], tz: 'Europe/Berlin' },
    { time: '08:30', days: ['MON', 'Fri'], tz: 'UTC' },
    { time: 'bad', days: [], tz: '' },
    { time: '09:00', days: ['nope'] },
    'a string',
    null,
    42,
  ];

  it('autoSyncWeeklyFromTrigger matches the vanilla', () => {
    for (const input of PARSE_INPUTS) {
      expect(autoSyncWeeklyFromTrigger(input as never), JSON.stringify(input)).toEqual(
        vanilla.autoSyncWeeklyFromTrigger(input as never),
      );
    }
  });

  it('pins the empty-days = every-day convention', () => {
    expect(autoSyncWeeklyFromTrigger({ time: '09:00', days: ['nope'] })).toEqual({
      time: '09:00',
      days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
      tz: 'UTC',
    });
    expect(autoSyncWeeklyFromTrigger(null)).toBe(null);
  });

  const LABEL_INPUTS = [
    null,
    { time: '09:00', days: [], tz: 'UTC' },
    { time: '09:00', days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], tz: 'UTC' },
    { time: '07:15', days: ['fri', 'mon'], tz: 'UTC' }, // out of order on purpose
    { time: '22:00', days: ['sun'], tz: 'UTC' },
  ];

  it('autoSyncWeeklyLabel matches the vanilla', () => {
    for (const input of LABEL_INPUTS) {
      expect(autoSyncWeeklyLabel(input as never), JSON.stringify(input)).toBe(
        vanilla.autoSyncWeeklyLabel(input as never),
      );
    }
  });

  it('pins canonical ordering as a literal', () => {
    expect(autoSyncWeeklyLabel({ time: '07:15', days: ['fri', 'mon'], tz: 'UTC' })).toBe(
      'Mon, Fri @ 07:15',
    );
    expect(autoSyncWeeklyLabel(null)).toBe('Unscheduled');
  });
});

describe('source labels + scheduling eligibility (differential)', () => {
  const SOURCES = [
    'spotify',
    'spotify_public',
    'tidal',
    'youtube',
    'deezer',
    'qobuz',
    'beatport',
    'file',
    'itunes_link',
    'listenbrainz',
    'lastfm',
    'soulsync_discovery',
    'something_new',
    '',
    null,
  ];

  it('autoSyncSourceLabel matches the vanilla for every source', () => {
    for (const s of SOURCES) {
      expect(autoSyncSourceLabel(s as never), String(s)).toBe(
        vanilla.autoSyncSourceLabel(s as never),
      );
    }
  });

  it('pins the odd labels as literals', () => {
    expect(autoSyncSourceLabel('file')).toBe('File Imports');
    expect(autoSyncSourceLabel('lastfm')).toBe('Last.fm Radio');
    expect(autoSyncSourceLabel('something_new')).toBe('something_new');
    expect(autoSyncSourceLabel(null)).toBe('Other');
  });

  it('autoSyncCanSchedulePlaylist excludes exactly file/beatport/lastfm', () => {
    for (const s of SOURCES.filter((x): x is string => typeof x === 'string')) {
      expect(autoSyncCanSchedulePlaylist({ source: s }), s).toBe(
        vanilla.autoSyncCanSchedulePlaylist({ source: s } as never),
      );
    }
    expect(autoSyncCanSchedulePlaylist({ source: 'file' })).toBe(false);
    expect(autoSyncCanSchedulePlaylist({ source: 'beatport' })).toBe(false);
    expect(autoSyncCanSchedulePlaylist({ source: 'lastfm' })).toBe(false);
    expect(autoSyncCanSchedulePlaylist({ source: 'spotify' })).toBe(true);
    expect(autoSyncCanSchedulePlaylist({})).toBe(true); // missing source is schedulable
    expect(autoSyncCanSchedulePlaylist(null)).toBe(false);
  });
});

describe('automation linkage (differential)', () => {
  const AUTOMATIONS = [
    { action_type: 'playlist_pipeline', action_config: { playlist_id: '42', all: false } },
    { action_type: 'playlist_pipeline', action_config: { all: true } },
    { action_type: 'playlist_pipeline', action_config: { all: 'true' } },
    { action_type: 'playlist_pipeline', action_config: { playlist_id: '' } },
    { action_type: 'playlist_pipeline', action_config: { playlist_id: 'abc' } },
    { action_type: 'playlist_pipeline', action_config: { playlist_id: 7 } },
    { action_type: 'playlist_pipeline' },
    { action_type: 'other' },
    null,
  ];

  it('autoSyncIsPipelineAutomation + autoSyncPlaylistIdFromAutomation match the vanilla', () => {
    for (const a of AUTOMATIONS) {
      // The vanilla's bare `auto && …` leaks null for a null row; the port
      // returns a real boolean. Truthiness-identical — compare coerced.
      expect(autoSyncIsPipelineAutomation(a as never), JSON.stringify(a)).toBe(
        Boolean(vanilla.autoSyncIsPipelineAutomation(a as never)),
      );
      expect(autoSyncPlaylistIdFromAutomation(a as never), JSON.stringify(a)).toBe(
        vanilla.autoSyncPlaylistIdFromAutomation(a as never),
      );
    }
  });

  const OWNERSHIP = [
    { owned_by: 'auto_sync' },
    { group_name: 'Playlist Auto-Sync' },
    { name: 'Auto-Sync: My Playlist' },
    { name: 'auto-sync: lowercase' },
    { owned_by: 'someone_else', group_name: 'Other', name: 'Sync stuff' },
    {},
    null,
  ];

  it('autoSyncIsScheduleOwned matches the vanilla (flag, legacy group, name prefix)', () => {
    for (const a of OWNERSHIP) {
      expect(autoSyncIsScheduleOwned(a as never), JSON.stringify(a)).toBe(
        vanilla.autoSyncIsScheduleOwned(a as never),
      );
    }
    expect(autoSyncIsScheduleOwned({ name: 'auto-sync: lowercase' })).toBe(false); // prefix is case-sensitive
  });
});

describe('personalized rows (differential)', () => {
  const KINDS = [
    { kind: 'daily_mix', name_template: 'Daily Mix' },
    {
      kind: 'time_machine',
      name_template: 'Time Machine — {variant}',
      requires_variant: true,
      variants: ['1990s', '2000s'],
    },
    { kind: 'time', name_template: 'Time — {variant}', requires_variant: true, variants: ['x'] },
    { kind: 'genre', name_template: 'Genre: {variant}', requires_variant: true, variants: [] },
  ];

  it('autoSyncKindLabel matches the vanilla, separators stripped', () => {
    for (const k of [...KINDS, { kind: 'bare' }, null]) {
      expect(autoSyncKindLabel(k as never), JSON.stringify(k)).toBe(
        vanilla.autoSyncKindLabel(k as never),
      );
    }
    expect(
      autoSyncKindLabel({ kind: 'time_machine', name_template: 'Time Machine — {variant}' }),
    ).toBe('Time Machine');
  });

  const PLAYLISTS = [
    { id: 1, source: 'spotify', source_playlist_id: 'sp1' },
    { id: 2, source: 'soulsync_discovery', source_playlist_id: 'ssd_daily_mix' },
    { id: 3, source: 'soulsync_discovery', source_playlist_id: 'ssd_time_machine_1990s' },
    { id: 4, source: 'soulsync_discovery', source_playlist_id: 'ssd_time_x' },
    { id: 5, source: 'soulsync_discovery', source_playlist_id: 'ssd_reverted_kind_1' },
    { id: 6, source: 'soulsync_discovery' },
  ];

  it('autoSyncEnrichDiscoveryRows matches the vanilla, longest-prefix rule included', () => {
    expect(autoSyncEnrichDiscoveryRows(PLAYLISTS, KINDS)).toEqual(
      vanilla.autoSyncEnrichDiscoveryRows(PLAYLISTS as never, KINDS as never),
    );
    // Fails open with no kinds metadata.
    expect(autoSyncEnrichDiscoveryRows(PLAYLISTS, [])).toEqual(
      vanilla.autoSyncEnrichDiscoveryRows(PLAYLISTS as never, [] as never),
    );
  });

  it('pins the drop rule: an unregistered ssd row disappears', () => {
    const out = autoSyncEnrichDiscoveryRows(PLAYLISTS, KINDS);
    expect(out.find((p) => p.id === 5)).toBeUndefined();
    // 'ssd_time_machine_1990s' must tag as time_machine, not the shorter 'time'.
    expect(out.find((p) => p.id === 3)?.kind).toBe('time_machine');
    expect(out.find((p) => p.id === 3)?.variant).toBe('1990s');
  });

  it('autoSyncGeneratedCountMap matches the vanilla', () => {
    const DATA = {
      success: true,
      playlists: [
        { kind: 'daily_mix', track_count: '25' },
        { kind: 'time_machine', variant: '1990s', track_count: 30 },
        { variant: 'orphan' },
        null,
      ],
    };
    expect([...autoSyncGeneratedCountMap(DATA).entries()]).toEqual([
      ...(vanilla.autoSyncGeneratedCountMap(DATA as never) as Map<string, number>).entries(),
    ]);
    expect([...autoSyncGeneratedCountMap({ success: false }).entries()]).toEqual([]);
  });

  it('autoSyncExpandPersonalizedRows matches the vanilla (negative ids, skip existing)', () => {
    const existing = [PLAYLISTS[1], PLAYLISTS[2]]; // ssd_daily_mix + ssd_time_machine_1990s
    const counts = new Map([['time_machine 2000s', 12]]);
    const ours = autoSyncExpandPersonalizedRows(KINDS, existing, counts);
    const theirs = vanilla.autoSyncExpandPersonalizedRows(
      KINDS as never,
      existing as never,
      counts as never,
    );
    expect(ours).toEqual(theirs);
    // daily_mix exists → skipped; 1990s exists → only 2000s + time/x remain.
    expect(ours.map((r) => r.source_playlist_id)).toEqual(['ssd_time_machine_2000s', 'ssd_time_x']);
    expect(ours.map((r) => r.id)).toEqual([-1, -2]);
    expect(ours[0].track_count).toBe(12);
  });

  it('autoSyncActionForPlaylist matches the vanilla for both row kinds', () => {
    const CASES = [
      { p: { _personalized: true, kind: 'time_machine', variant: '1990s' }, id: -1 },
      { p: { _personalized: true, kind: 'daily_mix', variant: '' }, id: -2 },
      { p: { id: 7, source: 'tidal' }, id: 7 },
      { p: null, id: 9 },
    ];
    for (const { p, id } of CASES) {
      expect(autoSyncActionForPlaylist(p as never, id), JSON.stringify(p)).toEqual(
        vanilla.autoSyncActionForPlaylist(p as never, id as never),
      );
    }
    // Empty variant must NOT put a variant key in the entry.
    expect(
      autoSyncActionForPlaylist({ _personalized: true, kind: 'daily_mix', variant: '' }, -2)
        .action_config,
    ).toEqual({ kinds: [{ kind: 'daily_mix' }], refresh_first: true });
  });

  it('autoSyncIsPersonalizedAutomation + autoSyncPersonalizedEntry match the vanilla', () => {
    const AUTOS = [
      {
        action_type: 'personalized_pipeline',
        action_config: { kinds: [{ kind: 'x', variant: 'y' }] },
      },
      { action_type: 'personalized_pipeline', action_config: { kinds: [{ kind: 'x' }] } },
      {
        action_type: 'personalized_pipeline',
        action_config: { kinds: [{ kind: 'a' }, { kind: 'b' }] },
      },
      { action_type: 'personalized_pipeline', action_config: { kinds: [{}] } },
      { action_type: 'personalized_pipeline', action_config: {} },
      { action_type: 'playlist_pipeline', action_config: { kinds: [{ kind: 'x' }] } },
      null,
    ];
    for (const a of AUTOS) {
      expect(autoSyncIsPersonalizedAutomation(a as never), JSON.stringify(a)).toBe(
        vanilla.autoSyncIsPersonalizedAutomation(a as never),
      );
      expect(autoSyncPersonalizedEntry(a as never), JSON.stringify(a)).toEqual(
        vanilla.autoSyncPersonalizedEntry(a as never),
      );
    }
  });

  it('autoSyncRowIdForPersonalized matches the vanilla (real row beats synthetic)', () => {
    const rows = [
      { id: 3, source: 'soulsync_discovery', source_playlist_id: 'ssd_time_machine_1990s' },
      { id: -4, _personalized: true, kind: 'time_machine', variant: '2000s' },
      { id: -5, _personalized: true, kind: 'daily_mix', variant: '' },
    ];
    const CASES = [
      { kind: 'time_machine', variant: '1990s' },
      { kind: 'time_machine', variant: '2000s' },
      { kind: 'daily_mix', variant: '' },
      { kind: 'missing', variant: '' },
      null,
    ];
    for (const entry of CASES) {
      expect(autoSyncRowIdForPersonalized(entry as never, rows), JSON.stringify(entry)).toBe(
        vanilla.autoSyncRowIdForPersonalized(entry as never, rows as never),
      );
    }
  });

  it('autoSyncGroupSidebarRows matches the vanilla (order + first-row label)', () => {
    const rows = [
      { id: 1, source: 'spotify' },
      { id: 2, kind: 'time_machine', variant: '1990s', kind_label: 'Time Machine' },
      { id: 3, kind: 'genre', variant: 'jazz' },
      { id: 4, kind: 'time_machine', variant: '2000s', kind_label: 'IGNORED (first wins)' },
      { id: 5, kind: 'singleton_no_variant' },
    ];
    const ours = autoSyncGroupSidebarRows(rows);
    expect(ours).toEqual(vanilla.autoSyncGroupSidebarRows(rows as never));
    expect(ours.flat.map((r) => r.id)).toEqual([1, 5]); // kind without variant stays flat
    expect(ours.groups.map((g) => g.label)).toEqual(['Time Machine', 'genre']);
    expect(ours.groups[0].rows.map((r) => r.id)).toEqual([2, 4]);
  });
});

describe('gaps the PR review proved unpinned (each mutation-verified)', () => {
  it('sub-hour minute intervals CLAMP to 1, never round to 0 (auto-sync.js 106)', () => {
    // Round alone would make a 10-minute schedule read as UNSCHEDULED.
    expect(autoSyncHoursFromTrigger({ interval: 10, unit: 'minutes' })).toBe(1);
    expect(autoSyncHoursFromTrigger({ interval: 29, unit: 'minutes' })).toBe(1);
    expect(autoSyncHoursFromTrigger({ interval: 30, unit: 'minutes' })).toBe(1);
    expect(autoSyncHoursFromTrigger({ interval: 90, unit: 'minutes' })).toBe(2);
  });

  it("all:'true' as a STRING still means all-playlists, even with a playlist_id", () => {
    // Without the string arm this pins an all-playlists schedule to one row.
    expect(
      autoSyncPlaylistIdFromAutomation({
        action_type: 'playlist_pipeline',
        action_config: { all: 'true', playlist_id: '42' },
      } as Parameters<typeof autoSyncPlaylistIdFromAutomation>[0]),
    ).toBeNull();
    expect(
      autoSyncPlaylistIdFromAutomation({
        action_type: 'playlist_pipeline',
        action_config: { all: true, playlist_id: '42' },
      } as Parameters<typeof autoSyncPlaylistIdFromAutomation>[0]),
    ).toBeNull();
    expect(
      autoSyncPlaylistIdFromAutomation({
        action_type: 'playlist_pipeline',
        action_config: { playlist_id: '42' },
      } as Parameters<typeof autoSyncPlaylistIdFromAutomation>[0]),
    ).toBe(42);
  });

  it('the synthetic fallback matches on kind AND variant (auto-sync.js 442)', () => {
    const rows = [
      { id: 7, _personalized: true, kind: 'decade', variant: '1990s' },
      { id: 8, _personalized: true, kind: 'decade', variant: '2000s' },
    ] as Parameters<typeof autoSyncRowIdForPersonalized>[1];
    // Kind alone would hand back the 1990s row for a 2000s entry.
    expect(autoSyncRowIdForPersonalized({ kind: 'decade', variant: '2000s' }, rows)).toBe(8);
    expect(autoSyncRowIdForPersonalized({ kind: 'decade', variant: '1990s' }, rows)).toBe(7);
  });
});

describe('buildAutoSyncScheduleState (471-569)', () => {
  const owned = { owned_by: 'auto_sync', action_type: 'playlist_pipeline' };
  const hourly = (id: number, playlistId: number, extra = {}) => ({
    ...owned,
    id,
    name: `Auto-Sync: p${playlistId}`,
    action_config: { playlist_id: playlistId },
    trigger_type: 'schedule',
    trigger_config: { interval: 24, unit: 'hours' },
    ...extra,
  });

  it('buckets an owned hourly pipeline onto its playlist', () => {
    const state = buildAutoSyncScheduleState([], [hourly(1, 7)]);
    expect(state.playlistSchedules['7']).toMatchObject({
      automation_id: 1,
      hours: 24,
      enabled: true,
      owned: true,
    });
    expect(state.automationPipelines).toEqual([]);
  });

  it('treats enabled as TRI-STATE, not truthiness', () => {
    // 487: false and 0 disable; anything else — including absent — enables.
    expect(buildAutoSyncScheduleState([], [hourly(1, 7)]).playlistSchedules['7'].enabled).toBe(
      true,
    );
    expect(
      buildAutoSyncScheduleState([], [hourly(1, 7, { enabled: false })]).playlistSchedules['7']
        .enabled,
    ).toBe(false);
    expect(
      buildAutoSyncScheduleState([], [hourly(1, 7, { enabled: 0 })]).playlistSchedules['7'].enabled,
    ).toBe(false);
    expect(
      buildAutoSyncScheduleState([], [hourly(1, 7, { enabled: 1 })]).playlistSchedules['7'].enabled,
    ).toBe(true);
  });

  it('sends a genuinely UNOWNED pipeline to the read-only panel', () => {
    // Ownership has THREE signals (autoSyncIsScheduleOwned) — the owned_by
    // flag, the legacy group name, and an 'Auto-Sync:' name prefix. Dropping
    // only the flag leaves the row owned via its name, so this fixture clears
    // all three or it proves nothing.
    const state = buildAutoSyncScheduleState(
      [],
      [{ ...hourly(1, 7), owned_by: undefined, name: 'Nightly refresh' }],
    );
    expect(state.playlistSchedules).toEqual({});
    expect(state.automationPipelines).toHaveLength(1);
  });

  it('accepts ownership by the legacy group name and by the name prefix', () => {
    const byGroup = buildAutoSyncScheduleState(
      [],
      [{ ...hourly(1, 7), owned_by: undefined, name: 'x', group_name: 'Playlist Auto-Sync' }],
    );
    expect(byGroup.playlistSchedules['7']).toBeDefined();

    const byName = buildAutoSyncScheduleState(
      [],
      [{ ...hourly(1, 7), owned_by: undefined, group_name: undefined }],
    );
    expect(byName.playlistSchedules['7']).toBeDefined();
  });

  it('sends an owned row with an UNPARSEABLE interval to the panel', () => {
    const state = buildAutoSyncScheduleState([], [hourly(1, 7, { trigger_config: {} })]);
    expect(state.playlistSchedules).toEqual({});
    expect(state.automationPipelines).toHaveLength(1);
  });

  it('buckets an owned weekly pipeline', () => {
    const state = buildAutoSyncScheduleState(
      [],
      [
        hourly(2, 9, {
          trigger_type: 'weekly_time',
          trigger_config: { time: '07:30', days: ['mon'], tz: 'UTC' },
        }),
      ],
    );
    expect(state.weeklySchedules['9']).toMatchObject({ time: '07:30', days: ['mon'], tz: 'UTC' });
    expect(state.automationPipelines).toEqual([]);
  });

  it('a NULL weekly trigger_config falls through as a broken row, NOT every-day', async () => {
    // 496-501, the vanilla's own comment. The schedule arm coerces with `|| {}`
    // and the weekly arm passes RAW, precisely so a hand-edited null lands in
    // the read-only panel instead of being handed to the codec's defensive
    // defaults — which would turn garbage into all seven days.
    const state = buildAutoSyncScheduleState(
      [],
      [hourly(3, 11, { trigger_type: 'weekly_time', trigger_config: null })],
    );
    expect(state.weeklySchedules).toEqual({});
    expect(state.automationPipelines).toHaveLength(1);

    // Proof the coercion WOULD have bucketed it: the codec accepts `{}` and
    // returns all seven days.
    expect(autoSyncWeeklyFromTrigger({})?.days).toHaveLength(7);
  });

  it('carries history through, defaulting both fields', () => {
    expect(buildAutoSyncScheduleState([], [])).toMatchObject({
      runHistory: [],
      runHistoryTotal: 0,
    });
    expect(buildAutoSyncScheduleState([], [], { history: [{ id: 1 }], total: 12 })).toMatchObject({
      runHistory: [{ id: 1 }],
      runHistoryTotal: 12,
    });
  });

  it('buckets a personalized schedule onto its row', () => {
    const playlists = [{ id: -5, _personalized: true, kind: 'weekly_mix', variant: 'a' }];
    const auto = {
      id: 4,
      name: 'Auto-Sync: mix',
      owned_by: 'auto_sync',
      action_type: 'personalized_pipeline',
      // action_config.kinds — an ARRAY of exactly one entry. A multi-kind
      // pipeline is an Automations-page construct and must never be mistaken
      // for a per-row board schedule.
      action_config: { kinds: [{ kind: 'weekly_mix', variant: 'a' }] },
      trigger_type: 'schedule',
      trigger_config: { interval: 12, unit: 'hours' },
    };
    const state = buildAutoSyncScheduleState(playlists, [auto]);
    expect(state.playlistSchedules['-5']).toMatchObject({ hours: 12 });
  });

  it('DROPS an unbucketable personalized row instead of panelling it', () => {
    // 526-528 vs 518 — the two passes disagree, and the difference is real:
    // the playlist_pipeline pass panels what it cannot bucket, this one does
    // not, so the row vanishes from the board entirely.
    const auto = {
      id: 5,
      owned_by: 'auto_sync',
      action_type: 'personalized_pipeline',
      action_config: { kinds: [{ kind: 'nope', variant: 'x' }] },
      trigger_type: 'schedule',
      trigger_config: { interval: 6, unit: 'hours' },
    };
    const state = buildAutoSyncScheduleState([], [auto]);
    expect(state.playlistSchedules).toEqual({});
    expect(state.automationPipelines).toEqual([]);
  });

  it('DROPS a personalized row whose TRIGGER is unbucketable, still without panelling', () => {
    // The earlier drop test returns early at the row-id lookup, so it never
    // reaches the trigger arms. This one resolves to a real row and then fails
    // to parse — the path where a stray `else` would panel it.
    const playlists = [{ id: -5, _personalized: true, kind: 'weekly_mix', variant: 'a' }];
    const auto = {
      id: 6,
      owned_by: 'auto_sync',
      action_type: 'personalized_pipeline',
      action_config: { kinds: [{ kind: 'weekly_mix', variant: 'a' }] },
      trigger_type: 'schedule',
      trigger_config: {},
    };
    const state = buildAutoSyncScheduleState(playlists, [auto]);
    expect(state.playlistSchedules).toEqual({});
    expect(state.automationPipelines).toEqual([]);
  });

  it('requires OWNERSHIP for a personalized row that would otherwise bucket', () => {
    const playlists = [{ id: -5, _personalized: true, kind: 'weekly_mix', variant: 'a' }];
    const auto = {
      id: 7,
      name: "Someone else's pipeline",
      action_type: 'personalized_pipeline',
      action_config: { kinds: [{ kind: 'weekly_mix', variant: 'a' }] },
      trigger_type: 'schedule',
      trigger_config: { interval: 12, unit: 'hours' },
    };
    // Identical row WITH ownership buckets, so the only difference is the flag.
    expect(
      buildAutoSyncScheduleState(playlists, [{ ...auto, owned_by: 'auto_sync' }]).playlistSchedules[
        '-5'
      ],
    ).toBeDefined();
    expect(buildAutoSyncScheduleState(playlists, [auto]).playlistSchedules).toEqual({});
  });

  it('drops an owned personalized row with a THIRD trigger type, without panelling', () => {
    // Neither 'schedule' nor 'weekly_time' — e.g. a manual personalized
    // pipeline. It resolves to a row and is owned, so it reaches the trigger
    // chain and falls off the end. The vanilla has no else there (526-557),
    // and adding one would surface it in the read-only panel.
    const playlists = [{ id: -5, _personalized: true, kind: 'weekly_mix', variant: 'a' }];
    const state = buildAutoSyncScheduleState(playlists, [
      {
        id: 8,
        owned_by: 'auto_sync',
        action_type: 'personalized_pipeline',
        action_config: { kinds: [{ kind: 'weekly_mix', variant: 'a' }] },
        trigger_type: 'manual',
        trigger_config: {},
      },
    ]);
    expect(state.playlistSchedules).toEqual({});
    expect(state.weeklySchedules).toEqual({});
    expect(state.automationPipelines).toEqual([]);
  });

  it('ignores automations that are not pipelines at all', () => {
    const state = buildAutoSyncScheduleState([], [{ action_type: 'something_else', id: 9 }]);
    expect(state.automationPipelines).toEqual([]);
    expect(state.playlistSchedules).toEqual({});
  });
});

describe("the hourly board's lane model (741-823)", () => {
  const row = (id: number, name: string, source = 'spotify') => ({ id, name, source });

  describe('autoSyncMatchesFilter', () => {
    it('matches the NAME or the SOURCE LABEL, case-insensitively', () => {
      const p = row(1, 'Late Night', 'tidal');
      expect(autoSyncMatchesFilter(p, 'late')).toBe(true);
      expect(autoSyncMatchesFilter(p, 'NIGHT')).toBe(true);
      // 744: the label, not the raw key — so 'tidal' finds it even though the
      // name does not contain it.
      expect(autoSyncMatchesFilter(p, 'tidal')).toBe(true);
      expect(autoSyncMatchesFilter(p, 'spotify')).toBe(false);
      // A source whose LABEL differs from its key proves it is the label being
      // searched: 'file' is labelled 'File Imports', so 'imports' matches
      // although the key contains no such text.
      expect(autoSyncMatchesFilter(row(2, 'Mixtape', 'file'), 'imports')).toBe(true);
    });

    it('matches everything for an empty or whitespace filter', () => {
      expect(autoSyncMatchesFilter(row(1, 'x'), '')).toBe(true);
      expect(autoSyncMatchesFilter(row(1, 'x'), '   ')).toBe(true);
    });

    it('survives a row with no name or source', () => {
      expect(autoSyncMatchesFilter({ id: 1 }, 'anything')).toBe(false);
      expect(autoSyncMatchesFilter({ id: 1 }, '')).toBe(true);
    });
  });

  describe('autoSyncGroupBySource', () => {
    it('groups by source, preserving encounter order within a group', () => {
      const groups = autoSyncGroupBySource([
        row(1, 'a', 'tidal'),
        row(2, 'b', 'file'),
        row(3, 'c', 'tidal'),
      ]);
      expect(groups.map((g) => g.source)).toEqual(['file', 'tidal']);
      expect(groups[1].rows.map((r) => r.id)).toEqual([1, 3]);
    });

    it('orders by DISPLAY LABEL, which the real labels cannot demonstrate', () => {
      // With today's twelve labels, key-order and label-order coincide for
      // every pair — so a test using real sources would pass just as happily
      // if this sorted by the key. An injected labeller that genuinely
      // reorders is the only way to assert the actual rule.
      const label = (s: string) => ({ zebra: 'Alpha', alpha: 'Zebra' })[s] || s;
      const groups = autoSyncGroupBySource([row(1, 'a', 'alpha'), row(2, 'b', 'zebra')], label);
      expect(groups.map((g) => g.source)).toEqual(['zebra', 'alpha']);
    });

    it("buckets a source-less row under 'other'", () => {
      expect(autoSyncGroupBySource([{ id: 1, name: 'x' }])[0].source).toBe('other');
    });

    it('is empty for no rows', () => {
      expect(autoSyncGroupBySource([])).toEqual([]);
    });
  });

  describe('autoSyncBuildLanes', () => {
    it('renders the ten standard buckets even when nothing is scheduled', () => {
      const lanes = autoSyncBuildLanes([], {});
      expect(lanes.map((l) => l.hours)).toEqual([...AUTO_SYNC_BUCKETS]);
      expect(lanes.every((l) => !l.isCustom && l.playlists.length === 0)).toBe(true);
    });

    it('MERGES an in-use custom interval into its sorted position', () => {
      // 795-802. A 6h schedule made on the Automations page would otherwise
      // have no lane and vanish from the board.
      const lanes = autoSyncBuildLanes([row(1, 'a')], { '1': { hours: 6 } });
      const hours = lanes.map((l) => l.hours);
      expect(hours).toContain(6);
      // Sorted ascending, so it sits between 4 and 8 rather than at the end.
      expect(hours.indexOf(6)).toBe(hours.indexOf(4) + 1);
      expect(hours.indexOf(8)).toBe(hours.indexOf(6) + 1);
      expect(lanes.find((l) => l.hours === 6)?.isCustom).toBe(true);
      expect(lanes.find((l) => l.hours === 24)?.isCustom).toBe(false);
    });

    it('does not duplicate a lane when TWO playlists share a custom interval', () => {
      // Both contribute 6 to customHours; without the de-dupe the board grows
      // two identical 6-hour lanes.
      const lanes = autoSyncBuildLanes([row(1, 'a'), row(2, 'b')], {
        '1': { hours: 6 },
        '2': { hours: 6 },
      });
      expect(lanes.filter((l) => l.hours === 6)).toHaveLength(1);
      expect(lanes.find((l) => l.hours === 6)?.playlists.map((p) => p.id)).toEqual([1, 2]);
    });

    it('does not duplicate a lane when a schedule uses a standard bucket', () => {
      const lanes = autoSyncBuildLanes([row(1, 'a')], { '1': { hours: 24 } });
      expect(lanes.filter((l) => l.hours === 24)).toHaveLength(1);
      expect(lanes.map((l) => l.hours)).toEqual([...AUTO_SYNC_BUCKETS]);
    });

    it('rejects corrupt hour values rather than inventing a lane', () => {
      const lanes = autoSyncBuildLanes([], {
        a: { hours: 0 },
        b: { hours: -5 },
        c: { hours: Number.NaN },
        d: undefined,
      });
      expect(lanes.map((l) => l.hours)).toEqual([...AUTO_SYNC_BUCKETS]);
    });

    it('assigns each playlist to the lane matching its scheduled hours', () => {
      const lanes = autoSyncBuildLanes([row(1, 'a'), row(2, 'b'), row(3, 'c')], {
        '1': { hours: 24 },
        '2': { hours: 24 },
        '3': { hours: 1 },
      });
      expect(lanes.find((l) => l.hours === 24)?.playlists.map((p) => p.id)).toEqual([1, 2]);
      expect(lanes.find((l) => l.hours === 1)?.playlists.map((p) => p.id)).toEqual([3]);
      expect(lanes.find((l) => l.hours === 48)?.playlists).toEqual([]);
    });

    it('leaves an UNSCHEDULED playlist out of every lane', () => {
      const lanes = autoSyncBuildLanes([row(1, 'a')], {});
      expect(lanes.every((l) => l.playlists.length === 0)).toBe(true);
    });

    it('handles the negative ids the synthetic personalized rows carry', () => {
      // The synthetic rows use NEGATIVE ids so they never collide with real
      // mirrored ones; they must still land in a lane.
      const lanes = autoSyncBuildLanes([row(-5, 'mix')], { '-5': { hours: 12 } });
      expect(lanes.find((l) => l.hours === 12)?.playlists.map((p) => p.id)).toEqual([-5]);
    });
  });
});

describe('the scheduled-card helpers (1978-2011)', () => {
  const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);

  describe('autoSyncParseUTC', () => {
    it('treats a bare timestamp as UTC rather than local', () => {
      expect(autoSyncParseUTC('2026-01-01T12:00:00')).toBe(NOW);
    });

    it('respects a timestamp that already carries its zone', () => {
      expect(autoSyncParseUTC('2026-01-01T12:00:00Z')).toBe(NOW);
      expect(autoSyncParseUTC('2026-01-01T13:00:00+01:00')).toBe(NOW);
      expect(autoSyncParseUTC('2026-01-01T11:00:00-01:00')).toBe(NOW);
    });

    it('is NaN for something unparseable, which callers check for', () => {
      expect(Number.isNaN(autoSyncParseUTC('not a date'))).toBe(true);
    });
  });

  describe('autoSyncNextRunLabel', () => {
    const at = (ms: number) => new Date(NOW + ms).toISOString().replace('Z', '');

    it('counts minutes below the hour and hours below the day', () => {
      expect(autoSyncNextRunLabel(at(90_000), NOW)).toBe('next in 2m');
      expect(autoSyncNextRunLabel(at(59 * 60_000), NOW)).toBe('next in 59m');
      // 60 minutes exactly crosses into the hours branch.
      expect(autoSyncNextRunLabel(at(60 * 60_000), NOW)).toBe('next in 1h');
      expect(autoSyncNextRunLabel(at(23 * 3600_000), NOW)).toBe('next in 23h');
      expect(autoSyncNextRunLabel(at(25 * 3600_000), NOW)).toBe('next in 2d');
    });

    it('rounds UP, so a run 30 seconds out is a minute away not zero', () => {
      expect(autoSyncNextRunLabel(at(30_000), NOW)).toBe('next in 1m');
    });

    it("says 'due now' once the moment has passed", () => {
      expect(autoSyncNextRunLabel(at(0), NOW)).toBe('due now');
      expect(autoSyncNextRunLabel(at(-60_000), NOW)).toBe('due now');
    });

    it('is empty for a missing or unparseable timestamp', () => {
      expect(autoSyncNextRunLabel(null, NOW)).toBe('');
      expect(autoSyncNextRunLabel('', NOW)).toBe('');
      expect(autoSyncNextRunLabel('garbage', NOW)).toBe('');
    });
  });

  describe('autoSyncPlaylistHealth', () => {
    const h = (playlist_id: number, status: string) => ({ playlist_id, status });

    it('is ok with no history at all', () => {
      expect(autoSyncPlaylistHealth([], 1)).toEqual({ level: 'ok', tooltip: '' });
      expect(autoSyncPlaylistHealth(null, 1).level).toBe('ok');
    });

    it('counts a skipped run as a failure, not a success', () => {
      expect(autoSyncPlaylistHealth([h(1, 'skipped'), h(1, 'success')], 1)).toEqual({
        level: 'warning',
        tooltip: '1 of last 2 runs failed',
      });
    });

    it('goes red only at three failures in the window', () => {
      expect(autoSyncPlaylistHealth([h(1, 'error'), h(1, 'error')], 1).level).toBe('warning');
      expect(autoSyncPlaylistHealth([h(1, 'error'), h(1, 'error'), h(1, 'error')], 1)).toEqual({
        level: 'failing',
        tooltip: 'Last 3 runs failed — check Run History tab',
      });
    });

    it('only reads the first three rows for that playlist', () => {
      const history = [h(1, 'success'), h(1, 'success'), h(1, 'success'), h(1, 'error')];
      expect(autoSyncPlaylistHealth(history, 1).level).toBe('ok');
    });

    it('filters by playlist BEFORE taking three, so another playlist cannot crowd it out', () => {
      // Three foreign rows sit in front; without the filter-first order the
      // window would contain none of playlist 1's runs.
      const history = [h(9, 'success'), h(9, 'success'), h(9, 'success'), h(1, 'error')];
      expect(autoSyncPlaylistHealth(history, 1)).toEqual({
        level: 'warning',
        tooltip: '1 of last 1 runs failed',
      });
    });

    it('matches a string playlist id against a numeric one', () => {
      expect(autoSyncPlaylistHealth([{ playlist_id: '7', status: 'error' }], 7).level).toBe(
        'warning',
      );
    });
  });
});

describe('getAutoSyncPipelinePlaylists (1104-1114)', () => {
  const p = (id: number, state: PipelineState | null) => ({
    id,
    name: `P${id}`,
    pipeline_state: state,
  });

  it('orders RUNNING first regardless of timestamps', () => {
    // Worth asserting here rather than only through the monitor panel: the
    // panel re-partitions running-first itself, so deleting this rule is
    // invisible from there. Any other consumer would silently lose it.
    const out = getAutoSyncPipelinePlaylists([
      p(1, { status: 'finished', finished_at: 9000 }),
      p(2, { status: 'running', started_at: 1 }),
    ]);
    expect(out.map((i) => i.playlist.id)).toEqual([2, 1]);
  });

  it('breaks ties on finished_at, falling back to started_at', () => {
    const out = getAutoSyncPipelinePlaylists([
      p(1, { status: 'error', started_at: 100 }),
      p(2, { status: 'finished', finished_at: 300 }),
      p(3, { status: 'skipped', started_at: 200 }),
    ]);
    expect(out.map((i) => i.playlist.id)).toEqual([2, 3, 1]);
  });

  it('drops idle and state-less rows', () => {
    const out = getAutoSyncPipelinePlaylists([
      p(1, { status: 'idle' }),
      p(2, null),
      p(3, {}),
      p(4, { status: 'running' }),
    ]);
    expect(out.map((i) => i.playlist.id)).toEqual([4]);
  });
});

describe('autoSyncFormatTrigger defaults (stats-automations.js 4155)', () => {
  it('defaults a schedule trigger to every 1 hours', () => {
    expect(autoSyncFormatTrigger('schedule', {}, undefined)).toBe('Every 1 hours');
  });

  it('keeps a supplied interval and unit', () => {
    expect(autoSyncFormatTrigger('schedule', { interval: 30, unit: 'minutes' }, undefined)).toBe(
      'Every 30 minutes',
    );
  });

  it('defaults a daily or weekly time to midnight', () => {
    expect(autoSyncFormatTrigger('daily_time', {}, undefined)).toBe('Daily at 00:00');
    expect(autoSyncFormatTrigger('weekly_time', { days: ['mon'] }, undefined)).toBe('Mon at 00:00');
  });

  it('names an unknown signal', () => {
    expect(autoSyncFormatTrigger('signal_received', {}, undefined)).toBe('Signal: unknown');
  });

  it('needs a config to take the typed branches at all', () => {
    // Every typed branch is guarded on `config` being truthy, so a missing
    // config falls through to the label map / humanizer.
    expect(autoSyncFormatTrigger('schedule', undefined, undefined)).toBe('Schedule');
  });
});

describe('the monitor pure core (1116-1129, 1163-1165)', () => {
  it('maps every pipeline status to its label', () => {
    expect(autoSyncPipelineStatusLabel('running')).toBe('Running');
    expect(autoSyncPipelineStatusLabel('finished')).toBe('Completed');
    expect(autoSyncPipelineStatusLabel('skipped')).toBe('Skipped');
    expect(autoSyncPipelineStatusLabel('error')).toBe('Needs attention');
    expect(autoSyncPipelineStatusLabel('anything else')).toBe('Idle');
    expect(autoSyncPipelineStatusLabel(undefined)).toBe('Idle');
  });

  it('maps status to class, with skipped SHARING the error class', () => {
    // The label distinguishes them; the styling does not. Both are true at
    // once and neither is a typo.
    expect(autoSyncPipelineStatusClass('skipped')).toBe('error');
    expect(autoSyncPipelineStatusClass('error')).toBe('error');
    expect(autoSyncPipelineStatusClass('running')).toBe('running');
    expect(autoSyncPipelineStatusClass('finished')).toBe('finished');
    expect(autoSyncPipelineStatusClass(undefined)).toBe('idle');
  });

  it('clamps progress into 0-100 and treats junk as zero', () => {
    expect(autoSyncPipelineProgress(42)).toBe(42);
    expect(autoSyncPipelineProgress('42')).toBe(42);
    expect(autoSyncPipelineProgress(500)).toBe(100);
    expect(autoSyncPipelineProgress(-1)).toBe(0);
    expect(autoSyncPipelineProgress('nonsense')).toBe(0);
    expect(autoSyncPipelineProgress(undefined)).toBe(0);
  });

  it('reads the NEWEST log line, and survives every empty shape', () => {
    expect(autoSyncPipelineLatestLog({ log: [{ message: 'a' }, { message: 'b' }] })).toBe('b');
    expect(autoSyncPipelineLatestLog({ log: [] })).toBe('');
    expect(autoSyncPipelineLatestLog({})).toBe('');
    expect(autoSyncPipelineLatestLog(null)).toBe('');
    expect(autoSyncPipelineLatestLog({ log: [{}] })).toBe('');
  });

  it('summarises the visible set, the count and both copy lines', () => {
    const idle = autoSyncMonitorSummary([]);
    expect(idle).toEqual({
      visible: [],
      runningCount: 0,
      title: 'No pipelines running',
      detail: 'Use Run now on a scheduled playlist when you want the pipeline immediately.',
    });

    const busy = autoSyncMonitorSummary([
      { id: 1, pipeline_state: { status: 'running' } },
      { id: 2, pipeline_state: { status: 'finished', finished_at: 5 } },
    ]);
    expect(busy.runningCount).toBe(1);
    expect(busy.title).toBe('1 pipeline running');
    expect(busy.detail).toBe('Live status refreshes while this modal is open.');
    expect(busy.visible.map((v) => v.playlist.id)).toEqual([1, 2]);
  });
});

describe('autoSyncHumanizeType (stats-automations.js 4144-4152)', () => {
  it('turns snake_case into words', () => {
    expect(autoSyncHumanizeType('deep_scan_library')).toBe('Deep Scan Library');
  });

  it('strips a leading video_ or music_ prefix, but only leading', () => {
    expect(autoSyncHumanizeType('video_deep_scan')).toBe('Deep Scan');
    expect(autoSyncHumanizeType('music_import')).toBe('Import');
    expect(autoSyncHumanizeType('scan_video_library')).toBe('Scan Video Library');
  });

  it("never lets an empty type through as ''", () => {
    expect(autoSyncHumanizeType('')).toBe('Unknown');
    expect(autoSyncHumanizeType(null)).toBe('Unknown');
    expect(autoSyncHumanizeType('__')).toBe('Unknown');
  });
});

describe('autoSyncAutomationCardFields (1883-1893)', () => {
  const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);
  const base = {
    id: 1,
    name: 'Nightly',
    action_type: 'playlist_pipeline',
    trigger_type: 'schedule',
    trigger_config: { interval: 6, unit: 'hours' },
    action_config: { playlist_id: 5 },
  };

  it('resolves the playlist name and its source label', () => {
    expect(
      autoSyncAutomationCardFields(base, [{ id: 5, name: 'Late Night', source: 'tidal' }], NOW),
    ).toEqual({
      name: 'Nightly',
      trigger: 'Every 6 hours',
      target: 'Late Night',
      sourceLabel: 'Tidal',
      next: 'not scheduled',
      enabled: true,
    });
  });

  it('describes an all-playlists pipeline without a playlist lookup', () => {
    const f = autoSyncAutomationCardFields({ ...base, action_config: { all: 'true' } }, [], NOW);
    expect(f.target).toBe('All refreshable mirrored playlists');
    expect(f.sourceLabel).toBe('All sources');
  });

  it('falls back through id, then to a generic target', () => {
    expect(autoSyncAutomationCardFields(base, [], NOW).target).toBe('Playlist #5');
    expect(autoSyncAutomationCardFields({ ...base, action_config: {} }, [], NOW).target).toBe(
      'Custom pipeline target',
    );
  });
});

describe('autoSyncNextHistoryLimit (1251-1254)', () => {
  it('steps by 50', () => {
    expect(autoSyncNextHistoryLimit(50)).toBe(100);
    expect(autoSyncNextHistoryLimit(100)).toBe(150);
  });

  it('stops at 500 however many times it is clicked', () => {
    expect(autoSyncNextHistoryLimit(480)).toBe(500);
    expect(autoSyncNextHistoryLimit(500)).toBe(500);
    expect(autoSyncNextHistoryLimit(9000)).toBe(500);
  });
});

describe('autoSyncNormalizeHistoryFilter (1246-1249)', () => {
  it('keeps the two real filters and rejects anything else', () => {
    expect(autoSyncNormalizeHistoryFilter('error')).toBe('error');
    expect(autoSyncNormalizeHistoryFilter('completed')).toBe('completed');
    expect(autoSyncNormalizeHistoryFilter('all')).toBe('all');
    expect(autoSyncNormalizeHistoryFilter('nonsense')).toBe('all');
    expect(autoSyncNormalizeHistoryFilter(undefined)).toBe('all');
  });
});

describe('the run-history pure core (1574-1882)', () => {
  const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);

  it('parses a snapshot from an object, a JSON string, or junk', () => {
    expect(autoSyncParseHistoryObject({ a: 1 })).toEqual({ a: 1 });
    expect(autoSyncParseHistoryObject('{"a":1}')).toEqual({ a: 1 });
    expect(autoSyncParseHistoryObject('not json')).toEqual({});
    expect(autoSyncParseHistoryObject('[1,2]')).toEqual([1, 2]);
    // A JSON scalar parses fine but is not an object, so it is discarded.
    expect(autoSyncParseHistoryObject('42')).toEqual({});
    expect(autoSyncParseHistoryObject(null)).toEqual({});
    expect(autoSyncParseHistoryObject(7)).toEqual({});
  });

  it('normalizes a row, parsing its three payloads and defaulting its id', () => {
    const out = autoSyncNormalizeHistoryEntry(
      { playlist_name: 'x', before_json: '{"track_count":1}' },
      3,
    );
    expect(out.id).toBe('history-3');
    expect(out.before_json).toEqual({ track_count: 1 });
    expect(out.after_json).toEqual({});
    expect(out.playlist_name).toBe('x');
    // A real id is kept, including 0 — `??` not `||`.
    expect(autoSyncNormalizeHistoryEntry({ id: 0 }, 1).id).toBe(0);
  });

  it('substitutes a whole placeholder row for a non-object', () => {
    for (const junk of [null, undefined, 'string', 42]) {
      const out = autoSyncNormalizeHistoryEntry(junk as never, 2);
      expect(out.id).toBe('unknown-2');
      expect(out.playlist_name).toBe('Playlist pipeline run');
      expect(out.status).toBe('completed');
      expect(out.trigger_source).toBe('pipeline');
    }
  });

  it('labels and colours a run status', () => {
    expect(autoSyncHistoryStatusLabel('completed')).toBe('Completed');
    expect(autoSyncHistoryStatusLabel('finished')).toBe('Completed');
    expect(autoSyncHistoryStatusLabel('error')).toBe('Error');
    expect(autoSyncHistoryStatusLabel('skipped')).toBe('Skipped');
    // An unknown status is echoed, not replaced.
    expect(autoSyncHistoryStatusLabel('quarantined')).toBe('quarantined');
    expect(autoSyncHistoryStatusLabel(undefined)).toBe('Run');

    expect(autoSyncHistoryStatusClass('error')).toBe('disabled');
    expect(autoSyncHistoryStatusClass('skipped')).toBe('disabled');
    expect(autoSyncHistoryStatusClass('completed')).toBe('enabled');
    expect(autoSyncHistoryStatusClass('anything')).toBe('enabled');
  });

  it('formats a duration, switching to m/s at a minute', () => {
    expect(autoSyncDurationLabel(0)).toBe('0s');
    expect(autoSyncDurationLabel(59)).toBe('59s');
    expect(autoSyncDurationLabel(60)).toBe('1m 0s');
    expect(autoSyncDurationLabel(75.4)).toBe('1m 15s');
    expect(autoSyncDurationLabel(-5)).toBe('0s');
    expect(autoSyncDurationLabel('nonsense')).toBe('0s');
  });

  it('computes and labels a delta', () => {
    expect(autoSyncDelta(12, 10)).toBe(2);
    expect(autoSyncDelta(10, 12)).toBe(-2);
    expect(autoSyncDelta(undefined, undefined)).toBe(0);
    expect(autoSyncDelta('12', '10')).toBe(2);

    expect(autoSyncDeltaLabel(12, 2, 'tracks')).toBe('12 tracks (+2)');
    expect(autoSyncDeltaLabel(10, -2, 'tracks')).toBe('10 tracks (-2)');
    expect(autoSyncDeltaLabel(10, 0, 'tracks')).toBe('10 tracks');

    expect(autoSyncDeltaClass(1)).toBe('pos');
    expect(autoSyncDeltaClass(-1)).toBe('neg');
    expect(autoSyncDeltaClass(0)).toBe('zero');
  });

  it('renders a relative age, and a date only when parseable', () => {
    expect(autoSyncTimeAgo(undefined, NOW)).toBe('Never');
    expect(autoSyncTimeAgo(new Date(NOW - 30_000).toISOString(), NOW)).toBe('just now');
    expect(autoSyncTimeAgo(new Date(NOW - 5 * 60_000).toISOString(), NOW)).toBe('5m ago');
    expect(autoSyncTimeAgo(new Date(NOW - 5 * 3600_000).toISOString(), NOW)).toBe('5h ago');
    expect(autoSyncTimeAgo(new Date(NOW - 5 * 86400_000).toISOString(), NOW)).toBe('5d ago');

    expect(autoSyncFormatDateTime(undefined)).toBe('');
    // Unparseable input is echoed RAW rather than swallowed (1864).
    expect(autoSyncFormatDateTime('whenever')).toBe('whenever');
    expect(autoSyncFormatDateTime('2026-01-01T12:00:00')).toContain('2026');
  });

  it('builds the three filter tabs from the whole window', () => {
    const tabs = autoSyncHistoryTabs([
      { status: 'completed' },
      { status: 'finished' },
      { status: 'error' },
    ]);
    expect(tabs.map((t) => [t.key, t.count, t.hasErrors])).toEqual([
      ['all', 3, false],
      ['error', 1, true],
      ['completed', 2, false],
    ]);
    expect(autoSyncHistoryTabs([])[1].hasErrors).toBe(false);
  });

  it('matches the filter predicate the tabs and the list share', () => {
    expect(autoSyncHistoryMatchesFilter({ status: 'skipped' }, 'error')).toBe(true);
    expect(autoSyncHistoryMatchesFilter({ status: 'finished' }, 'completed')).toBe(true);
    expect(autoSyncHistoryMatchesFilter({ status: 'error' }, 'completed')).toBe(false);
    expect(autoSyncHistoryMatchesFilter({ status: 'anything' }, 'all')).toBe(true);
    expect(autoSyncHistoryMatchesFilter(null, 'error')).toBe(false);
    expect(autoSyncHistoryMatchesFilter(null, 'all')).toBe(true);
  });

  it('takes the newest 20 log lines, in every shape they arrive in', () => {
    expect(autoSyncHistoryLogLines(undefined)).toEqual([]);
    expect(autoSyncHistoryLogLines([])).toEqual([]);
    expect(
      autoSyncHistoryLogLines([
        'plain',
        { message: 'msg', type: 'warn' },
        { log_line: 'legacy', log_type: 'error' },
        { nothing: true } as never,
      ]),
    ).toEqual([
      { text: 'plain', type: 'info' },
      { text: 'msg', type: 'warn' },
      { text: 'legacy', type: 'error' },
      // No message key at all → the object serialises itself.
      { text: '{"nothing":true}', type: 'info' },
    ]);

    const many = Array.from({ length: 25 }, (_, i) => `l${i}`);
    const kept = autoSyncHistoryLogLines(many);
    expect(kept).toHaveLength(20);
    expect(kept[0].text).toBe('l5');
  });

  it('builds the four stat cards in a fixed order', () => {
    const stats = autoSyncHistoryStats(
      { track_count: 10, discovered_count: 1 },
      { track_count: 12, discovered_count: 1 },
    );
    expect(stats.map((s) => s.label)).toEqual(['Tracks', 'Discovered', 'Wishlisted', 'In library']);
    expect(stats[0]).toEqual({ label: 'Tracks', before: 10, after: 12, delta: 2 });
    // A stat absent from both snapshots is zeroed, not dropped.
    expect(stats[3]).toEqual({ label: 'In library', before: 0, after: 0, delta: 0 });
  });

  it('keeps only result pills that carry a value, and never the status string', () => {
    expect(
      autoSyncHistoryResultPills({
        playlists_refreshed: 1,
        tracks_synced: 0,
        sync_skipped: null,
        wishlist_queued: '',
        tracks_discovered: 'completed',
      }),
    ).toEqual([
      { label: 'Refreshed', value: '1' },
      // Zero is a real count and stays.
      { label: 'Synced', value: '0' },
    ]);
    expect(autoSyncHistoryResultPills({})).toEqual([]);
  });

  it('labels a value for display, recursing into arrays', () => {
    expect(autoSyncValueLabel(undefined)).toBe('Not recorded');
    expect(autoSyncValueLabel('')).toBe('Not recorded');
    expect(autoSyncValueLabel(true)).toBe('Yes');
    expect(autoSyncValueLabel(false)).toBe('No');
    expect(autoSyncValueLabel([])).toBe('None');
    expect(autoSyncValueLabel(['a', true])).toBe('a, Yes');
    expect(autoSyncValueLabel({ a: 1 })).toBe('{"a":1}');
    expect(autoSyncValueLabel(0)).toBe('0');
  });
});

describe('the modal shell pure core (571-740, 1303-1313)', () => {
  it('names the four tabs, in the order the header renders them', () => {
    expect(AUTO_SYNC_TABS).toEqual(['schedule', 'weekly', 'automations', 'history']);
  });

  it('falls back to the hourly board for an unknown tab (733)', () => {
    expect(autoSyncNormalizeTab('weekly')).toBe('weekly');
    expect(autoSyncNormalizeTab('history')).toBe('history');
    expect(autoSyncNormalizeTab('nonsense')).toBe('schedule');
    expect(autoSyncNormalizeTab(undefined)).toBe('schedule');
  });

  it('summarises an empty board as zeroes', () => {
    expect(
      autoSyncSummary({
        playlists: [],
        playlistSchedules: {},
        weeklySchedules: {},
        automationPipelines: [],
        runHistory: [],
      }),
    ).toEqual({
      scheduledCount: 0,
      pausedCount: 0,
      enabledCount: 0,
      pipelineCount: 0,
      totalTracks: 0,
      historyErrorCount: 0,
    });
  });

  it('sums both schedule maps and parses a string track count', () => {
    const s = autoSyncSummary({
      playlists: [{ id: 1, track_count: 10 }, { id: 2, track_count: '5' }, { id: 3 }],
      playlistSchedules: { '1': { hours: 24, enabled: true } as never },
      weeklySchedules: { '2': { enabled: false } as never },
      automationPipelines: [{ id: 7 }],
      runHistory: [{ status: 'error' }, { status: 'skipped' }, { status: 'completed' }],
    });
    expect(s).toEqual({
      scheduledCount: 2,
      // scheduled minus enabled: the exception the strip actually shows.
      pausedCount: 1,
      enabledCount: 1,
      pipelineCount: 1,
      totalTracks: 15,
      historyErrorCount: 2,
    });
  });

  it('reads enabled as TRUTHINESS, which no other consumer does (660)', () => {
    // Documented divergence inside the vanilla itself: a schedule whose
    // `enabled` is missing counts as scheduled but not as active, where the
    // cards would call it enabled. Transcribed so the number matches.
    const s = autoSyncSummary({
      playlists: [],
      playlistSchedules: { '1': { hours: 24 } as never },
      weeklySchedules: {},
      automationPipelines: [],
      runHistory: [],
    });
    expect(s.scheduledCount).toBe(1);
    expect(s.enabledCount).toBe(0);
  });

  it('validates a custom interval the way the prompt used to', () => {
    expect(autoSyncParseCustomInterval('6')).toEqual({ hours: 6 });
    // parseInt semantics are kept: a trailing unit is ignored, not rejected.
    expect(autoSyncParseCustomInterval('36h')).toEqual({ hours: 36 });
    for (const bad of ['0', '-1', 'abc', '']) {
      expect(autoSyncParseCustomInterval(bad)).toEqual({
        error: 'Interval must be a whole number of hours, 1 or greater',
      });
    }
  });
});

describe('the save payload (2069-2082, 2266-2277)', () => {
  const playlist = { id: 5, name: 'Late Night', source: 'spotify', source_playlist_id: 'abc' };

  it('carries the three fields ownership is later read back from', () => {
    const payload = autoSyncSchedulePayload(playlist, 5, {
      trigger_type: 'schedule',
      trigger_config: { interval: 24, unit: 'hours' },
    });
    // autoSyncIsScheduleOwned reads owned_by, then falls back to the group and
    // the name prefix for pre-column rows — so all three matter.
    expect(payload.owned_by).toBe('auto_sync');
    expect(payload.group_name).toBe('Playlist Auto-Sync');
    expect(payload.name).toBe('Auto-Sync: Late Night');
    expect(payload.then_actions).toEqual([]);
    expect(payload.trigger_type).toBe('schedule');
    expect(payload.trigger_config).toEqual({ interval: 24, unit: 'hours' });
  });

  it('is identical apart from the trigger for the weekly path', () => {
    const hourly = autoSyncSchedulePayload(playlist, 5, {
      trigger_type: 'schedule',
      trigger_config: { interval: 24, unit: 'hours' },
    });
    const weekly = autoSyncSchedulePayload(playlist, 5, {
      trigger_type: 'weekly_time',
      trigger_config: { time: '09:00', days: ['mon'], tz: 'UTC' },
    });
    const strip = (p: Record<string, unknown>) => {
      const { trigger_type, trigger_config, ...rest } = p;
      void trigger_type;
      void trigger_config;
      return rest;
    };
    expect(strip(weekly)).toEqual(strip(hourly));
  });

  it('carries the action the row resolves to', () => {
    const payload = autoSyncSchedulePayload(playlist, 5, {
      trigger_type: 'schedule',
      trigger_config: {},
    });
    expect(payload.action_type).toBe('playlist_pipeline');
  });
});

describe('autoSyncSavedToast (2098, 2283)', () => {
  it('reads as an interval for hourly and as a phrase for weekly', () => {
    expect(autoSyncSavedToast('Late Night', 'hourly', '12h')).toBe(
      'Late Night scheduled every 12h',
    );
    // The weekly label arrives already capitalised, and is lowercased here.
    expect(autoSyncSavedToast('Late Night', 'weekly', 'Mon, Fri @ 09:00')).toBe(
      'Late Night scheduled mon, fri @ 09:00',
    );
  });
});

describe('isValidTimezone', () => {
  // The field is free text and a typo does not fail loudly: it produces a
  // schedule that quietly never runs at the hour you meant.
  it('accepts real IANA zones', () => {
    for (const tz of ['UTC', 'America/Los_Angeles', 'Europe/London', 'Asia/Tokyo']) {
      expect(isValidTimezone(tz)).toBe(true);
    }
  });

  it('rejects a typo, an empty value and obvious nonsense', () => {
    for (const tz of ['', 'America/Los_Angles', 'Europe/Lundon', 'nonsense', 'GMT+25']) {
      expect(isValidTimezone(tz)).toBe(false);
    }
  });
});
