/**
 * player downloads: every /api/player/offline route, typed, plus "this
 * computer" (what this browser saved, kept here and reported to the server
 * as a web device so every screen sees it). scrap/DOWNLOADS_CONTRACT.md is
 * the shape both the app and this follow.
 */

import { queryOptions } from '@tanstack/react-query';
import { HTTPError } from 'ky';

import { apiClient } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

import { deviceId } from './device';
import { formatBytes, type DownloadItemType } from './downloads';

export { formatBytes };

export type OfflineKind = 'video' | 'audio';
export type ItemType =
  | 'movie'
  | 'episode'
  | 'youtube'
  | 'home'
  | 'track'
  | 'audiobook'
  | 'podcast_episode';
export type GroupType =
  | 'movie'
  | 'episode'
  | 'season'
  | 'show'
  | 'youtube'
  | 'home'
  | 'home_album'
  | 'track'
  | 'album'
  | 'artist'
  | 'playlist'
  | 'audiobook'
  | 'podcast';
export type RuleType = 'show' | 'playlist' | 'podcast';
export type ItemState = 'downloaded' | 'downloading' | 'queued' | 'paused' | 'failed';

export interface PlanItem {
  item_type: ItemType;
  item_id: string;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  duration_s?: number | null;
  size_bytes?: number | null;
  source?: {
    container?: string | null;
    video_codec?: string | null;
    height?: number | null;
    hdr?: string | null;
    audio_codec?: string | null;
    bitrate_kbps?: number | null;
  } | null;
  on_device?: boolean;
  watched?: boolean;
  position_s?: number | null;
}

export interface PlanQuality {
  id: string;
  label: string;
  sub?: string | null;
  total_bytes: number;
  per_item_bytes: Record<string, number>;
}

export interface Rule {
  rule_id: number;
  type: RuleType;
  id: string;
  quality: string;
  keep_next?: number | null;
  delete_watched?: boolean;
  follow?: boolean;
  title?: string | null;
  artwork?: string | null;
  created_at?: string | null;
}

export interface Plan {
  success?: boolean;
  kind: OfflineKind;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  items: PlanItem[];
  qualities: PlanQuality[];
  group?: { type: GroupType; id: string };
  conversion_off?: boolean;
  rule?: Rule | null;
  free_bytes?: number | null;
  limit_bytes?: number | null;
}

export interface Prepared {
  id: string;
  status: 'ready' | 'queued' | 'converting' | 'failed';
  progress: number;
  url?: string | null;
  size_bytes?: number | null;
  error?: string | null;
  item_type?: ItemType;
  item_id?: string;
  quality?: string;
  filename?: string | null;
}

export interface ItemGroup {
  type: GroupType;
  id: string;
  title?: string | null;
  subtitle?: string | null;
  artwork?: string | null;
}

export interface DeviceItem {
  item_type: ItemType;
  item_id: string;
  quality: string;
  size_bytes: number;
  state: ItemState;
  progress?: number | null;
  title?: string | null;
  subtitle?: string | null;
  artwork?: string | null;
  group?: ItemGroup | null;
  kind?: OfflineKind;
  watched?: boolean;
  position_s?: number | null;
  duration_s?: number | null;
  /** when it got to its state (a download's finish), iso */
  downloaded_at?: string | null;
}

export interface Wish {
  wish_id: number;
  action: 'add' | 'remove';
  type: GroupType | ItemType;
  id: string;
  quality?: string | null;
  title?: string | null;
  subtitle?: string | null;
  artwork?: string | null;
}

export interface Device {
  device_id: string;
  device_name: string;
  // 'android' or 'web'
  platform: string;
  last_seen?: string | null;
  free_bytes?: number | null;
  limit_bytes?: number | null;
  used_bytes?: number | null;
  wifi_only?: boolean;
  items: DeviceItem[];
  rules: Rule[];
  wishes?: Wish[];
}

export interface OfflineSettings {
  allow_conversion_for_members: boolean;
  max_conversions: number;
  cache_gb: number;
}

export interface OfflineSettingsResponse {
  success?: boolean;
  settings: OfflineSettings;
  is_admin: boolean;
  allow_downloads: boolean;
  can_convert: boolean;
  cache_used_bytes: number;
}

/** the server's own words for a refusal, else the fallback */
async function call<T>(run: () => Promise<T>, fallback: string): Promise<T> {
  try {
    return await run();
  } catch (error) {
    if (error instanceof HTTPError) {
      let body: { message?: unknown; error?: unknown } | null = null;
      try {
        body = (await error.response.clone().json()) as { message?: unknown; error?: unknown };
      } catch {
        body = null;
      }
      const words =
        (typeof body?.message === 'string' && body.message) ||
        (typeof body?.error === 'string' && body.error.includes(' ') && body.error) ||
        fallback;
      throw new Error(words);
    }
    throw error instanceof Error ? error : new Error(fallback);
  }
}

// ── queries ─────────────────────────────────────────────────────────────

export const offlineKeys = {
  all: ['player', 'offline'] as const,
  devices: ['player', 'offline', 'devices'] as const,
  settings: ['player', 'offline', 'settings'] as const,
  plan: (type: string, id: string, device: string) =>
    ['player', 'offline', 'plan', type, id, device] as const,
};

export const planQuery = (type: GroupType, id: string, device: string) =>
  queryOptions({
    queryKey: offlineKeys.plan(type, id, device),
    queryFn: () =>
      call(
        () =>
          apiClient
            .get('player/offline/plan', {
              searchParams: { type, id, ...(device && device !== THIS ? { device } : {}) },
            })
            .json<Plan>(),
        "Couldn't size that up.",
      ),
    staleTime: 30_000,
    retry: false,
  });

export const devicesQuery = () =>
  queryOptions({
    queryKey: offlineKeys.devices,
    queryFn: async () =>
      (
        await call(
          () => apiClient.get('player/offline/devices').json<{ devices?: Device[] }>(),
          "Couldn't load your devices.",
        )
      ).devices ?? [],
    staleTime: 20_000,
    refetchInterval: 30_000,
  });

export const offlineSettingsQuery = () =>
  queryOptions({
    queryKey: offlineKeys.settings,
    queryFn: () =>
      call(
        () => apiClient.get('player/offline/settings').json<OfflineSettingsResponse>(),
        "Couldn't load the download settings.",
      ),
    staleTime: 60_000,
  });

// ── writes ──────────────────────────────────────────────────────────────

export function saveOfflineSettings(changes: Partial<OfflineSettings>) {
  return call(
    () =>
      apiClient
        .put('player/offline/settings', { json: changes })
        .json<{ settings: OfflineSettings }>(),
    "Couldn't save that setting.",
  );
}

export function prepare(itemType: ItemType, itemId: string, quality: string) {
  return call(
    () =>
      apiClient
        .post('player/offline/prepare', {
          json: { item_type: itemType, item_id: itemId, quality },
        })
        .json<Prepared>(),
    "Couldn't get that ready.",
  );
}

export function preparedStatus(id: string) {
  return call(
    () => apiClient.get(`player/offline/prepare/${encodeURIComponent(id)}`).json<Prepared>(),
    "Couldn't check on that.",
  );
}

export function sendWish(
  device: string,
  action: 'add' | 'remove',
  type: string,
  id: string,
  quality?: string,
) {
  return call(
    () =>
      apiClient
        .post('player/offline/wishes', {
          json: { device_id: device, action, type, id, ...(quality ? { quality } : {}) },
        })
        .json<{ wishes?: Wish[] }>(),
    action === 'add' ? "Couldn't send that to the device." : "Couldn't remove that.",
  );
}

export function saveRule(
  device: string,
  rule: {
    type: RuleType;
    id: string;
    quality: string;
    keep_next?: number;
    delete_watched?: boolean;
    follow?: boolean;
  },
) {
  return call(
    () =>
      apiClient
        .post('player/offline/rules', { json: { device_id: device, ...rule } })
        .json<{ rule: Rule }>(),
    "Couldn't save that smart download.",
  );
}

export function deleteRule(ruleId: number) {
  return call(
    () => apiClient.delete(`player/offline/rules/${ruleId}`).json<{ removed?: number }>(),
    "Couldn't turn that off.",
  );
}

// ── this computer ───────────────────────────────────────────────────────

/** the device picker's id for this browser */
export const THIS = 'this';

export interface SavedFile {
  item_type: ItemType;
  item_id: string;
  quality: string;
  size_bytes: number;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  saved_at: string;
  /** the season, album or playlist it was saved with, so the page can file it */
  group?: ItemGroup | null;
}

const SAVED_KEY = 'ss-offline-saved';
const SAVED_MAX = 500;

export function savedFiles(): SavedFile[] {
  try {
    const raw = localStorage.getItem(SAVED_KEY);
    const list = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(list) ? (list as SavedFile[]).filter((f) => f && f.item_id) : [];
  } catch {
    return [];
  }
}

function writeSaved(list: SavedFile[]) {
  try {
    localStorage.setItem(SAVED_KEY, JSON.stringify(list.slice(0, SAVED_MAX)));
  } catch {
    // no storage: this visit only
  }
}

export function rememberSaved(file: SavedFile) {
  const all = savedFiles();
  const same = (f: SavedFile) => f.item_type === file.item_type && f.item_id === file.item_id;
  const before = all.find(same);
  // saved again on its own: it stays filed under its season, album or playlist
  const loose = !file.group || file.group.type === file.item_type;
  const group = loose && before?.group ? before.group : file.group;
  writeSaved([{ ...file, group }, ...all.filter((f) => !same(f))]);
}

export function forgetSaved(itemType: string, itemId: string) {
  writeSaved(savedFiles().filter((f) => !(f.item_type === itemType && f.item_id === itemId)));
}

/** "Chrome on Windows" */
export function browserName(ua = typeof navigator === 'undefined' ? '' : navigator.userAgent) {
  const browser = ua.includes('Edg/')
    ? 'Edge'
    : ua.includes('OPR/')
      ? 'Opera'
      : ua.includes('Firefox/')
        ? 'Firefox'
        : ua.includes('Chrome/')
          ? 'Chrome'
          : ua.includes('Safari/')
            ? 'Safari'
            : 'Browser';
  const os = /Windows/.test(ua)
    ? 'Windows'
    : /Android/.test(ua)
      ? 'Android'
      : /iPhone|iPad/.test(ua)
        ? 'iOS'
        : /Mac OS X/.test(ua)
          ? 'Mac'
          : /Linux/.test(ua)
            ? 'Linux'
            : '';
  return os ? `${browser} on ${os}` : browser;
}

export function thisDeviceId(): string {
  return deviceId();
}

/** tell the server what this browser saved (its whole truth, as the app does) */
export function reportThisComputer() {
  const files = savedFiles();
  return call(
    () =>
      apiClient
        .post('player/offline/devices/report', {
          json: {
            device_id: deviceId(),
            device_name: browserName(),
            platform: 'web',
            free_bytes: null,
            limit_bytes: null,
            used_bytes: files.reduce((n, f) => n + (f.size_bytes || 0), 0),
            wifi_only: false,
            items: files.map((f) => ({
              item_type: f.item_type,
              item_id: f.item_id,
              quality: f.quality,
              size_bytes: f.size_bytes,
              state: 'downloaded',
              progress: 1,
              downloaded_at: f.saved_at,
            })),
          },
        })
        .json<unknown>(),
    "Couldn't note what this computer saved.",
  );
}

/** hand the browser the prepared file */
export function saveToComputer(url: string, filename?: string | null) {
  const a = document.createElement('a');
  a.href = url.startsWith('http') ? url : appURL(url);
  if (filename) a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ── words ───────────────────────────────────────────────────────────────

export const itemKey = (t: string, id: string) => `${t}:${id}`;

const QUALITY_WORDS: Record<string, string> = {
  original: 'Original',
  high: '1080p',
  phone: '720p',
  small: '480p',
  '320': '320 kbps',
  '256': '256 kbps',
  '128': '128 kbps',
};

export function qualityWord(q: string | null | undefined): string {
  return (q && QUALITY_WORDS[q]) || q || 'Original';
}

/** the size, or nothing when there isn't one */
export function bytes(n: number | null | undefined): string {
  return n && n > 0 ? formatBytes(n) : '';
}

/** "seen 2 h ago" */
export function seenAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '';
  const at = Date.parse(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  if (!Number.isFinite(at)) return '';
  const mins = Math.max(0, Math.round((now - at) / 60_000));
  if (mins < 2) return 'online now';
  if (mins < 60) return `seen ${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `seen ${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? 'seen yesterday' : `seen ${days} days ago`;
}

/** the quality picked last time, per kind of thing */
export function rememberedQuality(kind: OfflineKind): string | null {
  try {
    return localStorage.getItem(`ss-offline-quality-${kind}`);
  } catch {
    return null;
  }
}

export function rememberQuality(kind: OfflineKind, q: string) {
  try {
    localStorage.setItem(`ss-offline-quality-${kind}`, q);
  } catch {
    // not kept: picked again next time
  }
}

/** "6 episodes", "1 song" */
export function countWords(n: number, kind: OfflineKind, type?: string): string {
  const noun =
    type === 'movie'
      ? 'movie'
      : type === 'audiobook'
        ? 'book'
        : type === 'podcast' || type === 'podcast_episode'
          ? 'episode'
          : type === 'youtube'
            ? 'video'
            : kind === 'video'
              ? 'episode'
              : 'song';
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

/** a single download's item type, as the download sheet groups it */
export const GROUP_OF: Record<DownloadItemType, GroupType> = {
  movie: 'movie',
  episode: 'episode',
  youtube: 'youtube',
  podcast: 'podcast',
  audiobook: 'audiobook',
  track: 'track',
  album: 'album',
};
