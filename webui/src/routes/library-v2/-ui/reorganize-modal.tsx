import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import type { LibraryV2ReorganizeQueueItem } from '../-library-v2.types';

import {
  applyLibraryV2AlbumReorganize,
  applyLibraryV2ArtistReorganizeAll,
  fetchLibraryV2AlbumReorganizeSources,
  fetchLibraryV2ReorganizeQueueSnapshot,
  fetchLibraryV2ReorganizeSourcesGlobal,
  LIBRARY_V2_QUERY_KEY,
  previewLibraryV2AlbumReorganize,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';

const TERMINAL_QUEUE_STATUSES: ReadonlySet<LibraryV2ReorganizeQueueItem['status']> = new Set([
  'done',
  'failed',
  'cancelled',
]);

/** Poll the (legacy, shared) reorganize queue for one item by ``queueId``
 *  until it reaches a terminal status (deep-dive G7) — turns "N queued"
 *  fire-and-forget into visible live progress. Stops polling once terminal;
 *  a `null` queueId is a no-op. */
function useReorganizeQueueItem(queueId: string | null): LibraryV2ReorganizeQueueItem | null {
  const [item, setItem] = useState<LibraryV2ReorganizeQueueItem | null>(null);

  useEffect(() => {
    setItem(null);
    if (!queueId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        const snapshot = await fetchLibraryV2ReorganizeQueueSnapshot();
        if (cancelled) return;
        const all = [snapshot.active, ...snapshot.queued, ...snapshot.recent];
        const found = all.find((i) => i?.queueId === queueId) ?? null;
        setItem(found);
        if (found && TERMINAL_QUEUE_STATUSES.has(found.status)) return;
      } catch {
        // Network blip — keep the last known status, retry.
      }
      if (!cancelled) timer = setTimeout(() => void poll(), 1500);
    }
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [queueId]);

  return item;
}

/** Compact status line for a polled queue item — queued/running/done/failed. */
function ReorganizeQueueStatusLine({ item }: { item: LibraryV2ReorganizeQueueItem | null }) {
  if (!item) return null;
  if (item.status === 'queued') {
    return <div className={styles.inlineLoading}>Waiting in the reorganize queue…</div>;
  }
  if (item.status === 'running') {
    const total = item.progressTotal || 0;
    const done = item.progressProcessed || 0;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    return (
      <div className={styles.inlineLoading}>
        Reorganizing{total > 0 ? ` (${done}/${total} · ${pct}%)` : '…'}
        {item.currentTrack ? ` — ${item.currentTrack}` : ''}
      </div>
    );
  }
  if (item.status === 'done') {
    return (
      <div className={`${styles.grabBanner} ${styles.grab_ok}`}>
        Reorganize finished{item.resultStatus ? ` (${item.resultStatus})` : ''}.
      </div>
    );
  }
  if (item.status === 'cancelled') {
    return <div className={styles.searchError}>Reorganize cancelled.</div>;
  }
  return (
    <div className={styles.searchError}>
      Reorganize failed{item.resultStatus ? ` (${item.resultStatus})` : ''}.
    </div>
  );
}

function SourceModeFields({
  idPrefix,
  source,
  mode,
  sources,
  busy,
  onSourceChange,
  onModeChange,
}: {
  idPrefix: string;
  source: string | null;
  mode: 'api' | 'tags';
  sources: { source: string; label: string }[];
  busy: boolean;
  onSourceChange: (value: string | null) => void;
  onModeChange: (value: 'api' | 'tags') => void;
}) {
  return (
    <>
      <div className={styles.editRow}>
        <label htmlFor={`${idPrefix}-source`}>Metadata source</label>
        <select
          id={`${idPrefix}-source`}
          className={styles.select}
          value={source ?? ''}
          disabled={busy}
          onChange={(e) => onSourceChange(e.target.value || null)}
        >
          <option value="">Auto (configured primary + fallback)</option>
          {sources.map((s) => (
            <option key={s.source} value={s.source}>
              {s.label}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.editRow}>
        <label htmlFor={`${idPrefix}-mode`}>Metadata mode</label>
        <select
          id={`${idPrefix}-mode`}
          className={styles.select}
          value={mode}
          disabled={busy}
          onChange={(e) => onModeChange(e.target.value as 'api' | 'tags')}
        >
          <option value="api">Query metadata source (API)</option>
          <option value="tags">Read embedded file tags</option>
        </select>
      </div>
    </>
  );
}

/** Legacy per-album reorganize parity (docs §50): live preview of
 *  current-vs-proposed file paths, a source/mode picker, then apply — enqueued
 *  onto the same reorganize queue the legacy Enhanced View uses. */
export function AlbumReorganizeModal({
  albumId,
  albumTitle,
  onClose,
}: {
  albumId: number;
  albumTitle: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [source, setSource] = useState<string | null>(null);
  const [mode, setMode] = useState<'api' | 'tags'>('api');
  const [renameOnly, setRenameOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notQueuedReason, setNotQueuedReason] = useState<string | null>(null);
  const [queueId, setQueueId] = useState<string | null>(null);
  const queueItem = useReorganizeQueueItem(queueId);

  const sourcesQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'reorganize-sources', albumId],
    queryFn: () => fetchLibraryV2AlbumReorganizeSources(albumId),
  });
  const previewQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'reorganize-preview', albumId, source, mode],
    queryFn: () => previewLibraryV2AlbumReorganize(albumId, { source, mode }),
  });

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      const result = await applyLibraryV2AlbumReorganize(albumId, { source, mode, renameOnly });
      if (result.queueId) {
        setQueueId(result.queueId);
      } else {
        setNotQueuedReason(
          result.reason === 'already_queued'
            ? 'already queued'
            : (result.reason ?? 'unknown reason'),
        );
      }
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Reorganize failed');
    } finally {
      setBusy(false);
    }
  }

  const applied = queueId !== null || notQueuedReason !== null;
  const tracks = previewQuery.data?.tracks ?? [];
  const moving = tracks.filter((t) => t.matched && !t.unchanged);

  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div
        className={`${styles.modal} ${styles.modalWide}`}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3>Reorganize — {albumTitle}</h3>
          <button type="button" className={styles.iconAction} title="Close" onClick={onClose}>
            ✕
          </button>
        </div>

        <SourceModeFields
          idPrefix="lib2-reorganize"
          source={source}
          mode={mode}
          sources={sourcesQuery.data ?? []}
          busy={busy}
          onSourceChange={setSource}
          onModeChange={setMode}
        />
        <div className={styles.editRow}>
          <label htmlFor="lib2-reorganize-rename-only">
            <input
              id="lib2-reorganize-rename-only"
              type="checkbox"
              checked={renameOnly}
              disabled={busy}
              onChange={(e) => setRenameOnly(e.target.checked)}
            />{' '}
            Rename only (skip re-tag/quality checks)
          </label>
        </div>

        {notQueuedReason ? (
          <div className={styles.searchError}>Not queued ({notQueuedReason}).</div>
        ) : null}
        {queueId ? <ReorganizeQueueStatusLine item={queueItem} /> : null}
        {error ? <div className={styles.searchError}>{error}</div> : null}

        <div className={styles.resultsWrap}>
          {previewQuery.isLoading ? (
            <div className={styles.inlineLoading}>Computing preview…</div>
          ) : previewQuery.isError ? (
            <div className={styles.searchError}>
              {previewQuery.error instanceof Error ? previewQuery.error.message : 'Preview failed'}
            </div>
          ) : tracks.length === 0 ? (
            <div className={styles.inlineLoading}>No tracks found.</div>
          ) : (
            <table className={styles.trackTable}>
              <thead>
                <tr>
                  <th className={styles.colNum}>#</th>
                  <th>Title</th>
                  <th>Current path</th>
                  <th>New path</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {tracks.map((t, i) => (
                  <tr key={t.track_id ?? i}>
                    <td className={styles.colNum}>{t.track_number ?? '—'}</td>
                    <td>{t.title || '—'}</td>
                    <td title={t.current_path ?? undefined}>{t.current_path || '—'}</td>
                    <td title={t.new_path ?? undefined}>{t.new_path || '—'}</td>
                    <td className={styles.qualityText}>
                      {t.unchanged ? (
                        <span className={styles.statusOk}>unchanged</span>
                      ) : t.matched ? (
                        <span className={styles.statusWarn}>will move</span>
                      ) : (
                        <span className={styles.statusWarn}>{t.reason ?? 'not matched'}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className={styles.modalActions}>
          <span className={styles.muted}>
            {moving.length} of {tracks.length} track(s) will move
          </span>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            {applied ? 'Close' : 'Cancel'}
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={busy || tracks.length === 0 || applied}
            onClick={() => void apply()}
          >
            {busy ? 'Queueing…' : `Reorganize (${moving.length})`}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Poll the reorganize queue for this artist's still-pending items (deep-dive
 *  G7). The bulk enqueue endpoint only returns aggregate counts, not
 *  per-album queue ids, so this matches by `artistName` — a best-effort,
 *  read-only progress indicator, not an action target. Stops polling once
 *  nothing of this artist's is queued or running. */
function useArtistReorganizeQueueProgress(
  artistName: string,
  watch: boolean,
): { queued: number; running: boolean } | null {
  const [progress, setProgress] = useState<{ queued: number; running: boolean } | null>(null);

  useEffect(() => {
    if (!watch) {
      setProgress(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        const snapshot = await fetchLibraryV2ReorganizeQueueSnapshot();
        if (cancelled) return;
        const running = snapshot.active?.artistName === artistName;
        const queued = snapshot.queued.filter((i) => i.artistName === artistName).length;
        setProgress({ queued, running });
        if (!running && queued === 0) return;
      } catch {
        // Network blip — keep the last known status, retry.
      }
      if (!cancelled) timer = setTimeout(() => void poll(), 1500);
    }
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [artistName, watch]);

  return progress;
}

/** Legacy "Reorganize All" parity (docs §50): same source/mode pick applied
 *  to every album of the artist, each enqueued individually — no per-album
 *  preview at this scope (use the per-album action for that). */
export function ArtistReorganizeAllModal({
  artistId,
  artistName,
  onClose,
}: {
  artistId: number;
  artistName: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [source, setSource] = useState<string | null>(null);
  const [mode, setMode] = useState<'api' | 'tags'>('api');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [watching, setWatching] = useState(false);
  const progress = useArtistReorganizeQueueProgress(artistName, watching);

  const sourcesQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'reorganize-sources-global'],
    queryFn: fetchLibraryV2ReorganizeSourcesGlobal,
  });

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      const r = await applyLibraryV2ArtistReorganizeAll(artistId, { source, mode });
      setResult(
        `${r.enqueued} of ${r.totalAlbums} album(s) queued` +
          (r.alreadyQueued ? ` (${r.alreadyQueued} already queued)` : '') +
          '.',
      );
      if (r.enqueued > 0) setWatching(true);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Reorganize failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3>Reorganize All — {artistName}</h3>
          <button type="button" className={styles.iconAction} title="Close" onClick={onClose}>
            ✕
          </button>
        </div>
        <p className={styles.subtitle}>
          Applies to every album of this artist — each is queued individually. Use the per-album
          Reorganize action first if you want a preview.
        </p>

        <SourceModeFields
          idPrefix="lib2-reorganize-all"
          source={source}
          mode={mode}
          sources={sourcesQuery.data ?? []}
          busy={busy}
          onSourceChange={setSource}
          onModeChange={setMode}
        />

        {result ? <div className={`${styles.grabBanner} ${styles.grab_ok}`}>{result}</div> : null}
        {watching ? (
          progress && progress.queued === 0 && !progress.running ? (
            <div className={`${styles.grabBanner} ${styles.grab_ok}`}>
              All queued albums for this artist have finished.
            </div>
          ) : (
            <div className={styles.inlineLoading}>
              {progress
                ? `${progress.running ? 'Reorganizing now' : 'Waiting in queue'} — ${progress.queued} more queued for this artist…`
                : 'Checking queue…'}
            </div>
          )
        ) : null}
        {error ? <div className={styles.searchError}>{error}</div> : null}

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            {result ? 'Close' : 'Cancel'}
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={busy || Boolean(result)}
            onClick={() => void apply()}
          >
            {busy ? 'Queueing…' : 'Reorganize All Albums'}
          </button>
        </div>
      </div>
    </div>
  );
}
