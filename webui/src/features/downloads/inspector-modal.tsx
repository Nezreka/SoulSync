import { useEffect, useState, useSyncExternalStore } from 'react';
import { createRoot } from 'react-dom/client';

import type { GrabRule, InspectorCandidate } from './inspector';
import type { SourceColumnData } from './inspector-columns';

import { bestCandidateIndex, streamInspection } from './inspector';
import { SourceColumn } from './inspector-columns';

/**
 * The candidate inspector on its own: every download source's hits for one
 * track, what passed and what didn't and why, and a way to take one. Opened
 * from a wishlist item ("search manually") and from a failed download ("see
 * what every source has"). The redownload modal hosts the same columns inside
 * its own three steps.
 */

export interface InspectorTarget {
  name: string;
  artist?: string;
  album?: string;
  durationMs?: number;
  /** POSTed with searchBody; answers with the NDJSON inspector stream. */
  searchUrl: string;
  searchBody?: unknown;
  /** Start the chosen file. Throw with a message the person should see. */
  onGrab: (candidate: InspectorCandidate) => Promise<void>;
  cannotGrab?: GrabRule;
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

  useEffect(() => {
    let cancelled = false;
    void streamInspection(target.searchUrl, target.searchBody, (source, fresh, all, rejected) => {
      if (cancelled) return;
      setColumns((prev) => [...prev, { source, candidates: fresh, rejected }]);
      setCandidates([...all]);
    })
      .then(() => {
        if (!cancelled) setStreamDone(true);
      })
      .catch((error: Error) => {
        if (!cancelled) setStreamError(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, [target]);

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
  const expected = {
    name: target.name,
    artist: target.artist,
    album: target.album,
    duration_ms: target.durationMs,
  };

  const grab = async (candidate: InspectorCandidate) => {
    setStarting(true);
    setStartError('');
    try {
      await target.onGrab(candidate);
      window.showToast?.(`Downloading ${candidate.display_name || target.name}`, 'success');
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
            <h3 id="rdl-inspector-title">Search every source</h3>
            <p className="redownload-header-sub">
              What each download source has for this track, and why SoulSync would or wouldn't take
              it
            </p>
          </div>
          <button className="redownload-close" type="button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="redownload-current">
          <div className="redownload-current-info">
            <div className="redownload-current-title">{target.name}</div>
            <div className="redownload-current-meta">
              {[target.artist, target.album].filter(Boolean).join(' · ')}
            </div>
          </div>
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
    cannotGrab: (c) =>
      RELEASE_LEVEL.has(String(c.username || '').toLowerCase())
        ? "A torrent or Usenet hit is a whole release, so it can't be picked for one track. The wishlist's own download can use it."
        : null,
    onGrab: async (candidate) => {
      await postJson('/api/wishlist/inspect/download', {
        track_id: track.id,
        candidate: candidatePayload(candidate),
        override: overrideOf(candidate),
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
