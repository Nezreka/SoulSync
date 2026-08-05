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
