/**
 * home videos: the routes (api/player_home.py, scrap/HOME_VIDEOS_CONTRACT.md),
 * typed, and the words and items the pages build from them. clips from a
 * camera or a phone, never looked up: named and dated from the clip itself.
 */

import { queryOptions } from '@tanstack/react-query';

import { apiClient } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

import type { PlayItem } from '../-engine/types';

const MINUTE = 60_000;

export interface HomeVideo {
  id: number;
  title: string;
  /** local wall time, YYYY-MM-DDTHH:MM:SS */
  filmed_at: string | null;
  duration_s: number | null;
  width: number | null;
  height: number | null;
  album: string;
  album_key: string;
  /** null until the picture is made (in the background) */
  thumb_url: string | null;
  progress: { position_s: number; watched: boolean };
  missing: boolean;
}

export interface HomeAlbum {
  key: string;
  name: string;
  /** the folder under its root ('' for the clips at the top) */
  path?: string;
  count: number;
  first: string | null;
  last: string | null;
  /** clip ids, up to 3 */
  covers: number[];
}

export interface HomeLibraryRef {
  id: number;
  name: string;
  in_guide: boolean;
}

export interface HomeOverview {
  library: HomeLibraryRef;
  counts: { videos: number; first_year: number | null; last_year: number | null };
  on_this_day: HomeVideo[];
  albums: HomeAlbum[];
  months: { month: string; count: number }[];
  /** where the clips come from: a server library's name, a folder's */
  sources?: string[];
  can_edit?: boolean;
  scanning?: boolean;
}

export interface HomeVideoDetail extends HomeVideo {
  frames: { at_s: number; url: string }[];
  file_name?: string;
  auto_title?: string | null;
  auto_filmed_at?: string | null;
  filmed_source?: 'you' | 'camera' | 'name' | 'server' | 'file' | 'added' | null;
  notes?: string;
  thumb_at_s?: number | null;
  edited?: { title: boolean; filmed_at: boolean; thumb: boolean; notes: boolean };
  library?: number | null;
  can_edit?: boolean;
}

export interface HomeEdit {
  title?: string | null;
  filmed_at?: string | null;
  thumb_at_s?: number | null;
  notes?: string | null;
}

export interface HomeSources {
  server: 'plex' | 'jellyfin' | null;
  libraries: { title: string; count: number | null; on: boolean; root: string; videos?: number }[];
  folders: {
    path: string;
    root: string;
    videos: number;
    shared?: number;
    shared_with?: string | null;
  }[];
  error?: string | null;
  scan: HomeScan;
}

export interface HomeScan {
  state?: 'idle' | 'running' | 'done' | 'error';
  phase?: string;
  done?: number;
  total?: number;
  found?: number;
  only?: string | null;
  problems?: string[];
  error?: string | null;
}

export const PAGE = 60;

export const homeOverviewQuery = (library: number | undefined) =>
  queryOptions({
    queryKey: ['player', 'home', 'overview', library ?? 0],
    queryFn: async () =>
      await apiClient
        .get('player/home/overview', { searchParams: library ? { library } : {} })
        .json<HomeOverview>(),
    staleTime: 2 * MINUTE,
  });

export interface HomePageArgs {
  library?: number;
  month?: string;
  album?: string;
  q?: string;
  offset?: number;
  limit?: number;
}

/** one page of clips: the timeline, a month, an album or a search */
export async function fetchHomeVideos(args: HomePageArgs) {
  const searchParams: Record<string, string | number> = {
    offset: args.offset ?? 0,
    limit: args.limit ?? PAGE,
  };
  if (args.library) searchParams.library = args.library;
  if (args.month) searchParams.month = args.month;
  if (args.album) searchParams.album = args.album;
  if (args.q) searchParams.q = args.q;
  return await apiClient
    .get('player/home/videos', { searchParams })
    .json<{ videos: HomeVideo[]; total: number }>();
}

export const homeVideosQuery = (args: HomePageArgs) =>
  queryOptions({
    queryKey: ['player', 'home', 'videos', args],
    queryFn: async () => await fetchHomeVideos(args),
    staleTime: 2 * MINUTE,
  });

export const homeVideoQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'home', 'video', id],
    queryFn: async () => await apiClient.get(`player/home/video/${id}`).json<HomeVideoDetail>(),
    staleTime: 0,
  });

export async function saveHomeVideo(id: number, changes: HomeEdit): Promise<HomeVideoDetail> {
  return await apiClient.put(`player/home/video/${id}`, { json: changes }).json<HomeVideoDetail>();
}

export const homeSourcesQuery = () =>
  queryOptions({
    queryKey: ['player', 'home', 'sources'],
    queryFn: async () => await apiClient.get('player/home/sources').json<HomeSources>(),
    staleTime: 30_000,
  });

export async function putHomeSources(body: {
  home_more?: string[];
  folders?: string[];
}): Promise<HomeSources> {
  return await apiClient.put('player/home/sources', { json: body }).json<HomeSources>();
}

export async function homeScanStatus(): Promise<HomeScan> {
  return (await apiClient.get('player/home/scan').json<{ scan: HomeScan }>()).scan;
}

export async function checkHomeFolder(
  path: string,
): Promise<{ ok: boolean; message: string; videos?: number }> {
  return await apiClient
    .post('player/home/folder-check', { json: { path } })
    .json<{ ok: boolean; message: string; videos?: number }>();
}

// ── words ────────────────────────────────────────────────────────────────

const MONTHS = [
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
];

function parts(iso: string | null | undefined) {
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?(?:[T ](\d{2}):(\d{2}))?/.exec(iso ?? '');
  if (!m) return null;
  return {
    y: Number(m[1]),
    mo: Number(m[2]),
    d: m[3] ? Number(m[3]) : 1,
    h: m[4] ? Number(m[4]) : null,
    mi: m[5] ? Number(m[5]) : null,
  };
}

/** "2026-11" -> "November 2026" */
export function monthLabel(month: string): string {
  const p = parts(month);
  return p ? `${MONTHS[p.mo - 1]} ${p.y}` : month;
}

/** "Nov 22" */
export function dayBadge(iso: string | null | undefined): string {
  const p = parts(iso);
  return p ? `${MONTHS[p.mo - 1].slice(0, 3)} ${p.d}` : '';
}

/** "Nov 25, 2023" */
export function dayLabel(iso: string | null | undefined): string {
  const p = parts(iso);
  return p ? `${MONTHS[p.mo - 1].slice(0, 3)} ${p.d}, ${p.y}` : '';
}

/** "October 31, 2026 · 6:30 PM" */
export function longWhen(iso: string | null | undefined): string {
  const p = parts(iso);
  if (!p) return '';
  const day = `${MONTHS[p.mo - 1]} ${p.d}, ${p.y}`;
  if (p.h === null || p.mi === null || (p.h === 0 && p.mi === 0)) return day;
  return `${day} · ${p.h % 12 || 12}:${String(p.mi).padStart(2, '0')} ${p.h < 12 ? 'AM' : 'PM'}`;
}

/** "3 years ago", for on this day */
export function yearsAgo(iso: string | null | undefined, today = new Date()): string {
  const p = parts(iso);
  if (!p) return '';
  const n = today.getFullYear() - p.y;
  return n <= 0 ? 'This year' : n === 1 ? 'A year ago' : `${n} years ago`;
}

/** an album's span: "2010 – 2025", "Jul 2024", "" */
export function spanLabel(first: string | null, last: string | null): string {
  const a = parts(first);
  const b = parts(last);
  if (!a || !b) return '';
  if (a.y !== b.y) return `${a.y} – ${b.y}`;
  if (a.mo === b.mo) return `${MONTHS[a.mo - 1].slice(0, 3)} ${a.y}`;
  return String(a.y);
}

/** "412 videos · 2009 – 2026" */
export function countLine(o: Pick<HomeOverview, 'counts'>): string {
  const n = o.counts.videos;
  const words = `${n.toLocaleString()} ${n === 1 ? 'video' : 'videos'}`;
  const { first_year: a, last_year: b } = o.counts;
  if (!a || !b) return words;
  return `${words} · ${a === b ? a : `${a} – ${b}`}`;
}

/** where they come from, in words: 'from "Home Videos" and family' */
export function sourcesLine(sources: string[] | undefined): string {
  const list = (sources ?? []).filter(Boolean).map((x) => `“${x}”`);
  if (!list.length) return '';
  if (list.length === 1) return `from ${list[0]}`;
  return `from ${list.slice(0, -1).join(', ')} and ${list.at(-1)}`;
}

/** "date filmed" hint: where the date came from */
export function filmedHint(source: HomeVideoDetail['filmed_source']): string {
  switch (source) {
    case 'you':
      return 'You set this date. Clear it to go back to the one in the video.';
    case 'camera':
      return 'Read from the video itself. Change it if the camera’s clock was wrong.';
    case 'name':
      return 'Read from the file’s name. Change it if that’s not when it was filmed.';
    case 'server':
      return 'The date your media server gave it. Change it if it’s wrong.';
    case 'file':
      return 'The file’s own date, as nothing better was found. Change it if you know.';
    default:
      return 'When it was added. Set the day it was filmed.';
  }
}

/** a datetime-local input's value from a stored date */
export function inputWhen(iso: string | null | undefined): string {
  const p = parts(iso);
  if (!p) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${p.y}-${pad(p.mo)}-${pad(p.d)}T${pad(p.h ?? 0)}:${pad(p.mi ?? 0)}`;
}

export function thumbSrc(v: Pick<HomeVideo, 'thumb_url'>): string | null {
  return v.thumb_url ? appURL(v.thumb_url) : null;
}

/** a placeholder's colour while a clip has no picture yet, steady per clip */
export function placeholderHue(id: number): number {
  return (id * 47) % 360;
}

/** a clip as a queue item: the list it sits in plays on from it */
export function homeItem(v: HomeVideo, library?: number): PlayItem {
  return {
    type: 'home',
    id: String(v.id),
    title: v.title,
    subtitle: [v.album, dayLabel(v.filmed_at)].filter(Boolean).join(' · ') || undefined,
    artwork: thumbSrc(v),
    backdrop: thumbSrc(v),
    href: `/player/home-videos${library ? `?library=${library}` : ''}`,
    duration: v.duration_s ?? null,
    parentId: v.album_key || undefined,
    parentTitle: v.album || undefined,
  };
}

/** the playable ones from ``at`` on, in order */
export function homeQueue(videos: readonly HomeVideo[], at: number, library?: number) {
  const playable = videos.filter((v) => !v.missing);
  const start = Math.max(
    0,
    playable.findIndex((v) => v.id === videos[at]?.id),
  );
  return playable.slice(start).map((v) => homeItem(v, library));
}
