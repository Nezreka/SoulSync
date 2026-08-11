/**
 * The Auto Sync card's pure core. The formatters replicate auto-sync.js
 * labels (autoSyncIntervalLabel :68, autoSyncWeeklyLabel :146,
 * autoSyncSourceLabel :161) — pins here are literals lifted from those
 * functions' behavior so a drift in the replica fails loudly.
 */

import { describe, expect, it } from 'vitest';

import type { AutoSyncSeamState } from './-dash.autosync';

import {
  autoSyncCardRows,
  intervalLabel,
  nextRunText,
  sourceLabel,
  weeklyLabel,
} from './-dash.autosync';

const NOW = Date.parse('2026-08-11T12:00:00Z');

describe('intervalLabel', () => {
  it('matches the vanilla buckets', () => {
    expect(intervalLabel(1)).toBe('Every 1 hour');
    expect(intervalLabel(6)).toBe('Every 6 hours');
    expect(intervalLabel(24)).toBe('Every 1 day');
    expect(intervalLabel(72)).toBe('Every 3 days');
    expect(intervalLabel(168)).toBe('Every week');
  });
});

describe('weeklyLabel', () => {
  it('collapses full/empty weeks to Daily and orders days Mon–Sun', () => {
    expect(weeklyLabel('09:00', [])).toBe('Daily @ 09:00');
    expect(
      weeklyLabel('09:00', ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']),
    ).toBe('Daily @ 09:00');
    // Toggled on out of order — renders canonical.
    expect(weeklyLabel('04:30', ['fri', 'mon'])).toBe('Mon, Fri @ 04:30');
  });
});

describe('sourceLabel', () => {
  it('uses the vanilla map and capitalizes unknowns', () => {
    expect(sourceLabel('spotify')).toBe('Spotify');
    expect(sourceLabel('soulsync_discovery')).toBe('SoulSync Discovery');
    expect(sourceLabel('lastfm')).toBe('Last.fm Radio');
    expect(sourceLabel('weirdsource')).toBe('Weirdsource');
    expect(sourceLabel(undefined)).toBe('');
  });
});

describe('nextRunText', () => {
  it('buckets to minutes/hours/days and flags overdue', () => {
    expect(nextRunText('2026-08-11T12:30:00Z', NOW)).toBe('in 30m');
    expect(nextRunText('2026-08-11T15:00:00Z', NOW)).toBe('in 3h');
    expect(nextRunText('2026-08-13T12:00:00Z', NOW)).toBe('in 2d');
    expect(nextRunText('2026-08-11T11:00:00Z', NOW)).toBe('due now');
    expect(nextRunText(null, NOW)).toBeNull();
    expect(nextRunText('not-a-date', NOW)).toBeNull();
  });
});

function seamState(): AutoSyncSeamState {
  return {
    playlists: [
      { id: 7, name: 'Discover Weekly', custom_name: '', source: 'spotify' },
      { id: 9, name: 'Hot Hits', custom_name: 'My Hot Hits', source: 'tidal' },
    ],
    playlistSchedules: {
      '7': {
        automation_id: 101,
        automation_name: 'Auto-sync: Discover Weekly',
        hours: 6,
        enabled: true,
        next_run: '2026-08-11T13:00:00Z',
      },
      '55': {
        // No matching playlist row (personalized/ungenerated) — the
        // automation's own name carries the row.
        automation_id: 103,
        automation_name: 'Release Radar refresh',
        hours: 24,
        enabled: false,
        next_run: null,
      },
    },
    weeklySchedules: {
      '9': {
        automation_id: 102,
        automation_name: 'Auto-sync: Hot Hits',
        time: '04:00',
        days: ['mon'],
        enabled: true,
        next_run: '2026-08-11T12:10:00Z',
      },
    },
    runHistory: [
      // Newest-first, like the endpoint returns.
      { playlist_id: 7, status: 'error' },
      { playlist_id: 7, status: 'completed' },
      { playlist_id: 9, status: 'completed' },
    ],
  };
}

describe('autoSyncCardRows', () => {
  it('builds rows with names, cadence, health, and next-run, sorted by urgency', () => {
    const rows = autoSyncCardRows(seamState(), NOW);
    expect(rows.map((r) => r.name)).toEqual([
      'My Hot Hits', // in 10m — soonest enabled
      'Discover Weekly', // in 1h
      'Release Radar refresh', // disabled — last
    ]);

    const [hotHits, discover, radar] = rows;
    expect(hotHits.cadence).toBe('Mon @ 04:00');
    expect(hotHits.source).toBe('Tidal');
    expect(hotHits.health).toBe('good');
    expect(hotHits.nextRun).toBe('in 10m');

    // The NEWEST history entry wins — an error after a completed run.
    expect(discover.cadence).toBe('Every 6 hours');
    expect(discover.source).toBe('Spotify');
    expect(discover.health).toBe('bad');
    expect(discover.automationId).toBe(101);

    // No playlist row and no history — automation name, no source, no health.
    expect(radar.name).toBe('Release Radar refresh');
    expect(radar.source).toBe('');
    expect(radar.health).toBe('none');
    expect(radar.enabled).toBe(false);
    expect(radar.nextRun).toBeNull();
  });

  it('shows a playlist carrying both an hourly and a weekly schedule twice', () => {
    const state = seamState();
    state.weeklySchedules['7'] = {
      automation_id: 999,
      automation_name: 'weekly twin',
      time: '09:00',
      days: [],
      enabled: true,
      next_run: null,
    };
    const rows = autoSyncCardRows(state, NOW);
    expect(rows.filter((r) => r.key === '7')).toHaveLength(2);
  });

  it('survives an empty board', () => {
    expect(
      autoSyncCardRows(
        { playlists: [], playlistSchedules: {}, weeklySchedules: {}, runHistory: [] },
        NOW,
      ),
    ).toEqual([]);
  });
});
