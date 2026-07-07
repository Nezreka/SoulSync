// Tests for the pure-function helpers in `webui/static/auto-sync.js`.
// Run via:
//
//     node --test tests/static/test_auto_sync.mjs
//
// The pytest wrapper at `tests/test_auto_sync_js.py` shells out to
// `node --test` and surfaces the result inside the regular pytest run.
//
// The module is loaded into a sandboxed `vm` context with stubs for
// the few globals it relies on (`_autoParseUTC` for the timezone-aware
// next_run label). No DOM — just the calculation contract.

import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// Values returned from the sandboxed VM context are cross-realm — their
// prototype chain differs from the test realm's, so deepStrictEqual on
// raw VM objects fails even when shape and values match. JSON-round-trip
// to compare structural equality only.
function deepShapeEqual(actual, expected, msg) {
    assert.deepEqual(
        JSON.parse(JSON.stringify(actual)),
        JSON.parse(JSON.stringify(expected)),
        msg,
    );
}

const __dirname = dirname(fileURLToPath(import.meta.url));
const AUTOSYNC_PATH = resolve(__dirname, '..', '..', 'webui', 'static', 'auto-sync.js');

let AUTOSYNC_SOURCE;
before(() => {
    AUTOSYNC_SOURCE = readFileSync(AUTOSYNC_PATH, 'utf8');
});

// Match the actual implementation in stats-automations.js so the
// timezone-bug fix is exercised end-to-end through auto-sync.js.
function realAutoParseUTC(ts) {
    if (!ts) return NaN;
    if (/[Zz]$/.test(ts) || /[+-]\d{2}:\d{2}$/.test(ts)) return new Date(ts).getTime();
    return new Date(ts + 'Z').getTime();
}

function makeSandbox() {
    const sandbox = {
        window: {},
        document: { getElementById: () => null, body: {}, querySelectorAll: () => [] },
        console: { debug: () => {}, error: () => {}, log: () => {} },
        fetch: async () => { throw new Error('fetch not stubbed for this test'); },
        // Globals that auto-sync.js expects to find in the window namespace
        _autoParseUTC: realAutoParseUTC,
        _autoFormatTrigger: () => 'trigger',
        _esc: (s) => String(s),
        _escAttr: (s) => String(s),
        showToast: () => {},
        showConfirmDialog: async () => true,
        loadMirroredPlaylists: () => {},
        updateMirroredCardPhase: () => {},
        openMirroredPlaylistModal: () => {},
        closeMirroredModal: () => {},
        youtubePlaylistStates: {},
        setInterval: () => 0,
        clearInterval: () => {},
    };
    vm.createContext(sandbox);
    vm.runInContext(AUTOSYNC_SOURCE, sandbox);
    return sandbox;
}

// =========================================================================
// autoSyncTriggerForHours / autoSyncHoursFromTrigger — round-trip
// =========================================================================

describe('autoSyncTriggerForHours', () => {
    test('sub-day intervals become hours', () => {
        const sb = makeSandbox();
        deepShapeEqual(sb.autoSyncTriggerForHours(1), { interval: 1, unit: 'hours' });
        deepShapeEqual(sb.autoSyncTriggerForHours(12), { interval: 12, unit: 'hours' });
    });

    test('whole-day multiples become days', () => {
        const sb = makeSandbox();
        deepShapeEqual(sb.autoSyncTriggerForHours(24), { interval: 1, unit: 'days' });
        deepShapeEqual(sb.autoSyncTriggerForHours(48), { interval: 2, unit: 'days' });
        deepShapeEqual(sb.autoSyncTriggerForHours(168), { interval: 7, unit: 'days' });
    });

    test('non-day-multiple > 24 stays as hours', () => {
        const sb = makeSandbox();
        // 36h doesn't divide evenly into days, stay as hours
        deepShapeEqual(sb.autoSyncTriggerForHours(36), { interval: 36, unit: 'hours' });
    });

    test('invalid input defaults to 24 hours', () => {
        const sb = makeSandbox();
        // Per autoSyncTriggerForHours: `parseInt(undefined, 10) || 24` → 24, becomes 1 day
        deepShapeEqual(sb.autoSyncTriggerForHours(undefined), { interval: 1, unit: 'days' });
    });
});

describe('autoSyncHoursFromTrigger', () => {
    test('hours unit returned directly', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncHoursFromTrigger({ interval: 6, unit: 'hours' }), 6);
    });

    test('days unit multiplied by 24', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncHoursFromTrigger({ interval: 3, unit: 'days' }), 72);
    });

    test('weeks unit multiplied by 168', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncHoursFromTrigger({ interval: 1, unit: 'weeks' }), 168);
    });

    test('minutes unit rounds up to at least 1 hour', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncHoursFromTrigger({ interval: 30, unit: 'minutes' }), 1);
        assert.equal(sb.autoSyncHoursFromTrigger({ interval: 90, unit: 'minutes' }), 2);
    });

    test('zero or missing interval returns null', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncHoursFromTrigger({}), null);
        assert.equal(sb.autoSyncHoursFromTrigger({ interval: 0 }), null);
    });

    test('round-trip with autoSyncTriggerForHours preserves hour count', () => {
        const sb = makeSandbox();
        for (const hours of [1, 4, 12, 24, 48, 168]) {
            const config = sb.autoSyncTriggerForHours(hours);
            assert.equal(sb.autoSyncHoursFromTrigger(config), hours, `round-trip ${hours}`);
        }
    });
});

// =========================================================================
// Label helpers
// =========================================================================

describe('autoSyncBucketLabel', () => {
    test('weekly bucket', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncBucketLabel(168), 'Weekly');
    });

    test('day-multiple buckets', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncBucketLabel(24), '1d');
        assert.equal(sb.autoSyncBucketLabel(48), '2d');
    });

    test('sub-day buckets', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncBucketLabel(1), '1h');
        assert.equal(sb.autoSyncBucketLabel(12), '12h');
    });
});

describe('autoSyncIntervalLabel', () => {
    test('pluralization', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncIntervalLabel(1), 'Every 1 hour');
        assert.equal(sb.autoSyncIntervalLabel(2), 'Every 2 hours');
        assert.equal(sb.autoSyncIntervalLabel(24), 'Every 1 day');
        assert.equal(sb.autoSyncIntervalLabel(48), 'Every 2 days');
        assert.equal(sb.autoSyncIntervalLabel(168), 'Every week');
    });
});

describe('autoSyncSourceLabel', () => {
    test('known sources mapped', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncSourceLabel('spotify'), 'Spotify');
        assert.equal(sb.autoSyncSourceLabel('youtube'), 'YouTube');
    });

    test('unknown source returns the raw key', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncSourceLabel('newthing'), 'newthing');
    });

    test('falsy source returns "Other"', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncSourceLabel(''), 'Other');
        assert.equal(sb.autoSyncSourceLabel(null), 'Other');
    });
});

// =========================================================================
// Schedulability and ownership predicates
// =========================================================================

describe('autoSyncCanSchedulePlaylist', () => {
    test('blocks file and beatport sources', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncCanSchedulePlaylist({ source: 'file' }), false);
        assert.equal(sb.autoSyncCanSchedulePlaylist({ source: 'beatport' }), false);
    });

    test('allows refreshable sources', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncCanSchedulePlaylist({ source: 'spotify' }), true);
        assert.equal(sb.autoSyncCanSchedulePlaylist({ source: 'youtube' }), true);
    });

    test('null/undefined playlist returns falsy', () => {
        const sb = makeSandbox();
        assert.ok(!sb.autoSyncCanSchedulePlaylist(null));
        assert.ok(!sb.autoSyncCanSchedulePlaylist(undefined));
    });
});

describe('autoSyncIsPipelineAutomation', () => {
    test('matches playlist_pipeline action type', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncIsPipelineAutomation({ action_type: 'playlist_pipeline' }), true);
        assert.equal(sb.autoSyncIsPipelineAutomation({ action_type: 'process_wishlist' }), false);
    });
});

describe('autoSyncPlaylistIdFromAutomation', () => {
    test('extracts numeric playlist_id', () => {
        const sb = makeSandbox();
        const auto = { action_type: 'playlist_pipeline', action_config: { playlist_id: '42' } };
        assert.equal(sb.autoSyncPlaylistIdFromAutomation(auto), 42);
    });

    test('returns null when all=true (catch-all pipeline)', () => {
        const sb = makeSandbox();
        const auto = { action_type: 'playlist_pipeline', action_config: { all: true } };
        assert.equal(sb.autoSyncPlaylistIdFromAutomation(auto), null);
    });

    test('returns null for non-pipeline automations', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncPlaylistIdFromAutomation({ action_type: 'other' }), null);
    });

    test('returns null when playlist_id missing', () => {
        const sb = makeSandbox();
        const auto = { action_type: 'playlist_pipeline', action_config: {} };
        assert.equal(sb.autoSyncPlaylistIdFromAutomation(auto), null);
    });
});

describe('autoSyncIsScheduleOwned', () => {
    test('owned_by="auto_sync" wins over name/group', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncIsScheduleOwned({
            owned_by: 'auto_sync', name: 'Whatever', group_name: 'unrelated',
        }), true);
    });

    test('legacy name-prefix still recognized', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncIsScheduleOwned({ name: 'Auto-Sync: Discover Weekly' }), true);
    });

    test('legacy group_name still recognized', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncIsScheduleOwned({ group_name: 'Playlist Auto-Sync' }), true);
    });

    test('automation with no owned_by and no legacy markers returns false', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncIsScheduleOwned({ name: 'My Custom Pipeline' }), false);
    });
});

// =========================================================================
// State partitioning
// =========================================================================

describe('buildAutoSyncScheduleState', () => {
    test('partitions board-owned schedules from custom pipelines', () => {
        const sb = makeSandbox();
        const playlists = [{ id: 1, name: 'Discover Weekly' }, { id: 2, name: 'Top Hits' }];
        const automations = [
            {
                id: 10, action_type: 'playlist_pipeline', trigger_type: 'schedule',
                trigger_config: { interval: 1, unit: 'hours' },
                action_config: { playlist_id: '1' },
                owned_by: 'auto_sync',
                enabled: 1,
            },
            {
                id: 11, action_type: 'playlist_pipeline', trigger_type: 'schedule',
                trigger_config: { interval: 1, unit: 'days' },
                action_config: { playlist_id: '99' },  // not in playlists, but custom-owned
                enabled: 1,
                // no owned_by → custom pipeline
            },
        ];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        assert.equal(Object.keys(state.playlistSchedules).length, 1);
        assert.equal(state.playlistSchedules[1].automation_id, 10);
        assert.equal(state.playlistSchedules[1].hours, 1);
        assert.equal(state.automationPipelines.length, 1);
        assert.equal(state.automationPipelines[0].id, 11);
    });

    test('non-pipeline automations are ignored entirely', () => {
        const sb = makeSandbox();
        const automations = [
            { id: 20, action_type: 'process_wishlist', trigger_type: 'schedule' },
        ];
        const state = sb.buildAutoSyncScheduleState([], automations);
        deepShapeEqual(state.playlistSchedules, {});
        deepShapeEqual(state.automationPipelines, []);
    });
});

// =========================================================================
// Timezone-aware countdown — the headline bug this branch fixed
// =========================================================================

describe('autoSyncNextRunLabel', () => {
    test('empty string for missing input', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncNextRunLabel(''), '');
        assert.equal(sb.autoSyncNextRunLabel(null), '');
    });

    test('naive UTC string is parsed as UTC, not local', () => {
        const sb = makeSandbox();
        // Pick a time exactly one hour from now in UTC. If the parser
        // mistakenly treats the bare timestamp as LOCAL it would land
        // wildly far from 1h on machines in non-UTC timezones —
        // that's exactly the bug Cin's review flagged.
        const future = new Date(Date.now() + 60 * 60 * 1000);
        const iso = future.toISOString().slice(0, 19).replace('T', ' ');
        const label = sb.autoSyncNextRunLabel(iso);
        // Allow either "next in 60m" (right at the boundary) or "next in 1h"
        assert.ok(/^next in (60m|1h)$/.test(label), `expected ~1h, got "${label}"`);
    });

    test('"due now" when timestamp is in the past', () => {
        const sb = makeSandbox();
        const past = new Date(Date.now() - 60 * 1000).toISOString().slice(0, 19).replace('T', ' ');
        assert.equal(sb.autoSyncNextRunLabel(past), 'due now');
    });

    test('day-scale label when timestamp is days out', () => {
        const sb = makeSandbox();
        const future = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000);
        const iso = future.toISOString().slice(0, 19).replace('T', ' ');
        const label = sb.autoSyncNextRunLabel(iso);
        assert.match(label, /^next in \dd$/);
    });
});

// =========================================================================
// getMirroredSourceRef — playlist source URL resolution
// =========================================================================

describe('getMirroredSourceRef', () => {
    test('explicit source_ref wins', () => {
        const sb = makeSandbox();
        assert.equal(
            sb.getMirroredSourceRef({ source_ref: 'https://example.com/x' }),
            'https://example.com/x',
        );
    });

    test('falls back to description URL for spotify_public', () => {
        const sb = makeSandbox();
        const p = {
            source: 'spotify_public',
            description: 'https://open.spotify.com/playlist/abc',
        };
        assert.equal(sb.getMirroredSourceRef(p), 'https://open.spotify.com/playlist/abc');
    });

    test('non-URL description ignored, falls through to source_playlist_id', () => {
        const sb = makeSandbox();
        const p = {
            source: 'spotify_public',
            description: 'just a note about this playlist',
            source_playlist_id: 'abc123',
        };
        assert.equal(sb.getMirroredSourceRef(p), 'abc123');
    });

    test('empty playlist returns empty string', () => {
        const sb = makeSandbox();
        assert.equal(sb.getMirroredSourceRef({}), '');
    });
});


// =========================================================================
// Weekly schedule helpers — PR 3 of the schedule-types feature.
// =========================================================================


describe('detectBrowserTimezone', () => {
    test('returns IANA tz from Intl in the test runtime', () => {
        const sb = makeSandbox();
        // Node runs with a system tz; the resolved value must be a
        // non-empty string (any IANA name is acceptable here).
        const tz = sb.detectBrowserTimezone();
        assert.equal(typeof tz, 'string');
        assert.ok(tz.length > 0);
    });
});


describe('autoSyncWeeklyTrigger', () => {
    test('builds a clean payload from picker input', () => {
        const sb = makeSandbox();
        const result = sb.autoSyncWeeklyTrigger({
            time: '09:00',
            days: ['mon', 'wed', 'fri'],
            tz: 'America/Los_Angeles',
        });
        deepShapeEqual(result, {
            time: '09:00',
            days: ['mon', 'wed', 'fri'],
            tz: 'America/Los_Angeles',
        });
    });

    test('garbage time string defaults to 09:00', () => {
        const sb = makeSandbox();
        const result = sb.autoSyncWeeklyTrigger({
            time: 'lol', days: ['mon'], tz: 'UTC',
        });
        assert.equal(result.time, '09:00');
    });

    test('unrecognised weekday abbreviations dropped from payload', () => {
        const sb = makeSandbox();
        const result = sb.autoSyncWeeklyTrigger({
            time: '09:00',
            days: ['mon', 'garbage', 'wed', 'mond'],
            tz: 'UTC',
        });
        deepShapeEqual(result.days, ['mon', 'wed']);
    });

    test('missing tz falls back to browser-detected default', () => {
        const sb = makeSandbox();
        const result = sb.autoSyncWeeklyTrigger({
            time: '09:00', days: ['mon'],
        });
        assert.equal(typeof result.tz, 'string');
        assert.ok(result.tz.length > 0);
    });

    test('empty argument object produces all-defaults payload', () => {
        const sb = makeSandbox();
        const result = sb.autoSyncWeeklyTrigger({});
        assert.equal(result.time, '09:00');
        deepShapeEqual(result.days, []);
        assert.equal(typeof result.tz, 'string');
    });

    test('non-array days param defaults to empty', () => {
        const sb = makeSandbox();
        const result = sb.autoSyncWeeklyTrigger({
            time: '09:00', days: 'mon', tz: 'UTC',
        });
        deepShapeEqual(result.days, []);
    });
});


describe('autoSyncWeeklyFromTrigger', () => {
    test('round-trips with autoSyncWeeklyTrigger when days non-empty', () => {
        const sb = makeSandbox();
        const cfg = sb.autoSyncWeeklyTrigger({
            time: '09:00', days: ['mon', 'wed'], tz: 'UTC',
        });
        const parsed = sb.autoSyncWeeklyFromTrigger(cfg);
        deepShapeEqual(parsed, {
            time: '09:00', days: ['mon', 'wed'], tz: 'UTC',
        });
    });

    test('empty days list expands to every weekday', () => {
        const sb = makeSandbox();
        // Matches the next_run_at convention: empty days = every day.
        // UI needs the expanded form so the schedule renders under all
        // 7 day columns instead of looking unscheduled.
        const parsed = sb.autoSyncWeeklyFromTrigger({
            time: '14:00', days: [], tz: 'UTC',
        });
        deepShapeEqual(parsed.days,
            ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']);
    });

    test('uppercased / mixed-case day abbreviations normalised', () => {
        const sb = makeSandbox();
        const parsed = sb.autoSyncWeeklyFromTrigger({
            time: '09:00', days: ['MON', 'Wed'], tz: 'UTC',
        });
        deepShapeEqual(parsed.days, ['mon', 'wed']);
    });

    test('null / undefined config returns null', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncWeeklyFromTrigger(null), null);
        assert.equal(sb.autoSyncWeeklyFromTrigger(undefined), null);
    });

    test('garbage time falls back to 09:00', () => {
        const sb = makeSandbox();
        const parsed = sb.autoSyncWeeklyFromTrigger({
            time: 'garbage', days: ['mon'], tz: 'UTC',
        });
        assert.equal(parsed.time, '09:00');
    });

    test('missing tz defaults to UTC (not browser tz)', () => {
        // Trigger configs persisted in the DB without a tz field came
        // from the legacy engine path that used server-local naive
        // ``datetime.now()``. PR 2 routes those through the engine's
        // ``_default_tz``, NOT the browser's. So parse-back must surface
        // a stable default (UTC) — the UI should NOT silently rewrite
        // an existing row's tz when the user opens the editor.
        const sb = makeSandbox();
        const parsed = sb.autoSyncWeeklyFromTrigger({
            time: '09:00', days: ['mon'],
        });
        assert.equal(parsed.tz, 'UTC');
    });
});


describe('buildAutoSyncScheduleState — weekly_time bucketing', () => {
    test('weekly_time owned automation lands in weeklySchedules', () => {
        const sb = makeSandbox();
        const playlists = [{ id: 7, name: 'Daily Mix', source: 'spotify' }];
        const automations = [{
            id: 42,
            name: 'Auto-Sync: Daily Mix',
            enabled: true,
            owned_by: 'auto_sync',
            action_type: 'playlist_pipeline',
            action_config: { playlist_id: '7', all: false },
            trigger_type: 'weekly_time',
            trigger_config: { time: '09:00', days: ['mon', 'wed', 'fri'], tz: 'America/Los_Angeles' },
            next_run: '2026-06-01 16:00:00',
        }];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        assert.ok(state.weeklySchedules);
        const sched = state.weeklySchedules[7];
        assert.ok(sched, 'weekly schedule must surface in state.weeklySchedules[playlistId]');
        assert.equal(sched.automation_id, 42);
        assert.equal(sched.time, '09:00');
        deepShapeEqual(sched.days, ['mon', 'wed', 'fri']);
        assert.equal(sched.tz, 'America/Los_Angeles');
        // And NOT in playlistSchedules (mutual exclusion at the bucket level).
        assert.equal(state.playlistSchedules[7], undefined);
    });

    test('schedule (interval) automation stays in playlistSchedules', () => {
        const sb = makeSandbox();
        const playlists = [{ id: 7, name: 'Daily Mix', source: 'spotify' }];
        const automations = [{
            id: 42,
            owned_by: 'auto_sync',
            action_type: 'playlist_pipeline',
            action_config: { playlist_id: '7', all: false },
            trigger_type: 'schedule',
            trigger_config: { interval: 6, unit: 'hours' },
            enabled: true,
        }];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        assert.ok(state.playlistSchedules[7]);
        assert.equal(state.weeklySchedules[7], undefined);
    });

    test('non-owned weekly_time automation falls through to automationPipelines', () => {
        // Backward-compat: a hand-created weekly_time automation
        // NOT owned by auto_sync must NOT hijack the Weekly Board
        // — it stays as a regular automation pipeline visible on
        // the Automation Pipelines tab.
        const sb = makeSandbox();
        const playlists = [{ id: 7, name: 'Daily Mix', source: 'spotify' }];
        const automations = [{
            id: 99,
            name: 'My Custom Weekly Thing',
            // No owned_by, no Auto-Sync: prefix, no Playlist Auto-Sync group.
            action_type: 'playlist_pipeline',
            action_config: { playlist_id: '7', all: false },
            trigger_type: 'weekly_time',
            trigger_config: { time: '09:00', days: ['mon'], tz: 'UTC' },
            enabled: true,
        }];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        assert.equal(state.weeklySchedules[7], undefined);
        assert.equal(state.playlistSchedules[7], undefined);
        assert.equal(state.automationPipelines.length, 1);
        assert.equal(state.automationPipelines[0].id, 99);
    });

    test('legacy-named (Auto-Sync: prefix) weekly_time still recognised', () => {
        // Rows pre-dating the owned_by column should still be picked
        // up by the legacy name/group fallback.
        const sb = makeSandbox();
        const playlists = [{ id: 7, name: 'Daily Mix', source: 'spotify' }];
        const automations = [{
            id: 99,
            name: 'Auto-Sync: Daily Mix',  // legacy convention
            group_name: 'Playlist Auto-Sync',
            action_type: 'playlist_pipeline',
            action_config: { playlist_id: '7', all: false },
            trigger_type: 'weekly_time',
            trigger_config: { time: '09:00', days: ['mon'], tz: 'UTC' },
            enabled: true,
        }];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        assert.ok(state.weeklySchedules[7], 'legacy-named auto-sync row should bucket weekly');
    });

    test('garbage weekly_time config falls through to automationPipelines', () => {
        // Defensive — a hand-edited row with malformed trigger_config
        // should NOT crash state-build. autoSyncWeeklyFromTrigger
        // returns null for non-object configs; the bucket logic
        // routes nulls to automationPipelines as the catch-all.
        const sb = makeSandbox();
        const playlists = [{ id: 7, name: 'Daily Mix', source: 'spotify' }];
        const automations = [{
            id: 42,
            owned_by: 'auto_sync',
            action_type: 'playlist_pipeline',
            action_config: { playlist_id: '7', all: false },
            trigger_type: 'weekly_time',
            trigger_config: null,
            enabled: true,
        }];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        assert.equal(state.weeklySchedules[7], undefined);
        assert.equal(state.automationPipelines.length, 1);
    });
});


describe('autoSyncWeeklyLabel', () => {
    test('multi-day schedule renders ordered day list with time', () => {
        const sb = makeSandbox();
        // Input intentionally in non-canonical order to verify sort.
        const label = sb.autoSyncWeeklyLabel({
            time: '09:00', days: ['fri', 'mon', 'wed'], tz: 'UTC',
        });
        assert.equal(label, 'Mon, Wed, Fri @ 09:00');
    });

    test('full-week schedule collapses to Daily', () => {
        const sb = makeSandbox();
        const label = sb.autoSyncWeeklyLabel({
            time: '14:30',
            days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
            tz: 'UTC',
        });
        assert.equal(label, 'Daily @ 14:30');
    });

    test('single-day schedule shows just that day', () => {
        const sb = makeSandbox();
        const label = sb.autoSyncWeeklyLabel({
            time: '20:00', days: ['sun'], tz: 'UTC',
        });
        assert.equal(label, 'Sun @ 20:00');
    });

    test('null parsed value returns Unscheduled', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncWeeklyLabel(null), 'Unscheduled');
    });

    test('empty days array treated as daily (matches engine semantic)', () => {
        const sb = makeSandbox();
        const label = sb.autoSyncWeeklyLabel({
            time: '09:00', days: [], tz: 'UTC',
        });
        assert.equal(label, 'Daily @ 09:00');
    });
});

// =========================================================================
// Personalized (SoulSync Discovery) rows in the Auto-Sync board
// =========================================================================

describe('autoSyncExpandPersonalizedRows', () => {
    test('singleton kind → one synthetic row, no variant', () => {
        const sb = makeSandbox();
        const rows = sb.autoSyncExpandPersonalizedRows(
            [{ kind: 'popular_picks', name_template: 'Popular Picks' }], []);
        deepShapeEqual(rows, [{
            id: -1, source: 'soulsync_discovery', name: 'Popular Picks', track_count: 0,
            kind: 'popular_picks', variant: '', kind_label: '', source_playlist_id: 'ssd_popular_picks',
            _personalized: true,
        }]);
    });

    test('uses generated counts when provided (refreshed-but-unsynced variant)', () => {
        const sb = makeSandbox();
        // time_machine 2000s has been generated (100 tracks) but not synced, so it
        // has no mirror row; the synthetic row should still show 100, not 0.
        const genCounts = sb.autoSyncGeneratedCountMap({
            success: true,
            playlists: [{ kind: 'time_machine', variant: '2000s', track_count: 100 }],
        });
        const rows = sb.autoSyncExpandPersonalizedRows([{
            kind: 'time_machine', requires_variant: true, variants: ['2000s', '2010s'],
            name_template: 'Time Machine — {variant}',
        }], [], genCounts);
        const r2000 = rows.find(r => r.variant === '2000s');
        const r2010 = rows.find(r => r.variant === '2010s');
        assert.equal(r2000.track_count, 100);  // real generated count
        assert.equal(r2010.track_count, 0);    // never generated
    });

    test('track_count is 0 when no generated-counts map is passed', () => {
        const sb = makeSandbox();
        const rows = sb.autoSyncExpandPersonalizedRows(
            [{ kind: 'hidden_gems', name_template: 'Hidden Gems' }], []);
        assert.equal(rows[0].track_count, 0);
    });

    test('variant kind → one synthetic row per variant, ids stay negative', () => {
        const sb = makeSandbox();
        const rows = sb.autoSyncExpandPersonalizedRows([{
            kind: 'time_machine', requires_variant: true,
            variants: ['1980s', '1990s', '2000s'],
            name_template: 'Time Machine — {variant}',
        }], []);
        assert.equal(rows.length, 3);
        deepShapeEqual(rows.map(r => r.id), [-1, -2, -3]);
        deepShapeEqual(rows.map(r => r.variant), ['1980s', '1990s', '2000s']);
        deepShapeEqual(rows.map(r => r.source_playlist_id),
            ['ssd_time_machine_1980s', 'ssd_time_machine_1990s', 'ssd_time_machine_2000s']);
        assert.equal(rows[0].name, 'Time Machine — 1980s');
        assert.equal(rows[0].kind, 'time_machine');
        assert.equal(rows[0]._personalized, true);
    });

    test('skips a variant already present as a real mirrored row', () => {
        const sb = makeSandbox();
        const existing = [{
            id: 42, source: 'soulsync_discovery', source_playlist_id: 'ssd_time_machine_1980s',
            name: 'Time Machine — 1980s',
        }];
        const rows = sb.autoSyncExpandPersonalizedRows([{
            kind: 'time_machine', requires_variant: true,
            variants: ['1980s', '1990s'], name_template: 'Time Machine — {variant}',
        }], existing);
        // 1980s exists as real → skipped; only 1990s becomes synthetic.
        assert.equal(rows.length, 1);
        assert.equal(rows[0].variant, '1990s');
        assert.equal(rows[0].source_playlist_id, 'ssd_time_machine_1990s');
    });

    test('skips a singleton already present as a real mirrored row', () => {
        const sb = makeSandbox();
        const existing = [{
            id: 7, source: 'soulsync_discovery', source_playlist_id: 'ssd_hidden_gems',
        }];
        const rows = sb.autoSyncExpandPersonalizedRows(
            [{ kind: 'hidden_gems', name_template: 'Hidden Gems' }], existing);
        deepShapeEqual(rows, []);
    });

    test('variant kind with empty variants list produces nothing', () => {
        const sb = makeSandbox();
        const rows = sb.autoSyncExpandPersonalizedRows(
            [{ kind: 'genre_playlist', requires_variant: true, variants: [] }], []);
        deepShapeEqual(rows, []);
    });

    test('non-array kinds and skips malformed entries', () => {
        const sb = makeSandbox();
        deepShapeEqual(sb.autoSyncExpandPersonalizedRows(null, []), []);
        deepShapeEqual(sb.autoSyncExpandPersonalizedRows(undefined, []), []);
        // an entry with no `kind` is skipped
        const rows = sb.autoSyncExpandPersonalizedRows(
            [{ name_template: 'orphan' }, { kind: 'archives', name_template: 'The Archives' }], []);
        assert.equal(rows.length, 1);
        assert.equal(rows[0].kind, 'archives');
    });

    test('name_template with {variant} placeholder is substituted; falls back without it', () => {
        const sb = makeSandbox();
        const withTpl = sb.autoSyncExpandPersonalizedRows(
            [{ kind: 'genre_playlist', requires_variant: true, variants: ['rock'],
               name_template: 'Genre — {variant}' }], []);
        assert.equal(withTpl[0].name, 'Genre — rock');
        // no template → "<kind> <variant>" default
        const noTpl = sb.autoSyncExpandPersonalizedRows(
            [{ kind: 'genre_playlist', requires_variant: true, variants: ['rock'] }], []);
        assert.equal(noTpl[0].name, 'genre_playlist rock');
    });
});

describe('autoSyncGeneratedCountMap', () => {
    test('keys by kind+variant → track_count', () => {
        const sb = makeSandbox();
        const map = sb.autoSyncGeneratedCountMap({ success: true, playlists: [
            { kind: 'time_machine', variant: '2000s', track_count: 100 },
            { kind: 'hidden_gems', variant: '', track_count: 50 },
        ]});
        assert.equal(map.get('time_machine 2000s'), 100);
        assert.equal(map.get('hidden_gems '), 50);
        assert.equal(map.get('genre_playlist rock'), undefined);
    });

    test('empty / failed response → empty map', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncGeneratedCountMap(null).size, 0);
        assert.equal(sb.autoSyncGeneratedCountMap({ success: false }).size, 0);
        assert.equal(sb.autoSyncGeneratedCountMap({ success: true, playlists: [] }).size, 0);
    });
});

describe('autoSyncActionForPlaylist', () => {
    test('personalized row → personalized_pipeline with single kind entry (with variant)', () => {
        const sb = makeSandbox();
        const pl = { id: -2, _personalized: true, kind: 'time_machine', variant: '1990s' };
        deepShapeEqual(sb.autoSyncActionForPlaylist(pl, -2), {
            action_type: 'personalized_pipeline',
            action_config: { kinds: [{ kind: 'time_machine', variant: '1990s' }], refresh_first: true },
        });
    });

    test('personalized singleton row → personalized_pipeline, no variant key', () => {
        const sb = makeSandbox();
        const pl = { id: -1, _personalized: true, kind: 'hidden_gems', variant: '' };
        const action = sb.autoSyncActionForPlaylist(pl, -1);
        deepShapeEqual(action, {
            action_type: 'personalized_pipeline',
            action_config: { kinds: [{ kind: 'hidden_gems' }], refresh_first: true },
        });
        assert.ok(!('variant' in action.action_config.kinds[0]));
    });

    test('regression: ordinary mirrored row → playlist_pipeline by numeric id (unchanged)', () => {
        const sb = makeSandbox();
        const pl = { id: 5, name: 'Discover Weekly' };
        deepShapeEqual(sb.autoSyncActionForPlaylist(pl, 5), {
            action_type: 'playlist_pipeline',
            action_config: { playlist_id: '5', all: false },
        });
    });

    test('regression: null playlist still yields playlist_pipeline (defensive)', () => {
        const sb = makeSandbox();
        deepShapeEqual(sb.autoSyncActionForPlaylist(null, 9), {
            action_type: 'playlist_pipeline',
            action_config: { playlist_id: '9', all: false },
        });
    });
});

describe('autoSyncIsPersonalizedAutomation / autoSyncPersonalizedEntry', () => {
    test('recognizes a single-kind personalized_pipeline', () => {
        const sb = makeSandbox();
        const auto = { action_type: 'personalized_pipeline',
                       action_config: { kinds: [{ kind: 'time_machine', variant: '1980s' }] } };
        assert.equal(sb.autoSyncIsPersonalizedAutomation(auto), true);
        deepShapeEqual(sb.autoSyncPersonalizedEntry(auto), { kind: 'time_machine', variant: '1980s' });
    });

    test('single-kind without variant → empty-string variant', () => {
        const sb = makeSandbox();
        const auto = { action_type: 'personalized_pipeline',
                       action_config: { kinds: [{ kind: 'popular_picks' }] } };
        deepShapeEqual(sb.autoSyncPersonalizedEntry(auto), { kind: 'popular_picks', variant: '' });
    });

    test('multi-kind pipeline (Automations-page built) is NOT a per-row board schedule', () => {
        const sb = makeSandbox();
        const auto = { action_type: 'personalized_pipeline',
                       action_config: { kinds: [{ kind: 'a' }, { kind: 'b' }] } };
        assert.equal(sb.autoSyncIsPersonalizedAutomation(auto), true);
        assert.equal(sb.autoSyncPersonalizedEntry(auto), null);
    });

    test('playlist_pipeline is not personalized', () => {
        const sb = makeSandbox();
        const auto = { action_type: 'playlist_pipeline', action_config: { playlist_id: '1' } };
        assert.equal(sb.autoSyncIsPersonalizedAutomation(auto), false);
        assert.equal(sb.autoSyncPersonalizedEntry(auto), null);
    });

    test('missing/empty kinds → null entry', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncPersonalizedEntry(
            { action_type: 'personalized_pipeline', action_config: {} }), null);
        assert.equal(sb.autoSyncPersonalizedEntry(
            { action_type: 'personalized_pipeline', action_config: { kinds: [] } }), null);
        assert.equal(sb.autoSyncPersonalizedEntry(
            { action_type: 'personalized_pipeline', action_config: { kinds: [{}] } }), null);
    });
});

describe('autoSyncRowIdForPersonalized', () => {
    test('prefers the real mirrored row id when the kind is already generated', () => {
        const sb = makeSandbox();
        const playlists = [
            { id: 88, source: 'soulsync_discovery', source_playlist_id: 'ssd_time_machine_1980s' },
            { id: -3, _personalized: true, kind: 'time_machine', variant: '1980s' },
        ];
        assert.equal(
            sb.autoSyncRowIdForPersonalized({ kind: 'time_machine', variant: '1980s' }, playlists), 88);
    });

    test('falls back to the synthetic negative id when not yet generated', () => {
        const sb = makeSandbox();
        const playlists = [{ id: -3, _personalized: true, kind: 'time_machine', variant: '1980s' }];
        assert.equal(
            sb.autoSyncRowIdForPersonalized({ kind: 'time_machine', variant: '1980s' }, playlists), -3);
    });

    test('null when neither real nor synthetic row exists', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncRowIdForPersonalized({ kind: 'ghost', variant: '' }, []), null);
        assert.equal(sb.autoSyncRowIdForPersonalized(null, []), null);
    });
});

describe('buildAutoSyncScheduleState — personalized integration', () => {
    test('bucket a personalized schedule onto its synthetic row', () => {
        const sb = makeSandbox();
        const playlists = [
            { id: 1, name: 'Discover Weekly' },
            { id: -1, _personalized: true, source: 'soulsync_discovery', kind: 'time_machine',
              variant: '1980s', source_playlist_id: 'ssd_time_machine_1980s', name: 'Time Machine — 1980s' },
        ];
        const automations = [
            { id: 50, action_type: 'personalized_pipeline', trigger_type: 'schedule',
              trigger_config: { interval: 1, unit: 'days' },
              action_config: { kinds: [{ kind: 'time_machine', variant: '1980s' }], refresh_first: true },
              owned_by: 'auto_sync', enabled: 1 },
        ];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        // keyed on the synthetic id -1
        assert.equal(state.playlistSchedules[-1].automation_id, 50);
        assert.equal(state.playlistSchedules[-1].hours, 24);
        assert.equal(state.playlistSchedules[-1].owned, true);
        // personalized schedules never leak into the "custom pipelines" list
        deepShapeEqual(state.automationPipelines, []);
    });

    test('weekly personalized schedule buckets into weeklySchedules', () => {
        const sb = makeSandbox();
        const playlists = [
            { id: -1, _personalized: true, source: 'soulsync_discovery', kind: 'genre_playlist',
              variant: 'rock', source_playlist_id: 'ssd_genre_playlist_rock', name: 'Genre — rock' },
        ];
        const automations = [
            { id: 51, action_type: 'personalized_pipeline', trigger_type: 'weekly_time',
              trigger_config: { time: '08:00', days: ['mon', 'thu'], tz: 'UTC' },
              action_config: { kinds: [{ kind: 'genre_playlist', variant: 'rock' }] },
              owned_by: 'auto_sync', enabled: 1 },
        ];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        assert.equal(state.weeklySchedules[-1].automation_id, 51);
        assert.equal(state.weeklySchedules[-1].time, '08:00');
        deepShapeEqual(state.weeklySchedules[-1].days, ['mon', 'thu']);
    });

    test('personalized schedule re-keys onto the REAL row once generated', () => {
        const sb = makeSandbox();
        const playlists = [
            { id: 90, source: 'soulsync_discovery', source_playlist_id: 'ssd_time_machine_1980s',
              name: 'Time Machine — 1980s' },
        ];
        const automations = [
            { id: 52, action_type: 'personalized_pipeline', trigger_type: 'schedule',
              trigger_config: { interval: 6, unit: 'hours' },
              action_config: { kinds: [{ kind: 'time_machine', variant: '1980s' }] },
              owned_by: 'auto_sync', enabled: 1 },
        ];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        assert.equal(state.playlistSchedules[90].automation_id, 52);
        assert.equal(state.playlistSchedules[90].hours, 6);
    });

    test('non-owned personalized pipeline is ignored (not a board schedule)', () => {
        const sb = makeSandbox();
        const playlists = [
            { id: -1, _personalized: true, source: 'soulsync_discovery', kind: 'time_machine',
              variant: '1980s', source_playlist_id: 'ssd_time_machine_1980s' },
        ];
        const automations = [
            { id: 53, action_type: 'personalized_pipeline', trigger_type: 'schedule',
              trigger_config: { interval: 1, unit: 'days' },
              action_config: { kinds: [{ kind: 'time_machine', variant: '1980s' }] },
              enabled: 1 },  // no owned_by
        ];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        deepShapeEqual(state.playlistSchedules, {});
        deepShapeEqual(state.weeklySchedules, {});
    });

    test('multi-kind personalized pipeline never binds to a row', () => {
        const sb = makeSandbox();
        const playlists = [
            { id: -1, _personalized: true, source: 'soulsync_discovery', kind: 'time_machine',
              variant: '1980s', source_playlist_id: 'ssd_time_machine_1980s' },
        ];
        const automations = [
            { id: 54, action_type: 'personalized_pipeline', trigger_type: 'schedule',
              trigger_config: { interval: 1, unit: 'days' },
              action_config: { kinds: [{ kind: 'time_machine', variant: '1980s' }, { kind: 'hidden_gems' }] },
              owned_by: 'auto_sync', enabled: 1 },
        ];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        deepShapeEqual(state.playlistSchedules, {});
    });

    test('regression: mirrored playlist_pipeline schedules coexist untouched', () => {
        const sb = makeSandbox();
        const playlists = [
            { id: 1, name: 'Discover Weekly' },
            { id: -1, _personalized: true, source: 'soulsync_discovery', kind: 'time_machine',
              variant: '1980s', source_playlist_id: 'ssd_time_machine_1980s' },
        ];
        const automations = [
            { id: 10, action_type: 'playlist_pipeline', trigger_type: 'schedule',
              trigger_config: { interval: 1, unit: 'hours' },
              action_config: { playlist_id: '1' }, owned_by: 'auto_sync', enabled: 1 },
            { id: 50, action_type: 'personalized_pipeline', trigger_type: 'schedule',
              trigger_config: { interval: 1, unit: 'days' },
              action_config: { kinds: [{ kind: 'time_machine', variant: '1980s' }] },
              owned_by: 'auto_sync', enabled: 1 },
        ];
        const state = sb.buildAutoSyncScheduleState(playlists, automations);
        // both bucketed independently
        assert.equal(state.playlistSchedules[1].automation_id, 10);
        assert.equal(state.playlistSchedules[1].hours, 1);
        assert.equal(state.playlistSchedules[-1].automation_id, 50);
        assert.equal(Object.keys(state.playlistSchedules).length, 2);
    });
});

// =========================================================================
// Collapsible variant-kind groups in the sidebar
// =========================================================================

describe('autoSyncExpandPersonalizedRows — kind_label', () => {
    test('variant kind gets a group label with the {variant} suffix stripped', () => {
        const sb = makeSandbox();
        const rows = sb.autoSyncExpandPersonalizedRows([{
            kind: 'time_machine', requires_variant: true, variants: ['1980s'],
            name_template: 'Time Machine — {variant}',
        }], []);
        assert.equal(rows[0].kind_label, 'Time Machine');
    });

    test('colon / dash separators are trimmed too', () => {
        const sb = makeSandbox();
        const colon = sb.autoSyncExpandPersonalizedRows([{
            kind: 'genre_playlist', requires_variant: true, variants: ['rock'],
            name_template: 'Genre: {variant}',
        }], []);
        assert.equal(colon[0].kind_label, 'Genre');
        const dash = sb.autoSyncExpandPersonalizedRows([{
            kind: 'genre_playlist', requires_variant: true, variants: ['rock'],
            name_template: 'Genre - {variant}',
        }], []);
        assert.equal(dash[0].kind_label, 'Genre');
    });

    test('no name_template → kind_label falls back to the kind', () => {
        const sb = makeSandbox();
        const rows = sb.autoSyncExpandPersonalizedRows([{
            kind: 'seasonal_mix', requires_variant: true, variants: ['halloween'],
        }], []);
        assert.equal(rows[0].kind_label, 'seasonal_mix');
    });

    test('singleton kind has an empty kind_label (never grouped)', () => {
        const sb = makeSandbox();
        const rows = sb.autoSyncExpandPersonalizedRows(
            [{ kind: 'hidden_gems', name_template: 'Hidden Gems' }], []);
        assert.equal(rows[0].kind_label, '');
    });
});

describe('autoSyncGroupSidebarRows', () => {
    test('variant kinds bucket by kind; singletons and real rows stay flat', () => {
        const sb = makeSandbox();
        const rows = [
            { id: 5, name: 'Discover Weekly' },                       // real mirrored → flat
            { id: -1, _personalized: true, kind: 'popular_picks', variant: '' },  // singleton → flat
            { id: -2, _personalized: true, kind: 'time_machine', variant: '1980s', kind_label: 'Time Machine' },
            { id: -3, _personalized: true, kind: 'time_machine', variant: '1990s', kind_label: 'Time Machine' },
            { id: -4, _personalized: true, kind: 'genre_playlist', variant: 'rock', kind_label: 'Genre' },
        ];
        const { flat, groups } = sb.autoSyncGroupSidebarRows(rows);
        deepShapeEqual(flat.map(p => p.id), [5, -1]);
        assert.equal(groups.length, 2);
        assert.equal(groups[0].kind, 'time_machine');
        assert.equal(groups[0].label, 'Time Machine');
        deepShapeEqual(groups[0].rows.map(p => p.variant), ['1980s', '1990s']);
        assert.equal(groups[1].kind, 'genre_playlist');
        assert.equal(groups[1].label, 'Genre');
        assert.equal(groups[1].rows.length, 1);
    });

    test('group label falls back to the kind when kind_label is missing', () => {
        const sb = makeSandbox();
        const { groups } = sb.autoSyncGroupSidebarRows([
            { id: -1, _personalized: true, kind: 'time_machine', variant: '1980s' },
        ]);
        assert.equal(groups[0].label, 'time_machine');
    });

    test('empty / null input yields empty flat + groups', () => {
        const sb = makeSandbox();
        deepShapeEqual(sb.autoSyncGroupSidebarRows([]), { flat: [], groups: [] });
        deepShapeEqual(sb.autoSyncGroupSidebarRows(null), { flat: [], groups: [] });
    });
});

describe('autoSyncSidebarGroupHtml + toggleAutoSyncKindGroup', () => {
    // A tiny card renderer that tags id and the display name it was given, so we
    // can assert flat-vs-grouped placement and the variant-only labels.
    const renderer = (p, displayName) => `[card:${p.id}:${displayName || p.name}]`;

    test('flat cards render outside groups; variant cards inside, labelled by variant', () => {
        const sb = makeSandbox();
        const rows = [
            { id: -1, _personalized: true, kind: 'popular_picks', variant: '', name: 'Popular Picks' },
            { id: -2, _personalized: true, kind: 'time_machine', variant: '1980s', kind_label: 'Time Machine' },
            { id: -3, _personalized: true, kind: 'time_machine', variant: '1990s', kind_label: 'Time Machine' },
        ];
        const html = sb.autoSyncSidebarGroupHtml(rows, renderer);
        assert.ok(html.includes('[card:-1:Popular Picks]'), 'singleton flat card present');
        // grouped cards labelled by variant, not full name
        assert.ok(html.includes('[card:-2:1980s]'), 'grouped card uses variant label');
        assert.ok(html.includes('[card:-3:1990s]'));
        // one collapsible header with the kind label + a count of 2
        assert.ok(html.includes('auto-sync-kind-group'), 'group wrapper present');
        assert.ok(html.includes('data-kind="time_machine"'));
        assert.ok(html.includes('Time Machine'), 'group heading present');
        assert.ok(html.includes('>2</span>'), 'count reflects 2 variants');
    });

    test('collapsed by default; expanded once its kind is toggled on', () => {
        const sb = makeSandbox();
        const rows = [
            { id: -2, _personalized: true, kind: 'time_machine', variant: '1980s', kind_label: 'Time Machine' },
        ];
        const collapsed = sb.autoSyncSidebarGroupHtml(rows, renderer);
        assert.ok(!/auto-sync-kind-group expanded|auto-sync-kind-group .*expanded/.test(collapsed),
            'no expanded class before toggle');
        sb.toggleAutoSyncKindGroup('time_machine');
        const expanded = sb.autoSyncSidebarGroupHtml(rows, renderer);
        assert.ok(expanded.includes('auto-sync-kind-group expanded'),
            'expanded class present after toggle');
        // toggling again collapses
        sb.toggleAutoSyncKindGroup('time_machine');
        const recollapsed = sb.autoSyncSidebarGroupHtml(rows, renderer);
        assert.ok(!recollapsed.includes('auto-sync-kind-group expanded'));
    });

    test('"N on" badge counts scheduled variants via isScheduled predicate', () => {
        const sb = makeSandbox();
        const rows = [
            { id: -2, _personalized: true, kind: 'time_machine', variant: '1980s', kind_label: 'Time Machine' },
            { id: -3, _personalized: true, kind: 'time_machine', variant: '1990s', kind_label: 'Time Machine' },
        ];
        const isScheduled = (p) => p.id === -2;  // one of the two is scheduled
        const html = sb.autoSyncSidebarGroupHtml(rows, renderer, isScheduled);
        assert.ok(html.includes('has-active'), 'group flagged active');
        assert.ok(html.includes('1 on'), 'active badge shows 1');
    });

    test('no active badge when nothing in the group is scheduled', () => {
        const sb = makeSandbox();
        const rows = [
            { id: -2, _personalized: true, kind: 'time_machine', variant: '1980s', kind_label: 'Time Machine' },
        ];
        const html = sb.autoSyncSidebarGroupHtml(rows, renderer, () => false);
        assert.ok(!html.includes('has-active'));
        assert.ok(!html.includes(' on</span>'));
    });
});

// =========================================================================
// Enriching already-generated discovery rows so they group with the rest
// =========================================================================

describe('autoSyncKindLabel', () => {
    test('strips the {variant} suffix and separator', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncKindLabel({ kind: 'time_machine', name_template: 'Time Machine — {variant}' }), 'Time Machine');
        assert.equal(sb.autoSyncKindLabel({ kind: 'genre_playlist', name_template: 'Genre: {variant}' }), 'Genre');
    });
    test('falls back to the kind when no template', () => {
        const sb = makeSandbox();
        assert.equal(sb.autoSyncKindLabel({ kind: 'seasonal_mix' }), 'seasonal_mix');
    });
});

describe('autoSyncEnrichDiscoveryRows', () => {
    const KINDS = [
        { kind: 'hidden_gems', name_template: 'Hidden Gems' },
        { kind: 'time_machine', requires_variant: true, variants: ['1960s', '1970s'], name_template: 'Time Machine — {variant}' },
        { kind: 'genre_playlist', requires_variant: true, variants: ['rock'], name_template: 'Genre — {variant}' },
    ];

    test('tags a generated variant mirror row with kind/variant/kind_label', () => {
        const sb = makeSandbox();
        const rows = [
            { id: 1397, source: 'soulsync_discovery', source_playlist_id: 'ssd_time_machine_1960s',
              name: 'Time Machine — 1960s', track_count: 45 },
        ];
        const [r] = sb.autoSyncEnrichDiscoveryRows(rows, KINDS);
        assert.equal(r.kind, 'time_machine');
        assert.equal(r.variant, '1960s');
        assert.equal(r.kind_label, 'Time Machine');
        assert.equal(r.id, 1397);          // unchanged
        assert.equal(r.track_count, 45);   // unchanged
        assert.ok(!r._personalized);       // still a real row → schedules via playlist_pipeline
    });

    test('registered singleton row passes through flat and untouched', () => {
        const sb = makeSandbox();
        const rows = [
            { id: 939, source: 'soulsync_discovery', source_playlist_id: 'ssd_hidden_gems',
              name: 'Hidden Gems', track_count: 50 },
        ];
        const [r] = sb.autoSyncEnrichDiscoveryRows(rows, KINDS);
        assert.equal(r.id, 939);
        assert.ok(!('variant' in r) || !r.variant);  // no variant → stays flat
        assert.ok(!('kind' in r));                    // not tagged
    });

    test('drops an orphaned mirror whose kind is no longer registered', () => {
        const sb = makeSandbox();
        const rows = [
            { id: 1398, source: 'soulsync_discovery', source_playlist_id: 'ssd_year_mix_1970',
              name: 'Year Mix — 1970', track_count: 32 },
            { id: 939, source: 'soulsync_discovery', source_playlist_id: 'ssd_hidden_gems', name: 'Hidden Gems' },
        ];
        const out = sb.autoSyncEnrichDiscoveryRows(rows, KINDS);
        assert.equal(out.length, 1);                       // year_mix dropped
        assert.equal(out[0].source_playlist_id, 'ssd_hidden_gems');
    });

    test('non-discovery rows pass through unchanged', () => {
        const sb = makeSandbox();
        const rows = [{ id: 5, source: 'spotify', name: 'Discover Weekly' }];
        deepShapeEqual(sb.autoSyncEnrichDiscoveryRows(rows, KINDS), rows);
    });

    test('fails open with no kinds metadata (keeps every row, orphans included)', () => {
        const sb = makeSandbox();
        const rows = [
            { id: 1398, source: 'soulsync_discovery', source_playlist_id: 'ssd_year_mix_1970', name: 'Year Mix — 1970' },
        ];
        deepShapeEqual(sb.autoSyncEnrichDiscoveryRows(rows, []), rows);
        deepShapeEqual(sb.autoSyncEnrichDiscoveryRows(rows, null), rows);
    });

    test('generated + synthetic variants merge into ONE group', () => {
        const sb = makeSandbox();
        // Only a variant kind, so no singleton noise: a generated 1960s (real)
        // plus the not-yet-generated decades (synthetic).
        const TM_ONLY = [KINDS[1]];  // time_machine with variants 1960s, 1970s
        const real = [
            { id: 1397, source: 'soulsync_discovery', source_playlist_id: 'ssd_time_machine_1960s',
              name: 'Time Machine — 1960s', track_count: 45 },
        ];
        const enriched = sb.autoSyncEnrichDiscoveryRows(real, TM_ONLY);
        const synthetic = sb.autoSyncExpandPersonalizedRows(TM_ONLY, enriched);  // dedups 1960s
        const { flat, groups } = sb.autoSyncGroupSidebarRows([...enriched, ...synthetic]);
        assert.equal(flat.length, 0);  // nothing flat
        assert.equal(groups.length, 1);
        const tm = groups[0];
        assert.equal(tm.kind, 'time_machine');
        // 1960s (generated) + 1970s (synthetic) all under the one Time Machine group
        deepShapeEqual(tm.rows.map(r => r.variant).sort(), ['1960s', '1970s']);
        // the generated one kept its real id + track count
        const gen = tm.rows.find(r => r.variant === '1960s');
        assert.equal(gen.id, 1397);
        assert.equal(gen.track_count, 45);
        assert.ok(!gen._personalized);  // real row → still schedules as a mirrored playlist
    });
});
