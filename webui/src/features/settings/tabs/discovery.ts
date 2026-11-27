/**
 * What the Discovery tab shows, worked out the way settings.js did it
 * (SETTINGS_PORT.md, step 4).
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

/** #2332: when holiday music may play in mixes, radio and generated playlists */
export const HOLIDAY_MODES = [
  {
    value: 'season',
    label: 'In season',
    say: 'Holiday songs play only between the dates below, and a Holiday mix shows up then.',
  },
  {
    value: 'always',
    label: 'Always',
    say: 'Holiday songs can turn up in any mix, all year.',
  },
  {
    value: 'never',
    label: 'Never',
    say: 'Holiday songs stay out of every mix and radio, and there is no Holiday mix.',
  },
] as const;

export const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
] as const;

/** 'MM-DD' as [month, day], a bad value falls back */
export function parseDay(value: unknown, fallback: string): [number, number] {
  for (const raw of [value, fallback]) {
    const match = /^\s*(\d{1,2})-(\d{1,2})\s*$/.exec(String(raw ?? ''));
    if (match) {
      const month = Number(match[1]);
      const day = Number(match[2]);
      if (month >= 1 && month <= 12 && day >= 1 && day <= 31) return [month, day];
    }
  }
  return [11, 15];
}

export function formatDay(month: number, day: number): string {
  return `${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/** "November 15" */
export function sayDay(value: string): string {
  const [month, day] = parseDay(value, value);
  return `${MONTHS[month - 1]} ${day}`;
}

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
  holidayMode: string;
  holidayStart: string;
  holidayEnd: string;
  holidayTag: boolean;
}

export function resolveDiscovery(settings: SettingsPayload): DiscoveryValues {
  const m3u = settings.m3u_export ?? {};
  const sync = settings.playlist_sync ?? {};
  const stats = settings.listening_stats ?? {};
  const adventure = settings.discover?.adventurousness;
  const holiday = settings.holiday_music ?? {};
  const mode = String(holiday.mode ?? '');
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
    // older installs have no holiday_music section: in season, Nov 15 to Jan 6
    holidayMode: HOLIDAY_MODES.some((m) => m.value === mode) ? mode : 'season',
    holidayStart: formatDay(...parseDay(holiday.season_start, '11-15')),
    holidayEnd: formatDay(...parseDay(holiday.season_end, '01-06')),
    holidayTag: holiday.write_tag === true,
  };
}
