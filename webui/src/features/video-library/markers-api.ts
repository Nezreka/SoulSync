/** skip markers, the admin's calls (api/video/markers.py). */

export type MarkerItemKind = 'movie' | 'episode';
export type MarkerKind = 'intro' | 'recap' | 'credits' | 'preview';

export interface MarkerRow {
  kind: MarkerKind | 'commercial';
  start: number;
  end: number;
  source: string;
  label?: string;
  confidence?: number;
  post_credits?: { start: number; end: number };
  extra?: Record<string, unknown>;
}

export interface MarkerFile {
  file_id: number;
  name: string;
  duration: number | null;
  /** what the player shows */
  markers: MarkerRow[];
  /** every source, each with its own answer */
  sources: MarkerRow[];
  /** kinds an admin removed */
  removed: string[];
  analysis: {
    status: string;
    error?: string | null;
    analyzed_at?: string | null;
    attempts: number;
  };
  sidecar: { writes: boolean; written: boolean; reachable: boolean };
}

export interface MarkersView {
  kind: MarkerItemKind;
  id: number;
  files: MarkerFile[];
}

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
  if (!response.ok || !data || data.success === false) {
    throw new Error(data?.error || `Request failed (${response.status})`);
  }
  return data;
}

export function fetchMarkers(kind: MarkerItemKind, id: number): Promise<MarkersView> {
  return send<MarkersView>(`/api/video/markers/${kind}/${id}`);
}

export function setMarker(
  kind: MarkerItemKind,
  id: number,
  body: {
    kind: MarkerKind;
    start: number;
    end: number;
    file_id?: number;
    scope?: 'file' | 'season';
  },
): Promise<MarkersView> {
  return send<MarkersView>(`/api/video/markers/${kind}/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export function removeMarker(
  kind: MarkerItemKind,
  id: number,
  marker: MarkerKind,
  fileId?: number,
): Promise<MarkersView> {
  const q = new URLSearchParams({ kind: marker });
  if (fileId != null) q.set('file_id', String(fileId));
  return send<MarkersView>(`/api/video/markers/${kind}/${id}?${q}`, { method: 'DELETE' });
}

export function resetMarker(
  kind: MarkerItemKind,
  id: number,
  marker: MarkerKind,
  fileId?: number,
): Promise<MarkersView> {
  return send<MarkersView>(`/api/video/markers/${kind}/${id}/reset`, {
    method: 'POST',
    body: JSON.stringify({ kind: marker, file_id: fileId }),
  });
}

export function findMarkersNow(body: {
  show_id?: number;
  season?: number | null;
  movie_id?: number;
  episode_id?: number;
}): Promise<{ queued: number; files: number }> {
  return send(`/api/video/markers/find`, { method: 'POST', body: JSON.stringify(body) });
}
