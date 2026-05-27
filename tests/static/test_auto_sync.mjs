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
        document: { getElementById: () => null, body: {} },
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
