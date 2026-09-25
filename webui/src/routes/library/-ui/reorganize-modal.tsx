import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type ReactNode } from 'react';

import type {
  LibraryV2ReorganizeQueueItem,
  LibraryV2ReorganizeTrackPreview,
} from '../-library-v2.types';

import {
  applyLibraryV2AlbumReorganize,
  applyLibraryV2ArtistReorganizeAll,
  fetchLibraryV2ReorganizeQueueSnapshot,
  LIBRARY_V2_QUERY_KEY,
  previewLibraryV2AlbumReorganize,
} from '../-library-v2.api';
import { FilePathCellBody } from './file-path-cell';
import styles from './library-v2-page.module.css';
import { LibraryToolDialog } from './tool-dialog';

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
    return <div className={styles.inlineLoading}>Waiting in the rename / organize queue…</div>;
  }
  if (item.status === 'running') {
    const total = item.progressTotal || 0;
    const done = item.progressProcessed || 0;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    return (
      <div className={styles.inlineLoading}>
        Renaming / organizing{total > 0 ? ` (${done}/${total} · ${pct}%)` : '…'}
        {item.currentTrack ? ` — ${item.currentTrack}` : ''}
      </div>
    );
  }
  if (item.status === 'done') {
    return (
      <div className={`${styles.grabBanner} ${styles.grab_ok}`}>
        Rename / Organize finished{item.resultStatus ? ` (${item.resultStatus})` : ''}.
      </div>
    );
  }
  if (item.status === 'cancelled') {
    return <div className={styles.searchError}>Rename / Organize cancelled.</div>;
  }
  return (
    <div className={styles.searchError}>
      Rename / Organize failed{item.resultStatus ? ` (${item.resultStatus})` : ''}.
    </div>
  );
}

/** Legacy per-album reorganize parity (docs §50): live preview of
 *  current-vs-proposed file paths, then apply — enqueued
 *  onto the same reorganize queue the legacy Enhanced View uses. */
export function AlbumReorganizeModal({
  albumId,
  albumTitle,
  onClose,
  releasePicker,
  bulkAction,
  allReleases,
}: {
  albumId: number;
  albumTitle: string;
  onClose: () => void;
  releasePicker?: ReactNode;
  bulkAction?: ReactNode;
  allReleases?: {
    artistId: number;
    artistName: string;
    albums: Array<{ id: number; title: string }>;
  };
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notQueuedReason, setNotQueuedReason] = useState<string | null>(null);
  const [queueId, setQueueId] = useState<string | null>(null);
  const [showUnchanged, setShowUnchanged] = useState(false);
  const queueItem = useReorganizeQueueItem(queueId);
  const [bulkResult, setBulkResult] = useState<string | null>(null);
  const [watchingBulk, setWatchingBulk] = useState(false);
  const bulkProgress = useArtistReorganizeQueueProgress(
    allReleases?.artistName ?? '',
    watchingBulk,
  );

  // The plan is computed from the catalogue, so it does not vary by source or
  // mode and the query key has nothing else to depend on.
  const previewQuery = useQuery({
    queryKey: allReleases
      ? [
          ...LIBRARY_V2_QUERY_KEY,
          'reorganize-preview-all',
          allReleases.artistId,
          allReleases.albums.map((a) => a.id),
        ]
      : [...LIBRARY_V2_QUERY_KEY, 'reorganize-preview', albumId],
    queryFn: async () => {
      if (!allReleases) return previewLibraryV2AlbumReorganize(albumId, {});
      const tracks: Array<LibraryV2ReorganizeTrackPreview & { release_title?: string }> = [];
      // Bound file-reading work while loading every release in the selected scope.
      for (let offset = 0; offset < allReleases.albums.length; offset += 4) {
        const batch = await Promise.all(
          allReleases.albums.slice(offset, offset + 4).map(async (album) => {
            const preview = await queryClient.fetchQuery({
              queryKey: [...LIBRARY_V2_QUERY_KEY, 'reorganize-preview', album.id],
              queryFn: () => previewLibraryV2AlbumReorganize(album.id, {}),
            });
            return preview.tracks.map((track) => ({ ...track, release_title: album.title }));
          }),
        );
        tracks.push(...batch.flat());
      }
      return { tracks };
    },
  });

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      if (allReleases) {
        const result = await applyLibraryV2ArtistReorganizeAll(allReleases.artistId, {});
        setBulkResult(
          `${result.enqueued} releases queued${result.alreadyQueued ? ` · ${result.alreadyQueued} already queued` : ''}.`,
        );
        setWatchingBulk(result.enqueued > 0);
        await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
        return;
      }
      const result = await applyLibraryV2AlbumReorganize(albumId, {});
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
      setError(caught instanceof Error ? caught.message : 'Rename / Organize failed');
    } finally {
      setBusy(false);
    }
  }

  const applied = queueId !== null || notQueuedReason !== null || bulkResult !== null;
  const tracks: Array<LibraryV2ReorganizeTrackPreview & { release_title?: string }> =
    previewQuery.data?.tracks ?? [];
  const moving = tracks.filter(
    (t) => t.matched && !t.unchanged && !t.collision && Boolean(t.new_path_abs || t.new_path),
  );
  const unchangedCount = tracks.filter((track) => track.unchanged).length;
  const blockedCount = tracks.length - moving.length - unchangedCount;
  const visibleTracks = tracks.filter((t) => showUnchanged || !t.unchanged || moving.length === 0);

  return (
    <LibraryToolDialog
      title={`Preview Rename / Organize — ${albumTitle}`}
      description="File and folder names follow your organization template. Tags are left alone."
      onClose={onClose}
      footer={
        <div className={styles.modalActions}>
          {bulkAction}
          <span className={styles.modalActionsText}>
            {moving.length} ready · {unchangedCount} unchanged
            {blockedCount ? ` · ${blockedCount} skipped (see status)` : ''}
          </span>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            {applied ? 'Close' : 'Cancel'}
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={busy || moving.length === 0 || previewQuery.isError || applied}
            onClick={() => void apply()}
          >
            {busy ? 'Queueing…' : `Rename / Organize (${moving.length})`}
          </button>
        </div>
      }
    >
      <div className={styles.previewToolbar}>
        {releasePicker || (
          <span>
            {previewQuery.isLoading
              ? 'Computing paths…'
              : moving.length
                ? `${moving.length} files will move`
                : blockedCount
                  ? 'No files ready — review the status column'
                  : 'No path changes needed'}
          </span>
        )}
        <label className={styles.checkOption}>
          <input
            type="checkbox"
            checked={showUnchanged}
            onChange={(e) => setShowUnchanged(e.target.checked)}
          />
          Show unchanged
        </label>
      </div>

      {notQueuedReason ? (
        <div className={styles.searchError}>Not queued ({notQueuedReason}).</div>
      ) : null}
      {queueId ? <ReorganizeQueueStatusLine item={queueItem} /> : null}
      {bulkResult ? <div className={styles.grabBanner}>{bulkResult}</div> : null}
      {watchingBulk && bulkProgress ? (
        <div className={styles.previewQuiet}>
          {bulkProgress.running || bulkProgress.queued
            ? `${bulkProgress.running ? 'Renaming / organizing' : 'Waiting'} · ${bulkProgress.queued} queued`
            : 'No queued or running releases remain for this artist.'}
        </div>
      ) : null}
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
          <table className={`${styles.trackTable} ${styles.reorganizeTable}`}>
            <thead>
              <tr>
                <th className={styles.colNum}>#</th>
                <th>Title</th>
                <th>Current path</th>
                <th>After rename / organize</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {visibleTracks.map((t, i) => (
                <tr key={t.track_id ?? i}>
                  <td className={styles.colNum}>{t.track_number ?? '—'}</td>
                  <td>
                    {t.title || '—'}
                    {t.release_title ? (
                      <small className={styles.renameReleaseTitle}>{t.release_title}</small>
                    ) : null}
                  </td>
                  <td className={styles.pathComparison}>
                    <FilePathCellBody
                      path={t.current_path_abs || t.current_path}
                      display={t.current_path}
                    />
                  </td>
                  <td className={styles.pathComparison}>
                    <FilePathCellBody path={t.new_path_abs || t.new_path} display={t.new_path} />
                  </td>
                  <td className={styles.qualityText}>
                    {t.collision ? (
                      <span className={styles.statusWarn}>Path conflict</span>
                    ) : t.unchanged ? (
                      <span className={styles.statusOk}>unchanged</span>
                    ) : t.matched && (t.new_path_abs || t.new_path) ? (
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
    </LibraryToolDialog>
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

/** Explicit bulk scope. Each release can be previewed before queueing all. */
export function ArtistReorganizeAllModal({
  artistId,
  artistName,
  onClose,
  onBack,
  albums = [],
}: {
  artistId: number;
  albums?: Array<{ id: number; title: string; tracks_present: number }>;
  artistName: string;
  /** Dismiss the tool entirely. Always what the dialog's ✕ does. */
  onClose: () => void;
  /** Return to the per-release preview this was opened from, when there is
   *  one. Without it the footer's Cancel is the only exit and closes. */
  onBack?: () => void;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [watching, setWatching] = useState(false);
  const [previewAlbum, setPreviewAlbum] = useState<{ id: number; title: string } | null>(null);
  const releasesWithFiles = albums.filter((a) => a.tracks_present > 0);
  const progress = useArtistReorganizeQueueProgress(artistName, watching);

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      const r = await applyLibraryV2ArtistReorganizeAll(artistId, {});
      setResult(
        `${r.enqueued} of ${r.totalAlbums} album(s) queued` +
          (r.alreadyQueued ? ` (${r.alreadyQueued} already queued)` : '') +
          '.',
      );
      if (r.enqueued > 0) setWatching(true);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Rename / Organize failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <LibraryToolDialog
      title={`Rename / Organize All — ${artistName}`}
      description="Review individual releases, then queue the artist’s full library."
      onClose={onClose}
      footer={
        <div className={styles.modalActions}>
          {/* Once the releases are queued there is nothing to go back for, so
              the button stops being a step backwards and dismisses the tool —
              previously it always returned to the per-release preview, which
              left "Close" unable to close anything. */}
          <button
            type="button"
            className={styles.btnGhost}
            onClick={result || !onBack ? onClose : onBack}
          >
            {result ? 'Close' : onBack ? 'Back' : 'Cancel'}
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={busy || Boolean(result)}
            onClick={() => void apply()}
          >
            {busy ? 'Queueing…' : 'Rename / Organize All Releases'}
          </button>
        </div>
      }
    >
      <p className={styles.subtitle}>
        Each release is queued individually. Preview a release below to review its proposed paths
        before starting the full operation.
      </p>

      {releasesWithFiles.length ? (
        <div className={styles.reorganizeReleaseList}>
          {releasesWithFiles.map((album) => (
            <div key={album.id} className={styles.reorganizeRelease}>
              <div>
                <strong>{album.title}</strong>
                <span className={styles.previewQuiet}>
                  {album.tracks_present} tracks with files
                </span>
              </div>
              <button
                type="button"
                className={styles.btnGhost}
                onClick={() => setPreviewAlbum(album)}
              >
                Preview paths
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className={styles.previewQuiet}>No release previews available in this view.</p>
      )}

      {result ? <div className={`${styles.grabBanner} ${styles.grab_ok}`}>{result}</div> : null}
      {watching ? (
        progress && progress.queued === 0 && !progress.running ? (
          <div className={`${styles.grabBanner} ${styles.grab_ok}`}>
            No queued or running releases remain for this artist.
          </div>
        ) : (
          <div className={styles.inlineLoading}>
            {progress
              ? `${progress.running ? 'Renaming / organizing now' : 'Waiting in queue'} — ${progress.queued} more queued for this artist…`
              : 'Checking queue…'}
          </div>
        )
      ) : null}
      {error ? <div className={styles.searchError}>{error}</div> : null}

      {previewAlbum ? (
        <AlbumReorganizeModal
          albumId={previewAlbum.id}
          albumTitle={previewAlbum.title}
          onClose={() => setPreviewAlbum(null)}
        />
      ) : null}
    </LibraryToolDialog>
  );
}

/** Artist entry point opens an actual path preview; switching releases resets
 * only that release's queue state. Bulk actions retain their explicit scope. */
export function ArtistRenamePreviewModal({
  artistId,
  artistName,
  albums,
  onClose,
}: {
  artistId: number;
  artistName: string;
  albums: Array<{ id: number; title: string; tracks_present: number }>;
  onClose: () => void;
}) {
  const releases = albums.filter((album) => album.tracks_present > 0);
  const [selectedId, setSelectedId] = useState('all');
  const selected = releases.find((album) => String(album.id) === selectedId);
  if (!releases.length)
    return (
      <LibraryToolDialog title={`Preview Rename / Organize — ${artistName}`} onClose={onClose}>
        <div className={styles.inlineLoading}>No releases with files to rename or organize.</div>
      </LibraryToolDialog>
    );
  return (
    <AlbumReorganizeModal
      key={selectedId}
      albumId={selected?.id ?? releases[0]!.id}
      albumTitle={selected?.title ?? `${artistName} · All releases`}
      allReleases={selected ? undefined : { artistId, artistName, albums: releases }}
      onClose={onClose}
      releasePicker={
        <label className={styles.checkOption}>
          Scope
          <select
            className={styles.select}
            aria-label="Preview release"
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
          >
            <option value="all">All releases · {releases.length} releases</option>
            {releases.map((album) => (
              <option key={album.id} value={album.id}>
                {album.title} · {album.tracks_present}{' '}
                {album.tracks_present === 1 ? 'file' : 'files'}
              </option>
            ))}
          </select>
        </label>
      }
    />
  );
}
