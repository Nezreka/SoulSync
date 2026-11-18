/**
 * What the Discovery tab shows, worked out the way settings.js did it
 * (SETTINGS_PORT.md, step 4).
 */

import type { SettingsPayload } from '../settings-store';

export const SYNC_MODES = [
  { value: 'replace', label: 'Replace — recreate the playlist each sync' },
  { value: 'reconcile', label: 'Reconcile — edit in place, keep image & description (default)' },
  { value: 'append', label: 'Append — only add new tracks, never remove' },
] as const;

export const LOOKBACK_PERIODS = [
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days (Default)' },
  { value: '90', label: 'Last 90 days' },
  { value: '180', label: 'Last 6 months' },
  { value: 'all', label: 'Entire discography' },
] as const;

export const HEMISPHERES = [
  { value: 'northern', label: 'Northern Hemisphere' },
  { value: 'southern', label: 'Southern Hemisphere' },
] as const;

export interface DiscoveryValues {
  m3uExport: boolean;
  libraryM3u: boolean;
  libraryM3uPath: string;
  syncMode: string;
  wishlistMissing: boolean;
  createBackup: boolean;
  statsEnabled: boolean;
  statsInterval: number;
  adventurousness: number;
}

export function resolveDiscovery(settings: SettingsPayload): DiscoveryValues {
  const m3u = settings.m3u_export ?? {};
  const sync = settings.playlist_sync ?? {};
  const stats = settings.listening_stats ?? {};
  const adventure = settings.discover?.adventurousness;
  return {
    m3uExport: m3u.enabled === true,
    libraryM3u: m3u.library_enabled === true,
    libraryM3uPath: (m3u.library_path as string) || '',
    syncMode: (sync.mode as string) || 'reconcile',
    // #1455: default on, older installs come back undefined
    wishlistMissing: sync.wishlist_missing_tracks !== false,
    createBackup: sync.create_backup === true,
    statsEnabled: stats.enabled === true,
    statsInterval: (stats.poll_interval as number) || 30,
    adventurousness: typeof adventure === 'number' ? adventure : 0.3,
  };
}

/** the section hints settings.js wrote once the settings had loaded */
export function discoverySummaries(values: DiscoveryValues) {
  const playlists = [values.m3uExport ? 'M3U export' : '', values.libraryM3u ? 'library M3U' : '']
    .filter(Boolean)
    .join(' · ');
  const n = values.adventurousness;
  return {
    playlists: playlists || 'off',
    stats: values.statsEnabled ? 'collecting play history' : 'off',
    // ported as it was. the dial runs 0 to 1, so this always reads "plays it
    // safe": a bug on dev too, listed in SETTINGS_PORT.md, not changed here
    discovery: n <= 25 ? 'plays it safe' : n <= 60 ? 'balanced' : 'adventurous',
  };
}
