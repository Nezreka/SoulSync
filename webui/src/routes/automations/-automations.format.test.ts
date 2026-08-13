import { describe, expect, it } from 'vitest';

import {
  automationMeta,
  automationOutcome,
  formatAction,
  formatTrigger,
  humanizeType,
  lastResultFacts,
  parseServerTime,
  timeAgo,
  timeUntil,
} from './-automations.format';

// Every clock-dependent assertion passes `now` explicitly. A test that reads
// the real clock is a time bomb — tests/library/test_expired_cleanup.py went
// red on 2026-07-27 for exactly that reason, months after it was written.
const NOW = Date.parse('2026-07-27T12:00:00Z');
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const ahead = (ms: number) => new Date(NOW + ms).toISOString();

describe('parseServerTime', () => {
  it('reads a naive SQLite datetime as UTC, not local', () => {
    // The whole point: `new Date('2026-07-27 12:00:00')` is LOCAL time, so on
    // any machine east or west of UTC every relative label would be skewed.
    expect(parseServerTime('2026-07-27 12:00:00')).toBe(Date.parse('2026-07-27T12:00:00Z'));
  });

  it('respects an explicit zone', () => {
    expect(parseServerTime('2026-07-27T12:00:00Z')).toBe(Date.parse('2026-07-27T12:00:00Z'));
    expect(parseServerTime('2026-07-27T13:00:00+01:00')).toBe(Date.parse('2026-07-27T12:00:00Z'));
  });
});

describe('timeAgo', () => {
  it('says Never when unset', () => {
    expect(timeAgo(null, NOW)).toBe('Never');
    expect(timeAgo(undefined, NOW)).toBe('Never');
    expect(timeAgo('', NOW)).toBe('Never');
  });

  it('walks the buckets', () => {
    expect(timeAgo(ago(30_000), NOW)).toBe('just now');
    expect(timeAgo(ago(5 * 60_000), NOW)).toBe('5m ago');
    expect(timeAgo(ago(3 * 3_600_000), NOW)).toBe('3h ago');
    expect(timeAgo(ago(2 * 86_400_000), NOW)).toBe('2d ago');
  });

  it('floors, so a boundary never reads as the larger unit', () => {
    expect(timeAgo(ago(59_999), NOW)).toBe('just now');
    expect(timeAgo(ago(60_000), NOW)).toBe('1m ago');
    expect(timeAgo(ago(3_599_999), NOW)).toBe('59m ago');
  });
});

describe('timeUntil', () => {
  it('is empty when unset and "soon" once due', () => {
    expect(timeUntil(null, NOW)).toBe('');
    expect(timeUntil(ago(1000), NOW)).toBe('soon');
    expect(timeUntil(new Date(NOW).toISOString(), NOW)).toBe('soon');
  });

  it('rounds seconds and minutes UP so a pending run never shows "in 0m"', () => {
    expect(timeUntil(ahead(500), NOW)).toBe('in 1s');
    expect(timeUntil(ahead(61_000), NOW)).toBe('in 2m');
  });

  it('rounds hours and days to nearest', () => {
    expect(timeUntil(ahead(110 * 60_000), NOW)).toBe('in 2h');
    expect(timeUntil(ahead(36 * 3_600_000), NOW)).toBe('in 2d');
  });
});

describe('humanizeType', () => {
  it('strips the side prefix and title-cases', () => {
    expect(humanizeType('video_deep_scan_tv')).toBe('Deep Scan Tv');
    expect(humanizeType('run_duplicate_cleaner')).toBe('Run Duplicate Cleaner');
  });

  it('never returns raw snake_case or an empty label', () => {
    expect(humanizeType('')).toBe('Unknown');
    expect(humanizeType(null)).toBe('Unknown');
    expect(humanizeType('video_')).toBe('Unknown');
  });
});

describe('formatTrigger', () => {
  it('renders schedule, daily and weekly from config', () => {
    expect(formatTrigger('schedule', { interval: 6, unit: 'hours' })).toBe('Every 6 hours');
    expect(formatTrigger('daily_time', { time: '03:30' })).toBe('Daily at 03:30');
    expect(formatTrigger('weekly_time', { days: ['mon', 'fri'], time: '09:00' })).toBe(
      'Mon, Fri at 09:00',
    );
  });

  it('falls back within schedule/weekly when config keys are missing', () => {
    expect(formatTrigger('schedule', {})).toBe('Every 1 hours');
    expect(formatTrigger('weekly_time', {})).toBe('Every day at 00:00');
  });

  it('uses the label map for event triggers', () => {
    expect(formatTrigger('watchlist_new_release', null)).toBe('New Release Found');
  });

  it('prefers the map over block definitions, then falls back to them', () => {
    const block = (t: string) => (t === 'watchlist_new_release' ? 'WRONG' : `Block ${t}`);
    expect(formatTrigger('watchlist_new_release', null, block)).toBe('New Release Found');
    expect(formatTrigger('webhook_received', null, block)).toBe('Block webhook_received');
  });

  it('humanizes an unknown type rather than showing the identifier', () => {
    expect(formatTrigger('video_something_new', null)).toBe('Something New');
  });
});

describe('formatAction', () => {
  it('maps known music and video actions', () => {
    expect(formatAction('process_wishlist')).toBe('Process Wishlist');
    expect(formatAction('video_scan_library')).toBe('Scan Video Library');
  });

  it('falls back to block label then humanize', () => {
    expect(formatAction('mystery_action', (t) => `Block ${t}`)).toBe('Block mystery_action');
    expect(formatAction('mystery_action')).toBe('Mystery Action');
  });
});

describe('lastResultFacts', () => {
  it('returns null for non-objects', () => {
    expect(lastResultFacts(null)).toBeNull();
    expect(lastResultFacts('done')).toBeNull();
    expect(lastResultFacts([1, 2])).toBeNull();
  });

  it('skips status, underscore keys, zeros and empty strings', () => {
    expect(
      lastResultFacts({ status: 'ok', _internal: 5, downloaded: 0, note: '', zero: '0' }),
    ).toBeNull();
  });

  it('keeps at most three facts and underscores read as spaces', () => {
    const out = lastResultFacts({ tracks_added: 4, albums_found: 2, artists: 1, extra: 9 });
    expect(out?.full).toBe('tracks added: 4 · albums found: 2 · artists: 1');
  });

  it('drops long strings but keeps short ones', () => {
    expect(lastResultFacts({ mode: 'x'.repeat(25) })).toBeNull();
    expect(lastResultFacts({ mode: 'quick' })?.full).toBe('mode: quick');
  });

  it('truncates the shown text at 64 chars but keeps the full text', () => {
    const out = lastResultFacts({ alpha_key: 'a'.repeat(24), beta_key: 'b'.repeat(24) });
    expect(out!.full.length).toBeGreaterThan(64);
    expect(out!.shown.length).toBeLessThanOrEqual(64);
    expect(out!.shown.endsWith('…')).toBe(true);
    expect(out!.full.endsWith('…')).toBe(false);
  });
});

describe('automationMeta', () => {
  const base = { id: 1, name: 'a' };

  it('shows a countdown only for enabled timer triggers', () => {
    const next = ahead(3_600_000);
    expect(
      automationMeta({ ...base, trigger_type: 'schedule', enabled: 1, next_run: next }, NOW)
        .nextRun,
    ).toBe('in 1h');
    // disabled -> no countdown, because it is not going to run
    expect(
      automationMeta({ ...base, trigger_type: 'schedule', enabled: 0, next_run: next }, NOW)
        .nextRun,
    ).toBeUndefined();
    // event-driven -> no countdown even with a next_run value
    expect(
      automationMeta({ ...base, trigger_type: 'app_started', enabled: 1, next_run: next }, NOW)
        .nextRun,
    ).toBeUndefined();
  });

  it('marks event-driven automations as Listening, and timers not', () => {
    expect(
      automationMeta({ ...base, trigger_type: 'app_started', enabled: 1 }, NOW).listening,
    ).toBe(true);
    expect(automationMeta({ ...base, trigger_type: 'schedule', enabled: 1 }, NOW).listening).toBe(
      false,
    );
    expect(
      automationMeta({ ...base, trigger_type: 'app_started', enabled: 0 }, NOW).listening,
    ).toBe(false);
  });

  it('lets an error replace the result summary', () => {
    const meta = automationMeta({ ...base, last_error: 'boom', last_result: { tracks: 3 } }, NOW);
    expect(meta.error).toBe('boom');
    expect(meta.result).toBeUndefined();
  });

  it('drops a zero run_count rather than showing "Runs: 0"', () => {
    expect(automationMeta({ ...base, run_count: 0 }, NOW).runs).toBeUndefined();
    expect(automationMeta({ ...base, run_count: 3 }, NOW).runs).toBe(3);
  });
});

describe('automationMeta while the side is paused', () => {
  const base = { id: 1, name: 'a' };
  const next = ahead(3_600_000);

  it('drops the countdown — the engine is skipping that slot', () => {
    // The stored next_run is REAL (the engine keeps the schedule alive so
    // resuming does not cause a catch-up burst); what would be false is the
    // implication that it is going to fire.
    const timer = { ...base, trigger_type: 'schedule', enabled: 1, next_run: next };
    expect(automationMeta(timer, NOW, false).nextRun).toBe('in 1h');
    expect(automationMeta(timer, NOW, true).nextRun).toBeUndefined();
  });

  it('stops claiming to listen for events it will ignore', () => {
    const event = { ...base, trigger_type: 'app_started', enabled: 1 };
    expect(automationMeta(event, NOW, false).listening).toBe(true);
    expect(automationMeta(event, NOW, true).listening).toBe(false);
  });

  it('says paused for both trigger kinds', () => {
    expect(automationMeta({ ...base, trigger_type: 'schedule', enabled: 1 }, NOW, true).paused).toBe(
      true,
    );
    expect(
      automationMeta({ ...base, trigger_type: 'app_started', enabled: 1 }, NOW, true).paused,
    ).toBe(true);
  });

  it('says nothing about pause for an automation that is switched off anyway', () => {
    // It is already not running on its own account; "paused" would be a second
    // reason for the same silence and reads as though flipping the master back
    // on would start it.
    expect(automationMeta({ ...base, enabled: 0 }, NOW, true).paused).toBe(false);
  });

  it('leaves the history alone — last run, run count and errors are facts', () => {
    const meta = automationMeta(
      { ...base, enabled: 1, last_run: '2026-08-12 09:00:00', run_count: 7, last_error: 'boom' },
      NOW,
      true,
    );
    expect(meta.runs).toBe(7);
    expect(meta.error).toBe('boom');
    expect(meta.lastRun).toBeTruthy();
  });

  it('defaults to not paused, so every existing caller keeps its behaviour', () => {
    expect(automationMeta({ ...base, trigger_type: 'app_started', enabled: 1 }, NOW).listening).toBe(
      true,
    );
  });
});


describe('automationOutcome — the handler stops speaking its own dialect', () => {
  it('says what a watchlist scan accomplished', () => {
    expect(
      automationOutcome('scan_watchlist', {
        status: 'completed',
        artists_scanned: 42,
        new_tracks_found: 9,
        tracks_added_to_wishlist: 7,
      }),
    ).toEqual({ text: 'Checked 42 artists, wishlisted 7 tracks', kind: 'ok' });
  });

  it('distinguishes found-but-not-wishlisted from nothing at all', () => {
    expect(
      automationOutcome('scan_watchlist', {
        status: 'completed',
        artists_scanned: 5,
        new_tracks_found: 3,
        tracks_added_to_wishlist: 0,
      })?.text,
    ).toBe('Checked 5 artists, found 3 new tracks');
    expect(
      automationOutcome('scan_watchlist', { status: 'completed', artists_scanned: 5 })?.text,
    ).toBe('Checked 5 artists, nothing new');
  });

  it('singularises rather than printing "1 artists"', () => {
    expect(
      automationOutcome('scan_watchlist', {
        status: 'completed',
        artists_scanned: 1,
        tracks_added_to_wishlist: 1,
      })?.text,
    ).toBe('Checked 1 artist, wishlisted 1 track');
  });

  it('reports the duplicate cleaner in files, not keys', () => {
    expect(
      automationOutcome('run_duplicate_cleaner', {
        status: 'completed',
        files_scanned: 12000,
        duplicates_found: 8,
        files_deleted: 8,
        space_freed_mb: 240.5,
      })?.text,
    ).toBe('Found 8 duplicates, removed 8 files (240.5 MB)');
    // A clean library is the common case and deserves a sentence of its own.
    expect(
      automationOutcome('run_duplicate_cleaner', {
        status: 'completed',
        files_scanned: 12000,
        duplicates_found: 0,
      })?.text,
    ).toBe('Checked 12,000 files, no duplicates');
  });

  it('reads counters the handler stringified', () => {
    // refresh_mirrored returns str(n) for both of these.
    expect(
      automationOutcome('refresh_mirrored', { status: 'completed', refreshed: '3', errors: '0' })
        ?.text,
    ).toBe('Refreshed 3 playlists');
    expect(
      automationOutcome('refresh_mirrored', { status: 'completed', refreshed: '3', errors: '1' })
        ?.text,
    ).toBe('Refreshed 3 playlists, 1 error');
  });

  it('explains a skip instead of printing the word "reason"', () => {
    expect(
      automationOutcome('run_duplicate_cleaner', {
        status: 'skipped',
        reason: 'Duplicate cleaner already running',
      }),
    ).toEqual({ text: 'Skipped — Duplicate cleaner already running', kind: 'skipped' });
    expect(automationOutcome('anything', { status: 'skipped' })?.text).toBe('Skipped');
  });

  it('does not claim a result for a run that handed off to a worker', () => {
    // discover_playlist returns status:'started' — the real outcome lands later.
    expect(automationOutcome('discover_playlist', { status: 'started', playlist_count: '4' })).toEqual(
      { text: 'Handed off — still working', kind: 'skipped' },
    );
  });

  it('falls back to the generic facts for an action with no sentence', () => {
    expect(automationOutcome('some_future_action', { status: 'completed', widgets: 3 })).toEqual({
      text: 'widgets: 3',
      kind: 'ok',
    });
  });

  it('says nothing when there is nothing worth saying', () => {
    expect(automationOutcome('process_wishlist', { status: 'completed' })).toBeNull();
    expect(automationOutcome('process_wishlist', null)).toBeNull();
    // Arrays are not results; index-keyed nonsense must not reach a card.
    expect(automationOutcome('process_wishlist', [1, 2])).toBeNull();
  });
});
