import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate as useRouterNavigate } from '@tanstack/react-router';
import { type ReactNode, useRef, useState } from 'react';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type {
  LibraryV2AlbumDetail,
  LibraryV2AlbumSummary,
  LibraryV2ArtistDetail,
  LibraryV2ArtistSummary,
  LibraryV2PlaylistPipelineState,
  LibraryV2PlaylistSummary,
  LibraryV2PlaylistTrack,
  LibraryV2Track,
} from '../-library-v2.types';

import {
  autoGrabBest,
  bulkMonitorLibraryV2Releases,
  deleteLibraryV2Entity,
  deleteLibraryV2Files,
  editLibraryV2Artist,
  fetchLibraryV2ArtistDeletePreview,
  fetchLibraryV2ArtistHistory,
  fetchLibraryV2Duplicates,
  fetchLibraryV2FileDeletePreview,
  fetchLibraryV2ImportStatus,
  fetchLibraryV2JobStatus,
  LIBRARY_V2_ALBUM_TYPES,
  LIBRARY_V2_QUERY_KEY,
  libraryV2AlbumQueryOptions,
  libraryV2ArtistQueryOptions,
  libraryV2ArtistsQueryOptions,
  libraryV2EnabledQueryOptions,
  libraryV2MirrorStatusQueryOptions,
  libraryV2PlaylistQueryOptions,
  libraryV2PlaylistsQueryOptions,
  moveLibraryV2TrackFile,
  processWishlist,
  refreshLibraryV2,
  refreshLibraryV2Discography,
  retryLibraryV2Mirror,
  runRepairJob,
  runLibraryV2PlaylistPipeline,
  setLibraryV2Monitored,
  startLibraryV2Import,
  startLibraryV2UpgradeScan,
  unlinkLibraryV2Duplicate,
  updateLibraryV2MetadataOverrides,
  type Lib2EntityRef,
  type LibraryV2AlbumType,
} from '../-library-v2.api';
import { Route } from '../route';
import { InteractiveSearchModal } from './interactive-search';
import styles from './library-v2-page.module.css';
import { QualityProfileModal } from './quality-profile-modal';
import { RetagModal } from './retag-modal';

interface QpTarget {
  entity: 'artists' | 'albums';
  id: number;
  currentProfileId: number;
  title: string;
}

/** Row/toolbar action dispatch: the label drives the behaviour, the optional
 *  entity ref carries WHICH lib2 track/album the action is for so grabs keep
 *  their entity + quality-profile context (audit P1-16/P1-17). */
type ActionHandler = (action: string, entity?: Lib2EntityRef) => void;

function trackProgress(present: number, total: number): string {
  return `${present}/${total}`;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = units[0];
  for (let i = 1; i < units.length && value >= 1024; i += 1) {
    value /= 1024;
    unit = units[i];
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`;
}

/** Only "Interactive Search" opens the manual results window. */
const INTERACTIVE_RE = /^Interactive Search\b/;
/** "Search Monitored" triggers wishlist processing (checked BEFORE the
 *  auto-grab route — its label also starts with "Search"). */
const SEARCH_MONITORED_RE = /^Search Monitored\b/;
/** Per-track "Search" / "Grab Release" auto-search + grab the best result. */
const AUTO_GRAB_RE = /^(Search|Grab Release)\b/;

/** Build a source-search query from an artist name + an action label like
 *  "Interactive Search: Title (Album)". */
function buildSearchQuery(artistName: string, action: string): string {
  const idx = action.indexOf(': ');
  if (idx === -1) return artistName; // artist-level search
  const rest = action
    .slice(idx + 2)
    .replace(/\s*\([^)]*\)\s*$/, '') // drop trailing "(album)" context
    .replace(/\s*-\s*missing\s*$/i, '')
    .trim();
  return `${artistName} ${rest}`.trim();
}

/** Real quality string: format + bitrate (+ bit-depth/sample-rate when scanned). */
function qualityText(file: LibraryV2Track['file']): string {
  if (!file) return '-';
  const fmt = (file.format ?? '').toUpperCase();
  const kbps = file.bitrate
    ? file.bitrate > 5000
      ? Math.round(file.bitrate / 1000)
      : file.bitrate
    : null;
  return [fmt, kbps ? `${kbps} kbps` : null].filter(Boolean).join(' / ') || '-';
}

const BOOKMARK_PATH = 'M5 3.5A1.5 1.5 0 0 1 6.5 2h11A1.5 1.5 0 0 1 19 3.5V22l-7-4.2L5 22V3.5z';

const ICON_PATHS = {
  back: 'M15 18l-6-6 6-6M9 12h12',
  refresh: 'M21 12a9 9 0 0 1-15.3 6.4M3 12A9 9 0 0 1 18.3 5.6M18 3v5h-5M6 21v-5h5',
  search: 'M11 19a8 8 0 1 1 5.7-2.3L21 21',
  interactive: 'M4 5h16M4 12h10M4 19h7M17 14l4 4-4 4',
  organize: 'M4 7h16M7 7v12M17 7v12M4 19h16',
  retag: 'M20 10l-8.5 8.5a2 2 0 0 1-2.8 0L4 13.8V4h9.8L20 10zM8 8h.01',
  tracks: 'M9 18V5l10-2v13M9 18a3 3 0 1 1-2-2.8M19 16a3 3 0 1 1-2-2.8',
  history: 'M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 7v5l3 2',
  import: 'M12 3v12M8 11l4 4 4-4M4 21h16',
  monitor: BOOKMARK_PATH,
  edit: 'M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4zM13 7l4 4',
  delete: 'M4 7h16M9 7V4h6v3M8 7l1 13h6l1-13',
  expand: 'M8 3H3v5M16 3h5v5M8 21H3v-5M21 16v5h-5',
  collapse: 'M9 3v6H3M15 3v6h6M9 21v-6H3M15 21v-6h6',
  download: 'M12 3v12M8 11l4 4 4-4M5 21h14',
  profile: 'M5 6h14M5 12h14M5 18h9',
  folder: 'M3 6h7l2 2h9v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z',
  close: 'M6 6l12 12M18 6L6 18',
} as const;

type IconName = keyof typeof ICON_PATHS;

function SvgIcon({ name, filled }: { name: IconName; filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d={ICON_PATHS[name]}
        fill={filled ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function detailedQualityText(file: LibraryV2Track['file']): string {
  if (!file) return qualityText(file);
  const fmt = (file.format ?? '').toUpperCase();
  const kbps = file.bitrate
    ? file.bitrate > 5000
      ? Math.round(file.bitrate / 1000)
      : file.bitrate
    : null;
  const bitDepth = file.bit_depth ? `${file.bit_depth}-bit` : null;
  const sampleRate = file.sample_rate
    ? `${Number((file.sample_rate / 1000).toFixed(file.sample_rate % 1000 === 0 ? 0 : 1))} kHz`
    : null;
  const resolution = [bitDepth, sampleRate].filter(Boolean).join('/');
  return (
    [fmt, resolution || null, kbps ? `${kbps} kbps` : null].filter(Boolean).join(' / ') ||
    qualityText(file)
  );
}

// --- shared building blocks --------------------------------------------------

function useNavigate() {
  return useRouterNavigate({ from: Route.fullPath });
}

/** Cover/poster image with a graceful placeholder when no artwork resolves.
 *  ``thumb`` requests the small resized variant for fast list rendering. */
function Artwork({
  src,
  alt,
  className,
  thumb,
}: {
  src: string;
  alt: string;
  className: string;
  thumb?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const url = src ? (thumb ? `${src}?size=thumb` : src) : '';
  if (!url || failed) {
    return (
      <div className={`${className} ${styles.artPlaceholder}`} aria-label={alt}>
        ♪
      </div>
    );
  }
  return (
    <img className={className} src={url} alt={alt} loading="lazy" onError={() => setFailed(true)} />
  );
}

function useMonitorMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { entity: 'artists' | 'albums' | 'tracks'; id: number; monitored: boolean }) =>
      setLibraryV2Monitored(v.entity, v.id, v.monitored),
    onSettled: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
  });
}

function mutationErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message : fallback;
}

/** Lidarr-style monitor toggle (filled bookmark = monitored). */
export function MonitorToggle({
  entity,
  id,
  monitored,
}: {
  entity: 'artists' | 'albums' | 'tracks';
  id: number;
  monitored: boolean;
}) {
  const mutation = useMonitorMutation();
  const nextMonitored = !monitored;
  return (
    <span className={styles.monitorControl} onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className={`${styles.monitorBtn} ${monitored ? styles.monitorOn : ''}`}
        aria-label={
          mutation.isPending
            ? 'Updating monitoring'
            : monitored
              ? 'Stop monitoring'
              : 'Start monitoring'
        }
        title={
          mutation.isError
            ? 'Monitoring update failed — click to retry'
            : monitored
              ? 'Monitored — click to stop'
              : 'Not monitored — click to monitor'
        }
        disabled={mutation.isPending}
        onClick={() => mutation.mutate({ entity, id, monitored: nextMonitored })}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d={BOOKMARK_PATH} strokeLinejoin="round" />
        </svg>
      </button>
      {mutation.isError ? (
        <span
          className={styles.monitorError}
          role="alert"
          title={mutationErrorMessage(mutation.error, 'Monitoring update failed')}
        >
          Update failed — click bookmark to retry
        </span>
      ) : null}
    </span>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  title,
  busy,
  disabled,
  tone = 'default',
}: {
  icon: IconName;
  label: ReactNode;
  onClick: () => void;
  title?: string;
  busy?: boolean;
  disabled?: boolean;
  tone?: 'default' | 'danger';
}) {
  return (
    <button
      type="button"
      className={`${styles.toolButton} ${tone === 'danger' ? styles.toolDanger : ''}`}
      disabled={busy || disabled}
      title={title}
      onClick={onClick}
    >
      <SvgIcon name={busy ? 'refresh' : icon} />
      <span>{label}</span>
    </button>
  );
}

function IconActionButton({
  icon,
  title,
  onClick,
  disabled,
  tone = 'default',
}: {
  icon: IconName;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'default' | 'danger';
}) {
  return (
    <button
      type="button"
      className={`${styles.iconAction} ${tone === 'danger' ? styles.toolDanger : ''}`}
      title={title}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <SvgIcon name={icon} />
    </button>
  );
}

function ModalShell({
  title,
  wide,
  onClose,
  children,
}: {
  title: string;
  wide?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div
        className={`${styles.modal} ${wide ? styles.modalWide : ''}`}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3>{title}</h3>
          <IconActionButton icon="close" title="Close" onClick={onClose} />
        </div>
        {children}
      </div>
    </div>
  );
}

/** Lidarr-style artist monitoring options: one click applies a monitoring
 *  strategy across the artist's releases (runs as a background bulk job). */
export function MonitoringModal({
  artistId,
  monitorNewItems,
  onClose,
}: {
  artistId: number;
  monitorNewItems: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [failedBulkAction, setFailedBulkAction] = useState<{
    scope: 'all' | 'missing';
    monitored: boolean;
    label: string;
  } | null>(null);
  const initialNewItems =
    monitorNewItems === 'none' || monitorNewItems === 'new' ? monitorNewItems : 'all';
  const [newItems, setNewItems] = useState<'all' | 'none' | 'new'>(initialNewItems);
  const futureReleasesMutation = useMutation({
    mutationFn: (value: 'all' | 'none' | 'new') => editLibraryV2Artist(artistId, value),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
  });

  function saveFutureReleases(value: 'all' | 'none' | 'new') {
    setNewItems(value);
    futureReleasesMutation.mutate(value);
  }

  async function apply(scope: 'all' | 'missing', monitored: boolean, label: string) {
    setBusy(label);
    setBulkError(null);
    setFailedBulkAction(null);
    try {
      const jobId = await bulkMonitorLibraryV2Releases(artistId, scope, monitored);
      const jobError = await awaitBulkJob(queryClient, jobId);
      if (jobError) throw new Error(jobError);
      onClose();
    } catch (caught) {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      setBulkError(mutationErrorMessage(caught, 'Bulk monitoring failed'));
      setFailedBulkAction({ scope, monitored, label });
      setBusy(null);
    }
  }

  const options: Array<{ label: string; desc: string; run: () => void }> = [
    {
      label: 'Monitor all releases',
      desc: 'Every album, EP and single becomes wanted (missing tracks queue for download).',
      run: () => void apply('all', true, 'all'),
    },
    {
      label: 'Monitor missing only',
      desc: 'Only releases with missing tracks become wanted; complete ones stay untouched.',
      run: () => void apply('missing', true, 'missing'),
    },
    {
      label: 'Unmonitor everything',
      desc: 'Stop wanting all releases; wishlist entries are withdrawn.',
      run: () => void apply('all', false, 'none'),
    },
  ];

  return (
    <ModalShell title="Artist Monitoring" onClose={onClose}>
      <div className={styles.qpList}>
        {options.map((o) => (
          <button
            key={o.label}
            type="button"
            className={styles.qpOption}
            disabled={busy !== null}
            onClick={o.run}
          >
            <span className={styles.qpName}>{busy === o.label ? 'Applying…' : o.label}</span>
            <span className={styles.qpDesc}>{o.desc}</span>
          </button>
        ))}
      </div>
      {bulkError && failedBulkAction ? (
        <div className={styles.mutationError} role="alert">
          <span>{bulkError}</span>
          <button
            type="button"
            className={styles.inlineRetry}
            onClick={() =>
              void apply(failedBulkAction.scope, failedBulkAction.monitored, failedBulkAction.label)
            }
          >
            Retry
          </button>
        </div>
      ) : null}
      <div className={styles.editRow}>
        <label htmlFor="lib2-monitor-new">Future releases</label>
        <select
          id="lib2-monitor-new"
          className={styles.select}
          value={newItems}
          disabled={futureReleasesMutation.isPending}
          onChange={(e) => {
            const value = e.target.value as 'all' | 'none' | 'new';
            saveFutureReleases(value);
          }}
        >
          <option value="all">Monitor new releases</option>
          <option value="new">Monitor new releases (from now on)</option>
          <option value="none">Don't monitor new releases</option>
        </select>
      </div>
      {futureReleasesMutation.isPending ? (
        <div className={styles.mutationFeedback} role="status">
          Saving future-release monitoring…
        </div>
      ) : futureReleasesMutation.isError ? (
        <div className={styles.mutationError} role="alert">
          <span>
            {mutationErrorMessage(
              futureReleasesMutation.error,
              'Future-release monitoring could not be saved',
            )}
          </span>
          <button
            type="button"
            className={styles.inlineRetry}
            onClick={() => futureReleasesMutation.mutate(newItems)}
          >
            Retry
          </button>
        </div>
      ) : futureReleasesMutation.isSuccess ? (
        <div className={styles.mutationSuccess} role="status">
          Future-release monitoring saved.
        </div>
      ) : null}
    </ModalShell>
  );
}

/** Recent downloads for this artist, from the pipeline's provenance records. */
function HistoryModal({ artistId, onClose }: { artistId: number; onClose: () => void }) {
  const historyQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'history', artistId],
    queryFn: () => fetchLibraryV2ArtistHistory(artistId),
  });
  const rows = historyQuery.data ?? [];
  return (
    <ModalShell title="History" wide onClose={onClose}>
      <div className={styles.resultsWrap}>
        {historyQuery.isLoading ? (
          <div className={styles.inlineLoading}>Loading history…</div>
        ) : rows.length === 0 ? (
          <div className={styles.inlineLoading}>No recorded downloads for this artist yet.</div>
        ) : (
          <table className={styles.trackTable}>
            <thead>
              <tr>
                <th>Date</th>
                <th>Title</th>
                <th>Album</th>
                <th>Source</th>
                <th>Quality</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((h, i) => (
                <tr key={i}>
                  <td className={styles.muted}>
                    {h.date ? h.date.slice(0, 16).replace('T', ' ') : '—'}
                  </td>
                  <td title={h.file_path ?? undefined}>{h.title ?? '—'}</td>
                  <td>{h.album ?? '—'}</td>
                  <td>
                    <span className={styles.sourceBadge}>{h.source ?? '—'}</span>
                  </td>
                  <td className={styles.qualityText}>
                    {[
                      h.quality,
                      h.bit_depth ? `${h.bit_depth}-bit` : null,
                      h.sample_rate ? `${Math.round(h.sample_rate / 100) / 10} kHz` : null,
                    ]
                      .filter(Boolean)
                      .join(' / ') || '—'}
                  </td>
                  <td>{h.status ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </ModalShell>
  );
}

/** Keep catalog removal and ADR-05 physical deletion visibly separate. */
function DeleteConfirmModal({
  entity,
  id,
  title,
  onDone,
  onClose,
}: {
  entity: 'artists' | 'albums';
  id: number;
  title: string;
  onDone: () => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileBusy, setFileBusy] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [fileResult, setFileResult] = useState<string | null>(null);
  const [confirmFileDelete, setConfirmFileDelete] = useState(false);
  // Show the real blast radius before the user commits: only releases the
  // artist OWNS are removed; featured appearances on other artists' releases
  // survive (they are merely detached).
  const preview = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'delete-preview', entity, id],
    queryFn: () => fetchLibraryV2ArtistDeletePreview(id),
    enabled: entity === 'artists',
  });
  const filePreview = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'file-delete-preview', entity, id],
    queryFn: () => fetchLibraryV2FileDeletePreview(entity, id),
  });
  const physical = filePreview.data;
  const physicalReady = Boolean(physical && physical.file_count > 0 && physical.unsafe_count === 0);
  return (
    <ModalShell title={`Delete ${entity === 'artists' ? 'Artist' : 'Album'}`} onClose={onClose}>
      <p>
        Remove <strong>{title}</strong> from the library? Monitoring stops and wishlist entries are
        withdrawn. <strong>Files on disk are not deleted.</strong>
      </p>
      {entity === 'artists' && preview.data ? (
        <p className={styles.muted}>
          Removes {preview.data.albums} release{preview.data.albums === 1 ? '' : 's'} /{' '}
          {preview.data.tracks} track{preview.data.tracks === 1 ? '' : 's'} owned by this artist.
          {preview.data.detached_albums > 0
            ? ` Appears on ${preview.data.detached_albums} other ${
                preview.data.detached_albums === 1 ? 'release' : 'releases'
              } — those stay in the library.`
            : ''}
        </p>
      ) : null}
      {error ? <div className={styles.searchError}>{error}</div> : null}
      <div className={styles.modalActions}>
        <button
          type="button"
          className={styles.btnGhost}
          disabled={busy || fileBusy}
          onClick={onClose}
        >
          Cancel
        </button>
        <button
          type="button"
          className={styles.btnDanger}
          disabled={busy || fileBusy}
          onClick={() => {
            setBusy(true);
            void deleteLibraryV2Entity(entity, id)
              .then(async () => {
                await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
                onDone();
              })
              .catch((e) => {
                setError(e instanceof Error ? e.message : 'Delete failed');
                setBusy(false);
              });
          }}
        >
          {busy ? 'Removing…' : 'Remove from library'}
        </button>
      </div>

      <section className={styles.fileDeletePanel}>
        <h4>Delete physical files</h4>
        <p>
          This is a separate, irreversible command. It keeps the artist, release, and tracks in
          Library v2 and marks their file records as deleted.
        </p>
        {filePreview.isLoading ? <p className={styles.muted}>Checking file roots…</p> : null}
        {filePreview.error ? (
          <div className={styles.searchError}>{filePreview.error.message}</div>
        ) : null}
        {physical ? (
          <>
            <p>
              {physical.file_count} physical file{physical.file_count === 1 ? '' : 's'} ·{' '}
              {formatFileSize(physical.total_size)}
              {physical.configured_roots.length > 0
                ? ` · ${physical.configured_roots.length} configured library root${
                    physical.configured_roots.length === 1 ? '' : 's'
                  }`
                : ' · no configured library root'}
            </p>
            {physical.files.length > 0 ? (
              <ul className={styles.fileDeleteList}>
                {physical.files.map((file) => (
                  <li key={file.path ?? file.file_ids.join('-')}>
                    <span>{file.path ?? file.stored_paths[0] ?? 'Unresolved file'}</span>
                    <small>
                      {file.deletable
                        ? `${formatFileSize(file.size ?? 0)} · ${file.root}`
                        : `Blocked: ${file.reason ?? 'unsafe path'}`}
                    </small>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={styles.muted}>No linked physical files remain.</p>
            )}
            {physical.unsafe_count > 0 ? (
              <div className={styles.searchError}>
                Physical deletion is blocked because {physical.unsafe_count} file
                {physical.unsafe_count === 1 ? ' is' : 's are'} unresolved or outside a configured
                library root.
              </div>
            ) : null}
          </>
        ) : null}
        {fileError ? <div className={styles.searchError}>{fileError}</div> : null}
        {fileResult ? <p>{fileResult}</p> : null}
        <label className={styles.fileDeleteConfirm}>
          <input
            type="checkbox"
            checked={confirmFileDelete}
            disabled={!physicalReady || fileBusy}
            onChange={(event) => setConfirmFileDelete(event.target.checked)}
          />
          I understand these files will be permanently deleted from disk.
        </label>
        <div className={styles.modalActions}>
          <button
            type="button"
            className={styles.btnDanger}
            disabled={!physicalReady || !confirmFileDelete || fileBusy || busy}
            onClick={() => {
              if (!physical) return;
              setFileBusy(true);
              setFileError(null);
              setFileResult(null);
              void deleteLibraryV2Files(entity, id, physical.preview_token)
                .then(async (operation) => {
                  const deleted = operation.items.filter(
                    (item) => item.status === 'deleted',
                  ).length;
                  const failed = operation.items.filter((item) => item.status === 'failed').length;
                  setFileResult(
                    failed > 0
                      ? `Deleted ${deleted} file${deleted === 1 ? '' : 's'}; ${failed} failed. Journal ${operation.id}.`
                      : `Deleted ${deleted} physical file${deleted === 1 ? '' : 's'}. Journal ${operation.id}.`,
                  );
                  setConfirmFileDelete(false);
                  await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
                  await filePreview.refetch();
                })
                .catch((e) => {
                  setFileError(e instanceof Error ? e.message : 'Physical file deletion failed');
                })
                .finally(() => setFileBusy(false));
            }}
          >
            {fileBusy
              ? 'Deleting files…'
              : `Permanently delete ${physical?.deletable_count ?? 0} file${
                  physical?.deletable_count === 1 ? '' : 's'
                }`}
          </button>
        </div>
      </section>
    </ModalShell>
  );
}

/** Correct effective release metadata without rewriting provider baselines. */
type EditableAlbumMetadata = Pick<
  LibraryV2AlbumSummary | LibraryV2AlbumDetail,
  'id' | 'title' | 'year' | 'album_type' | 'user_overrides'
>;

function EditAlbumModal({ album, onClose }: { album: EditableAlbumMetadata; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(album.title);
  const [year, setYear] = useState(album.year === null ? '' : String(album.year));
  const [albumType, setAlbumType] = useState<LibraryV2AlbumType>(
    (LIBRARY_V2_ALBUM_TYPES as readonly string[]).includes(album.album_type)
      ? (album.album_type as LibraryV2AlbumType)
      : 'album',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const normalizedTitle = title.trim();
  const normalizedYear = year.trim() === '' ? null : Number(year);
  const values: Record<string, unknown> = {};
  if (normalizedTitle !== album.title) values.title = normalizedTitle;
  if (normalizedYear !== album.year) values.year = normalizedYear;
  if (albumType !== album.album_type) values.album_type = albumType;
  const resettable = ['title', 'year', 'album_type'].filter(
    (field) => field in album.user_overrides,
  );

  async function save(valuesToSet: Record<string, unknown>, clear: string[] = []) {
    setBusy(true);
    setError(null);
    try {
      await updateLibraryV2MetadataOverrides('release_group', album.id, valuesToSet, clear);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Edit failed');
      setBusy(false);
    }
  }

  return (
    <ModalShell title={`Edit — ${album.title}`} onClose={onClose}>
      <div className={styles.editRow}>
        <label htmlFor="lib2-album-title">Title</label>
        <input
          id="lib2-album-title"
          className={styles.searchInput}
          value={title}
          disabled={busy}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-album-year">Year</label>
        <input
          id="lib2-album-year"
          className={styles.searchInput}
          type="number"
          min={0}
          max={9999}
          value={year}
          disabled={busy}
          onChange={(event) => setYear(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-album-type">Release type</label>
        <select
          id="lib2-album-type"
          className={styles.select}
          value={albumType}
          disabled={busy}
          onChange={(e) => setAlbumType(e.target.value as LibraryV2AlbumType)}
        >
          {LIBRARY_V2_ALBUM_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </option>
          ))}
        </select>
      </div>
      {error ? <div className={styles.searchError}>{error}</div> : null}
      <div className={styles.modalActions}>
        {resettable.length > 0 ? (
          <button
            type="button"
            className={styles.btnGhost}
            disabled={busy}
            onClick={() => void save({}, resettable)}
          >
            Restore provider values
          </button>
        ) : null}
        <button type="button" className={styles.btnGhost} disabled={busy} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={
            busy ||
            !normalizedTitle ||
            (normalizedYear !== null &&
              (!Number.isInteger(normalizedYear) || normalizedYear < 0 || normalizedYear > 9999)) ||
            Object.keys(values).length === 0
          }
          onClick={() => void save(values)}
        >
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </ModalShell>
  );
}

function EditArtistModal({
  artist,
  onClose,
}: {
  artist: LibraryV2ArtistDetail;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(artist.name);
  const [genres, setGenres] = useState(artist.genres.join(', '));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const normalizedName = name.trim();
  const normalizedGenres = genres
    .split(',')
    .map((genre) => genre.trim())
    .filter(Boolean);
  const values: Record<string, unknown> = {};
  if (normalizedName !== artist.name) values.name = normalizedName;
  if (normalizedGenres.join('\u0000') !== artist.genres.join('\u0000')) {
    values.genres = normalizedGenres;
  }
  const resettable = ['name', 'genres'].filter((field) => field in artist.user_overrides);

  async function save(valuesToSet: Record<string, unknown>, clear: string[] = []) {
    setBusy(true);
    setError(null);
    try {
      await updateLibraryV2MetadataOverrides('artist', artist.id, valuesToSet, clear);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Edit failed');
      setBusy(false);
    }
  }

  return (
    <ModalShell title={`Edit — ${artist.name}`} onClose={onClose}>
      <div className={styles.editRow}>
        <label htmlFor="lib2-artist-name">Artist name</label>
        <input
          id="lib2-artist-name"
          className={styles.searchInput}
          value={name}
          disabled={busy}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-artist-genres">Genres</label>
        <input
          id="lib2-artist-genres"
          className={styles.searchInput}
          value={genres}
          disabled={busy}
          placeholder="Pop, Soul"
          onChange={(event) => setGenres(event.target.value)}
        />
      </div>
      {error ? <div className={styles.searchError}>{error}</div> : null}
      <div className={styles.modalActions}>
        {resettable.length > 0 ? (
          <button
            type="button"
            className={styles.btnGhost}
            disabled={busy}
            onClick={() => void save({}, resettable)}
          >
            Restore provider values
          </button>
        ) : null}
        <button type="button" className={styles.btnGhost} disabled={busy} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={busy || !normalizedName || Object.keys(values).length === 0}
          onClick={() => void save(values)}
        >
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </ModalShell>
  );
}

/** Maintenance jobs (the existing repair workers), runnable from the artist
 *  page like Lidarr's Tasks. Jobs with `scoped: true` honor an artist scope
 *  when triggered from here; the rest scan the whole library. */
type MaintenanceDataBasis = 'legacy' | 'lib2' | 'mixed';

const MAINTENANCE_BASIS_LABEL: Record<MaintenanceDataBasis, string> = {
  legacy: 'legacy catalog',
  lib2: 'Library v2',
  mixed: 'legacy + files',
};

const MAINTENANCE_JOBS: Array<{
  id: string;
  label: string;
  desc: string;
  basis: MaintenanceDataBasis;
  scoped?: boolean;
}> = [
  {
    id: 'metadata_gap_filler',
    label: 'Metadata Gap Fill',
    desc: 'Fill missing metadata identifiers (ISRC, MusicBrainz) from providers.',
    basis: 'legacy',
    scoped: true,
  },
  {
    id: 'unknown_artist_fixer',
    label: 'Fix Unknown Artist',
    desc: 'Resolve tracks filed under Unknown/placeholder artists (always library-wide).',
    basis: 'mixed',
  },
  {
    id: 'album_tag_consistency',
    label: 'Album Tag Consistency',
    desc: 'Align album-level tags (album artist, year, art) across each album.',
    basis: 'mixed',
    scoped: true,
  },
  {
    id: 'library_reorganize',
    label: 'Rename / Reorganize Files',
    desc: 'Move this artist’s allowlisted files into the configured folder/name scheme.',
    basis: 'mixed',
    scoped: true,
  },
  {
    id: 'single_album_dedup',
    label: 'Single/Album Dedup',
    desc: 'Find redundant single files for this artist (review under Stats → Repair).',
    basis: 'legacy',
    scoped: true,
  },
  {
    id: 'library_retag',
    label: 'Library Retag',
    desc: 'Rewrite tags from library metadata.',
    basis: 'mixed',
    scoped: true,
  },
  {
    id: 'lib2_upgrade_scan',
    label: 'Library v2 Upgrade Scan',
    desc: 'Queue monitored tracks below their quality profile cutoff (also schedulable under Stats → Repair).',
    basis: 'lib2',
  },
];

function MaintenanceModal({
  artistId,
  artistName,
  onClose,
}: {
  artistId: number;
  artistName: string;
  onClose: () => void;
}) {
  const [state, setState] = useState<Record<string, 'queued' | 'error'>>({});
  return (
    <ModalShell title="Maintenance" onClose={onClose}>
      <p className={styles.qpSubtitle}>
        Jobs marked <span className={styles.qpCurrent}>this artist</span> run scoped to{' '}
        <strong>{artistName}</strong>; the rest scan the whole library. Progress under Stats →
        Repair jobs.
      </p>
      <div className={styles.qpList}>
        {MAINTENANCE_JOBS.map((job) => (
          <button
            key={job.id}
            type="button"
            className={styles.qpOption}
            disabled={state[job.id] === 'queued'}
            onClick={() => {
              void runRepairJob(job.id, job.scoped ? { id: artistId, name: artistName } : undefined)
                .then(() => setState((s) => ({ ...s, [job.id]: 'queued' })))
                .catch(() => setState((s) => ({ ...s, [job.id]: 'error' })));
            }}
          >
            <span className={styles.qpName}>
              {job.label}
              <span className={styles.qpBasis}>{MAINTENANCE_BASIS_LABEL[job.basis]}</span>
              {job.scoped ? <span className={styles.qpCurrent}>this artist</span> : null}
              {state[job.id] === 'queued' ? <span className={styles.statusOk}>queued</span> : null}
              {state[job.id] === 'error' ? (
                <span className={styles.statusWarn}>failed to queue</span>
              ) : null}
            </span>
            <span className={styles.qpDesc}>{job.desc}</span>
          </button>
        ))}
      </div>
    </ModalShell>
  );
}

/** Manage Tracks: the same recording appearing both as a single and on an
 *  album (linked by the importer via canonical_track_id). Shows each side's
 *  quality and lets the user decide which version stays wanted; file-level
 *  dedup is the single_album_dedup maintenance job. */
function ManageTracksModal({ artistId, onClose }: { artistId: number; onClose: () => void }) {
  const queryClient = useQueryClient();
  const dupesQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'duplicates', artistId],
    queryFn: () => fetchLibraryV2Duplicates(artistId),
  });
  const pairs = dupesQuery.data ?? [];
  const [busyTracks, setBusyTracks] = useState<Set<number>>(new Set());
  const [rowError, setRowError] = useState<string | null>(null);

  function withBusy(trackId: number, action: Promise<unknown>) {
    setRowError(null);
    setBusyTracks((s) => new Set(s).add(trackId));
    void action
      .then(() => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }))
      .catch((e) => setRowError(e instanceof Error ? e.message : 'Action failed'))
      .finally(() =>
        setBusyTracks((s) => {
          const next = new Set(s);
          next.delete(trackId);
          return next;
        }),
      );
  }

  function unlink(trackId: number) {
    withBusy(trackId, unlinkLibraryV2Duplicate(trackId));
  }

  function moveFile(fromTrackId: number, toTrackId: number) {
    withBusy(fromTrackId, moveLibraryV2TrackFile(fromTrackId, toTrackId));
  }

  function fileText(side: { file: { format: string | null; bitrate: number | null } | null }) {
    if (!side.file) return 'no file';
    const fmt = (side.file.format ?? '').toUpperCase();
    const kbps = side.file.bitrate
      ? side.file.bitrate > 5000
        ? Math.round(side.file.bitrate / 1000)
        : side.file.bitrate
      : null;
    return [fmt, kbps ? `${kbps} kbps` : null].filter(Boolean).join(' / ') || 'file';
  }

  return (
    <ModalShell title="Manage Tracks — single ↔ album duplicates" wide onClose={onClose}>
      <p className={styles.qpSubtitle}>
        The same recording released as a single and on an album. Unmonitor the version you don't
        want kept up to date; <strong>Move file</strong> re-homes all source file links onto the
        other version (disk untouched — run Rename/Reorganize after); removing duplicate{' '}
        <em>files</em> is the "Single/Album Dedup" job under Maintenance.
      </p>
      {rowError ? <div className={styles.searchError}>{rowError}</div> : null}
      <div className={styles.resultsWrap}>
        {dupesQuery.isLoading ? (
          <div className={styles.inlineLoading}>Scanning for duplicates…</div>
        ) : pairs.length === 0 ? (
          <div className={styles.inlineLoading}>
            No single↔album duplicates found for this artist.
          </div>
        ) : (
          <table className={styles.trackTable}>
            <thead>
              <tr>
                <th>Title</th>
                <th>Single version</th>
                <th className={styles.colMonitor}>Mon.</th>
                <th>Album version</th>
                <th className={styles.colMonitor}>Mon.</th>
                <th className={styles.colActions}></th>
              </tr>
            </thead>
            <tbody>
              {pairs.map((p, i) => (
                <tr key={`${p.single.track_id}-${i}`}>
                  <td>{p.title ?? '—'}</td>
                  <td className={styles.qualityText}>
                    {p.single.album_title ?? '—'}
                    <span className={styles.muted}> · {fileText(p.single)}</span>
                  </td>
                  <td>
                    <MonitorToggle
                      entity="tracks"
                      id={p.single.track_id}
                      monitored={p.single.monitored}
                    />
                  </td>
                  <td className={styles.qualityText}>
                    {p.album.album_title ?? '—'}
                    <span className={styles.muted}> · {fileText(p.album)}</span>
                  </td>
                  <td>
                    <MonitorToggle
                      entity="tracks"
                      id={p.album.track_id}
                      monitored={p.album.monitored}
                    />
                  </td>
                  <td className={styles.trackActions}>
                    {p.single.file && !p.album.file ? (
                      <button
                        type="button"
                        className={styles.toolButton}
                        disabled={busyTracks.has(p.single.track_id)}
                        title="Attach the single's file to the album version instead (file stays on disk; the single stops being wanted)"
                        onClick={() => moveFile(p.single.track_id, p.album.track_id)}
                      >
                        {busyTracks.has(p.single.track_id) ? '…' : 'Move → album'}
                      </button>
                    ) : null}
                    {p.album.file && !p.single.file ? (
                      <button
                        type="button"
                        className={styles.toolButton}
                        disabled={busyTracks.has(p.album.track_id)}
                        title="Attach the album's file to the single version instead (file stays on disk; the album track stops being wanted)"
                        onClick={() => moveFile(p.album.track_id, p.single.track_id)}
                      >
                        {busyTracks.has(p.album.track_id) ? '…' : 'Move → single'}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className={styles.toolButton}
                      disabled={busyTracks.has(p.single.track_id)}
                      title="Not the same recording? Unlink the pair — the single becomes independent again"
                      onClick={() => unlink(p.single.track_id)}
                    >
                      {busyTracks.has(p.single.track_id) ? '…' : 'Unlink'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </ModalShell>
  );
}

// --- page root ---------------------------------------------------------------

export function LibraryV2Page() {
  useReactPageShell('library-v2');
  const search = Route.useSearch();
  const enabledQuery = useQuery(libraryV2EnabledQueryOptions());

  if (enabledQuery.data === false) {
    return (
      <div className={styles.page}>
        <div className={styles.emptyState}>
          <h2>Library v2 is disabled</h2>
          <p>
            Enable <code>features.library_v2</code> in Settings to try the experimental library
            manager.
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      <MirrorStatusBanner />
      {search.playlist ? (
        <PlaylistDetailView playlistId={search.playlist} />
      ) : search.album ? (
        <AlbumDetailView albumId={search.album} />
      ) : search.artist ? (
        <ArtistDetailView artistId={search.artist} />
      ) : search.section === 'playlists' ? (
        <PlaylistIndexView />
      ) : (
        <ArtistIndexView />
      )}
    </>
  );
}

/** Split-brain guard (audit P0-04): monitor changes mirror into the legacy
 *  wishlist through a transactional outbox. When ops are stuck or failed,
 *  say so — the UI must not show "monitored" while the pipeline never
 *  learned about it. */
function MirrorStatusBanner() {
  const queryClient = useQueryClient();
  const statusQuery = useQuery(libraryV2MirrorStatusQueryOptions());
  const retry = useMutation({
    mutationFn: retryLibraryV2Mirror,
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: [...LIBRARY_V2_QUERY_KEY, 'mirror-status'] }),
  });
  const s = statusQuery.data;
  if (!s || (s.pending === 0 && s.failed === 0)) return null;
  const label =
    s.failed > 0
      ? `${s.failed} wishlist sync ${s.failed === 1 ? 'operation' : 'operations'} failed — monitoring shown here may not match what the pipeline searches.`
      : `${s.pending} wishlist sync ${s.pending === 1 ? 'operation' : 'operations'} pending…`;
  return (
    <div className={`${styles.grabBanner} ${s.failed > 0 ? styles.grab_err : styles.grab_busy}`}>
      <span>{label}</span>
      <button
        type="button"
        className={styles.grabBannerClose}
        disabled={retry.isPending}
        onClick={() => retry.mutate()}
      >
        {retry.isPending ? 'Retrying…' : 'Retry'}
      </button>
    </div>
  );
}

// --- artist overview ---------------------------------------------------------

function ArtistIndexView() {
  const search = Route.useSearch();
  const navigate = useNavigate();
  // Debounce the filter box: navigating per keystroke fires a request each key.
  const searchDebounce = useRef<number | undefined>(undefined);
  const artistsQuery = useQuery(
    libraryV2ArtistsQueryOptions({
      q: search.q,
      sort: search.sort,
      page: search.page,
      monitored: search.monitored,
    }),
  );

  const artists = artistsQuery.data?.artists ?? [];
  const pagination = artistsQuery.data?.pagination;
  const isEmpty = !artistsQuery.isLoading && artists.length === 0 && !search.q;

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Library</h1>
          <p className={styles.subtitle}>
            {pagination ? `${pagination.total_count} artists` : 'Experimental library manager'}
          </p>
        </div>
        <div className={styles.headerActions}>
          <ImportButton hasArtists={artists.length > 0} />
        </div>
      </header>

      <div className={styles.toolbar}>
        <LibrarySectionTabs />
        <input
          className={styles.searchInput}
          type="text"
          placeholder="Filter artists…"
          defaultValue={search.q}
          onChange={(e) => {
            const value = e.target.value;
            window.clearTimeout(searchDebounce.current);
            searchDebounce.current = window.setTimeout(() => {
              void navigate({ search: (prev) => ({ ...prev, q: value, page: 1 }) });
            }, 300);
          }}
        />
        <select
          className={styles.select}
          value={search.monitored}
          onChange={(e) =>
            void navigate({
              search: (p) => ({ ...p, monitored: e.target.value as typeof p.monitored, page: 1 }),
            })
          }
        >
          <option value="all">All</option>
          <option value="monitored">Monitored</option>
          <option value="unmonitored">Unmonitored</option>
        </select>
        <select
          className={styles.select}
          value={search.sort}
          onChange={(e) =>
            void navigate({
              search: (p) => ({ ...p, sort: e.target.value as typeof p.sort, page: 1 }),
            })
          }
        >
          <option value="name">Name</option>
          <option value="added">Recently added</option>
          <option value="albums">Album count</option>
          <option value="tracks">Track count</option>
        </select>
        <div className={styles.viewToggle}>
          <button
            type="button"
            className={search.view === 'cards' ? styles.viewActive : ''}
            onClick={() => void navigate({ search: (p) => ({ ...p, view: 'cards' }) })}
          >
            Cards
          </button>
          <button
            type="button"
            className={search.view === 'table' ? styles.viewActive : ''}
            onClick={() => void navigate({ search: (p) => ({ ...p, view: 'table' }) })}
          >
            Table
          </button>
        </div>
      </div>

      {artistsQuery.isLoading ? (
        <div className={styles.loading}>Loading…</div>
      ) : isEmpty ? (
        <div className={styles.emptyState}>
          <h2>Your v2 library is empty</h2>
          <p>Import your existing library to populate the new manager.</p>
          <ImportButton hasArtists={false} prominent />
        </div>
      ) : search.view === 'table' ? (
        <ArtistTable artists={artists} />
      ) : (
        <ArtistCards artists={artists} />
      )}

      {pagination && pagination.total_pages > 1 ? (
        <div className={styles.pagination}>
          <button
            type="button"
            disabled={!pagination.has_prev}
            onClick={() => void navigate({ search: (p) => ({ ...p, page: p.page - 1 }) })}
          >
            ←
          </button>
          <span>
            Page {pagination.page} of {pagination.total_pages}
          </span>
          <button
            type="button"
            disabled={!pagination.has_next}
            onClick={() => void navigate({ search: (p) => ({ ...p, page: p.page + 1 }) })}
          >
            →
          </button>
        </div>
      ) : null}
    </div>
  );
}

// --- playlists (Phase E; thin UI over the shared mirrored pipeline) ---------

function LibrarySectionTabs() {
  const navigate = useNavigate();
  const search = Route.useSearch();
  return (
    <div className={styles.viewToggle} aria-label="Library section">
      <button
        type="button"
        className={search.section === 'artists' ? styles.viewActive : ''}
        onClick={() =>
          void navigate({
            search: (previous) => ({
              ...previous,
              section: 'artists',
              q: '',
              playlist: undefined,
              artist: undefined,
              album: undefined,
            }),
          })
        }
      >
        Artists
      </button>
      <button
        type="button"
        className={search.section === 'playlists' ? styles.viewActive : ''}
        onClick={() =>
          void navigate({
            search: (previous) => ({
              ...previous,
              section: 'playlists',
              q: '',
              playlist: undefined,
              artist: undefined,
              album: undefined,
              page: 1,
            }),
          })
        }
      >
        Playlists
      </button>
    </div>
  );
}

function playlistSourceLabel(source: string): string {
  const labels: Record<string, string> = {
    spotify: 'Spotify',
    tidal: 'Tidal',
    qobuz: 'Qobuz',
    deezer: 'Deezer',
    youtube: 'YouTube',
    listenbrainz: 'ListenBrainz',
    lastfm: 'Last.fm',
    soulsync_discovery: 'SoulSync Discovery',
    file: 'File',
  };
  return labels[source] ?? source.replaceAll('_', ' ');
}

function activePipeline(state: LibraryV2PlaylistPipelineState | null): boolean {
  return state?.status === 'running';
}

function pipelineLabel(state: LibraryV2PlaylistPipelineState | null): string | null {
  if (!state || state.status === 'idle') return null;
  if (state.status === 'running') return `${state.phase || 'Running'} · ${state.progress || 0}%`;
  if (state.status === 'finished') return 'Last pipeline completed';
  if (state.status === 'skipped') return state.error || 'Pipeline skipped';
  if (state.status === 'error') return state.error || 'Pipeline failed';
  return state.status;
}

function PlaylistPipelineButton({ playlist }: { playlist: LibraryV2PlaylistSummary }) {
  const queryClient = useQueryClient();
  const unsupported = playlist.source === 'file' || playlist.source === 'beatport';
  const mutation = useMutation({
    mutationFn: () => runLibraryV2PlaylistPipeline(playlist.id),
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: [...LIBRARY_V2_QUERY_KEY, 'playlists'] }),
        queryClient.invalidateQueries({
          queryKey: [...LIBRARY_V2_QUERY_KEY, 'playlist', playlist.id],
        }),
      ]);
    },
  });
  const running = mutation.isPending || activePipeline(playlist.pipeline_state);
  return (
    <span className={styles.playlistRunWrap}>
      <ActionButton
        icon="refresh"
        label={running ? 'Pipeline running…' : 'Run pipeline'}
        busy={running}
        disabled={unsupported}
        title={
          unsupported
            ? 'This source cannot be refreshed by the mirrored-playlist pipeline'
            : 'Refresh source, discover metadata, sync to the server, then process the wishlist'
        }
        onClick={() => mutation.mutate()}
      />
      {mutation.isError ? (
        <span className={styles.statusWarn}>
          {mutation.error instanceof Error ? mutation.error.message : 'Could not start pipeline'}
        </span>
      ) : null}
    </span>
  );
}

function PlaylistPipelineState({ state }: { state: LibraryV2PlaylistPipelineState | null }) {
  const label = pipelineLabel(state);
  if (!label) return null;
  const progress = Math.max(0, Math.min(100, state?.progress ?? 0));
  return (
    <div
      className={`${styles.playlistPipeline} ${state?.status === 'error' ? styles.playlistPipelineError : ''}`}
    >
      <div className={styles.playlistPipelineRow}>
        <span>{label}</span>
        {state?.status === 'running' ? <span>{progress}%</span> : null}
      </div>
      {state?.status === 'running' ? (
        <div className={styles.playlistProgressTrack}>
          <span style={{ width: `${progress}%` }} />
        </div>
      ) : null}
    </div>
  );
}

function PlaylistIndexView() {
  const navigate = useNavigate();
  const search = Route.useSearch();
  const playlistsQuery = useQuery(libraryV2PlaylistsQueryOptions());
  const playlists = (playlistsQuery.data ?? []).filter((playlist) => {
    const needle = search.q.trim().toLocaleLowerCase();
    if (!needle) return true;
    return [playlist.display_name, playlist.name, playlist.owner, playlist.source].some((value) =>
      String(value ?? '')
        .toLocaleLowerCase()
        .includes(needle),
    );
  });

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Library</h1>
          <p className={styles.subtitle}>
            {playlistsQuery.data ? `${playlistsQuery.data.length} mirrored playlists` : 'Playlists'}
          </p>
        </div>
      </header>
      <div className={styles.toolbar}>
        <LibrarySectionTabs />
        <input
          className={styles.searchInput}
          type="search"
          placeholder="Filter playlists…"
          value={search.q}
          onChange={(event) =>
            void navigate({ search: (previous) => ({ ...previous, q: event.target.value }) })
          }
        />
      </div>
      {playlistsQuery.isError ? (
        <div className={styles.emptyState}>Could not load mirrored playlists.</div>
      ) : playlistsQuery.isLoading ? (
        <div className={styles.loading}>Loading…</div>
      ) : playlists.length === 0 ? (
        <div className={styles.emptyState}>
          <h2>{search.q ? 'No matching playlists' : 'No mirrored playlists yet'}</h2>
          <p>
            Mirror a playlist on the Playlists page first. Library v2 reuses that same persistent
            mirror and pipeline.
          </p>
        </div>
      ) : (
        <div className={styles.playlistGrid}>
          {playlists.map((playlist) => (
            <article key={playlist.id} className={styles.playlistCard}>
              <button
                type="button"
                className={styles.playlistCardLink}
                onClick={() =>
                  void navigate({
                    search: (previous) => ({
                      ...previous,
                      section: 'playlists',
                      playlist: playlist.id,
                      artist: undefined,
                      album: undefined,
                    }),
                  })
                }
              >
                <Artwork
                  src={playlist.image_url ?? ''}
                  alt={playlist.display_name || playlist.name}
                  className={styles.playlistArtwork}
                  thumb
                />
                <span className={styles.playlistCardBody}>
                  <span className={styles.playlistCardTitle}>
                    {playlist.display_name || playlist.name}
                  </span>
                  <span className={styles.playlistMeta}>
                    {playlistSourceLabel(playlist.source)}
                    {playlist.owner ? ` · ${playlist.owner}` : ''}
                  </span>
                  <span className={styles.playlistCounts}>
                    <span>{playlist.total_count ?? playlist.track_count} tracks</span>
                    <span>{playlist.in_library_count ?? 0} in library</span>
                    <span>{playlist.wishlisted_count ?? 0} wanted</span>
                    <span>{playlist.discovered_count ?? 0} discovered</span>
                  </span>
                </span>
              </button>
              <div className={styles.playlistCardFooter}>
                <PlaylistPipelineState state={playlist.pipeline_state} />
                <PlaylistPipelineButton playlist={playlist} />
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function playlistTrackDiscovered(track: LibraryV2PlaylistTrack): boolean {
  try {
    const parsed: unknown = JSON.parse(track.extra_data || '{}');
    return Boolean(
      parsed && typeof parsed === 'object' && 'discovered' in parsed && parsed.discovered,
    );
  } catch {
    return false;
  }
}

function PlaylistDetailView({ playlistId }: { playlistId: number }) {
  const navigate = useNavigate();
  const playlistQuery = useQuery(libraryV2PlaylistQueryOptions(playlistId));
  const playlist = playlistQuery.data;
  const summary = playlist as LibraryV2PlaylistSummary | undefined;
  return (
    <div className={styles.page}>
      <BackLink
        onClick={() =>
          void navigate({
            search: (previous) => ({
              ...previous,
              section: 'playlists',
              playlist: undefined,
              artist: undefined,
              album: undefined,
            }),
          })
        }
      >
        ← Playlists
      </BackLink>
      {playlistQuery.isError ? (
        <div className={styles.emptyState}>Playlist not found.</div>
      ) : playlistQuery.isLoading || !playlist || !summary ? (
        <div className={styles.loading}>Loading…</div>
      ) : (
        <>
          <header className={styles.detailHeader}>
            <Artwork
              src={playlist.image_url ?? ''}
              alt={playlist.display_name || playlist.name}
              className={styles.detailThumb}
            />
            <div className={styles.detailMeta}>
              <h1 className={styles.title}>{playlist.display_name || playlist.name}</h1>
              <p className={styles.subtitle}>
                {playlistSourceLabel(playlist.source)}
                {playlist.owner ? ` · ${playlist.owner}` : ''}
              </p>
              <div className={styles.detailLabels}>
                <span className={styles.detailLabel}>
                  <SvgIcon name="tracks" />
                  {playlist.tracks.length} tracks
                </span>
                <span className={styles.detailLabel}>
                  <SvgIcon name="download" />
                  {playlist.in_library_count ?? 0} in library
                </span>
                <span className={styles.detailLabel}>
                  <SvgIcon name="monitor" />
                  {playlist.wishlisted_count ?? 0} wanted
                </span>
              </div>
              <PlaylistPipelineButton playlist={summary} />
            </div>
          </header>
          <PlaylistPipelineState state={playlist.pipeline_state} />
          <div className={styles.trackTableWrap}>
            <table className={styles.trackTable}>
              <thead>
                <tr>
                  <th className={styles.colNum}>#</th>
                  <th>Title</th>
                  <th>Artist</th>
                  <th>Album</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {playlist.tracks.map((track) => (
                  <tr key={track.id} className={styles.staticRow}>
                    <td className={styles.colNum}>{track.position}</td>
                    <td>{track.track_name}</td>
                    <td>{track.artist_name}</td>
                    <td>{track.album_name || <span className={styles.muted}>—</span>}</td>
                    <td>
                      {playlistTrackDiscovered(track) ? (
                        <span className={styles.statusOk}>discovered</span>
                      ) : (
                        <span className={styles.muted}>pending discovery</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function ArtistCards({ artists }: { artists: LibraryV2ArtistSummary[] }) {
  const navigate = useNavigate();
  return (
    <div className={styles.cardGrid}>
      {artists.map((artist) => (
        <button
          key={artist.id}
          type="button"
          className={styles.artistCard}
          onClick={() => void navigate({ search: (p) => ({ ...p, artist: artist.id }) })}
        >
          <div className={styles.artistThumbWrap}>
            <Artwork
              src={artist.image_url ?? ''}
              alt={artist.name}
              className={styles.artistThumb}
              thumb
            />
            <span className={styles.cardMonitor}>
              <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
            </span>
          </div>
          <div className={styles.artistInfo}>
            <span className={styles.artistName}>{artist.name}</span>
            <span className={styles.artistMeta}>
              {artist.album_count} albums · {artist.single_count} singles
            </span>
            <span className={styles.artistMeta}>
              {trackProgress(artist.tracks_present, artist.track_count)} tracks
              {artist.tracks_missing > 0 ? (
                <span className={styles.missingBadge}>{artist.tracks_missing} missing</span>
              ) : null}
            </span>
          </div>
        </button>
      ))}
    </div>
  );
}

function ArtistTable({ artists }: { artists: LibraryV2ArtistSummary[] }) {
  const navigate = useNavigate();
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th className={styles.colMonitor}>Mon.</th>
          <th>Artist</th>
          <th className={styles.colNum}>Albums</th>
          <th className={styles.colNum}>Singles</th>
          <th className={styles.colNum}>Tracks</th>
          <th className={styles.colNum}>Missing</th>
        </tr>
      </thead>
      <tbody>
        {artists.map((artist) => (
          <tr
            key={artist.id}
            className={styles.tableRow}
            onClick={() => void navigate({ search: (p) => ({ ...p, artist: artist.id }) })}
          >
            <td>
              <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
            </td>
            <td>
              <span className={styles.cellArtist}>
                <Artwork
                  src={artist.image_url ?? ''}
                  alt={artist.name}
                  className={styles.rowThumb}
                  thumb
                />
                <span>{artist.name}</span>
              </span>
            </td>
            <td className={styles.colNum}>{artist.album_count}</td>
            <td className={styles.colNum}>{artist.single_count}</td>
            <td className={styles.colNum}>
              {trackProgress(artist.tracks_present, artist.track_count)}
            </td>
            <td className={styles.colNum}>
              {artist.tracks_missing > 0 ? artist.tracks_missing : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- artist detail (Lidarr-style: expandable album/single tables) ------------

function AlbumDetailView({ albumId }: { albumId: number }) {
  const navigate = useNavigate();
  const albumQuery = useQuery(libraryV2AlbumQueryOptions(albumId));
  const album = albumQuery.data;
  const [modalAction, setModalAction] = useState<{
    action: string;
    entity?: Lib2EntityRef;
  } | null>(null);
  const [grabBanner, setGrabBanner] = useState<{
    tone: 'busy' | 'ok' | 'err';
    text: string;
  } | null>(null);
  const [showEdit, setShowEdit] = useState(false);

  function handleAction(action: string, entity?: Lib2EntityRef) {
    if (INTERACTIVE_RE.test(action)) {
      setModalAction({ action, entity });
      return;
    }
    if (!AUTO_GRAB_RE.test(action) || !album) return;
    const query = buildSearchQuery(album.primary_artist?.name ?? '', action);
    setGrabBanner({ tone: 'busy', text: `Searching "${query}"…` });
    void autoGrabBest(query, {}, entity)
      .then((best) => {
        if (!best) {
          setGrabBanner({ tone: 'err', text: `No results for "${query}".` });
          return;
        }
        const title = best.result_type === 'album' ? best.album_title : best.title;
        setGrabBanner({ tone: 'ok', text: `Grabbing "${title}" from ${best.username}.` });
      })
      .catch((error) =>
        setGrabBanner({
          tone: 'err',
          text: error instanceof Error ? error.message : 'Search failed',
        }),
      );
  }

  const goBack = () =>
    navigate({
      search: (previous) => ({
        ...previous,
        album: undefined,
        artist: album?.primary_artist?.id ?? previous.artist,
      }),
    });

  return (
    <div className={styles.page}>
      <BackLink onClick={() => void goBack()}>
        ← {album?.primary_artist ? album.primary_artist.name : 'Library'}
      </BackLink>
      {albumQuery.isError ? (
        <div className={styles.emptyState}>Album not found.</div>
      ) : albumQuery.isLoading || !album ? (
        <div className={styles.loading}>Loading…</div>
      ) : (
        <>
          {grabBanner ? (
            <div className={`${styles.grabBanner} ${styles[`grab_${grabBanner.tone}`]}`}>
              <span>{grabBanner.text}</span>
              <button
                type="button"
                className={styles.grabBannerClose}
                onClick={() => setGrabBanner(null)}
              >
                ✕
              </button>
            </div>
          ) : null}
          <header className={styles.detailHeader}>
            <Artwork src={album.image_url ?? ''} alt={album.title} className={styles.detailThumb} />
            <div className={styles.detailMeta}>
              <div className={styles.detailTitleRow}>
                <MonitorToggle entity="albums" id={album.id} monitored={album.monitored} />
                <h1 className={styles.title}>{album.title}</h1>
                <IconActionButton
                  icon="edit"
                  title="Edit metadata"
                  onClick={() => setShowEdit(true)}
                />
              </div>
              <p className={styles.subtitle}>
                {[album.primary_artist?.name, album.album_type, album.release_date ?? album.year]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              <div className={styles.detailLabels}>
                <span className={styles.detailLabel}>
                  <SvgIcon name="profile" />
                  {album.quality_profile?.name ?? 'No quality profile'}
                </span>
                <span className={styles.detailLabel}>
                  <SvgIcon name="tracks" />
                  {trackProgress(album.tracks_present, album.track_count)} tracks
                </span>
                <span className={styles.detailLabel}>
                  <SvgIcon name={album.monitored ? 'monitor' : 'close'} />
                  {album.monitored ? 'Monitored' : 'Unmonitored'}
                </span>
              </div>
              {album.genres.length > 0 ? (
                <p className={styles.genres}>{album.genres.join(', ')}</p>
              ) : null}
            </div>
          </header>
          <AlbumTrackTable albumId={album.id} onAction={handleAction} />
          {showEdit ? <EditAlbumModal album={album} onClose={() => setShowEdit(false)} /> : null}
          {modalAction && INTERACTIVE_RE.test(modalAction.action) ? (
            <InteractiveSearchModal
              initialQuery={buildSearchQuery(album.primary_artist?.name ?? '', modalAction.action)}
              qualityProfile={album.quality_profile}
              entity={modalAction.entity}
              onClose={() => setModalAction(null)}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

/** Filter for the release toggle: "My Library" keeps owned or wanted releases;
 *  "All Releases" shows the full provider discography. */
function visibleReleases(
  entries: LibraryV2AlbumSummary[],
  mode: 'library' | 'all',
): LibraryV2AlbumSummary[] {
  if (mode === 'all') return entries;
  return entries.filter((e) => e.origin !== 'discography' || e.monitored);
}

function ArtistDetailView({ artistId }: { artistId: number }) {
  const navigate = useNavigate();
  const search = Route.useSearch();
  const releasesMode = search.releases;
  const artistQuery = useQuery(libraryV2ArtistQueryOptions(artistId));
  const artist = artistQuery.data;
  const [refreshing, setRefreshing] = useState(false);
  const [discographyBusy, setDiscographyBusy] = useState(false);
  const [upgradeScanBusy, setUpgradeScanBusy] = useState(false);
  const [modalAction, setModalAction] = useState<{
    action: string;
    entity?: Lib2EntityRef;
  } | null>(null);
  const [showMonitoring, setShowMonitoring] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showMaintenance, setShowMaintenance] = useState(false);
  const [showManageTracks, setShowManageTracks] = useState(false);
  const [showEditArtist, setShowEditArtist] = useState(false);
  const [retagTarget, setRetagTarget] = useState<{
    entity: 'artists' | 'albums';
    id: number;
    title: string;
  } | null>(null);
  const [editAlbumTarget, setEditAlbumTarget] = useState<LibraryV2AlbumSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{
    entity: 'artists' | 'albums';
    id: number;
    title: string;
  } | null>(null);
  const [grabBanner, setGrabBanner] = useState<{
    tone: 'busy' | 'ok' | 'err';
    text: string;
  } | null>(null);
  const [qpTarget, setQpTarget] = useState<QpTarget | null>(null);
  const queryClient = useQueryClient();
  const artistName = artist?.name ?? '';

  async function searchUpgrades() {
    setUpgradeScanBusy(true);
    setGrabBanner({ tone: 'busy', text: 'Scanning monitored tracks for quality upgrades…' });
    try {
      const jobId = await startLibraryV2UpgradeScan();
      const error = await awaitBulkJob(queryClient, jobId);
      setGrabBanner(
        error
          ? { tone: 'err', text: `Upgrade scan failed: ${error}` }
          : {
              tone: 'ok',
              text: 'Upgrade scan finished — genuine upgrade candidates were queued to the wishlist.',
            },
      );
    } catch (e) {
      setGrabBanner({ tone: 'err', text: e instanceof Error ? e.message : 'Upgrade scan failed' });
    } finally {
      setUpgradeScanBusy(false);
    }
  }

  async function refresh() {
    setRefreshing(true);
    try {
      await refreshLibraryV2('artists', artistId);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } finally {
      setRefreshing(false);
    }
  }

  async function updateDiscography() {
    setDiscographyBusy(true);
    setGrabBanner({ tone: 'busy', text: 'Fetching full discography…' });
    try {
      const stats = await refreshLibraryV2Discography(artistId);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      setGrabBanner({
        tone: 'ok',
        text: `Discography updated from ${stats.source ?? 'provider'}: ${stats.added} new, ${stats.enriched} matched.`,
      });
    } catch (e) {
      setGrabBanner({
        tone: 'err',
        text: e instanceof Error ? e.message : 'Discography refresh failed',
      });
    } finally {
      setDiscographyBusy(false);
    }
  }

  function setReleasesMode(mode: 'library' | 'all') {
    void navigate({ search: (p) => ({ ...p, releases: mode }) });
    // First switch to "All Releases" with nothing persisted yet → fetch it.
    if (mode === 'all' && artist && artist.discography_count === 0 && !discographyBusy) {
      void updateDiscography();
    }
  }

  /** "Search Monitored" = run the wishlist processor. Every monitored missing
   *  or upgradable track is already mirrored into the wishlist, so processing
   *  it searches and downloads exactly those — it must NOT blind-grab the best
   *  result for a bare artist-name query (an arbitrary release). */
  function searchMonitored() {
    setGrabBanner({ tone: 'busy', text: 'Starting wishlist processing…' });
    void processWishlist()
      .then((message) =>
        setGrabBanner({
          tone: 'ok',
          text: `${message} — monitored missing tracks are searched through the normal pipeline (progress on the Wishlist page).`,
        }),
      )
      .catch((e) =>
        setGrabBanner({
          tone: 'err',
          text: e instanceof Error ? e.message : 'Wishlist processing failed to start',
        }),
      );
  }

  /** Route a toolbar/row action: Interactive Search opens the window;
   *  Search Monitored runs the wishlist processor; per-track Search / Grab
   *  auto-searches and downloads the best result for that specific track. */
  function handleAction(action: string, entity?: Lib2EntityRef) {
    if (action === 'Quality Profile' && artist) {
      setQpTarget({
        entity: 'artists',
        id: artistId,
        currentProfileId: artist.quality_profile?.id ?? 1,
        title: artist.name,
      });
      return;
    }
    if (INTERACTIVE_RE.test(action)) {
      setModalAction({ action, entity });
      return;
    }
    if (SEARCH_MONITORED_RE.test(action)) {
      searchMonitored();
      return;
    }
    if (AUTO_GRAB_RE.test(action)) {
      const query = buildSearchQuery(artistName, action);
      setGrabBanner({ tone: 'busy', text: `Searching "${query}"…` });
      void autoGrabBest(query, {}, entity)
        .then((best) => {
          if (!best) {
            setGrabBanner({ tone: 'err', text: `No results for "${query}".` });
          } else {
            const t = best.result_type === 'album' ? best.album_title : best.title;
            setGrabBanner({ tone: 'ok', text: `Grabbing "${t}" from ${best.username}.` });
          }
        })
        .catch((e) =>
          setGrabBanner({ tone: 'err', text: e instanceof Error ? e.message : 'Search failed' }),
        );
    }
  }

  return (
    <div className={styles.page}>
      <BackLink onClick={() => void navigate({ search: (p) => ({ ...p, artist: undefined }) })}>
        ← All artists
      </BackLink>
      {artistQuery.isLoading || !artist ? (
        <div className={styles.loading}>Loading…</div>
      ) : (
        <>
          <div className={styles.pageToolbar}>
            <div className={styles.toolbarGroup}>
              <ActionButton
                icon="refresh"
                label={refreshing ? 'Refreshing...' : 'Refresh & Scan'}
                title="Refresh information and scan disk"
                busy={refreshing}
                onClick={() => void refresh()}
              />
              <ActionButton
                icon="search"
                label="Search Monitored"
                title="Process the wishlist: search + download all monitored missing tracks"
                onClick={() => handleAction('Search Monitored')}
              />
              <ActionButton
                icon="interactive"
                label="Interactive Search"
                title="Search all SoulSync sources manually"
                onClick={() => handleAction('Interactive Search')}
              />
              <ActionButton
                icon="download"
                label={discographyBusy ? 'Updating…' : 'Update Discography'}
                title="Fetch every release this artist has published (metadata only)"
                busy={discographyBusy}
                onClick={() => void updateDiscography()}
              />
            </div>
            <div className={styles.toolbarGroup}>
              <ActionButton
                icon="download"
                label={upgradeScanBusy ? 'Scanning…' : 'Search Upgrades'}
                title="Queue monitored tracks whose files are below their quality profile's cutoff"
                busy={upgradeScanBusy}
                onClick={() => void searchUpgrades()}
              />
              <ActionButton
                icon="retag"
                label="Preview Retag"
                title="Compare file tags against library metadata and rewrite them"
                onClick={() =>
                  setRetagTarget({ entity: 'artists', id: artistId, title: artist.name })
                }
              />
              <ActionButton
                icon="organize"
                label="Maintenance"
                title="Run library-wide repair jobs (gap fill, unknown artist, consistency, rename)"
                onClick={() => setShowMaintenance(true)}
              />
              <ActionButton
                icon="import"
                label="Manual Import"
                title="Open the Import page to bring staged files into the library"
                onClick={() => void navigate({ to: '/import' })}
              />
              <ActionButton
                icon="tracks"
                label="Manage Tracks"
                title="Review single↔album duplicate recordings and their monitor state"
                onClick={() => setShowManageTracks(true)}
              />
              <ActionButton
                icon="history"
                label="History"
                title="Recent downloads recorded for this artist"
                onClick={() => setShowHistory(true)}
              />
            </div>
            <div className={styles.toolbarGroup}>
              <ActionButton
                icon="edit"
                label="Edit Metadata"
                title="Correct artist metadata without rewriting provider data"
                onClick={() => setShowEditArtist(true)}
              />
              <ActionButton
                icon="monitor"
                label="Monitoring"
                title="Apply a monitoring strategy across this artist's releases"
                onClick={() => setShowMonitoring(true)}
              />
              <ActionButton
                icon="profile"
                label="Quality Profile"
                onClick={() => handleAction('Quality Profile')}
              />
              <ActionButton
                icon="delete"
                label="Delete"
                tone="danger"
                title="Remove this artist from the library (files stay on disk)"
                onClick={() =>
                  setDeleteTarget({ entity: 'artists', id: artistId, title: artist.name })
                }
              />
            </div>
          </div>

          {grabBanner ? (
            <div className={`${styles.grabBanner} ${styles[`grab_${grabBanner.tone}`]}`}>
              <span>{grabBanner.text}</span>
              <button
                type="button"
                className={styles.grabBannerClose}
                onClick={() => setGrabBanner(null)}
              >
                ✕
              </button>
            </div>
          ) : null}

          <header className={styles.detailHeader}>
            <Artwork
              src={artist.image_url ?? ''}
              alt={artist.name}
              className={styles.detailThumb}
            />
            <div className={styles.detailMeta}>
              <div className={styles.detailTitleRow}>
                <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
                <h1 className={styles.title}>{artist.name}</h1>
              </div>
              <p className={styles.subtitle}>
                {artist.album_count} albums · {artist.single_count} singles
                {artist.monitored ? ' · Monitored (watchlist)' : ''}
              </p>
              <div className={styles.detailLabels}>
                <span className={styles.detailLabel}>
                  <SvgIcon name="profile" />
                  {artist.quality_profile?.name ?? 'No quality profile'}
                </span>
                <span className={styles.detailLabel}>
                  <SvgIcon name={artist.monitored ? 'monitor' : 'close'} />
                  {artist.monitored ? 'Monitored' : 'Unmonitored'}
                </span>
                <span className={styles.detailLabel}>
                  <SvgIcon name="tracks" />
                  {artist.albums.length + (artist.eps?.length ?? 0) + artist.singles.length}{' '}
                  releases
                </span>
              </div>
              {artist.genres.length > 0 ? (
                <p className={styles.genres}>{artist.genres.join(', ')}</p>
              ) : null}
            </div>
          </header>

          <div className={styles.releasesBar}>
            <div className={styles.releasesToggle}>
              <button
                type="button"
                className={releasesMode === 'library' ? styles.viewActive : ''}
                onClick={() => setReleasesMode('library')}
              >
                My Library
              </button>
              <button
                type="button"
                className={releasesMode === 'all' ? styles.viewActive : ''}
                onClick={() => setReleasesMode('all')}
              >
                All Releases
                {artist.discography_count > 0 ? (
                  <span className={styles.sectionCount}>{artist.discography_count}</span>
                ) : null}
              </button>
            </div>
            <span className={styles.releasesHint}>
              {releasesMode === 'all'
                ? 'Full discography from the metadata provider — monitor a release to add it to Wanted.'
                : 'Releases in your library (plus monitored ones).'}
            </span>
          </div>

          <AlbumGroup
            title="Albums"
            albums={visibleReleases(artist.albums, releasesMode)}
            artistId={artistId}
            scope="albums"
            onAction={handleAction}
            onQualityProfile={setQpTarget}
            onDelete={(album) =>
              setDeleteTarget({ entity: 'albums', id: album.id, title: album.title })
            }
            onRetag={(album) =>
              setRetagTarget({ entity: 'albums', id: album.id, title: album.title })
            }
            onEdit={setEditAlbumTarget}
          />
          <AlbumGroup
            title="EPs"
            albums={visibleReleases(artist.eps ?? [], releasesMode)}
            artistId={artistId}
            scope="eps"
            onAction={handleAction}
            onQualityProfile={setQpTarget}
            onDelete={(album) =>
              setDeleteTarget({ entity: 'albums', id: album.id, title: album.title })
            }
            onRetag={(album) =>
              setRetagTarget({ entity: 'albums', id: album.id, title: album.title })
            }
            onEdit={setEditAlbumTarget}
          />
          <AlbumGroup
            title="Singles"
            albums={visibleReleases(artist.singles, releasesMode)}
            artistId={artistId}
            scope="singles"
            onAction={handleAction}
            onQualityProfile={setQpTarget}
            onDelete={(album) =>
              setDeleteTarget({ entity: 'albums', id: album.id, title: album.title })
            }
            onRetag={(album) =>
              setRetagTarget({ entity: 'albums', id: album.id, title: album.title })
            }
            onEdit={setEditAlbumTarget}
          />
          {modalAction && INTERACTIVE_RE.test(modalAction.action) ? (
            <InteractiveSearchModal
              initialQuery={buildSearchQuery(artist.name, modalAction.action)}
              qualityProfile={artist.quality_profile}
              entity={modalAction.entity}
              onClose={() => setModalAction(null)}
            />
          ) : null}
          {showMonitoring ? (
            <MonitoringModal
              artistId={artistId}
              monitorNewItems={artist.monitor_new_items}
              onClose={() => setShowMonitoring(false)}
            />
          ) : null}
          {showHistory ? (
            <HistoryModal artistId={artistId} onClose={() => setShowHistory(false)} />
          ) : null}
          {showMaintenance ? (
            <MaintenanceModal
              artistId={artist.id}
              artistName={artist.name}
              onClose={() => setShowMaintenance(false)}
            />
          ) : null}
          {showManageTracks ? (
            <ManageTracksModal artistId={artistId} onClose={() => setShowManageTracks(false)} />
          ) : null}
          {showEditArtist ? (
            <EditArtistModal artist={artist} onClose={() => setShowEditArtist(false)} />
          ) : null}
          {retagTarget ? (
            <RetagModal
              entity={retagTarget.entity}
              id={retagTarget.id}
              title={retagTarget.title}
              onClose={() => setRetagTarget(null)}
            />
          ) : null}
          {editAlbumTarget ? (
            <EditAlbumModal album={editAlbumTarget} onClose={() => setEditAlbumTarget(null)} />
          ) : null}
          {deleteTarget ? (
            <DeleteConfirmModal
              entity={deleteTarget.entity}
              id={deleteTarget.id}
              title={deleteTarget.title}
              onDone={() => {
                setDeleteTarget(null);
                if (deleteTarget.entity === 'artists') {
                  void navigate({ search: (p) => ({ ...p, artist: undefined }) });
                }
              }}
              onClose={() => setDeleteTarget(null)}
            />
          ) : null}
          {qpTarget ? (
            <QualityProfileModal
              entity={qpTarget.entity}
              id={qpTarget.id}
              currentProfileId={qpTarget.currentProfileId}
              title={qpTarget.title}
              onClose={() => setQpTarget(null)}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

/** Poll the background bulk-job status until it settles, then refresh. */
async function awaitBulkJob(
  queryClient: ReturnType<typeof useQueryClient>,
  jobId: string,
): Promise<string | null> {
  for (let i = 0; i < 300; i += 1) {
    const state = await fetchLibraryV2JobStatus(jobId);
    if (!state.running) {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      return state.error;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  return 'Timed out waiting for the bulk job';
}

/** Lidarr-style album list: each album is a block whose header expands to reveal
 *  its track table — contained in the block (no fragile nested-table colspans). */
function AlbumGroup({
  title,
  albums,
  artistId,
  scope,
  onAction,
  onQualityProfile,
  onDelete,
  onRetag,
  onEdit,
}: {
  title: string;
  albums: LibraryV2AlbumSummary[];
  artistId: number;
  scope: 'albums' | 'eps' | 'singles';
  onAction: ActionHandler;
  onQualityProfile: (target: QpTarget) => void;
  onDelete: (album: LibraryV2AlbumSummary) => void;
  onRetag: (album: LibraryV2AlbumSummary) => void;
  onEdit: (album: LibraryV2AlbumSummary) => void;
}) {
  const queryClient = useQueryClient();
  const [bulkBusy, setBulkBusy] = useState(false);
  if (albums.length === 0) return null;
  const allMonitored = albums.every((a) => a.monitored);

  async function bulkMonitor(monitored: boolean) {
    setBulkBusy(true);
    try {
      const jobId = await bulkMonitorLibraryV2Releases(artistId, scope, monitored);
      await awaitBulkJob(queryClient, jobId);
    } catch {
      // Job endpoint already logs; refresh so the UI shows the actual state.
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } finally {
      setBulkBusy(false);
    }
  }

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>
        {title} <span className={styles.sectionCount}>{albums.length}</span>
        <button
          type="button"
          className={styles.sectionBulk}
          disabled={bulkBusy}
          title={
            allMonitored
              ? `Stop monitoring all ${title.toLowerCase()}`
              : `Monitor all ${title.toLowerCase()} (adds missing tracks to Wanted)`
          }
          onClick={() => void bulkMonitor(!allMonitored)}
        >
          <SvgIcon name="monitor" filled={allMonitored} />
          {bulkBusy ? 'Working…' : allMonitored ? 'Unmonitor all' : 'Monitor all'}
        </button>
      </h2>
      <div className={styles.albumList}>
        {albums.map((album) => (
          <AlbumBlock
            key={album.id}
            album={album}
            onAction={onAction}
            onQualityProfile={onQualityProfile}
            onDelete={onDelete}
            onRetag={onRetag}
            onEdit={onEdit}
          />
        ))}
      </div>
    </section>
  );
}

function AlbumBlock({
  album,
  onAction,
  onQualityProfile,
  onDelete,
  onRetag,
  onEdit,
}: {
  album: LibraryV2AlbumSummary;
  onAction: ActionHandler;
  onQualityProfile: (target: QpTarget) => void;
  onDelete: (album: LibraryV2AlbumSummary) => void;
  onRetag: (album: LibraryV2AlbumSummary) => void;
  onEdit: (album: LibraryV2AlbumSummary) => void;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const complete = album.tracks_missing === 0 && album.track_count > 0;
  const pct = album.track_count ? Math.round((100 * album.tracks_present) / album.track_count) : 0;
  const unowned = album.origin === 'discography' && album.tracks_present === 0;
  return (
    <div className={`${styles.albumBlock} ${open ? styles.albumBlockOpen : ''}`}>
      <div className={styles.albumHead} onClick={() => setOpen(!open)}>
        <span className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`}>›</span>
        <MonitorToggle entity="albums" id={album.id} monitored={album.monitored} />
        <Artwork
          src={album.image_url ?? ''}
          alt={album.title}
          className={styles.albumHeadThumb}
          thumb
        />
        <div className={styles.albumHeadMeta}>
          <span className={styles.albumHeadTitle}>{album.title}</span>
          <span className={styles.albumHeadSub}>
            {[album.album_type, album.release_date || (album.year ? String(album.year) : null)]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </div>
        <div className={styles.albumProgress}>
          <div className={styles.progressBar}>
            <div
              className={styles.progressFill}
              data-complete={complete ? 'true' : 'false'}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className={styles.progressLabel}>
            {trackProgress(album.tracks_present, album.track_count)}
          </span>
        </div>
        {unowned ? (
          <span className={styles.statusNotOwned}>not in library</span>
        ) : (
          <span className={complete ? styles.statusOk : styles.statusWarn}>
            {complete ? 'complete' : `${album.tracks_missing} missing`}
          </span>
        )}
        <span className={styles.albumActions}>
          <IconActionButton
            icon="expand"
            title="Open album detail"
            onClick={() =>
              void navigate({ search: (previous) => ({ ...previous, album: album.id }) })
            }
          />
          <IconActionButton
            icon="search"
            title="Search Monitored"
            onClick={() => onAction(`Search Monitored: ${album.title}`)}
          />
          <IconActionButton
            icon="interactive"
            title="Interactive Search"
            onClick={() =>
              onAction(`Interactive Search: ${album.title}`, {
                albumId: album.id,
                qualityProfileId: album.quality_profile_id,
              })
            }
          />
          <IconActionButton
            icon="profile"
            title="Quality Profile"
            onClick={() =>
              onQualityProfile({
                entity: 'albums',
                id: album.id,
                currentProfileId: album.quality_profile_id,
                title: album.title,
              })
            }
          />
          <IconActionButton icon="retag" title="Preview Retag" onClick={() => onRetag(album)} />
          <IconActionButton
            icon="edit"
            title="Edit release (correct the album/EP/single type)"
            onClick={() => onEdit(album)}
          />
          <IconActionButton
            icon="delete"
            title="Remove album from library (files stay on disk)"
            tone="danger"
            onClick={() => onDelete(album)}
          />
        </span>
      </div>
      {open ? <AlbumTrackTable albumId={album.id} resolve={unowned} onAction={onAction} /> : null}
    </div>
  );
}

function AlbumTrackTable({
  albumId,
  resolve,
  onAction,
}: {
  albumId: number;
  /** Discography-only releases materialize their provider tracklist on expand. */
  resolve?: boolean;
  onAction: ActionHandler;
}) {
  const albumQuery = useQuery(libraryV2AlbumQueryOptions(albumId, { resolve }));
  const album = albumQuery.data;
  if (albumQuery.isLoading || !album) {
    return <div className={styles.inlineLoading}>Loading tracks…</div>;
  }
  return (
    <div className={styles.trackTableWrap}>
      <table className={styles.trackTable}>
        <thead>
          <tr>
            <th className={styles.colMonitor}></th>
            <th className={styles.colNum}>#</th>
            <th>Title</th>
            <th>Artists</th>
            <th>Quality</th>
            <th>File</th>
            <th>Metadata</th>
            <th className={styles.colActions}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {album.tracks.map((track, i) => (
            <TrackRow
              key={track.id ?? `missing-${i}`}
              track={track}
              albumTitle={album.title}
              entityBase={{ albumId: album.id, qualityProfileId: album.quality_profile?.id }}
              onAction={onAction}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TrackRow({
  track,
  albumTitle,
  entityBase,
  onAction,
}: {
  track: LibraryV2Track;
  albumTitle: string;
  entityBase: Lib2EntityRef;
  onAction: ActionHandler;
}) {
  const missing = track.file_status === 'missing';
  const label = track.title ?? `Track ${track.track_number ?? '?'} - missing`;
  const entity: Lib2EntityRef = { ...entityBase, ...(track.id ? { trackId: track.id } : {}) };
  return (
    <tr className={missing ? styles.missingRow : styles.staticRow}>
      <td>
        {track.id ? (
          <MonitorToggle entity="tracks" id={track.id} monitored={track.monitored} />
        ) : null}
      </td>
      <td className={styles.colNum}>{track.track_number ?? '—'}</td>
      <td>{track.title ?? <span className={styles.muted}>{label}</span>}</td>
      <td>{track.artists.map((a) => a.name).join(', ')}</td>
      <td className={styles.qualityText}>
        {detailedQualityText(track.file)}
        {track.file && track.meets_profile === false ? (
          <span className={styles.qBelow} title="Below the album's quality profile">
            below profile
          </span>
        ) : track.file && track.upgrade_candidate ? (
          <span className={styles.qUpgrade} title="A higher-quality version may be available">
            upgrade ↑
          </span>
        ) : null}
      </td>
      <td>
        <FileStatusBadge status={track.file_status} />
      </td>
      <td>
        {track.id ? (
          track.metadata_gaps.length === 0 ? (
            <span className={styles.statusOk}>complete</span>
          ) : (
            <span className={styles.statusWarn} title={track.metadata_gaps.join(', ')}>
              {track.metadata_gaps.length} missing
            </span>
          )
        ) : (
          <span className={styles.muted}>—</span>
        )}
      </td>
      <td className={styles.trackActions}>
        <IconActionButton
          icon="search"
          title="Search"
          disabled={!track.id}
          onClick={() => onAction(`Search: ${label} (${albumTitle})`, entity)}
        />
        <IconActionButton
          icon="interactive"
          title="Interactive Search"
          disabled={!track.id}
          onClick={() => onAction(`Interactive Search: ${label} (${albumTitle})`, entity)}
        />
        <IconActionButton
          icon="download"
          title="Grab / Download"
          disabled={!track.id}
          onClick={() => onAction(`Grab Release: ${label} (${albumTitle})`, entity)}
        />
      </td>
    </tr>
  );
}

function FileStatusBadge({ status }: { status: LibraryV2Track['file_status'] }) {
  if (status === 'present') return <span className={styles.statusPresent}>present</span>;
  if (status === 'duplicate_single') return <span className={styles.statusDup}>also on album</span>;
  return <span className={styles.statusMissing}>missing</span>;
}

function BackLink({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" className={styles.backLink} onClick={onClick}>
      {children}
    </button>
  );
}

function ImportButton({ hasArtists, prominent }: { hasArtists: boolean; prominent?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function runImport() {
    setBusy(true);
    setMessage('Importing…');
    try {
      await startLibraryV2Import(false);
      for (let i = 0; i < 600; i += 1) {
        const state = await fetchLibraryV2ImportStatus();
        if (!state.running) {
          setMessage(state.error ? `Failed: ${state.error}` : 'Imported — refreshing…');
          if (!state.error) window.location.reload();
          break;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className={styles.importWrap}>
      <button
        type="button"
        className={prominent ? styles.btnPrimary : styles.btnGhost}
        disabled={busy}
        onClick={() => void runImport()}
      >
        {busy ? 'Importing…' : hasArtists ? 'Re-import library' : 'Import library'}
      </button>
      {message ? <span className={styles.importMsg}>{message}</span> : null}
    </span>
  );
}
