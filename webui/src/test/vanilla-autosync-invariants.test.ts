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
const SOURCE = readFileSync(
  resolve(__dirname, '../../static/auto-sync.js'),
  'utf8',
);

/** Everything between `async function NAME(` and the next column-0 brace. */
function functionBody(name: string): string {
  const start = SOURCE.indexOf(`async function ${name}(`);
  expect(start, `${name} should exist in auto-sync.js`).toBeGreaterThan(-1);
  const end = SOURCE.indexOf('\n}', start);
  expect(end, `${name} should be a complete declaration`).toBeGreaterThan(start);
  return SOURCE.slice(start, end);
}

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
      SOURCE.match(/const existing(Weekly|Hourly) = _autoSyncScheduleState\.\w+\?\.\[playlistId\];\s*\n\s*if \(existing/g) ||
      [];
    expect(inlineDeletes).toHaveLength(0);
  });

  it('reads the OPPOSITE map for each kind', () => {
    const body = functionBody('dropOpposingAutoSyncSchedule');
    // 'hourly' means "I am installing an hourly one", so it deletes the weekly.
    expect(body).toMatch(/keep === 'hourly'\s*\n?\s*\?\s*_autoSyncScheduleState\.weeklySchedules/);
    expect(body).toContain(': _autoSyncScheduleState.playlistSchedules');
  });
});
