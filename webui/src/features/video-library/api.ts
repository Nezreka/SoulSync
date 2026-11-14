/** the library tab's server calls (api/video/library_manage.py). */

import type { MatchCandidate, TitleKind, TitleLibrary } from './types';

async function send<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  let data: (T & { error?: string; success?: boolean }) | null = null;
  try {
    data = (await response.json()) as T & { error?: string; success?: boolean };
  } catch {
    data = null;
  }
  if (!response.ok || !data || data.error || data.success === false) {
    throw new Error(data?.error || `Request failed (${response.status})`);
  }
  return data;
}

export function fetchTitleLibrary(
  kind: TitleKind,
  id: number,
  season?: number | null,
): Promise<TitleLibrary> {
  const q = season != null ? `?season=${encodeURIComponent(season)}` : '';
  return send<TitleLibrary>(`/api/video/detail/${kind}/${id}/library${q}`);
}

export interface RenamePlan {
  current: string;
  expected: string;
  conforms: boolean;
}

export function fetchRenamePlan(fileId: number): Promise<RenamePlan> {
  return send<RenamePlan>(`/api/video/files/${fileId}/rename-plan`);
}

export interface FileActionResult {
  success: boolean;
  action?: string;
  message?: string;
  recycled?: boolean;
  remaining?: number;
  /** what Undo reverses; absent when nothing changed or it can't come back */
  batch?: string | null;
  /** episodes that stopped being hunted with the delete */
  unmonitored?: number;
}

export function renameFile(fileId: number): Promise<FileActionResult> {
  return send<FileActionResult>(`/api/video/files/${fileId}/rename`, {
    method: 'POST',
    body: '{}',
  });
}

export interface AcquisitionState {
  total?: number;
  counts?: {
    owned?: number;
    wanted?: number;
    queued?: number;
    downloading?: number;
    failed?: number;
  };
}

/** where acquisition stands for the title right now (the page's own endpoint) */
export function fetchAcquisition(kind: TitleKind, id: number): Promise<AcquisitionState> {
  return send<AcquisitionState>(`/api/video/detail/${kind}/${id}/acquisition`);
}

export interface BulkResult {
  success: boolean;
  done: number;
  unchanged: number;
  failed: number;
  errors: { file_id: number; error: string }[];
  /** what Undo reverses */
  batch?: string | null;
  /** episodes that stopped being hunted with the delete */
  unmonitored?: number;
}

/** rename to the template, or recycle, a chosen set of files */
export function bulkFiles(
  action: 'rename' | 'delete',
  fileIds: number[],
  kind: TitleKind,
  options: { titleId?: number; stopHunting?: boolean } = {},
): Promise<BulkResult> {
  return send<BulkResult>('/api/video/files/bulk', {
    method: 'POST',
    body: JSON.stringify({
      action,
      file_ids: fileIds,
      kind,
      id: options.titleId,
      stop_hunting: options.stopHunting || undefined,
    }),
  });
}

export interface RenamePreview {
  files: number;
  to_rename: number;
  already_named: number;
  unreachable: number;
  changes: { file_id: number; current: string; expected: string }[];
  more: number;
}

/** what Rename would do to these files, before anything moves */
export function fetchRenamePreview(fileIds: number[]): Promise<RenamePreview> {
  return send<RenamePreview>(`/api/video/files/rename-preview?file_ids=${fileIds.join(',')}`);
}

export interface DeletePreview {
  files: number;
  size_bytes: number;
  unreachable: number;
  /** episodes or movies that would be left with no copy at all */
  emptied: number;
  recycle: boolean;
  items: {
    file_id: number;
    label: string;
    filename: string;
    size_bytes: number;
    reachable: boolean;
  }[];
  more: number;
}

/** what deleting these files would take */
export function fetchDeletePreview(fileIds: number[]): Promise<DeletePreview> {
  return send<DeletePreview>(`/api/video/files/delete-preview?file_ids=${fileIds.join(',')}`);
}

/** hunt, or stop hunting, a picked set of one show's episodes */
export function setEpisodesMonitored(
  showId: number,
  episodeIds: number[],
  monitored: boolean,
): Promise<{ success: boolean; episodes: number }> {
  return send(`/api/video/detail/show/${showId}/episodes/monitor`, {
    method: 'POST',
    body: JSON.stringify({ episode_ids: episodeIds, monitored }),
  });
}

/** the padlock: automation leaves a locked title and its files alone */
export function setPadlock(
  kind: TitleKind,
  id: number,
  locked: boolean,
): Promise<{ success: boolean; locked: boolean }> {
  return send(`/api/video/detail/${kind}/${id}/padlock`, {
    method: 'POST',
    body: JSON.stringify({ locked }),
  });
}

export interface EpisodeExtra {
  overview?: string | null;
  still_url?: string | null;
  imdb_id?: string | null;
  guest_stars?: {
    name: string;
    character?: string | null;
    photo?: string | null;
    tmdb_id?: number | null;
  }[];
}

/** an episode's guest stars and still, from TMDB */
export async function fetchEpisodeExtra(
  tmdbId: number,
  season: number,
  episode: number,
): Promise<EpisodeExtra> {
  const response = await fetch(`/api/video/episode/${tmdbId}/${season}/${episode}`, {
    headers: { Accept: 'application/json' },
  });
  const data = (response.ok ? await response.json().catch(() => null) : null) as
    | (EpisodeExtra & { error?: string })
    | null;
  return data && !data.error ? data : {};
}

export interface ReassignResult {
  success: boolean;
  action: string;
  renamed: boolean;
  rename_note?: string;
  message?: string;
  batch?: string | null;
}

/** file one episode's file under another episode of the same show */
export function reassignFile(fileId: number, episodeId: number): Promise<ReassignResult> {
  return send<ReassignResult>(`/api/video/files/${fileId}/reassign`, {
    method: 'POST',
    body: JSON.stringify({ episode_id: episodeId }),
  });
}

export interface CopyRef {
  file_id: number;
  filename: string;
  size_bytes: number;
  resolution_label: string;
  reachable?: boolean;
}

export interface ExtraCopiesPlan {
  groups: { label: string; keep: CopyRef; remove: CopyRef[] }[];
  files: number;
  size_bytes: number;
  unreachable: number;
}

export function fetchExtraCopies(kind: TitleKind, id: number): Promise<ExtraCopiesPlan> {
  return send<ExtraCopiesPlan>(`/api/video/files/extra-copies/${kind}/${id}`);
}

export interface ExtraCopiesResult {
  success: boolean;
  removed: number;
  failed: number;
  size_bytes: number;
  unreachable: number;
}

export function removeExtraCopies(kind: TitleKind, id: number): Promise<ExtraCopiesResult> {
  return send<ExtraCopiesResult>(`/api/video/files/extra-copies/${kind}/${id}`, {
    method: 'POST',
    body: '{}',
  });
}

export interface ConsolidatePlan {
  target: string;
  files: number;
  size_bytes: number;
  /** bytes that have to be copied because they sit on another drive */
  copy_bytes: number;
  free_bytes: number | null;
  enough_room: boolean;
  moves: {
    file_id: number;
    filename: string;
    label: string;
    size_bytes: number;
    copies: boolean;
  }[];
  skipped: { file_id: number; filename: string; reason: string }[];
}

export function fetchConsolidatePlan(showId: number, target?: string): Promise<ConsolidatePlan> {
  const q = target ? `?target=${encodeURIComponent(target)}` : '';
  return send<ConsolidatePlan>(`/api/video/files/consolidate/${showId}${q}`);
}

export function startConsolidate(showId: number, target: string): Promise<{ success: boolean }> {
  return send<{ success: boolean }>(`/api/video/files/consolidate/${showId}`, {
    method: 'POST',
    body: JSON.stringify({ target }),
  });
}

export interface ConsolidateState {
  known: boolean;
  running: boolean;
  target?: string;
  total?: number;
  done?: number;
  moved?: number;
  failed?: number;
  skipped?: number;
  bytes_total?: number;
  bytes_done?: number;
  current?: string | null;
  stopped?: boolean;
  error?: string | null;
  errors?: { filename: string; error: string }[];
}

export function fetchConsolidateState(showId: number): Promise<ConsolidateState> {
  return send<ConsolidateState>(`/api/video/files/consolidate/${showId}/status`);
}

export async function stopConsolidate(showId: number): Promise<void> {
  await fetch(`/api/video/files/consolidate/${showId}/stop`, { method: 'POST' });
}

/** the wishlist answers success:false when nothing was NEW (all already
 *  wished), which is fine here; only a refused or failed request throws */
async function wish(body: unknown): Promise<{ added?: number }> {
  const response = await fetch('/api/video/wishlist/add', {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = (await response.json().catch(() => null)) as {
    added?: number;
    error?: string;
  } | null;
  if (!response.ok || !data) throw new Error(data?.error || `Request failed (${response.status})`);
  return data;
}

export interface WishEpisode {
  season_number: number;
  episode_number: number;
  title?: string | null;
  air_date?: string | null;
  still_url?: string | null;
  season_poster_url?: string | null;
}

/**
 * wishlist a show's episodes, then search for them now. an owned episode on
 * the wishlist is an upgrade watch: only a strictly better copy is grabbed,
 * and it replaces the file. returns how many were newly added.
 */
export async function wishlistEpisodes(
  show: { tmdb_id: number; title: string; library_id: number },
  episodes: WishEpisode[],
): Promise<number> {
  const res = await wish({
    show: { ...show, poster_url: `/api/video/poster/show/${show.library_id}` },
    episodes,
  });
  document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
  await send('/api/video/wishlist/search', {
    method: 'POST',
    body: JSON.stringify({ scope: 'show', tmdb_id: show.tmdb_id }),
  });
  return res.added ?? 0;
}

/** the same for a movie: wish it (an upgrade watch when owned) and search now */
export async function wishlistMovie(movie: {
  tmdb_id: number;
  title: string;
  year: number | null;
  library_id: number;
}): Promise<void> {
  await wish({ movie: { ...movie, poster_url: `/api/video/poster/movie/${movie.library_id}` } });
  document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
  await send('/api/video/wishlist/search', {
    method: 'POST',
    body: JSON.stringify({ scope: 'movie', tmdb_id: movie.tmdb_id }),
  });
}

export async function searchMatches(
  kind: TitleKind,
  id: number,
  query: string,
): Promise<MatchCandidate[]> {
  const res = await send<{ results: MatchCandidate[] }>(
    `/api/video/detail/${kind}/${id}/identify/search?q=${encodeURIComponent(query)}`,
  );
  return res.results ?? [];
}

export interface IdentifyResult {
  success: boolean;
  tmdb_id: number;
  title: string;
  year: number | null;
  renamed: boolean;
}

export function identifyTitle(
  kind: TitleKind,
  id: number,
  tmdbId: number,
): Promise<IdentifyResult> {
  return send<IdentifyResult>(`/api/video/detail/${kind}/${id}/identify`, {
    method: 'POST',
    body: JSON.stringify({ tmdb_id: tmdbId }),
  });
}

export function releaseIdentity(kind: TitleKind, id: number): Promise<{ success: boolean }> {
  return send<{ success: boolean }>(`/api/video/detail/${kind}/${id}/identify/release`, {
    method: 'POST',
    body: '{}',
  });
}
