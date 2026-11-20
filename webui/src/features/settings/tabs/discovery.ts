/**
 * What the Discovery tab shows, worked out the way settings.js did it
 * (SETTINGS_PORT.md, step 4). The hero sentences are discovery/words.ts.
 */

import type { SettingsPayload } from '../settings-store';

/** the server playlist sync modes, each with what it does in a line */
export const SYNC_MODES = [
  {
    value: 'replace',
    label: 'Replace',
    say: 'Deletes the playlist on your server and makes it again each sync. Its image and description go with it.',
  },
  {
    value: 'reconcile',
    label: 'Reconcile',
    say: 'Edits the playlist in place, so its image and description stay. The default.',
  },
  {
    value: 'append',
    label: 'Append',
    say: 'Only ever adds new tracks. Nothing is removed.',
  },
] as const;

export const LOOKBACK_PERIODS = [
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days (default)' },
  { value: '90', label: 'Last 90 days' },
  { value: '180', label: 'Last 6 months' },
  { value: 'all', label: 'Entire discography' },
] as const;

export const HEMISPHERES = [
  { value: 'northern', label: 'Northern' },
  { value: 'southern', label: 'Southern' },
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
