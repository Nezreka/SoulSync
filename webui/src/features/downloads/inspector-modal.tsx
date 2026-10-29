import { useEffect, useState, useSyncExternalStore } from 'react';
import { createRoot } from 'react-dom/client';

import type { GrabRule, InspectorCandidate, InspectorSearchMeta } from './inspector';
import type { SourceColumnData } from './inspector-columns';

import { bestCandidateIndex, streamInspection } from './inspector';
import { SourceColumn } from './inspector-columns';

/**
 * The candidate inspector on its own: every download source's hits for one
 * track, what passed and what didn't and why, and a way to take one. Opened
 * from a wishlist item ("Interactive Search") and from a failed download
 * (also "Interactive Search"). The redownload modal hosts the same columns
 * inside its own three steps.
 */

/** A title/artist typed over the target's own, when those were wrong. */
export interface InspectorQuery {
  name: string;
  artist: string;
}

export interface InspectorTarget {
  name: string;
  artist?: string;
  album?: string;
  durationMs?: number;
  /** POSTed with searchBody; answers with the NDJSON inspector stream. */
  searchUrl: string;
  searchBody?: unknown;
  /** Start the chosen file. Throw with a message the person should see. */
  onGrab: (candidate: InspectorCandidate, query?: InspectorQuery) => Promise<void>;
  cannotGrab?: GrabRule;
  /** The title/artist can be retyped and searched again; searchBody gets them. */
  editable?: boolean;
}

export function CandidateInspectorModal({
  target,
  onClose,
}: {
  target: InspectorTarget;
  onClose: () => void;
}) {
  const [columns, setColumns] = useState<SourceColumnData[]>([]);
  const [candidates, setCandidates] = useState<InspectorCandidate[]>([]);
  const [streamDone, setStreamDone] = useState(false);
  const [streamError, setStreamError] = useState('');
  const [pickedIdx, setPickedIdx] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');
  // Provenance + policy facet from the first streamed payload: one per
  // inspection (core/downloads/provenance.py).
  const [searchMeta, setSearchMeta] = useState<InspectorSearchMeta | null>(null);
  // the openers don't know the length, the server does
  const [serverDurationMs, setServerDurationMs] = useState(0);
  const [query, setQuery] = useState<InspectorQuery | null>(null);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void streamInspection(
      target.searchUrl,
      query ? { ...(target.searchBody as object), ...query } : target.searchBody,
      (source, fresh, all, rejected, meta) => {
        if (cancelled) return;
        setColumns((prev) => [...prev, { source, candidates: fresh, rejected }]);
        setCandidates([...all]);
        if (meta?.provenance) setSearchMeta((prev) => prev ?? meta);
        if (meta?.expected_duration_ms) setServerDurationMs(meta.expected_duration_ms);
      },
    )
      .then(() => {
        if (!cancelled) setStreamDone(true);
      })
      .catch((error: Error) => {
        if (!cancelled) setStreamError(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, [target, query]);

  const search = (next: InspectorQuery) => {
    setEditing(false);
    if (next.name === shown.name && next.artist === shown.artist) return;
    setColumns([]);
    setCandidates([]);
    setStreamDone(false);
    setStreamError('');
    setPickedIdx(null);
    setStartError('');
    setServerDurationMs(0);
    setQuery(next);
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const bestIdx = bestCandidateIndex(candidates, target.cannotGrab);
  const selectedIdx = pickedIdx ?? (bestIdx >= 0 ? bestIdx : null);
  const selected = selectedIdx != null ? candidates[selectedIdx] : null;
  const shown = query ?? { name: target.name, artist: target.artist || '' };
  const expected = {
    name: shown.name,
    artist: shown.artist,
    // a new artist means the row's album was someone else's (the server drops it too)
    album: query && query.artist !== (target.artist || '') ? undefined : target.album,
    duration_ms: (query ? 0 : target.durationMs) || serverDurationMs || undefined,
  };

  const grab = async (candidate: InspectorCandidate) => {
    setStarting(true);
    setStartError('');
    try {
      await (query ? target.onGrab(candidate, query) : target.onGrab(candidate));
      window.showToast?.(`Downloading ${candidate.display_name || shown.name}`, 'success');
      onClose();
    } catch (error) {
      setStartError((error as Error).message);
      setStarting(false);
    }
  };

  return (
    <div
      className="redownload-overlay rdl-inspector"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rdl-inspector-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="redownload-modal">
        <div className="redownload-header">
          <div>
            <h3 id="rdl-inspector-title">Interactive Search</h3>
            <p className="redownload-header-sub">
              What each download source has for this track, and why SoulSync would or wouldn't take
              it
            </p>
            {searchMeta?.provenance ? (
              <p className="redownload-header-meta">
                Searched {String(searchMeta.provenance.searched_at || '').split(' ')[0]}
                {searchMeta.policy?.target_label
                  ? ` · ladder rung ${(searchMeta.policy.target_index ?? 0) + 1} of ${searchMeta.policy.target_count ?? 0}: ${searchMeta.policy.target_label}`
                  : ''}
              </p>
            ) : null}
          </div>
          <button className="redownload-close" type="button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="redownload-current">
          {editing ? (
            <QueryEditor initial={shown} onSearch={search} onCancel={() => setEditing(false)} />
          ) : (
            <>
              <div className="redownload-current-info">
                <div className="redownload-current-title">{shown.name}</div>
                <div className="redownload-current-meta">
                  {[shown.artist, expected.album].filter(Boolean).join(' · ')}
                  {query ? ' · downloads as typed' : ''}
                </div>
              </div>
              {target.editable ? (
                <button
                  type="button"
                  className="redownload-btn secondary rdl-query-edit"
                  onClick={() => setEditing(true)}
                >
                  Edit search
                </button>
              ) : null}
            </>
          )}
        </div>
        <div className="redownload-body">
          <div className="rdl-src-columns">
            {streamError ? (
              <div className="redownload-error">Error: {streamError}</div>
            ) : columns.length === 0 ? (
              streamDone ? (
                <div className="rdl-src-col-empty">No download sources answered.</div>
              ) : (
                <div className="redownload-loading">
                  <div className="server-search-spinner" />
                  Searching download sources...
                </div>
              )
            ) : (
              columns.map((column) => (
                <SourceColumn
                  column={column}
                  selectedIdx={selectedIdx}
                  bestIdx={bestIdx}
                  onPick={setPickedIdx}
                  expected={expected}
                  onOverride={(row) => void grab(row)}
                  cannotGrab={target.cannotGrab}
                  key={column.source}
                />
              ))
            )}
          </div>
          {startError ? (
            <div className="redownload-error" role="alert">
              Couldn't start: {startError}
            </div>
          ) : null}
        </div>
        <div className="redownload-sticky-footer">
          <div className="redownload-actions">
            <button className="redownload-btn secondary" type="button" onClick={onClose}>
              Cancel
            </button>
            <button
              className="redownload-btn primary"
              type="button"
              disabled={!selected || starting}
              onClick={() => selected && void grab(selected)}
            >
              {starting
                ? 'Starting...'
                : selected
                  ? 'Download Selected'
                  : streamDone
                    ? 'No match to download'
                    : 'Waiting for results...'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Retype the title/artist when the row's own are wrong, and search again. */
function QueryEditor({
  initial,
  onSearch,
  onCancel,
}: {
  initial: InspectorQuery;
  onSearch: (query: InspectorQuery) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [artist, setArtist] = useState(initial.artist);
  return (
    <form
      className="rdl-query-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) onSearch({ name: name.trim(), artist: artist.trim() });
      }}
    >
      <label>
        <span>Title</span>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        <span>Artist</span>
        <input value={artist} onChange={(e) => setArtist(e.target.value)} />
      </label>
      <button type="submit" className="redownload-btn primary" disabled={!name.trim()}>
        Search
      </button>
      <button type="button" className="redownload-btn secondary" onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}

// ---- one host for the whole app, opened from React or the classic pages ----

let current: { target: InspectorTarget; key: number } | null = null;
let openCount = 0;
const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) listener();
}

export function openCandidateInspector(target: InspectorTarget): void {
  openCount += 1;
  current = { target, key: openCount };
  notify();
}

export function closeCandidateInspector(): void {
  current = null;
  notify();
}

export function CandidateInspectorHost() {
  const open = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
  if (!open) return null;
  return (
    <CandidateInspectorModal
      target={open.target}
      onClose={closeCandidateInspector}
      key={open.key}
    />
  );
}

async function postJson(url: string, body: unknown): Promise<Record<string, unknown>> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let data: Record<string, unknown> = {};
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    /* not json */
  }
  if (!response.ok || data.success === false || data.error) {
    const message = typeof data.error === 'string' && data.error ? data.error : '';
    throw new Error(message || `Request failed (${response.status})`);
  }
  return data;
}

/** A rejected row taken anyway says which rule it overrode, for that one grab. */
function overrideOf(candidate: InspectorCandidate) {
  const d = candidate.decision;
  return d && !d.accepted ? { code: d.code, stage: d.stage } : undefined;
}

function candidatePayload(candidate: InspectorCandidate) {
  return {
    username: candidate.username,
    filename: candidate.filename,
    size: candidate.size ?? 0,
    bitrate: candidate.bitrate ?? 0,
    duration: candidate.duration ?? 0,
    quality: candidate.quality ?? '',
    free_upload_slots: candidate.free_upload_slots ?? 0,
    upload_speed: candidate.upload_speed ?? 0,
    queue_length: candidate.queue_length ?? 0,
    artist: candidate.artist ?? '',
    title: candidate.title ?? '',
  };
}

const RELEASE_LEVEL = new Set(['torrent', 'usenet', 'lidarr']);

/** Wishlist "search manually": pick a file, download exactly that. */
export function openWishlistInspector(track: {
  id: string;
  name: string;
  artist?: string;
  album?: string;
}): void {
  openCandidateInspector({
    name: track.name,
    artist: track.artist,
    album: track.album,
    searchUrl: '/api/wishlist/inspect',
    searchBody: { track_id: track.id },
    editable: true,
    cannotGrab: (c) =>
      RELEASE_LEVEL.has(String(c.username || '').toLowerCase())
        ? "A torrent or Usenet hit is a whole release, so it can't be picked for one track. The wishlist's own download can use it."
        : null,
    onGrab: async (candidate, query) => {
      await postJson('/api/wishlist/inspect/download', {
        track_id: track.id,
        candidate: candidatePayload(candidate),
        override: overrideOf(candidate),
        ...query,
      });
      window.updateWishlistCount?.();
    },
  });
}

/** A failed or not-found download: what every source has, and retry with a pick. */
export function openDownloadTaskInspector(
  taskId: string,
  track: { name?: string; artist?: string; album?: string },
): void {
  openCandidateInspector({
    name: track.name || 'Unknown track',
    artist: track.artist,
    album: track.album,
    searchUrl: `/api/downloads/task/${encodeURIComponent(taskId)}/inspect`,
    onGrab: async (candidate) => {
      await postJson(`/api/downloads/task/${encodeURIComponent(taskId)}/download-candidate`, {
        ...candidatePayload(candidate),
        override: overrideOf(candidate),
      });
    },
  });
}

const HOST_ID = 'candidate-inspector-root';

/** Mount the host once and give the classic pages a way in. */
export function mountCandidateInspectorHost(): void {
  if (typeof document === 'undefined' || document.getElementById(HOST_ID)) return;
  const el = document.createElement('div');
  el.id = HOST_ID;
  document.body.appendChild(el);
  createRoot(el).render(<CandidateInspectorHost />);
  window.openDownloadTaskInspector = openDownloadTaskInspector;
}
