/** P7 pure core — literal assertions against the vanilla originals. */

import { describe, expect, it } from 'vitest';

import {
  formatDbSize,
  formatTimeAgo,
  libraryCardView,
  relativeTime,
  SYNC_SOURCE_LABELS,
  syncCardView,
} from './-dash.library';

const NOW = new Date('2026-08-04T12:00:00Z');
const ago = (mins: number) => new Date(NOW.getTime() - mins * 60000);

describe('formatTimeAgo (api-monitor.js:1342)', () => {
  it('walks the vanilla buckets, yesterday included', () => {
    expect(formatTimeAgo(ago(0), NOW)).toBe('just now');
    expect(formatTimeAgo(ago(5), NOW)).toBe('5m ago');
    expect(formatTimeAgo(ago(3 * 60), NOW)).toBe('3h ago');
    expect(formatTimeAgo(ago(25 * 60), NOW)).toBe('yesterday');
    expect(formatTimeAgo(ago(3 * 24 * 60), NOW)).toBe('3d ago');
    expect(formatTimeAgo(ago(10 * 24 * 60), NOW)).toBe(ago(10 * 24 * 60).toLocaleDateString());
  });
});

describe('relativeTime (pages-extra.js:1117 — the sync-card variant)', () => {
  it('has NO yesterday bucket and a 7-day cutoff', () => {
    const iso = (mins: number) => ago(mins).toISOString();
    expect(relativeTime(iso(0), NOW.getTime())).toBe('just now');
    expect(relativeTime(iso(45), NOW.getTime())).toBe('45m ago');
    expect(relativeTime(iso(26 * 60), NOW.getTime())).toBe('1d ago');
    expect(relativeTime(iso(6 * 24 * 60), NOW.getTime())).toBe('6d ago');
    expect(relativeTime(iso(8 * 24 * 60), NOW.getTime())).toBe(
      ago(8 * 24 * 60).toLocaleDateString(),
    );
  });
});

describe('formatDbSize', () => {
  it('flips sub-MB to KB', () => {
    expect(formatDbSize(0.5)).toBe('512 KB');
    expect(formatDbSize(12.34)).toBe('12.3 MB');
    expect(formatDbSize(1)).toBe('1.0 MB');
  });
});

describe('libraryCardView — the five states', () => {
  const connected = { media_server: { connected: true }, active_media_server: 'plex' };
  const stats = { artists: 1200, albums: 3400, tracks: 56000, database_size_mb: 42.5 };

  it('scanning outranks everything and hides stats without data', () => {
    const view = libraryCardView(null, connected, true, NOW);
    expect(view.cardClass).toBe('library-status-card scanning');
    expect(view.title).toBe('Library Scan');
    expect(view.subtitle).toBe('Updating library database...');
    expect(view.scanLabel).toBe('Stop');
    expect(view.scanScanning).toBe(true);
    expect(view.deepVisible).toBe(false);
    expect(view.statsVisible).toBe(false);
    expect(view.progressVisible).toBe(true);
  });

  it('scanning keeps the stats row when data exists', () => {
    const view = libraryCardView(stats, connected, true, NOW);
    expect(view.statsVisible).toBe(true);
    expect(view.stats!.tracks).toBe((56000).toLocaleString());
  });

  it('no server configured', () => {
    const view = libraryCardView(stats, { active_media_server: 'none' }, false, NOW);
    expect(view.cardClass).toBe('library-status-card needs-setup');
    expect(view.title).toBe('No Media Server');
    expect(view.subtitle).toBe('Connect a server to get started');
    expect(view.scanVisible).toBe(false);
    expect(view.message).toEqual({ kind: 'no-server', serverName: '' });
  });

  it('server configured but disconnected', () => {
    const view = libraryCardView(
      stats,
      { media_server: { connected: false }, active_media_server: 'jellyfin' },
      false,
      NOW,
    );
    expect(view.title).toBe('Jellyfin — Disconnected');
    expect(view.subtitle).toBe('Cannot reach your media server');
    expect(view.message).toEqual({ kind: 'disconnected', serverName: 'Jellyfin' });
  });

  it('connected but empty library', () => {
    const view = libraryCardView({ tracks: 0 }, connected, false, NOW);
    expect(view.cardClass).toBe('library-status-card empty-library');
    expect(view.title).toBe('Plex Connected');
    expect(view.subtitle).toBe('Library database is empty');
    expect(view.scanLabel).toBe('Scan Now');
    expect(view.deepVisible).toBe(false);
    expect(view.message).toEqual({ kind: 'empty', serverName: 'Plex' });
  });

  it('healthy library with data', () => {
    const lastUpdate = ago(5).toISOString();
    const view = libraryCardView({ ...stats, last_update: lastUpdate }, connected, false, NOW);
    expect(view.cardClass).toBe('library-status-card has-data');
    expect(view.title).toBe('Plex Library');
    expect(view.subtitle).toBe(
      `Last refreshed 5m ago · ${(3400).toLocaleString()} albums · 42.5 MB db`,
    );
    expect(view.scanLabel).toBe('Quick Scan');
    expect(view.deepVisible).toBe(true);
    expect(view.stats).toEqual({
      artists: (1200).toLocaleString(),
      albums: (3400).toLocaleString(),
      tracks: (56000).toLocaleString(),
      size: '42.5 MB',
    });
    expect(view.message).toBeNull();
  });

  it('healthy without last_update reads Never; invalid dates too', () => {
    expect(libraryCardView(stats, connected, false, NOW).subtitle).toContain('Last refreshed Never');
    expect(
      libraryCardView({ ...stats, last_update: 'garbage' }, connected, false, NOW).subtitle,
    ).toContain('Last refreshed Never');
  });
});

describe('syncCardView', () => {
  it('computes pct, health bands and the counts line', () => {
    const view = syncCardView(
      {
        id: 7,
        source: 'spotify',
        playlist_name: 'Bangers',
        tracks_found: 8,
        total_tracks: 10,
        tracks_downloaded: 3,
        tracks_failed: 1,
      },
      NOW.getTime(),
    );
    expect(view.pct).toBe(80);
    expect(view.healthClass).toBe('health-good');
    expect(view.sourceLabel).toBe('Spotify');
    expect(view.name).toBe('Bangers');
    expect(view.counts).toBe('8/10 matched · 3 ⬇ · 1 ✗');
  });

  it('health bands: <50 bad, <80 warn', () => {
    const at = (found: number) =>
      syncCardView({ tracks_found: found, total_tracks: 100 }, NOW.getTime()).healthClass;
    expect(at(49)).toBe('health-bad');
    expect(at(79)).toBe('health-warn');
    expect(at(80)).toBe('health-good');
    // zero total → pct 0 → bad
    expect(syncCardView({}, NOW.getTime()).healthClass).toBe('health-bad');
  });

  it('artist entries compose the em-dash name; unknown sources pass through', () => {
    const view = syncCardView(
      { artist_name: 'BYLT', album_name: 'Neon', source: 'slsk-custom' },
      NOW.getTime(),
    );
    expect(view.name).toBe('BYLT — Neon');
    expect(view.sourceLabel).toBe('slsk-custom');
    expect(syncCardView({}, NOW.getTime()).name).toBe('Unknown');
    expect(syncCardView({}, NOW.getTime()).sourceLabel).toBe('Unknown');
    expect(Object.keys(SYNC_SOURCE_LABELS)).toHaveLength(6);
  });

  it('bare counts without downloads or failures', () => {
    expect(syncCardView({ tracks_found: 2, total_tracks: 4 }, NOW.getTime()).counts).toBe(
      '2/4 matched',
    );
  });
});
