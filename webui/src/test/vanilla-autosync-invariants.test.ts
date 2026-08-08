import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * One-schedule-per-playlist, guarded at the source level.
 *
 * Auto-Sync can install two kinds of automation for a playlist: an hourly
 * `schedule` and a `weekly_time`. The engine will happily run BOTH, so every
 * save path has to drop the opposing one first or the playlist refreshes on
 * two cadences at once.
 *
 * That invariant used to be copy-pasted into the two interactive save paths.
 * The third — `saveAutoSyncPlaylistScheduleSilent`, the one the Bulk menu
 * drives — never got a copy, so bulk-scheduling a source left every
 * weekly-scheduled playlist in it running on both. The enforcement is now a
 * single helper; this test asserts every save path still calls it.
 *
 * Source-level rather than behavioural because auto-sync.js is a browser
 * script with no module boundary — the React port's own bulk path carries the
 * behavioural tests. DELETE THIS FILE with auto-sync.js at the flip; a failure
 * here after that point means the file is gone, not that the invariant broke.
 */
const SOURCE = readFileSync(resolve(__dirname, '../../static/auto-sync.js'), 'utf8');

/** Everything between `async function NAME(` and the next column-0 brace. */
function functionBody(name: string): string {
  const start = SOURCE.indexOf(`async function ${name}(`);
  expect(start, `${name} should exist in auto-sync.js`).toBeGreaterThan(-1);
  const end = SOURCE.indexOf('\n}', start);
  expect(end, `${name} should be a complete declaration`).toBeGreaterThan(start);
  return SOURCE.slice(start, end);
}

describe('the sync page binds its listeners exactly once', () => {
  /**
   * initializeSyncPage is called at boot AND from loadPageData's `case 'sync'`,
   * which runs on every navigation to the page. The markup is static, so the
   * listeners only need binding once — binding per visit stacked a duplicate
   * set each time, and one of them is the Start Sync button whose handler is a
   * toggle. After an even number of visits, one click started the sync and
   * immediately cancelled it.
   */
  const SYNC_SERVICES = readFileSync(resolve(__dirname, '../../static/sync-services.js'), 'utf8');

  function initBody(): string {
    const start = SYNC_SERVICES.indexOf('function initializeSyncPage() {');
    expect(start, 'initializeSyncPage should exist').toBeGreaterThan(-1);
    const end = SYNC_SERVICES.indexOf('\n}', start);
    return SYNC_SERVICES.slice(start, end);
  }

  it('returns early once the bindings have run', () => {
    const body = initBody();
    expect(body).toContain('if (_syncPageListenersBound) return;');
    expect(body).toContain('_syncPageListenersBound = true;');
  });

  it('sets the flag BEFORE binding anything', () => {
    // Guard-then-bind, not bind-then-guard: a throw mid-binding must not leave
    // the flag clear and let the next visit stack a second partial set.
    const body = initBody();
    expect(body.indexOf('_syncPageListenersBound = true;')).toBeLessThan(
      body.indexOf('addEventListener'),
    );
  });

  it('keeps the per-visit refreshes OUTSIDE the guard', () => {
    const body = initBody();
    const guard = body.indexOf('if (_syncPageListenersBound) return;');
    expect(guard).toBeGreaterThan(-1);
    // All three are idempotent refreshes, not bindings, and must run every
    // visit.
    // Each is asserted PRESENT before its position is compared: indexOf gives
    // -1 for a deleted call, and -1 is less than the guard index, so a
    // position-only check passes when the call is gone. Caught by mutation.
    for (const call of [
      'ensureBeatportContentLoaded()',
      'updateBeatportClearButtonState()',
      'initializeLiveLogViewer()',
    ]) {
      const at = body.indexOf(call);
      expect(at, `${call} should still be called`).toBeGreaterThan(-1);
      expect(at, `${call} should run on every visit, not once`).toBeLessThan(guard);
    }
  });

  it('restarts log polling on every visit, because navigation always stops it', () => {
    // This one is load-bearing for a cross-file reason, so it gets its own
    // test. `loadPageData` calls `stopLogPolling()` unconditionally at the top,
    // for EVERY page — including sync itself — and `initializeLiveLogViewer` is
    // the only thing that ever calls `startLogPolling`. Leaving it below the
    // binding guard meant polling stopped on the first navigation and never
    // came back. Only visible with the socket down: `tool:logs` writes the same
    // textarea via `updateLogsFromData`, and the HTTP poll is its twin.
    const INIT = readFileSync(resolve(__dirname, '../../static/init.js'), 'utf8');
    const loadPageData = INIT.slice(
      INIT.indexOf('async function loadPageData(pageId) {'),
      INIT.indexOf("case 'sync':"),
    );
    expect(loadPageData, 'loadPageData should still exist ahead of its sync case').not.toBe('');
    expect(loadPageData).toContain('stopLogPolling();');

    // One call site, and it is the hoisted one — a second copy left at the
    // bottom of the bindings would make this test pass while the bug returned.
    const calls = SYNC_SERVICES.match(/^\s*initializeLiveLogViewer\(\);$/gm) || [];
    expect(calls).toHaveLength(1);
  });

  it('declares the flag at module scope, not inside the function', () => {
    // A `let` inside the function would reset on every call and guard nothing.
    expect(SYNC_SERVICES).toMatch(/^let _syncPageListenersBound = false;$/m);
    expect(initBody()).not.toContain('let _syncPageListenersBound');
  });
});

describe('bulk unschedule sees BOTH kinds of schedule', () => {
  /**
   * The sibling of the save-path bug: the bulk paths predate weekly schedules,
   * so "Unschedule all" read the hourly map alone. It undercounted in its own
   * confirm dialog, said there was nothing to unschedule when weekly schedules
   * existed, and left them running.
   */
  it('resolves a playlist through the shared both-kinds accessor', () => {
    const body = functionBody('bulkUnscheduleAutoSyncSource');
    expect(body).toContain('autoSyncSchedulesForPlaylist(p.id).length');
    expect(body).toContain('for (const schedule of autoSyncSchedulesForPlaylist(playlist.id))');
    // The old single-map reads must be gone, not merely supplemented.
    expect(body).not.toContain('playlistSchedules[p.id]');
    expect(body).not.toContain('playlistSchedules[playlist.id]');
  });

  it('the accessor reads both maps', () => {
    const accessor = SOURCE.slice(
      SOURCE.indexOf('function autoSyncSchedulesForPlaylist('),
      SOURCE.indexOf('\n}', SOURCE.indexOf('function autoSyncSchedulesForPlaylist(')),
    );
    expect(accessor).toContain('playlistSchedules?.[playlistId]');
    expect(accessor).toContain('weeklySchedules?.[playlistId]');
    // ...and drops the absent one rather than deleting `undefined.automation_id`.
    expect(accessor).toContain('.filter(Boolean)');
  });

  it('tells the user both kinds are going', () => {
    expect(functionBody('bulkUnscheduleAutoSyncSource')).toContain(
      'Removes the Auto-Sync schedules, hourly and weekly.',
    );
  });
});

describe('auto-sync save paths enforce one schedule per playlist', () => {
  const SAVE_PATHS: [string, 'hourly' | 'weekly'][] = [
    ['saveAutoSyncPlaylistSchedule', 'hourly'],
    // The bulk path. This is the one that was missing it.
    ['saveAutoSyncPlaylistScheduleSilent', 'hourly'],
    ['saveAutoSyncWeeklySchedule', 'weekly'],
  ];

  it.each(SAVE_PATHS)('%s drops the opposing %s schedule', (fn, keep) => {
    expect(functionBody(fn)).toContain(`dropOpposingAutoSyncSchedule(playlistId, '${keep}')`);
  });

  it('drops the opposing schedule BEFORE writing the new automation', () => {
    // Ordering matters: a delete issued after the POST could race the create
    // and remove the schedule that was just installed.
    for (const [fn] of SAVE_PATHS) {
      const body = functionBody(fn);
      const drop = body.indexOf('dropOpposingAutoSyncSchedule');
      const write = body.indexOf('fetch(');
      expect(drop, `${fn} should call the helper`).toBeGreaterThan(-1);
      expect(write, `${fn} should write an automation`).toBeGreaterThan(-1);
      expect(drop, `${fn} drops the opposing schedule after writing`).toBeLessThan(write);
    }
  });

  it('keeps the enforcement in exactly ONE place', () => {
    // The bug was three copies where one had drifted. If a fourth save path
    // appears, it must call the helper rather than inline its own delete.
    const helper = SOURCE.match(/async function dropOpposingAutoSyncSchedule\(/g) || [];
    expect(helper).toHaveLength(1);
    const inlineDeletes =
      SOURCE.match(
        /const existing(Weekly|Hourly) = _autoSyncScheduleState\.\w+\?\.\[playlistId\];\s*\n\s*if \(existing/g,
      ) || [];
    expect(inlineDeletes).toHaveLength(0);
  });

  it('reads the OPPOSITE map for each kind', () => {
    const body = functionBody('dropOpposingAutoSyncSchedule');
    // 'hourly' means "I am installing an hourly one", so it deletes the weekly.
    expect(body).toMatch(/keep === 'hourly'\s*\n?\s*\?\s*_autoSyncScheduleState\.weeklySchedules/);
    expect(body).toContain(': _autoSyncScheduleState.playlistSchedules');
  });
});
