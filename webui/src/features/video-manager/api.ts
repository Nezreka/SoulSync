/** the video library manager's server calls (api/video/manager.py, bulk.py). */

import type {
  BulkAction,
  BulkStatus,
  Facets,
  FilesPage,
  Filters,
  Side,
  Sort,
  TitleKind,
  TitlesPage,
} from './types';

export class ManagerError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function send<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  let data: (T & { error?: string; success?: boolean; ok?: boolean }) | null = null;
  try {
    data = (await response.json()) as T & { error?: string; success?: boolean; ok?: boolean };
  } catch {
    data = null;
  }
  if (!response.ok || !data || data.error || data.success === false || data.ok === false) {
    throw new ManagerError(data?.error || `Request failed (${response.status})`, response.status);
  }
  return data;
}

/** the filters as query parameters; empty ones are left out */
export function filterParams(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (key === 'needs') continue;
    if (value) params.set(key, String(value));
  }
  if (filters.needs.length) params.set('needs', filters.needs.join(','));
  return params;
}

function listParams(side: Side, filters: Filters, sort: Sort, page: number, limit: number) {
  const params = filterParams(filters);
  params.set('kind', side);
  params.set('sort', sort.by);
  params.set('dir', sort.dir);
  params.set('page', String(page));
  params.set('limit', String(limit));
  return params;
}

export function fetchTitles(
  side: Side,
  filters: Filters,
  sort: Sort,
  page: number,
  limit: number,
): Promise<TitlesPage> {
  return send<TitlesPage>(
    `/api/video/manager/titles?${listParams(side, filters, sort, page, limit)}`,
  );
}

export function fetchFiles(
  side: Side,
  filters: Filters,
  sort: Sort,
  page: number,
  limit: number,
  titleId?: number,
): Promise<FilesPage> {
  const params = listParams(side, filters, sort, page, limit);
  if (titleId) params.set('title_id', String(titleId));
  return send<FilesPage>(`/api/video/manager/files?${params}`);
}

export function fetchFacets(side: Side): Promise<Facets> {
  return send<Facets>(`/api/video/manager/facets?kind=${side}`);
}

export function fetchMatchingIds(
  side: Side,
  filters: Filters,
): Promise<{ ids: number[]; truncated: boolean }> {
  const params = filterParams(filters);
  params.set('kind', side);
  return send<{ ids: number[]; truncated: boolean }>(`/api/video/manager/ids?${params}`);
}

export interface RenamePreview {
  files: number;
  to_rename: number;
  already_named: number;
  unreachable: number;
  changes: { file_id: number; current: string; expected: string }[];
  more: number;
}

export function fetchRenamePreview(kind: TitleKind, ids: number[]): Promise<RenamePreview> {
  return send<RenamePreview>(`/api/video/manager/rename-preview?kind=${kind}&ids=${ids.join(',')}`);
}

export interface DeletePreview {
  titles: number;
  sample: string[];
  files: number;
  size_bytes: number;
  blocked: { title: string; reason: string }[];
}

export function fetchDeletePreview(kind: TitleKind, ids: number[]): Promise<DeletePreview> {
  return send<DeletePreview>(`/api/video/manager/delete-preview?kind=${kind}&ids=${ids.join(',')}`);
}

export function startBulk(
  kind: TitleKind,
  ids: number[],
  action: BulkAction,
  params: Record<string, unknown> = {},
): Promise<{ ok: boolean; total?: number; label?: string; added?: number; skipped?: number }> {
  return send('/api/video/bulk/start', {
    method: 'POST',
    body: JSON.stringify({ kind, ids, action, params }),
  });
}

export async function fetchBulkStatus(): Promise<BulkStatus> {
  const response = await fetch('/api/video/bulk/status', {
    headers: { Accept: 'application/json' },
  });
  return (await response.json()) as BulkStatus;
}

export interface CollectionRef {
  id: number;
  name: string;
  media_type: string;
}

export async function fetchCollections(kind: TitleKind): Promise<CollectionRef[]> {
  const response = await fetch('/api/video/collections', {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) return [];
  const data = (await response.json()) as { collections?: CollectionRef[] };
  return (data.collections ?? []).filter((c) => (c.media_type || 'movie') === kind);
}

/** the legacy list, for a profile the manager is not for (posters only) */
export interface LegacyItem {
  id: number | string;
  title: string;
  year?: number | null;
  has_poster?: boolean;
  resolution?: string | null;
  status?: string | null;
  episode_count?: number;
  episode_owned?: number;
  thumb_url?: string | null;
  video_count?: number;
}

export async function fetchLegacyLibrary(
  kind: 'movies' | 'shows' | 'channels',
  search: string,
  sort: string,
  page: number,
  limit: number,
): Promise<{ items: LegacyItem[]; pagination: TitlesPage['pagination'] }> {
  const params = new URLSearchParams({ kind, sort, page: String(page), limit: String(limit) });
  if (search) params.set('search', search);
  return send(`/api/video/library?${params}`);
}

export interface TitleDetail {
  id: number;
  title: string;
  sort_title?: string | null;
  year: number | null;
  overview?: string | null;
  tagline?: string | null;
  content_rating?: string | null;
  genres?: string[];
  studio?: string | null;
  network?: string | null;
  locked_fields?: string[];
  tmdb_id?: number | null;
  quality_profile_id?: number | null;
  series_type?: string | null;
}

export function fetchTitleDetail(kind: TitleKind, id: number): Promise<TitleDetail> {
  return send<TitleDetail>(`/api/video/detail/${kind}/${id}`);
}

export function saveTitleFields(
  kind: TitleKind,
  id: number,
  changes: Record<string, unknown>,
): Promise<{ ok: boolean; applied: string[]; locked: string[]; pushed: boolean }> {
  return send(`/api/video/detail/${kind}/${id}/metadata`, {
    method: 'PUT',
    body: JSON.stringify({ changes }),
  });
}

export function setFieldLock(
  kind: TitleKind,
  id: number,
  field: string,
  locked: boolean,
): Promise<{ ok: boolean }> {
  return send(`/api/video/detail/${kind}/${id}/lock`, {
    method: 'POST',
    body: JSON.stringify({ field, locked }),
  });
}

export interface HistoryRow {
  id: number;
  title?: string | null;
  release_title?: string | null;
  /** completed | import_failed | failed | cancelled */
  outcome?: string | null;
  quality_label?: string | null;
  resolution?: string | null;
  source?: string | null;
  size_bytes?: number | null;
  season_number?: number | null;
  episode_number?: number | null;
  episode_title?: string | null;
  error?: string | null;
  grabbed_at?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
}

export async function fetchTitleHistory(kind: TitleKind, id: number): Promise<HistoryRow[]> {
  const data = await send<{ history: HistoryRow[] }>(`/api/video/detail/${kind}/${id}/history`);
  return data.history ?? [];
}
