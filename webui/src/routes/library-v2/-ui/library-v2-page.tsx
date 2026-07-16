import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useNavigate as useRouterNavigate } from '@tanstack/react-router';
import { type ReactNode, useEffect, useRef, useState } from 'react';

import { getShellBridge } from '@/platform/shell/bridge';
import { useReactPageShell } from '@/platform/shell/route-controllers';

import type {
  LibraryV2AlbumDetail,
  LibraryV2AlbumSummary,
  LibraryV2ArtistDetail,
  LibraryV2ArtistSummary,
  LibraryV2ArtistTableColumns,
  LibraryV2FileTags,
  LibraryV2ManualSkip,
  LibraryV2MatchService,
  LibraryV2PlaylistPipelineState,
  LibraryV2PlaylistSummary,
  LibraryV2PlaylistTrack,
  LibraryV2Track,
  LibraryV2TrackFile,
  LibraryV2TrackTableColumns,
} from '../-library-v2.types';

import {
  analyzeLibraryV2TrackReplayGain,
  blacklistLibraryV2Source,
  bulkMonitorLibraryV2Releases,
  deleteLibraryV2Entity,
  deleteLibraryV2Files,
  editLibraryV2Artist,
  editTrackFileTag,
  enrichLibraryV2Entity,
  fetchLibraryV2ArtistDeletePreview,
  fetchLibraryV2ArtistHistory,
  fetchLibraryV2Artists,
  fetchLibraryV2ArtistTrackFiles,
  fetchLibraryV2Duplicates,
  fetchLibraryV2FileDeletePreview,
  fetchLibraryV2ImportStatus,
  fetchLibraryV2JobStatus,
  fetchLibraryV2TrackLyrics,
  LIBRARY_V2_ALBUM_TYPES,
  LIBRARY_V2_QUERY_KEY,
  libraryV2AlbumMatchStatusQueryOptions,
  libraryV2AlbumQueryOptions,
  libraryV2ArtistAliasesQueryOptions,
  libraryV2ArtistMatchStatusQueryOptions,
  libraryV2ArtistQueryOptions,
  libraryV2ArtistsQueryOptions,
  libraryV2EnabledQueryOptions,
  libraryV2MirrorStatusQueryOptions,
  libraryV2PlaylistQueryOptions,
  libraryV2PlaylistsQueryOptions,
  libraryV2QualityProfilesQueryOptions,
  libraryV2TrackFileTagsQueryOptions,
  libraryV2TrackSourceInfoQueryOptions,
  libraryV2UiPreferencesQueryOptions,
  linkLibraryV2ArtistAlias,
  manualMatchLibraryV2Entity,
  materializeLibraryV2MissingTrack,
  moveLibraryV2TrackFile,
  searchLibraryV2MatchService,
  refreshLibraryV2,
  refreshLibraryV2Discography,
  retryLibraryV2Mirror,
  runRepairJob,
  runLibraryV2PlaylistPipeline,
  setLibraryV2Monitored,
  startLibraryV2AlbumReplayGain,
  startLibraryV2Import,
  startLibraryV2ScopedSearch,
  startLibraryV2UpgradeScan,
  unlinkLibraryV2ArtistAlias,
  unlinkLibraryV2Duplicate,
  updateLibraryV2MetadataOverrides,
  updateLibraryV2UiPreferences,
  writeLibraryV2Tags,
  type Lib2EntityRef,
  type LibraryV2AlbumType,
  type LibraryV2ArtistTrackFile,
  type LibraryV2HistoryCategory,
} from '../-library-v2.api';
import { computeTrackEditValues } from '../-metadata-edit';
import { Route } from '../route';
import { AlbumArtPickerModal, ArtistImagePickerModal } from './art-picker-modal';
import { InteractiveSearchModal } from './interactive-search';
import styles from './library-v2-page.module.css';
import { QualityProfileModal, QualityProfilePicker } from './quality-profile-modal';
import { AlbumReorganizeModal, ArtistReorganizeAllModal } from './reorganize-modal';
import { RetagModal } from './retag-modal';

/** Artist-level quality-profile target only — albums/EPs/singles use the
 *  self-contained, consolidated AlbumDetailModal instead (per user request:
 *  merge Quality+Edit at album level, but keep artist-level actions separate). */
interface QpTarget {
  entity: 'artists';
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

/** Single clamp for every derived progress percent (P2-20): counters can
 *  exceed their nominal total under races/consolidation, and must never
 *  render as a >100% or negative bar/label. */
export function clampPercent(value: number | null | undefined): number {
  if (value == null || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

/** `duration` travels in milliseconds (`lib2_tracks.duration`) end to end. */
function formatDuration(ms: number | null | undefined): string {
  if (ms == null || ms <= 0) return '—';
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
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

/** Release dates from library-origin metadata sometimes carry a full
 *  timestamp (e.g. "1982-11-29T08:00:00Z" or "1994-06-21 00:00:00"); the UI
 *  only ever wants the calendar date. */
function formatReleaseDate(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined || value === '') return null;
  const str = String(value);
  return str.length >= 10 ? str.slice(0, 10) : str;
}

/** Only "Interactive Search" opens the manual results window. */
const INTERACTIVE_RE = /^Interactive Search\b/;
/** "Automatic Search" (any scope) / per-track "Search" / "Grab Release" all
 *  route to the scoped server-side search (deep-dive C1) — the entity ref
 *  carried alongside the action string decides artist/album/track scope. */
const SCOPED_SEARCH_RE = /^(Automatic Search|Search|Grab Release)\b/;

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

const BOOKMARK_PATH = 'M5 3.5A1.5 1.5 0 0 1 6.5 2h11A1.5 1.5 0 0 1 19 3.5V22l-7-4.2L5 22V3.5z';

const ICON_PATHS = {
  back: 'M15 18l-6-6 6-6M9 12h12',
  refresh: 'M21 12a9 9 0 0 1-15.3 6.4M3 12A9 9 0 0 1 18.3 5.6M18 3v5h-5M6 21v-5h5',
  search: 'M11 19a8 8 0 1 1 5.7-2.3L21 21',
  interactive: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2 M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  automatic: 'M11 19a8 8 0 1 1 5.7-2.3L21 21',
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
  quality: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  star: 'M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z',
  userProfile:
    'M224 256A128 128 0 1 0 224 0a128 128 0 1 0 0 256zm-45.7 48C79.8 304 0 383.8 0 482.3C0 498.7 13.3 512 29.7 512l388.6 0c16.4 0 29.7-13.3 29.7-29.7C448 383.8 368.2 304 269.7 304l-91.4 0z',
  folder: 'M3 6h7l2 2h9v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z',
  close: 'M6 6l12 12M18 6L6 18',
  info: 'M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM12 16v-4M12 8h.01',
  gain: 'M3 12h3l2-7 3 15 3-11 2 5h5',
  play: 'M8 5l11 7-11 7V5z',
  cover: 'M4 4h16v16H4z M4 16l4-4 3 3 5-6 4 5',
  more: 'M3.4,12 a1.6,1.6 0 1,0 3.2,0 a1.6,1.6 0 1,0 -3.2,0 M10.4,12 a1.6,1.6 0 1,0 3.2,0 a1.6,1.6 0 1,0 -3.2,0 M17.4,12 a1.6,1.6 0 1,0 3.2,0 a1.6,1.6 0 1,0 -3.2,0',
  settings:
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
} as const;

type IconName = keyof typeof ICON_PATHS;
type IconRenderMode = 'stroke' | 'fill';

function SvgIcon({
  name,
  filled,
  renderMode = 'stroke',
}: {
  name: IconName;
  filled?: boolean;
  renderMode?: IconRenderMode;
}) {
  const isFillIcon = renderMode === 'fill' || name === 'userProfile';
  return (
    <svg viewBox={isFillIcon ? '0 0 512 512' : '0 0 24 24'} aria-hidden="true">
      <path
        d={ICON_PATHS[name]}
        fill={isFillIcon || filled ? 'currentColor' : 'none'}
        stroke={!isFillIcon && !filled ? 'currentColor' : 'none'}
        strokeLinecap={!isFillIcon ? 'round' : undefined}
        strokeLinejoin={!isFillIcon ? 'round' : undefined}
      />
    </svg>
  );
}

/** Quality display: compact boxes for format, resolution, bitrate. */
function QualityDisplay({ file }: { file: LibraryV2Track['file'] | null | undefined }) {
  if (!file) return <span className={styles.qualityMissing}>-</span>;

  const fmt = (file.format ?? '').toUpperCase() || null;
  const kbps = file.bitrate
    ? file.bitrate > 5000
      ? Math.round(file.bitrate / 1000)
      : file.bitrate
    : null;
  const bitDepth = file.bit_depth ? `${file.bit_depth}bit` : null;
  const sampleRate = file.sample_rate
    ? `${Number((file.sample_rate / 1000).toFixed(file.sample_rate % 1000 === 0 ? 0 : 1))}kHz`
    : null;
  const resolution = [bitDepth, sampleRate].filter(Boolean).join('/');
  // Format + resolution share one badge (e.g. "FLAC · 16bit/44.1kHz"); bitrate
  // stays its own badge since it's independently meaningful for lossy files.
  const formatBadge = [fmt, resolution || null].filter(Boolean).join(' · ');

  return (
    <span className={styles.qualityDisplay}>
      {formatBadge && <span className={styles.qualityTag}>{formatBadge}</span>}
      {kbps && <span className={styles.qualityTag}>{kbps} kbps</span>}
    </span>
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
    mutationFn: async (v: {
      entity: 'artists' | 'albums' | 'tracks';
      id: number | null;
      monitored: boolean;
      albumId?: number;
      trackNumber?: number;
      discNumber?: number;
      title?: string;
    }) => {
      let targetId = v.id;
      if (targetId == null && v.entity === 'tracks') {
        if (v.albumId == null || v.trackNumber == null) {
          throw new Error('This track cannot be monitored yet');
        }
        const created = await materializeLibraryV2MissingTrack(v.albumId, {
          track_number: v.trackNumber,
          disc_number: v.discNumber ?? 1,
          title: v.title,
        });
        targetId = created.track_id;
      }
      if (targetId != null) {
        return setLibraryV2Monitored(v.entity, targetId, v.monitored);
      }
    },
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
  albumId,
  trackNumber,
  discNumber,
  title,
}: {
  entity: 'artists' | 'albums' | 'tracks';
  id: number | null;
  monitored: boolean;
  albumId?: number;
  trackNumber?: number;
  discNumber?: number;
  title?: string;
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
        onClick={() =>
          mutation.mutate({
            entity,
            id,
            monitored: nextMonitored,
            albumId,
            trackNumber,
            discNumber,
            title,
          })
        }
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

export function ArtistRefreshButton({ artistId }: { artistId: number }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => refreshLibraryV2('artists', artistId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
  });

  return (
    <span className={styles.toolbarMutationControl}>
      <ActionButton
        icon="refresh"
        label={
          mutation.isPending
            ? 'Refreshing...'
            : mutation.isError
              ? 'Retry Refresh & Scan'
              : 'Refresh & Scan'
        }
        title="Refresh information and scan disk"
        busy={mutation.isPending}
        onClick={() => mutation.mutate()}
      />
      {mutation.isError ? (
        <span className={styles.toolbarMutationError} role="alert">
          {mutationErrorMessage(mutation.error, 'Refresh & Scan failed')}
        </span>
      ) : null}
    </span>
  );
}

function ModalShell({
  title,
  wide,
  detail,
  onClose,
  children,
}: {
  title: string;
  wide?: boolean;
  /** Fixed width+height (tab body scrolls internally) so tabbed content
   *  (track/album detail modals) doesn't resize/jump when switching tabs. */
  detail?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div
        className={`${styles.modal} ${wide ? styles.modalWide : ''} ${detail ? styles.modalDetail : ''}`}
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

// --- metadata match chips (legacy Enhanced-View parity) ---------------------

function matchChipClass(status: string): string {
  if (status === 'matched') return styles.matchMatched;
  if (status === 'not_found') return styles.matchNotFound;
  return styles.matchPending;
}

function getServiceAbbreviation(service: string): string {
  switch (service.toLowerCase()) {
    case 'spotify':
      return 'SP';
    case 'musicbrainz':
      return 'MB';
    case 'deezer':
      return 'Dz';
    case 'jiosaavn':
      return 'JS';
    case 'audiodb':
      return 'ADB';
    case 'itunes':
      return 'iT';
    case 'lastfm':
      return 'LFM';
    case 'genius':
      return 'Gen';
    case 'bandcamp':
      return 'BC';
    case 'amazon':
      return 'Amz';
    default:
      return service.substring(0, 3);
  }
}

/** A row of provider match chips. Clicking a chip opens the manual-match modal
 *  (reuses the app-wide match endpoints via the legacy entity id). */
export function MatchChips({
  entityType,
  entityName,
  services,
  abbreviated = false,
  showAll = false,
}: {
  entityType: 'artist' | 'album' | 'track';
  entityName: string;
  services: LibraryV2MatchService[];
  abbreviated?: boolean;
  /** B5 opt-in override: show every provider chip, including ones this
   *  instance never configured (A8's default hides those as noise). */
  showAll?: boolean;
}) {
  const [active, setActive] = useState<LibraryV2MatchService | null>(null);
  // A8: hide chips for providers nobody configured on this instance — a
  // permanently grey Tidal/Qobuz/… row was pure noise. `available` is
  // `undefined` for older cached responses, which reads as available.
  const visible = showAll ? services : services.filter((s) => s.available !== false);
  if (!visible.length) return null;
  return (
    <div className={abbreviated ? styles.trackMatchChips : styles.matchChips}>
      {visible.map((s) => {
        const details = [
          s.external_id ? `id: ${s.external_id}` : 'no id',
          s.last_attempted ? `last: ${s.last_attempted.slice(0, 16).replace('T', ' ')}` : null,
          s.legacy_entity_id != null ? 'click to (re)match' : null,
        ]
          .filter(Boolean)
          .join(' · ');
        const tip = `${s.label}: ${s.status} (${details})`;
        return (
          <button
            key={s.service}
            type="button"
            className={`${styles.matchChip} ${abbreviated ? styles.trackMatchChip : ''} ${matchChipClass(s.status)}`}
            title={tip}
            disabled={s.legacy_entity_id == null}
            onClick={() => setActive(s)}
          >
            {abbreviated ? getServiceAbbreviation(s.service) : `${s.label}: ${s.status}`}
          </button>
        );
      })}
      {active && active.legacy_entity_id != null ? (
        <ManualMatchModal
          entityType={entityType}
          entityName={entityName}
          service={active}
          onClose={() => setActive(null)}
        />
      ) : null}
    </div>
  );
}

function TrackVerificationBadge({ file }: { file: LibraryV2TrackFile | null }) {
  if (!file || !file.verification_status) return null;
  const status = file.verification_status;
  let className = '';
  let label = '';
  let tooltip = '';
  switch (status) {
    case 'verified':
      className = styles.verificationVerified;
      label = 'AcoustID ✓';
      tooltip = 'AcoustID fingerprint matched the expected track';
      break;
    case 'human_verified':
      className = styles.verificationHuman;
      label = 'AcoustID Human';
      tooltip = 'Human verified: you approved this file, skipping AcoustID';
      break;
    case 'force_imported':
      className = styles.verificationForced;
      label = 'AcoustID Bypassed';
      tooltip = 'Force-imported: AcoustID check bypassed (accepted version-mismatch fallback)';
      break;
    case 'unverified':
      className = styles.verificationUnverified;
      label = 'AcoustID Unverified';
      tooltip = 'Imported but not hard-confirmed: AcoustID could not verify this file';
      break;
    default:
      return null;
  }
  return (
    <span className={`${styles.verificationBadge} ${className}`} title={tooltip}>
      {label}
    </span>
  );
}

function ManualMatchModal({
  entityType,
  entityName,
  service,
  onClose,
}: {
  entityType: 'artist' | 'album' | 'track';
  entityName: string;
  service: LibraryV2MatchService;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState(entityName);
  const search = useMutation({
    mutationFn: () =>
      searchLibraryV2MatchService({ service: service.service, entity_type: entityType, query }),
  });
  const apply = useMutation({
    mutationFn: (serviceId: string) =>
      manualMatchLibraryV2Entity({
        entity_type: entityType,
        legacy_entity_id: service.legacy_entity_id as number,
        service: service.service,
        service_id: serviceId,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    },
  });
  const results = search.data ?? [];
  return (
    <ModalShell title={`Match ${entityType} on ${service.label}`} onClose={onClose}>
      <div className={styles.matchSearchRow}>
        <input
          className={styles.searchInput}
          value={query}
          disabled={apply.isPending}
          placeholder={`Search ${service.label}…`}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') search.mutate();
          }}
        />
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={search.isPending || !query.trim()}
          onClick={() => search.mutate()}
        >
          {search.isPending ? 'Searching…' : 'Search'}
        </button>
      </div>
      {search.isError ? (
        <div className={styles.searchError}>
          {mutationErrorMessage(search.error, 'Provider search failed')}
        </div>
      ) : null}
      {apply.isError ? (
        <div className={styles.searchError}>
          {mutationErrorMessage(apply.error, 'Manual match failed')}
        </div>
      ) : null}
      <div className={styles.matchResults}>
        {search.isSuccess && results.length === 0 ? (
          <div className={styles.inlineLoading}>No results — try a different search.</div>
        ) : null}
        {results.map((r) => (
          <div key={`${r.provider ?? service.service}:${r.id}`} className={styles.matchResultRow}>
            <div className={styles.matchResultInfo}>
              <span className={styles.matchResultName}>{r.name || 'Unknown'}</span>
              {r.extra ? <span className={styles.matchResultExtra}>{r.extra}</span> : null}
              <span className={styles.matchResultId}>
                ID: {r.id}
                {r.provider && r.provider !== service.service ? ` (${r.provider})` : ''}
              </span>
            </div>
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={apply.isPending}
              onClick={() => apply.mutate(r.id)}
            >
              {apply.isPending ? 'Matching…' : 'Match'}
            </button>
          </div>
        ))}
      </div>
    </ModalShell>
  );
}

/** Providers each entity type supports for Enrich (docs §44) — mirrors
 *  ``core.library2.match_status.SERVICES``' per-entity-type column map
 *  (Genius has no album column, Discogs has no track column, Bandcamp has
 *  no artist column), which the backend re-validates regardless. */
const ENRICH_SERVICES: Record<
  'artists' | 'albums' | 'tracks',
  { value: string; label: string; icon: string }[]
> = {
  artists: [
    { value: 'spotify', label: 'Spotify', icon: '🟢' },
    { value: 'musicbrainz', label: 'MusicBrainz', icon: '🟠' },
    { value: 'deezer', label: 'Deezer', icon: '🟣' },
    { value: 'itunes', label: 'iTunes', icon: '🔴' },
    { value: 'audiodb', label: 'AudioDB', icon: '🔵' },
    { value: 'discogs', label: 'Discogs', icon: '🟤' },
    { value: 'lastfm', label: 'Last.fm', icon: '⚪' },
    { value: 'genius', label: 'Genius', icon: '🟡' },
    { value: 'tidal', label: 'Tidal', icon: '⬛' },
    { value: 'qobuz', label: 'Qobuz', icon: '🔷' },
    { value: 'amazon', label: 'Amazon', icon: '🛒' },
    { value: 'jiosaavn', label: 'JioSaavn', icon: '🎵' },
  ],
  albums: [
    { value: 'spotify', label: 'Spotify', icon: '🟢' },
    { value: 'musicbrainz', label: 'MusicBrainz', icon: '🟠' },
    { value: 'deezer', label: 'Deezer', icon: '🟣' },
    { value: 'itunes', label: 'iTunes', icon: '🔴' },
    { value: 'audiodb', label: 'AudioDB', icon: '🔵' },
    { value: 'discogs', label: 'Discogs', icon: '🟤' },
    { value: 'lastfm', label: 'Last.fm', icon: '⚪' },
    { value: 'tidal', label: 'Tidal', icon: '⬛' },
    { value: 'qobuz', label: 'Qobuz', icon: '🔷' },
    { value: 'amazon', label: 'Amazon', icon: '🛒' },
    { value: 'jiosaavn', label: 'JioSaavn', icon: '🎵' },
    { value: 'bandcamp', label: 'Bandcamp', icon: '🔹' },
  ],
  tracks: [
    { value: 'spotify', label: 'Spotify', icon: '🟢' },
    { value: 'musicbrainz', label: 'MusicBrainz', icon: '🟠' },
    { value: 'deezer', label: 'Deezer', icon: '🟣' },
    { value: 'itunes', label: 'iTunes', icon: '🔴' },
    { value: 'audiodb', label: 'AudioDB', icon: '🔵' },
    { value: 'lastfm', label: 'Last.fm', icon: '⚪' },
    { value: 'genius', label: 'Genius', icon: '🟡' },
    { value: 'tidal', label: 'Tidal', icon: '⬛' },
    { value: 'qobuz', label: 'Qobuz', icon: '🔷' },
    { value: 'amazon', label: 'Amazon', icon: '🛒' },
    { value: 'jiosaavn', label: 'JioSaavn', icon: '🎵' },
    { value: 'bandcamp', label: 'Bandcamp', icon: '🔹' },
  ],
};

/** Legacy Enrich-dropdown parity (docs §44): pick one provider, re-query it
 *  for this single entity. Delegates to the same worker the legacy Enhanced
 *  View uses; the lib2 row is resynced server-side so the refreshed fields
 *  (genres/bio/label/etc.) show up without a full re-import. */
function EnrichDropdown({
  entity,
  entityId,
  entityName,
  wrapperRef,
  onClose,
  align = 'left',
  submenu = false,
}: {
  entity: 'artists' | 'albums' | 'tracks';
  entityId: number;
  entityName: string;
  wrapperRef: React.RefObject<HTMLSpanElement | null>;
  onClose: () => void;
  align?: 'left' | 'right';
  submenu?: boolean;
}) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (service: string) => {
      if (service === 'all') {
        const services = ENRICH_SERVICES[entity];
        window.showToast?.(`Enriching ${entityName} from all services...`, 'info');
        let resynced = false;
        for (const s of services) {
          try {
            const res = await enrichLibraryV2Entity(entity, entityId, s.value);
            if (res.resynced) resynced = true;
          } catch (e) {
            console.error(`Bulk enrich failed for ${s.value}:`, e);
          }
        }
        return { resynced };
      }
      window.showToast?.(`Enriching ${entityName} from ${service}...`, 'info');
      return enrichLibraryV2Entity(entity, entityId, service);
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      if (data?.resynced) {
        window.showToast?.('Enriched and refreshed.', 'success');
      } else {
        window.showToast?.('Enriched (nothing new found).', 'success');
      }
    },
    onError: (error) => {
      window.showToast?.(mutationErrorMessage(error, 'Enrichment failed'), 'error');
    },
  });

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        onClose();
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose, wrapperRef]);

  return (
    <div
      className={`${styles.enrichDropdownMenu} ${align === 'right' ? styles.alignRight : styles.alignLeft} ${
        submenu ? styles.enrichDropdownSubmenu : ''
      }`}
    >
      <button
        type="button"
        className={styles.enrichDropdownItem}
        disabled={mutation.isPending}
        onClick={(e) => {
          e.stopPropagation();
          mutation.mutate('all');
          onClose();
        }}
      >
        <span className={styles.enrichDropdownIcon}>✨</span>
        <span className={styles.enrichDropdownLabel}>Enrich with all</span>
      </button>
      <div className={styles.enrichDivider} />
      {ENRICH_SERVICES[entity].map((s) => (
        <button
          key={s.value}
          type="button"
          className={styles.enrichDropdownItem}
          disabled={mutation.isPending}
          onClick={(e) => {
            e.stopPropagation();
            mutation.mutate(s.value);
            onClose();
          }}
        >
          <span className={styles.enrichDropdownIcon}>{s.icon}</span>
          <span className={styles.enrichDropdownLabel}>{s.label}</span>
        </button>
      ))}
    </div>
  );
}

/** Fetches an artist's provider match chips and renders them. */
function ArtistMatchChips({ artistId, artistName }: { artistId: number; artistName: string }) {
  const query = useQuery(libraryV2ArtistMatchStatusQueryOptions(artistId));
  if (!query.data?.length) return null;
  return <MatchChips entityType="artist" entityName={artistName} services={query.data} />;
}

/** §40: alias-group chips on the artist header + a "Link alias" action.
 *  ``artistId`` is always the CANONICAL id here (get_artist redirects an
 *  alias id's detail response to its canonical — see docs §24.4), so the
 *  rendered chips are exactly its linked aliases. Deliberately minimal (no
 *  suggestion/recovery UX) — that is §41's separate, larger scope. */
function ArtistAliases({ artistId, artistName }: { artistId: number; artistName: string }) {
  const queryClient = useQueryClient();
  const [linking, setLinking] = useState(false);
  const query = useQuery(libraryV2ArtistAliasesQueryOptions(artistId));
  const unlink = useMutation({
    mutationFn: (aliasId: number) => unlinkLibraryV2ArtistAlias(aliasId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    },
  });
  const aliases = (query.data?.aliases ?? []).filter((m) => m.id !== artistId);
  return (
    <div className={styles.aliasChips}>
      {aliases.map((m) => (
        <span key={m.id} className={styles.aliasChip}>
          {m.name}
          <button
            type="button"
            className={styles.aliasChipRemove}
            title={`Unlink ${m.name} (it becomes a standalone artist again)`}
            disabled={unlink.isPending}
            onClick={() => unlink.mutate(m.id)}
          >
            ✕
          </button>
        </span>
      ))}
      <button
        type="button"
        className={styles.aliasLinkButton}
        title="Link another artist row in your library as an alias of this one (same real artist, different provider identity)"
        onClick={() => setLinking(true)}
      >
        + Link alias
      </button>
      {linking ? (
        <LinkArtistAliasModal
          artistId={artistId}
          artistName={artistName}
          onClose={() => setLinking(false)}
        />
      ) : null}
    </div>
  );
}

/** Search the local library for the OTHER artist row to link as an alias —
 *  reuses the existing artist search endpoint (no new search infra). */
function LinkArtistAliasModal({
  artistId,
  artistName,
  onClose,
}: {
  artistId: number;
  artistName: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const search = useMutation({
    mutationFn: (q: string) =>
      fetchLibraryV2Artists({ q, sort: 'name', page: 1, monitored: 'all' }),
  });
  const link = useMutation({
    mutationFn: (aliasOfId: number) => linkLibraryV2ArtistAlias(artistId, aliasOfId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    },
  });
  const results = (search.data?.artists ?? []).filter((a) => a.id !== artistId);
  return (
    <ModalShell title={`Link an alias of ${artistName}`} onClose={onClose}>
      <div className={styles.matchSearchRow}>
        <input
          className={styles.searchInput}
          value={query}
          disabled={link.isPending}
          placeholder="Search your library for the other artist row…"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') search.mutate(query);
          }}
        />
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={search.isPending || !query.trim()}
          onClick={() => search.mutate(query)}
        >
          {search.isPending ? 'Searching…' : 'Search'}
        </button>
      </div>
      {search.isError ? (
        <div className={styles.searchError}>
          {mutationErrorMessage(search.error, 'Search failed')}
        </div>
      ) : null}
      {link.isError ? (
        <div className={styles.searchError}>{mutationErrorMessage(link.error, 'Link failed')}</div>
      ) : null}
      <div className={styles.matchResults}>
        {search.isSuccess && results.length === 0 ? (
          <div className={styles.inlineLoading}>No matching artists in your library.</div>
        ) : null}
        {results.map((a) => (
          <div key={a.id} className={styles.matchResultRow}>
            <div className={styles.matchResultInfo}>
              <span className={styles.matchResultName}>{a.name}</span>
              <span className={styles.matchResultId}>ID: {a.id}</span>
            </div>
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={link.isPending}
              onClick={() => link.mutate(a.id)}
            >
              {link.isPending ? 'Linking…' : 'Link'}
            </button>
          </div>
        ))}
      </div>
    </ModalShell>
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

const HISTORY_CATEGORY_LABELS: Record<LibraryV2HistoryCategory, string> = {
  grabbed: 'Grabbed',
  imported: 'Imported',
  failed: 'Failed',
  quarantined: 'Quarantined',
  blocklist: 'Blocklist',
  moved: 'Moved',
  deleted: 'Deleted',
  override: 'Override',
  info: 'Info',
};

/** Merged pipeline history for this artist — grabs, imports, quarantine,
 *  catalog moves and physical deletes, not just raw downloads (§A6/C3). */
function HistoryModal({ artistId, onClose }: { artistId: number; onClose: () => void }) {
  const historyQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'history', artistId],
    queryFn: () => fetchLibraryV2ArtistHistory(artistId),
  });
  const [category, setCategory] = useState<LibraryV2HistoryCategory | 'all'>('all');
  const allRows = historyQuery.data ?? [];
  const availableCategories = Array.from(new Set(allRows.map((h) => h.category)));
  const rows = category === 'all' ? allRows : allRows.filter((h) => h.category === category);
  return (
    <ModalShell title="History" wide onClose={onClose}>
      {availableCategories.length > 1 ? (
        <div className={styles.searchOptions}>
          <label className={styles.checkOption}>
            Filter:
            <select
              value={category}
              onChange={(event) =>
                setCategory(event.target.value as LibraryV2HistoryCategory | 'all')
              }
            >
              <option value="all">All events</option>
              {availableCategories.map((c) => (
                <option key={c} value={c}>
                  {HISTORY_CATEGORY_LABELS[c] ?? c}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}
      <div className={styles.resultsWrap}>
        {historyQuery.isLoading ? (
          <div className={styles.inlineLoading}>Loading history…</div>
        ) : rows.length === 0 ? (
          <div className={styles.inlineLoading}>No recorded history for this artist yet.</div>
        ) : (
          <table className={styles.trackTable}>
            <thead>
              <tr>
                <th>Date</th>
                <th>Event</th>
                <th>Detail</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((h, i) => (
                <tr key={i}>
                  <td className={styles.muted}>
                    {h.date ? h.date.slice(0, 16).replace('T', ' ') : '—'}
                  </td>
                  <td>
                    <span className={styles.sourceBadge} data-tone={h.category}>
                      {h.title ?? h.event_type}
                    </span>
                  </td>
                  <td>{h.detail ?? '—'}</td>
                  <td className={styles.muted}>{h.source ?? '—'}</td>
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
  | 'id'
  | 'title'
  | 'year'
  | 'album_type'
  | 'release_date'
  | 'explicit'
  | 'label'
  | 'style'
  | 'mood'
  | 'user_overrides'
>;

/** Album/EP/single detail, consolidated behind one Edit button (same pattern
 *  as the per-track detail modal, per user request — keep it uniform across
 *  album/EP/single; artist-level Quality Profile / Edit stay separate). */
interface AlbumDetailTarget extends EditableAlbumMetadata {
  quality_profile_id: number;
}

type AlbumDetailTab = 'quality' | 'metadata';

function AlbumDetailModal({ album, onClose }: { album: AlbumDetailTarget; onClose: () => void }) {
  const [tab, setTab] = useState<AlbumDetailTab>('quality');
  return (
    <ModalShell title={album.title} detail onClose={onClose}>
      <div className={styles.detailTabs}>
        {(['quality', 'metadata'] as const).map((t) => (
          <button
            key={t}
            type="button"
            className={`${styles.detailTab} ${tab === t ? styles.detailTabActive : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'quality' ? 'Quality' : 'Metadata'}
          </button>
        ))}
      </div>
      <div className={styles.tabBody}>
        {tab === 'quality' ? (
          <QualityProfilePicker
            entity="albums"
            id={album.id}
            currentProfileId={album.quality_profile_id}
            onSaved={onClose}
          />
        ) : null}
        {tab === 'metadata' ? <AlbumMetadataForm album={album} onSaved={onClose} /> : null}
      </div>
    </ModalShell>
  );
}

function AlbumMetadataForm({
  album,
  onSaved,
}: {
  album: EditableAlbumMetadata;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(album.title);
  const [year, setYear] = useState(album.year === null ? '' : String(album.year));
  const [releaseDate, setReleaseDate] = useState(album.release_date ?? '');
  const [albumType, setAlbumType] = useState<LibraryV2AlbumType>(
    (LIBRARY_V2_ALBUM_TYPES as readonly string[]).includes(album.album_type)
      ? (album.album_type as LibraryV2AlbumType)
      : 'album',
  );
  const [explicitFlag, setExplicitFlag] = useState<'' | 'yes' | 'no'>(
    album.explicit === true ? 'yes' : album.explicit === false ? 'no' : '',
  );
  const [label, setLabel] = useState(album.label ?? '');
  const [style, setStyle] = useState(album.style ?? '');
  const [mood, setMood] = useState(album.mood ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const normalizedTitle = title.trim();
  const normalizedYear = year.trim() === '' ? null : Number(year);
  const normalizedReleaseDate = releaseDate.trim();
  const normalizedLabel = label.trim();
  const normalizedStyle = style.trim();
  const normalizedMood = mood.trim();
  const normalizedExplicit = explicitFlag === '' ? null : explicitFlag === 'yes';
  const initialExplicit = album.explicit === true ? 'yes' : album.explicit === false ? 'no' : '';
  const values: Record<string, unknown> = {};
  if (normalizedTitle !== album.title) values.title = normalizedTitle;
  if (normalizedYear !== album.year) values.year = normalizedYear;
  if (albumType !== album.album_type) values.album_type = albumType;
  if (normalizedReleaseDate !== (album.release_date ?? '')) {
    values.release_date = normalizedReleaseDate || null;
  }
  if (explicitFlag !== initialExplicit) values.explicit = normalizedExplicit;
  if (normalizedLabel !== (album.label ?? '')) values.label = normalizedLabel || null;
  if (normalizedStyle !== (album.style ?? '')) values.style = normalizedStyle || null;
  if (normalizedMood !== (album.mood ?? '')) values.mood = normalizedMood || null;
  const resettable = [
    'title',
    'year',
    'album_type',
    'release_date',
    'explicit',
    'label',
    'style',
    'mood',
  ].filter((field) => field in album.user_overrides);

  async function save(valuesToSet: Record<string, unknown>, clear: string[] = []) {
    setBusy(true);
    setError(null);
    try {
      await updateLibraryV2MetadataOverrides('release_group', album.id, valuesToSet, clear);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onSaved();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Edit failed');
      setBusy(false);
    }
  }

  return (
    <>
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
        <label htmlFor="lib2-album-release-date">Release date</label>
        <input
          id="lib2-album-release-date"
          className={styles.searchInput}
          type="text"
          placeholder="YYYY-MM-DD"
          value={releaseDate}
          disabled={busy}
          onChange={(event) => setReleaseDate(event.target.value)}
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
      <div className={styles.editRow}>
        <label htmlFor="lib2-album-explicit">Explicit</label>
        <select
          id="lib2-album-explicit"
          className={styles.select}
          value={explicitFlag}
          disabled={busy}
          onChange={(e) => setExplicitFlag(e.target.value as '' | 'yes' | 'no')}
        >
          <option value="">Unknown</option>
          <option value="yes">Explicit</option>
          <option value="no">Clean</option>
        </select>
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-album-label">Label</label>
        <input
          id="lib2-album-label"
          className={styles.searchInput}
          value={label}
          disabled={busy}
          onChange={(event) => setLabel(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-album-style">Style</label>
        <input
          id="lib2-album-style"
          className={styles.searchInput}
          value={style}
          disabled={busy}
          onChange={(event) => setStyle(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-album-mood">Mood</label>
        <input
          id="lib2-album-mood"
          className={styles.searchInput}
          value={mood}
          disabled={busy}
          onChange={(event) => setMood(event.target.value)}
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
    </>
  );
}

/** B1/B2/B4: the consolidated "…" overflow menu for album actions — details,
 *  retag, ReplayGain, reorganize, cover, enrich, delete. Used by both the
 *  collapsed album row (AlbumBlock) and the album deep-link header
 *  (AlbumDetailView), so both surfaces offer the identical action set
 *  instead of the row alone owning everything and the detail view almost
 *  nothing. `onDeleted` lets the deep-link view navigate back to the artist
 *  after a successful delete; the row doesn't need it (it just disappears
 *  once the query invalidates). */
function AlbumOverflowMenu({
  album,
  onDeleted,
}: {
  album: AlbumDetailTarget;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [showSubmenu, setShowSubmenu] = useState(false);
  const [showRetag, setShowRetag] = useState(false);
  const [showReorganize, setShowReorganize] = useState(false);
  const [showArtPicker, setShowArtPicker] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const replaygain = useMutation({
    mutationFn: async () => {
      const jobId = await startLibraryV2AlbumReplayGain(album.id);
      const jobError = await awaitBulkJob(queryClient, jobId);
      if (jobError) throw new Error(jobError);
    },
  });

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  return (
    <span ref={wrapRef} className={styles.overflowWrap} onClick={(e) => e.stopPropagation()}>
      <IconActionButton icon="more" title="More actions" onClick={() => setOpen((v) => !v)} />
      {open ? (
        <div className={`${styles.overflowMenu} ${styles.alignRight}`}>
          <button
            type="button"
            className={styles.overflowMenuItem}
            onClick={() => {
              setShowDetails(true);
              setOpen(false);
            }}
          >
            Album details
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            onClick={() => {
              setShowRetag(true);
              setOpen(false);
            }}
          >
            Preview retag
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            disabled={replaygain.isPending}
            onClick={() => {
              replaygain.mutate();
              setOpen(false);
            }}
          >
            {replaygain.isPending ? 'Analyzing ReplayGain…' : 'Analyze ReplayGain'}
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            onClick={() => {
              setShowReorganize(true);
              setOpen(false);
            }}
          >
            Reorganize
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            onClick={() => {
              setShowArtPicker(true);
              setOpen(false);
            }}
          >
            Change cover
          </button>
          <div
            className={styles.submenuContainer}
            onMouseEnter={() => setShowSubmenu(true)}
            onMouseLeave={() => setShowSubmenu(false)}
          >
            <button
              type="button"
              className={styles.overflowMenuItem}
              onClick={(e) => {
                e.stopPropagation();
                setShowSubmenu((v) => !v);
              }}
            >
              Enrich… <span className={styles.submenuChevron}>›</span>
            </button>
            {showSubmenu ? (
              <EnrichDropdown
                entity="albums"
                entityId={album.id}
                entityName={album.title}
                wrapperRef={wrapRef}
                align="right"
                submenu
                onClose={() => {
                  setShowSubmenu(false);
                  setOpen(false);
                }}
              />
            ) : null}
          </div>
          <button
            type="button"
            className={`${styles.overflowMenuItem} ${styles.overflowMenuItemDanger}`}
            onClick={() => {
              setShowDelete(true);
              setOpen(false);
            }}
          >
            Delete
          </button>
        </div>
      ) : null}
      {showRetag ? (
        <RetagModal
          entity="albums"
          id={album.id}
          title={album.title}
          onClose={() => setShowRetag(false)}
        />
      ) : null}
      {showReorganize ? (
        <AlbumReorganizeModal
          albumId={album.id}
          albumTitle={album.title}
          onClose={() => setShowReorganize(false)}
        />
      ) : null}
      {showArtPicker ? (
        <AlbumArtPickerModal
          albumId={album.id}
          albumTitle={album.title}
          onClose={() => setShowArtPicker(false)}
        />
      ) : null}
      {showDetails ? (
        <AlbumDetailModal album={album} onClose={() => setShowDetails(false)} />
      ) : null}
      {showDelete ? (
        <DeleteConfirmModal
          entity="albums"
          id={album.id}
          title={album.title}
          onDone={() => {
            setShowDelete(false);
            onDeleted?.();
          }}
          onClose={() => setShowDelete(false)}
        />
      ) : null}
    </span>
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
  const [summary, setSummary] = useState(artist.summary ?? '');
  const [style, setStyle] = useState(artist.style ?? '');
  const [mood, setMood] = useState(artist.mood ?? '');
  const [label, setLabel] = useState(artist.label ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const normalizedName = name.trim();
  const normalizedSummary = summary.trim();
  const normalizedStyle = style.trim();
  const normalizedMood = mood.trim();
  const normalizedLabel = label.trim();
  const normalizedGenres = genres
    .split(',')
    .map((genre) => genre.trim())
    .filter(Boolean);
  const values: Record<string, unknown> = {};
  if (normalizedName !== artist.name) values.name = normalizedName;
  if (normalizedSummary !== (artist.summary ?? '')) values.summary = normalizedSummary || null;
  if (normalizedStyle !== (artist.style ?? '')) values.style = normalizedStyle || null;
  if (normalizedMood !== (artist.mood ?? '')) values.mood = normalizedMood || null;
  if (normalizedLabel !== (artist.label ?? '')) values.label = normalizedLabel || null;
  if (normalizedGenres.join('\u0000') !== artist.genres.join('\u0000')) {
    values.genres = normalizedGenres;
  }
  const resettable = ['name', 'genres', 'summary', 'style', 'mood', 'label'].filter(
    (field) => field in artist.user_overrides,
  );

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
      <div className={styles.editRow}>
        <label htmlFor="lib2-artist-summary">Biography</label>
        <textarea
          id="lib2-artist-summary"
          className={styles.searchInput}
          rows={4}
          value={summary}
          disabled={busy}
          placeholder="Short biography / summary"
          onChange={(event) => setSummary(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-artist-style">Style</label>
        <input
          id="lib2-artist-style"
          className={styles.searchInput}
          value={style}
          disabled={busy}
          onChange={(event) => setStyle(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-artist-mood">Mood</label>
        <input
          id="lib2-artist-mood"
          className={styles.searchInput}
          value={mood}
          disabled={busy}
          onChange={(event) => setMood(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-artist-label">Label</label>
        <input
          id="lib2-artist-label"
          className={styles.searchInput}
          value={label}
          disabled={busy}
          onChange={(event) => setLabel(event.target.value)}
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

type ManageTracksTab = 'duplicates' | 'files';

/** Manage Tracks: "Duplicates" (single↔album pairs, unchanged) plus a new
 *  "Files" tab (C2 — Lidarr's "Manage Track Files") listing every physical
 *  file this artist owns for bulk selection + ADR-05 delete. */
function ManageTracksModal({ artistId, onClose }: { artistId: number; onClose: () => void }) {
  const [tab, setTab] = useState<ManageTracksTab>('files');
  return (
    <ModalShell title="Manage Tracks" wide onClose={onClose}>
      <div className={styles.detailTabs}>
        {(['files', 'duplicates'] as const).map((t) => (
          <button
            key={t}
            type="button"
            className={`${styles.detailTab} ${tab === t ? styles.detailTabActive : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'files' ? 'Files' : 'Duplicates'}
          </button>
        ))}
      </div>
      <div className={styles.tabBody}>
        {tab === 'duplicates' ? <ManageTracksDuplicatesTab artistId={artistId} /> : null}
        {tab === 'files' ? <ArtistFilesTab artistId={artistId} /> : null}
      </div>
    </ModalShell>
  );
}

function ManageTracksDuplicatesTab({ artistId }: { artistId: number }) {
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
    <>
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
    </>
  );
}

/** C2 (Manage Track Files): flat, paginated, selectable list of every
 *  physical file this artist owns — bulk-delete goes through the same
 *  ADR-05 preview/execute contract as the single-entity delete flow
 *  (`DeleteConfirmModal`), scoped to the checked file ids. */
function ArtistFilesTab({ artistId }: { artistId: number }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirming, setConfirming] = useState(false);

  const filesQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'track-files', artistId, search, page],
    queryFn: () => fetchLibraryV2ArtistTrackFiles(artistId, { search, page, limit: 100 }),
  });
  const files = filesQuery.data?.files ?? [];
  const pagination = filesQuery.data?.pagination;
  const allOnPageSelected = files.length > 0 && files.every((f) => selected.has(f.file_id));

  function toggle(fileId: number) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(fileId)) next.delete(fileId);
      else next.add(fileId);
      return next;
    });
  }

  function toggleAllOnPage() {
    setSelected((s) => {
      const next = new Set(s);
      for (const f of files) {
        if (allOnPageSelected) next.delete(f.file_id);
        else next.add(f.file_id);
      }
      return next;
    });
  }

  function qualityText(f: LibraryV2ArtistTrackFile) {
    const parts = [(f.format ?? '').toUpperCase() || null];
    if (f.bit_depth && f.sample_rate) {
      parts.push(`${f.bit_depth}/${Math.round(f.sample_rate / 1000)}kHz`);
    }
    // bitrate is stored inconsistently (bps for some sources, already kbps
    // for others) — same heuristic as QualityDisplay/fileText elsewhere.
    const kbps = f.bitrate ? (f.bitrate > 5000 ? Math.round(f.bitrate / 1000) : f.bitrate) : null;
    if (kbps) parts.push(`${kbps}kbps`);
    return parts.filter(Boolean).join(' · ') || '—';
  }

  return (
    <>
      <input
        className={styles.searchInput}
        type="text"
        placeholder="Filter by track or album…"
        value={search}
        onChange={(e) => {
          setSearch(e.target.value);
          setPage(1);
        }}
      />
      {filesQuery.isLoading ? (
        <div className={styles.inlineLoading}>Loading files…</div>
      ) : files.length === 0 ? (
        <div className={styles.inlineLoading}>No files found.</div>
      ) : (
        <>
          <table className={styles.trackTable}>
            <thead>
              <tr>
                <th>
                  <input type="checkbox" checked={allOnPageSelected} onChange={toggleAllOnPage} />
                </th>
                <th>Track</th>
                <th>Album</th>
                <th>Quality</th>
                <th>Size</th>
                <th>State</th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.file_id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(f.file_id)}
                      onChange={() => toggle(f.file_id)}
                    />
                  </td>
                  <td>
                    {f.track_number != null ? `${f.track_number}. ` : ''}
                    {f.track_title ?? '—'}
                  </td>
                  <td className={styles.qualityText}>{f.album_title ?? '—'}</td>
                  <td className={styles.qualityText}>{qualityText(f)}</td>
                  <td>{formatFileSize(f.size ?? 0)}</td>
                  <td className={styles.muted}>{f.file_state}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {pagination && pagination.total_pages > 1 ? (
            <div className={styles.pagination}>
              <button
                type="button"
                disabled={!pagination.has_prev}
                onClick={() => setPage((p) => p - 1)}
              >
                ←
              </button>
              <span>
                Page {pagination.page} of {pagination.total_pages}
              </span>
              <button
                type="button"
                disabled={!pagination.has_next}
                onClick={() => setPage((p) => p + 1)}
              >
                →
              </button>
            </div>
          ) : null}
        </>
      )}
      <div className={styles.modalActions}>
        <span className={styles.modalActionsText}>{selected.size} selected</span>
        <button
          type="button"
          className={styles.btnDanger}
          disabled={selected.size === 0}
          onClick={() => setConfirming(true)}
        >
          Delete selected…
        </button>
      </div>
      {confirming ? (
        <FilesDeleteConfirm
          entity="artists"
          eid={artistId}
          fileIds={[...selected]}
          onDone={() => {
            setSelected(new Set());
            setConfirming(false);
            void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
          }}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </>
  );
}

/** Scoped ADR-05 preview/execute for a caller-selected file-id subset — same
 *  contract and UX as `DeleteConfirmModal`'s physical-file section, just
 *  bounded to `fileIds` instead of the whole entity. Entity-generic (C2's
 *  artist Files tab, B6's album-scoped track-table bulk delete). */
function FilesDeleteConfirm({
  entity,
  eid,
  fileIds,
  onDone,
  onCancel,
}: {
  entity: 'artists' | 'albums';
  eid: number;
  fileIds: number[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const preview = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'file-delete-preview', entity, eid, fileIds],
    queryFn: () => fetchLibraryV2FileDeletePreview(entity, eid, fileIds),
  });
  const physical = preview.data;
  const physicalReady = Boolean(physical && physical.file_count > 0 && physical.unsafe_count === 0);

  return (
    <section className={styles.fileDeletePanel}>
      <h4>
        Delete {fileIds.length} selected file{fileIds.length === 1 ? '' : 's'}
      </h4>
      {preview.isLoading ? <p className={styles.muted}>Checking file roots…</p> : null}
      {preview.error ? <div className={styles.searchError}>{preview.error.message}</div> : null}
      {physical ? (
        <>
          <p>
            {physical.file_count} file{physical.file_count === 1 ? '' : 's'} ·{' '}
            {formatFileSize(physical.total_size)}
          </p>
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
          {physical.unsafe_count > 0 ? (
            <div className={styles.searchError}>
              Physical deletion is blocked because {physical.unsafe_count} file
              {physical.unsafe_count === 1 ? ' is' : 's are'} unresolved or outside a configured
              library root.
            </div>
          ) : null}
        </>
      ) : null}
      {error ? <div className={styles.searchError}>{error}</div> : null}
      <label className={styles.fileDeleteConfirm}>
        <input
          type="checkbox"
          checked={confirmed}
          disabled={!physicalReady || busy}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        I understand these files will be permanently deleted from disk.
      </label>
      <div className={styles.modalActions}>
        <button type="button" className={styles.btnGhost} disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className={styles.btnDanger}
          disabled={!physicalReady || !confirmed || busy}
          onClick={() => {
            if (!physical) return;
            setBusy(true);
            setError(null);
            void deleteLibraryV2Files(entity, eid, physical.preview_token, fileIds)
              .then(() => onDone())
              .catch((e) =>
                setError(e instanceof Error ? e.message : 'Physical file deletion failed'),
              )
              .finally(() => setBusy(false));
          }}
        >
          {busy
            ? 'Deleting…'
            : `Permanently delete ${physical?.deletable_count ?? 0} file${
                physical?.deletable_count === 1 ? '' : 's'
              }`}
        </button>
      </div>
    </section>
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
export function MirrorStatusBanner() {
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
      <span>
        {label}
        {retry.isError ? (
          <span className={styles.mirrorRetryError} role="alert">
            {mutationErrorMessage(retry.error, 'Mirror retry failed')}
          </span>
        ) : null}
      </span>
      <button
        type="button"
        className={styles.grabBannerClose}
        disabled={retry.isPending}
        onClick={() => retry.mutate()}
      >
        {retry.isPending ? 'Retrying…' : retry.isError ? 'Retry again' : 'Retry'}
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

  // Only fetched for the table view (D6) — the card grid doesn't use either.
  const isTableView = search.view === 'table';
  const profilesQuery = useQuery({
    ...libraryV2QualityProfilesQueryOptions(),
    enabled: isTableView,
  });
  const prefsQuery = useQuery({ ...libraryV2UiPreferencesQueryOptions(), enabled: isTableView });
  const profileNameById = new Map((profilesQuery.data ?? []).map((p) => [p.id, p.name]));
  const artistTableColumns = prefsQuery.data?.artist_table.columns ?? {
    quality_profile: false,
    genres: false,
    added: false,
  };

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
          <UpgradeScanButton />
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
        {isTableView ? <ArtistTableOptionsMenu columns={artistTableColumns} /> : null}
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
        <ArtistTable
          artists={artists}
          columns={artistTableColumns}
          profileNameById={profileNameById}
        />
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
  if (state.status === 'running')
    return `${state.phase || 'Running'} · ${clampPercent(state.progress)}%`;
  if (state.status === 'finished') return 'Last pipeline completed';
  if (state.status === 'skipped') return state.error || 'Pipeline skipped';
  if (state.status === 'error') return state.error || 'Pipeline failed';
  return state.status;
}

export function PlaylistPipelineButton({ playlist }: { playlist: LibraryV2PlaylistSummary }) {
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
        label={running ? 'Pipeline running…' : mutation.isError ? 'Retry pipeline' : 'Run pipeline'}
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
  const progress = clampPercent(state?.progress);
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

export function ArtistCard({
  artist,
  onOpen,
}: {
  artist: LibraryV2ArtistSummary;
  onOpen: (artistId: number) => void;
}) {
  return (
    <article className={styles.artistCard}>
      <button
        type="button"
        className={styles.artistCardLink}
        aria-label={`Open ${artist.name}`}
        onClick={() => onOpen(artist.id)}
      >
        <Artwork
          src={artist.image_url ?? ''}
          alt={artist.name}
          className={styles.artistThumb}
          thumb
        />
        <span className={styles.artistInfo}>
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
        </span>
      </button>
      <span className={styles.cardMonitor}>
        <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
      </span>
    </article>
  );
}

function ArtistCards({ artists }: { artists: LibraryV2ArtistSummary[] }) {
  const navigate = useNavigate();
  return (
    <div className={styles.cardGrid}>
      {artists.map((artist) => (
        <ArtistCard
          key={artist.id}
          artist={artist}
          onOpen={(artistId) => void navigate({ search: (p) => ({ ...p, artist: artistId }) })}
        />
      ))}
    </div>
  );
}

function ArtistTable({
  artists,
  columns,
  profileNameById,
}: {
  artists: LibraryV2ArtistSummary[];
  columns: LibraryV2ArtistTableColumns;
  profileNameById: Map<number, string>;
}) {
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
          {columns.quality_profile ? <th>Quality Profile</th> : null}
          {columns.genres ? <th>Genre</th> : null}
          {columns.added ? <th>Added</th> : null}
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
            {columns.quality_profile ? (
              <td>{profileNameById.get(artist.quality_profile_id) ?? '—'}</td>
            ) : null}
            {columns.genres ? <td>{artist.genres.join(', ') || '—'}</td> : null}
            {columns.added ? <td>{formatReleaseDate(artist.added_at) ?? '—'}</td> : null}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- artist detail (Lidarr-style: expandable album/single tables) ------------

function AlbumDetailView({ albumId }: { albumId: number }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
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

  function handleAction(action: string, entity?: Lib2EntityRef) {
    if (INTERACTIVE_RE.test(action)) {
      setModalAction({ action, entity });
      return;
    }
    if (!SCOPED_SEARCH_RE.test(action)) return;
    const scope = entity?.trackId
      ? { entity: 'tracks' as const, id: entity.trackId }
      : { entity: 'albums' as const, id: albumId };
    setGrabBanner({ tone: 'busy', text: 'Searching…' });
    void runScopedSearch(queryClient, scope.entity, scope.id).then(setGrabBanner);
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
                <AlbumOverflowMenu
                  album={{
                    id: album.id,
                    title: album.title,
                    year: album.year,
                    album_type: album.album_type,
                    release_date: album.release_date,
                    explicit: album.explicit,
                    label: album.label,
                    style: album.style,
                    mood: album.mood,
                    user_overrides: album.user_overrides,
                    quality_profile_id: album.quality_profile?.id ?? 1,
                  }}
                  onDeleted={goBack}
                />
              </div>
              <p className={styles.subtitle}>
                {[
                  album.primary_artist?.name,
                  album.album_type,
                  formatReleaseDate(album.release_date) ?? album.year,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              <div className={styles.detailLabels}>
                <span className={`${styles.detailLabel} ${styles.labelProfile}`}>
                  <SvgIcon name="star" />
                  {album.quality_profile?.name ?? 'No quality profile'}
                </span>
                <span className={styles.detailLabel}>
                  <SvgIcon name="tracks" />
                  {trackProgress(album.tracks_present, album.track_count)} tracks
                </span>
                <span
                  className={`${styles.detailLabel} ${album.monitored ? styles.labelMonitored : styles.labelUnmonitored}`}
                >
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

/** Decides whether the "All Releases" tab should trigger a discography
 *  fetch. Shared by both the explicit toggle click and the mount-time case
 *  (URL already has `releases=all`, e.g. from a bookmark/back-navigation) so
 *  the fetch isn't tied to a click event that may never fire. `alreadyAttempted`
 *  is a per-mode-switch guard: without it, a genuinely-empty provider
 *  discography (count stays 0 after a completed fetch) would re-trigger on
 *  every `discographyBusy` false-transition — an infinite fetch loop. */
export function shouldAutoFetchDiscography(params: {
  discographyCount: number | undefined;
  discographyBusy: boolean;
  alreadyAttempted: boolean;
}): boolean {
  const { discographyCount, discographyBusy, alreadyAttempted } = params;
  if (alreadyAttempted || discographyBusy) return false;
  return discographyCount === 0;
}

/** B4: artist-toolbar decluttering — Preview Retag/Reorganize All/Maintenance/
 *  Manual Import/Enrich are secondary "files & tools" actions, tucked behind
 *  one dropdown instead of five separate buttons next to the Lidarr-core
 *  primary bar (Refresh & Scan/Automatic Search/Interactive Search/Update
 *  Discography). */
function ArtistToolsMenu({
  artistId,
  artistName,
  onRetag,
  onReorganizeAll,
  onMaintenance,
  onManualImport,
}: {
  artistId: number;
  artistName: string;
  onRetag: () => void;
  onReorganizeAll: () => void;
  onMaintenance: () => void;
  onManualImport: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [showSubmenu, setShowSubmenu] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
        setShowSubmenu(false);
      }
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  return (
    <span ref={wrapRef} className={styles.overflowWrap} onClick={(e) => e.stopPropagation()}>
      <ActionButton
        icon="organize"
        label="Files/Tools"
        title="Preview Retag, Reorganize All, Maintenance, Manual Import, Enrich"
        onClick={() => setOpen((v) => !v)}
      />
      {open ? (
        <div className={`${styles.overflowMenu} ${styles.alignLeft}`}>
          <button
            type="button"
            className={styles.overflowMenuItem}
            onClick={() => {
              onRetag();
              setOpen(false);
            }}
          >
            Preview Retag
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            onClick={() => {
              onReorganizeAll();
              setOpen(false);
            }}
          >
            Reorganize All
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            onClick={() => {
              onMaintenance();
              setOpen(false);
            }}
          >
            Maintenance
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            onClick={() => {
              onManualImport();
              setOpen(false);
            }}
          >
            Manual Import
          </button>
          <div
            className={styles.submenuContainer}
            onMouseEnter={() => setShowSubmenu(true)}
            onMouseLeave={() => setShowSubmenu(false)}
          >
            <button
              type="button"
              className={styles.overflowMenuItem}
              onClick={(e) => {
                e.stopPropagation();
                setShowSubmenu((v) => !v);
              }}
            >
              Enrich… <span className={styles.submenuChevron}>›</span>
            </button>
            {showSubmenu ? (
              <EnrichDropdown
                entity="artists"
                entityId={artistId}
                entityName={artistName}
                wrapperRef={wrapRef}
                align="left"
                submenu
                onClose={() => {
                  setShowSubmenu(false);
                  setOpen(false);
                }}
              />
            ) : null}
          </div>
        </div>
      ) : null}
    </span>
  );
}

function ArtistDetailView({ artistId }: { artistId: number }) {
  const navigate = useNavigate();
  const search = Route.useSearch();
  const releasesMode = search.releases;
  const artistQuery = useQuery(libraryV2ArtistQueryOptions(artistId));
  const artist = artistQuery.data;
  const [discographyBusy, setDiscographyBusy] = useState(false);
  const [modalAction, setModalAction] = useState<{
    action: string;
    entity?: Lib2EntityRef;
  } | null>(null);
  const [showMonitoring, setShowMonitoring] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showMaintenance, setShowMaintenance] = useState(false);
  const [showManageTracks, setShowManageTracks] = useState(false);
  const [showReorganizeAll, setShowReorganizeAll] = useState(false);
  const [showEditArtist, setShowEditArtist] = useState(false);
  const [showArtPicker, setShowArtPicker] = useState(false);
  // Album-scoped retag/delete now live inside each album's own
  // AlbumOverflowMenu (B1/B2) — this state is only for the artist-level
  // toolbar's own Preview Retag / Delete buttons.
  const [retagTarget, setRetagTarget] = useState<{
    entity: 'artists';
    id: number;
    title: string;
  } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{
    entity: 'artists';
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
  const attemptedDiscographyFetchRef = useRef(false);

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
  }

  // Auto-fetches the discography for "All Releases" — on an explicit toggle
  // click AND on mount when the URL already has `releases=all` (bookmark,
  // back-navigation), which a click-only handler would never see.
  useEffect(() => {
    if (releasesMode !== 'all') {
      attemptedDiscographyFetchRef.current = false;
      return;
    }
    if (
      shouldAutoFetchDiscography({
        discographyCount: artist?.discography_count,
        discographyBusy,
        alreadyAttempted: attemptedDiscographyFetchRef.current,
      })
    ) {
      attemptedDiscographyFetchRef.current = true;
      void updateDiscography();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [releasesMode, artist?.discography_count, discographyBusy]);

  /** Route a toolbar/row action: Interactive Search opens the window;
   *  Automatic Search (any scope) / per-track Search run the scoped
   *  server-side search (deep-dive C1) — the entity ref decides whether it
   *  searches this one track, this one album, or the whole artist. */
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
    if (SCOPED_SEARCH_RE.test(action)) {
      const scope = resolveSearchScope(entity, artistId);
      setGrabBanner({ tone: 'busy', text: 'Searching…' });
      void runScopedSearch(queryClient, scope.entity, scope.id).then(setGrabBanner);
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
              <ArtistRefreshButton artistId={artistId} />
              <ActionButton
                icon="automatic"
                label="Automatic Search"
                title="Search missing/upgradable tracks for this artist"
                onClick={() => handleAction('Automatic Search')}
              />
              <ActionButton
                icon="interactive"
                label="Interactive Search"
                title="Manually select from search results across all configured sources"
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
              <ArtistToolsMenu
                artistId={artistId}
                artistName={artistName}
                onRetag={() =>
                  setRetagTarget({ entity: 'artists', id: artistId, title: artist.name })
                }
                onReorganizeAll={() => setShowReorganizeAll(true)}
                onMaintenance={() => setShowMaintenance(true)}
                onManualImport={() => void navigate({ to: '/import' })}
              />
            </div>
            <div className={styles.toolbarGroup}>
              <ActionButton
                icon="tracks"
                label="Manage Tracks"
                title="Review single↔album duplicate recordings, files, and their monitor state"
                onClick={() => setShowManageTracks(true)}
              />
              <ActionButton
                icon="history"
                label="History"
                title="Recent downloads recorded for this artist"
                onClick={() => setShowHistory(true)}
              />
              <ActionButton
                icon="edit"
                label="Edit Metadata"
                title="Correct artist metadata without rewriting provider data"
                onClick={() => setShowEditArtist(true)}
              />
              <ActionButton
                icon="cover"
                label="Change Photo"
                title="Pick from alternate artist photos"
                onClick={() => setShowArtPicker(true)}
              />
              <ActionButton
                icon="monitor"
                label="Monitoring"
                title="Apply a monitoring strategy across this artist's releases"
                onClick={() => setShowMonitoring(true)}
              />
              <ActionButton
                icon="star"
                label={`Profile: ${artist.quality_profile?.name ?? 'None'}`}
                title="Change default quality profile for this artist"
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
                <span className={`${styles.detailLabel} ${styles.labelProfile}`}>
                  <SvgIcon name="star" />
                  {artist.quality_profile?.name ?? 'No quality profile'}
                </span>
                <span
                  className={`${styles.detailLabel} ${artist.monitored ? styles.labelMonitored : styles.labelUnmonitored}`}
                >
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
              <ArtistMatchChips artistId={artist.id} artistName={artist.name} />
              <ArtistAliases artistId={artist.id} artistName={artist.name} />
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
          />
          <AlbumGroup
            title="EPs"
            albums={visibleReleases(artist.eps ?? [], releasesMode)}
            artistId={artistId}
            scope="eps"
            onAction={handleAction}
          />
          <AlbumGroup
            title="Singles"
            albums={visibleReleases(artist.singles, releasesMode)}
            artistId={artistId}
            scope="singles"
            onAction={handleAction}
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
          {showReorganizeAll ? (
            <ArtistReorganizeAllModal
              artistId={artistId}
              artistName={artist.name}
              onClose={() => setShowReorganizeAll(false)}
            />
          ) : null}

          {showEditArtist ? (
            <EditArtistModal artist={artist} onClose={() => setShowEditArtist(false)} />
          ) : null}
          {showArtPicker ? (
            <ArtistImagePickerModal
              artistId={artist.id}
              artistName={artist.name}
              onClose={() => setShowArtPicker(false)}
            />
          ) : null}
          {retagTarget ? (
            <RetagModal
              entity={retagTarget.entity}
              id={retagTarget.id}
              title={retagTarget.title}
              onClose={() => setRetagTarget(null)}
            />
          ) : null}
          {deleteTarget ? (
            <DeleteConfirmModal
              entity={deleteTarget.entity}
              id={deleteTarget.id}
              title={deleteTarget.title}
              onDone={() => {
                setDeleteTarget(null);
                void navigate({ search: (p) => ({ ...p, artist: undefined }) });
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

/** Deep-dive C1: run the scoped Automatic Search endpoint for exactly one
 *  artist/album/track and report a banner-ready outcome. Replaces the old
 *  client-side best-pick heuristic (A4) — the server does the searching,
 *  candidate-walking and grabbing through the normal wishlist pipeline. */
async function runScopedSearch(
  queryClient: ReturnType<typeof useQueryClient>,
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
): Promise<{ tone: 'ok' | 'err'; text: string }> {
  try {
    const jobId = await startLibraryV2ScopedSearch(entity, id);
    const error = await awaitBulkJob(queryClient, jobId);
    if (error) return { tone: 'err', text: `Search failed: ${error}` };
    return {
      tone: 'ok',
      text: 'Search started for the monitored missing/upgradable tracks in scope — progress on the Downloads page.',
    };
  } catch (e) {
    return { tone: 'err', text: e instanceof Error ? e.message : 'Search failed' };
  }
}

/** Resolve the scope a fired "Automatic Search" / "Search" action targets:
 *  the entity ref's most specific id wins (track > album), falling back to
 *  the artist the action originated from. */
function resolveSearchScope(
  entity: Lib2EntityRef | undefined,
  fallbackArtistId: number,
): { entity: 'artists' | 'albums' | 'tracks'; id: number } {
  if (entity?.trackId) return { entity: 'tracks', id: entity.trackId };
  if (entity?.albumId) return { entity: 'albums', id: entity.albumId };
  return { entity: 'artists', id: fallbackArtistId };
}

/** Lidarr-style album list: each album is a block whose header expands to reveal
 *  its track table — contained in the block (no fragile nested-table colspans). */
export function SectionBulkMonitorButton({
  artistId,
  scope,
  title,
  allMonitored,
  albumIds,
}: {
  artistId: number;
  scope: 'albums' | 'eps' | 'singles';
  title: string;
  allMonitored: boolean;
  albumIds: number[];
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const targetMonitored = !allMonitored;

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      const jobId = await bulkMonitorLibraryV2Releases(artistId, scope, targetMonitored, albumIds);
      const jobError = await awaitBulkJob(queryClient, jobId);
      if (jobError) throw new Error(jobError);
    } catch (caught) {
      setError(mutationErrorMessage(caught, `Could not update ${title.toLowerCase()}`));
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className={styles.sectionBulkControl}>
      <button
        type="button"
        className={styles.sectionBulk}
        disabled={busy}
        title={
          allMonitored
            ? `Stop monitoring all ${title.toLowerCase()}`
            : `Monitor all ${title.toLowerCase()} (adds missing tracks to Wanted)`
        }
        onClick={() => void apply()}
      >
        <SvgIcon name="monitor" filled={allMonitored} />
        {busy ? 'Working…' : allMonitored ? 'Unmonitor all' : 'Monitor all'}
      </button>
      {error ? (
        <span className={styles.sectionBulkError} role="alert">
          <span>{error}</span>
          <button type="button" className={styles.inlineRetry} onClick={() => void apply()}>
            Retry
          </button>
        </span>
      ) : null}
    </span>
  );
}

function AlbumGroup({
  title,
  albums,
  artistId,
  scope,
  onAction,
}: {
  title: string;
  albums: LibraryV2AlbumSummary[];
  artistId: number;
  scope: 'albums' | 'eps' | 'singles';
  onAction: ActionHandler;
}) {
  if (albums.length === 0) return null;
  const allMonitored = albums.every((a) => a.monitored);

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>
        {title} <span className={styles.sectionCount}>{albums.length}</span>
        <SectionBulkMonitorButton
          artistId={artistId}
          scope={scope}
          title={title}
          allMonitored={allMonitored}
          albumIds={albums.map((album) => album.id)}
        />
      </h2>
      <div className={styles.albumList}>
        {albums.map((album) => (
          <AlbumBlock key={album.id} album={album} onAction={onAction} />
        ))}
      </div>
    </section>
  );
}

function AlbumBlock({
  album,
  onAction,
}: {
  album: LibraryV2AlbumSummary;
  onAction: ActionHandler;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const profilesQuery = useQuery(libraryV2QualityProfilesQueryOptions());
  const profileName =
    (profilesQuery.data ?? []).find((p) => p.id === album.quality_profile_id)?.name ?? null;
  const releaseDate =
    formatReleaseDate(album.release_date) || (album.year ? String(album.year) : null);
  const complete = album.tracks_missing === 0 && album.track_count > 0;
  const pct = album.track_count
    ? clampPercent((100 * album.tracks_present) / album.track_count)
    : 0;
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
          <button
            type="button"
            className={styles.albumHeadTitleLink}
            title="Open album detail"
            onClick={(e) => {
              e.stopPropagation();
              void navigate({ search: (previous) => ({ ...previous, album: album.id }) });
            }}
          >
            {album.title}
          </button>
          <span className={styles.albumHeadBadges}>
            <span className={styles.albumTypeBadge}>{album.album_type}</span>
            {releaseDate ? (
              <span className={styles.albumDateBadge} title="Release date">
                {releaseDate}
              </span>
            ) : null}
            {profileName ? (
              <span className={styles.qualityProfileBadge} title="Quality profile">
                <SvgIcon name="star" />
                {profileName}
              </span>
            ) : null}
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
            icon="automatic"
            title="Automatic Search — search missing/upgradable tracks on this album"
            onClick={() =>
              onAction(`Automatic Search: ${album.title}`, {
                albumId: album.id,
                qualityProfileId: album.quality_profile_id,
              })
            }
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
          <AlbumOverflowMenu
            album={{
              id: album.id,
              title: album.title,
              year: album.year,
              album_type: album.album_type,
              release_date: album.release_date,
              explicit: album.explicit,
              label: album.label,
              style: album.style,
              mood: album.mood,
              user_overrides: album.user_overrides,
              quality_profile_id: album.quality_profile_id,
            }}
          />
        </span>
      </div>
      {open ? <AlbumTrackTable albumId={album.id} resolve={unowned} onAction={onAction} /> : null}
    </div>
  );
}

/** B5 defaults, mirroring core/library2/ui_preferences.py's
 *  DEFAULT_PREFERENCES — used only until the real preferences query lands
 *  (it's cached/fast, so this is a brief flash at most). */
const DEFAULT_TRACK_TABLE_COLUMNS: LibraryV2TrackTableColumns = {
  disc: false,
  artists: true,
  duration: true,
  bpm: true,
  match: true,
  quality: true,
  features: true,
  metadata: true,
  file_path: false,
  play: false,
};

const TRACK_TABLE_COLUMN_LABELS: Record<keyof LibraryV2TrackTableColumns, string> = {
  disc: 'Disc #',
  artists: 'Artists',
  duration: 'Duration',
  bpm: 'BPM',
  match: 'Match',
  quality: 'Quality',
  features: 'Features',
  metadata: 'Metadata',
  file_path: 'File path',
  play: 'Play button',
};

type TrackSortKey = 'number' | 'title' | 'duration' | 'bpm';
type TrackSort = { key: TrackSortKey; dir: 'asc' | 'desc' };

/** Clientside-only (B6) — every field is already in the fetched payload, so
 *  there's no reason to round-trip a sort choice through the server. */
function sortTracks(tracks: LibraryV2Track[], sort: TrackSort | null): LibraryV2Track[] {
  if (!sort) return tracks;
  const dir = sort.dir === 'asc' ? 1 : -1;
  const value = (t: LibraryV2Track): number | string => {
    switch (sort.key) {
      case 'number':
        return t.track_number ?? Number.MAX_SAFE_INTEGER;
      case 'title':
        return (t.title ?? '').toLowerCase();
      case 'duration':
        return t.duration ?? -1;
      case 'bpm':
        return t.bpm ?? -1;
    }
  };
  return [...tracks].sort((a, b) => {
    const av = value(a);
    const bv = value(b);
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return 0;
  });
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSort,
  className,
}: {
  label: string;
  sortKey: TrackSortKey;
  sort: TrackSort | null;
  onSort: (key: TrackSortKey) => void;
  className?: string;
}) {
  const active = sort?.key === sortKey;
  return (
    <th className={className}>
      <button type="button" className={styles.sortableHeader} onClick={() => onSort(sortKey)}>
        {label}
        {active ? (
          <span className={styles.sortIndicator}>{sort?.dir === 'asc' ? '▲' : '▼'}</span>
        ) : null}
      </button>
    </th>
  );
}

/** B5: gear popover to pick which optional columns show and whether to show
 *  every match-provider chip (vs. A8's default of only configured
 *  providers). Persisted server-side so picks survive a reload. */
function useUiPreferencesMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: Parameters<typeof updateLibraryV2UiPreferences>[0]) =>
      updateLibraryV2UiPreferences(patch),
    onSuccess: (preferences) =>
      queryClient.setQueryData([...LIBRARY_V2_QUERY_KEY, 'ui-preferences'], preferences),
  });
}

/** Shared gear-popover column-visibility menu (B5 pattern) — one generic body
 *  reused by both the track table and the artist-overview table (round 5,
 *  D6) instead of two near-identical popovers. `extra` renders additional
 *  non-column toggles (e.g. the track table's "show all match providers"). */
function ColumnsOptionsMenu<K extends string>({
  title,
  columnLabels,
  columns,
  onToggle,
  extra,
}: {
  title: string;
  columnLabels: Record<K, string>;
  columns: Record<K, boolean>;
  onToggle: (key: K) => void;
  extra?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const columnKeys = Object.keys(columnLabels) as K[];

  return (
    <span ref={wrapRef} className={styles.overflowWrap} onClick={(e) => e.stopPropagation()}>
      <IconActionButton icon="settings" title={title} onClick={() => setOpen((v) => !v)} />
      {open ? (
        <div className={`${styles.overflowMenu} ${styles.tableOptionsMenu} ${styles.alignRight}`}>
          <div className={styles.tableOptionsGroupLabel}>Columns</div>
          {columnKeys.map((key) => (
            <label key={key} className={styles.tableOptionsItem}>
              <input type="checkbox" checked={columns[key]} onChange={() => onToggle(key)} />
              {columnLabels[key]}
            </label>
          ))}
          {extra ? (
            <>
              <div className={styles.tableOptionsDivider} />
              {extra}
            </>
          ) : null}
        </div>
      ) : null}
    </span>
  );
}

function TrackTableOptionsMenu({
  columns,
  showAllProviders,
}: {
  columns: LibraryV2TrackTableColumns;
  showAllProviders: boolean;
}) {
  const mutation = useUiPreferencesMutation();
  return (
    <ColumnsOptionsMenu
      title="Table options — columns & match providers"
      columnLabels={TRACK_TABLE_COLUMN_LABELS}
      columns={columns}
      onToggle={(key) => mutation.mutate({ track_table: { columns: { [key]: !columns[key] } } })}
      extra={
        <label className={styles.tableOptionsItem}>
          <input
            type="checkbox"
            checked={showAllProviders}
            onChange={() =>
              mutation.mutate({ track_table: { show_all_match_providers: !showAllProviders } })
            }
          />
          Show all match providers
        </label>
      }
    />
  );
}

/** Round 5 (deep-dive D6): same gear pattern for the artist-overview table's
 *  optional Quality Profile/Genre/Added columns. */
const ARTIST_TABLE_COLUMN_LABELS: Record<keyof LibraryV2ArtistTableColumns, string> = {
  quality_profile: 'Quality Profile',
  genres: 'Genre',
  added: 'Added',
};

function ArtistTableOptionsMenu({ columns }: { columns: LibraryV2ArtistTableColumns }) {
  const mutation = useUiPreferencesMutation();
  return (
    <ColumnsOptionsMenu
      title="Table options — columns"
      columnLabels={ARTIST_TABLE_COLUMN_LABELS}
      columns={columns}
      onToggle={(key) => mutation.mutate({ artist_table: { columns: { [key]: !columns[key] } } })}
    />
  );
}

/** B6 bulk action bar for the track table's row-selection checkboxes.
 *  Deliberate reuse-first: Monitor/ReplayGain fan out the existing
 *  single-track mutations with Promise.all (no new backend), Write Tags
 *  calls the already-multi-track /tags/write job, and Delete reuses the
 *  same ADR-05 file_ids-scoped flow C2 built for the artist Files tab —
 *  just scoped to this album's selected tracks instead of the whole artist. */
function TrackTableBulkBar({
  albumId,
  tracks,
  onClear,
}: {
  albumId: number;
  tracks: LibraryV2Track[];
  onClear: () => void;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [showBulkEdit, setShowBulkEdit] = useState(false);

  const trackIds = tracks.filter((t) => t.id != null).map((t) => t.id as number);
  const fileIds = tracks.map((t) => t.file?.file_id).filter((id): id is number => id != null);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } catch (e) {
      setError(mutationErrorMessage(e, `${label} failed`));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={styles.bulkBar}>
      <span className={styles.bulkBarCount}>{tracks.length} selected</span>
      <button
        type="button"
        className={styles.bulkBarButton}
        disabled={busy !== null}
        onClick={() =>
          void run('Monitor', async () => {
            await Promise.all(trackIds.map((id) => setLibraryV2Monitored('tracks', id, true)));
          })
        }
      >
        {busy === 'Monitor' ? 'Monitoring…' : 'Monitor'}
      </button>
      <button
        type="button"
        className={styles.bulkBarButton}
        disabled={busy !== null}
        onClick={() =>
          void run('Unmonitor', async () => {
            await Promise.all(trackIds.map((id) => setLibraryV2Monitored('tracks', id, false)));
          })
        }
      >
        {busy === 'Unmonitor' ? 'Unmonitoring…' : 'Unmonitor'}
      </button>
      <button
        type="button"
        className={styles.bulkBarButton}
        disabled={busy !== null || trackIds.length === 0}
        onClick={() =>
          void run('Write Tags', async () => {
            const jobId = await writeLibraryV2Tags(trackIds);
            const jobError = await awaitBulkJob(queryClient, jobId);
            if (jobError) throw new Error(jobError);
          })
        }
      >
        {busy === 'Write Tags' ? 'Writing…' : 'Write Tags'}
      </button>
      <button
        type="button"
        className={styles.bulkBarButton}
        disabled={busy !== null || trackIds.length === 0}
        onClick={() =>
          void run('ReplayGain', async () => {
            await Promise.all(trackIds.map((id) => analyzeLibraryV2TrackReplayGain(id)));
          })
        }
      >
        {busy === 'ReplayGain' ? 'Analyzing…' : 'ReplayGain'}
      </button>
      <button
        type="button"
        className={styles.bulkBarButton}
        disabled={busy !== null || trackIds.length === 0}
        onClick={() => setShowBulkEdit(true)}
      >
        Bulk edit…
      </button>
      <button
        type="button"
        className={`${styles.bulkBarButton} ${styles.bulkBarButtonDanger}`}
        disabled={busy !== null || fileIds.length === 0}
        onClick={() => setConfirmingDelete(true)}
      >
        Delete files…
      </button>
      <button type="button" className={styles.bulkBarClear} onClick={onClear}>
        Clear
      </button>
      {error ? (
        <span className={styles.bulkBarError} role="alert">
          {error}
        </span>
      ) : null}
      {confirmingDelete ? (
        <FilesDeleteConfirm
          entity="albums"
          eid={albumId}
          fileIds={fileIds}
          onDone={() => {
            setConfirmingDelete(false);
            onClear();
            void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
          }}
          onCancel={() => setConfirmingDelete(false)}
        />
      ) : null}
      {showBulkEdit ? (
        <BulkEditTracksModal
          trackIds={trackIds}
          onClose={() => setShowBulkEdit(false)}
          onSaved={() => {
            setShowBulkEdit(false);
            onClear();
          }}
        />
      ) : null}
    </div>
  );
}

/** §48 (Rich-Metadata-Edit rest): apply the same style/mood/bpm/explicit
 *  value to every selected track in one go. Unlike the per-track form, there
 *  is no single shared baseline to diff against across a multi-track
 *  selection — so each field is opt-in via its own checkbox ("apply this to
 *  all selected tracks") rather than computed as a diff. Reuses the existing
 *  per-field override endpoint (one PATCH per track per field), the same one
 *  the single-track metadata form already calls — no new backend endpoint. */
export function BulkEditTracksModal({
  trackIds,
  onClose,
  onSaved,
}: {
  trackIds: number[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const [applyStyle, setApplyStyle] = useState(false);
  const [style, setStyle] = useState('');
  const [applyMood, setApplyMood] = useState(false);
  const [mood, setMood] = useState('');
  const [applyBpm, setApplyBpm] = useState(false);
  const [bpm, setBpm] = useState('');
  const [applyExplicit, setApplyExplicit] = useState(false);
  const [explicitFlag, setExplicitFlag] = useState<'yes' | 'no'>('yes');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsedBpm = bpm.trim() === '' ? null : Number(bpm);
  const bpmValid =
    !applyBpm || (parsedBpm !== null && Number.isFinite(parsedBpm) && parsedBpm >= 0);
  const nothingSelected = !applyStyle && !applyMood && !applyBpm && !applyExplicit;

  async function save() {
    setBusy(true);
    setError(null);
    const values: Record<string, unknown> = {};
    if (applyStyle) values.style = style.trim() || null;
    if (applyMood) values.mood = mood.trim() || null;
    if (applyBpm) values.bpm = parsedBpm;
    if (applyExplicit) values.explicit = explicitFlag === 'yes';
    try {
      await Promise.all(
        trackIds.map((id) => updateLibraryV2MetadataOverrides('track', id, values)),
      );
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onSaved();
    } catch (caught) {
      setError(mutationErrorMessage(caught, 'Bulk edit failed'));
      setBusy(false);
    }
  }

  return (
    <ModalShell
      title={`Bulk edit — ${trackIds.length} track${trackIds.length === 1 ? '' : 's'}`}
      onClose={onClose}
    >
      <div className={styles.editRow}>
        <label>
          <input
            type="checkbox"
            checked={applyStyle}
            disabled={busy}
            onChange={(e) => setApplyStyle(e.target.checked)}
          />{' '}
          Style
        </label>
        <input
          className={styles.searchInput}
          aria-label="Style value"
          value={style}
          disabled={busy || !applyStyle}
          onChange={(event) => setStyle(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label>
          <input
            type="checkbox"
            checked={applyMood}
            disabled={busy}
            onChange={(e) => setApplyMood(e.target.checked)}
          />{' '}
          Mood
        </label>
        <input
          className={styles.searchInput}
          aria-label="Mood value"
          value={mood}
          disabled={busy || !applyMood}
          onChange={(event) => setMood(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label>
          <input
            type="checkbox"
            checked={applyBpm}
            disabled={busy}
            onChange={(e) => setApplyBpm(e.target.checked)}
          />{' '}
          BPM
        </label>
        <input
          className={styles.searchInput}
          aria-label="BPM value"
          type="number"
          min={0}
          step="0.1"
          value={bpm}
          disabled={busy || !applyBpm}
          onChange={(event) => setBpm(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label>
          <input
            type="checkbox"
            checked={applyExplicit}
            disabled={busy}
            onChange={(e) => setApplyExplicit(e.target.checked)}
          />{' '}
          Explicit
        </label>
        <select
          className={styles.select}
          aria-label="Explicit value"
          value={explicitFlag}
          disabled={busy || !applyExplicit}
          onChange={(e) => setExplicitFlag(e.target.value as 'yes' | 'no')}
        >
          <option value="yes">Explicit</option>
          <option value="no">Clean</option>
        </select>
      </div>
      {error ? <div className={styles.searchError}>{error}</div> : null}
      <div className={styles.modalActions}>
        <button type="button" className={styles.btnGhost} disabled={busy} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={busy || nothingSelected || !bpmValid}
          onClick={() => void save()}
        >
          {busy
            ? 'Saving…'
            : `Apply to ${trackIds.length} track${trackIds.length === 1 ? '' : 's'}`}
        </button>
      </div>
    </ModalShell>
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
  const matchQuery = useQuery(libraryV2AlbumMatchStatusQueryOptions(albumId));
  const profilesQuery = useQuery(libraryV2QualityProfilesQueryOptions());
  const prefsQuery = useQuery(libraryV2UiPreferencesQueryOptions());
  const album = albumQuery.data;
  const [sort, setSort] = useState<TrackSort | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  if (albumQuery.isLoading || !album) {
    return <div className={styles.inlineLoading}>Loading tracks…</div>;
  }
  const albumMatch = matchQuery.data?.album ?? [];
  const trackMatch = matchQuery.data?.tracks ?? {};
  const profileNameById = new Map((profilesQuery.data ?? []).map((p) => [p.id, p.name]));
  const columns = prefsQuery.data?.track_table.columns ?? DEFAULT_TRACK_TABLE_COLUMNS;
  const showAllProviders = prefsQuery.data?.track_table.show_all_match_providers ?? false;

  const sortedTracks = sortTracks(album.tracks, sort);
  const selectableIds = album.tracks.filter((t) => t.id != null).map((t) => t.id as number);
  const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selected.has(id));
  const selectedTracks = album.tracks.filter((t) => t.id != null && selected.has(t.id as number));

  function toggleSort(key: TrackSortKey) {
    setSort((s) => {
      if (!s || s.key !== key) return { key, dir: 'asc' };
      if (s.dir === 'asc') return { key, dir: 'desc' };
      return null;
    });
  }

  function toggleSelected(id: number) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className={styles.trackTableWrap}>
      <div className={styles.trackTableToolbar}>
        {albumMatch.length > 0 ? (
          <div className={styles.albumMatchRow}>
            <span className={styles.albumMatchLabel}>Matched via</span>
            <MatchChips
              entityType="album"
              entityName={album.title}
              services={albumMatch}
              showAll={showAllProviders}
            />
          </div>
        ) : (
          <span />
        )}
        <TrackTableOptionsMenu columns={columns} showAllProviders={showAllProviders} />
      </div>
      {selected.size > 0 ? (
        <TrackTableBulkBar
          albumId={albumId}
          tracks={selectedTracks}
          onClear={() => setSelected(new Set())}
        />
      ) : null}
      <table className={styles.trackTable}>
        <thead>
          <tr>
            <th className={styles.colCheckbox}>
              <input
                type="checkbox"
                checked={allSelected}
                disabled={selectableIds.length === 0}
                aria-label="Select all tracks"
                onChange={() => setSelected(allSelected ? new Set() : new Set(selectableIds))}
              />
            </th>
            <th className={styles.colMonitor}></th>
            <SortableHeader
              className={styles.colNum}
              label="#"
              sortKey="number"
              sort={sort}
              onSort={toggleSort}
            />
            {columns.disc ? <th className={styles.colNum}>Disc</th> : null}
            <SortableHeader label="Title" sortKey="title" sort={sort} onSort={toggleSort} />
            {columns.artists ? <th>Artists</th> : null}
            {columns.duration ? (
              <SortableHeader
                className={styles.colDuration}
                label="Duration"
                sortKey="duration"
                sort={sort}
                onSort={toggleSort}
              />
            ) : null}
            {columns.bpm ? (
              <SortableHeader
                className={styles.colBpm}
                label="BPM"
                sortKey="bpm"
                sort={sort}
                onSort={toggleSort}
              />
            ) : null}
            {columns.match ? <th>Match</th> : null}
            {columns.quality ? <th>Quality</th> : null}
            {columns.features ? <th className={styles.colFeatures}>Features</th> : null}
            {columns.metadata ? <th>Metadata</th> : null}
            {columns.file_path ? <th>File</th> : null}
            {columns.play ? <th className={styles.colPlay}></th> : null}
            <th className={styles.colActions}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {sortedTracks.map((track, i) => (
            <TrackRow
              key={track.id ?? `missing-${i}`}
              track={track}
              albumTitle={album.title}
              entityBase={{ albumId: album.id, qualityProfileId: album.quality_profile?.id }}
              matchServices={track.id ? (trackMatch[track.id] ?? []) : []}
              profileName={profileNameById.get(track.quality_profile_id) ?? null}
              columns={columns}
              showAllProviders={showAllProviders}
              selected={track.id != null && selected.has(track.id)}
              onToggleSelect={
                track.id != null ? () => toggleSelected(track.id as number) : undefined
              }
              onAction={onAction}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Mirrors core/library2/status.py EXPECTED_TAGS (order = display order). */
const METADATA_TAG_LABELS: Record<string, string> = {
  title: 'Title',
  artist: 'Artist',
  album: 'Album',
  albumartist: 'Album Artist',
  track_number: 'Track #',
  disc_number: 'Disc #',
  year: 'Year',
  genre: 'Genre',
  cover: 'Cover Art',
};
const METADATA_TAG_ORDER = Object.keys(METADATA_TAG_LABELS);

function metadataGapsTooltip(gaps: string[]): string {
  const present = METADATA_TAG_ORDER.filter((tag) => !gaps.includes(tag)).map(
    (tag) => METADATA_TAG_LABELS[tag],
  );
  const missing = gaps.map((tag) => METADATA_TAG_LABELS[tag] ?? tag);
  const parts = [`Present: ${present.length ? present.join(', ') : 'none'}`];
  if (missing.length) parts.push(`Missing: ${missing.join(', ')}`);
  return parts.join(' / ');
}

function TrackRow({
  track,
  albumTitle,
  entityBase,
  matchServices,
  profileName,
  columns,
  showAllProviders,
  selected,
  onToggleSelect,
  onAction,
}: {
  track: LibraryV2Track;
  albumTitle: string;
  entityBase: Lib2EntityRef;
  matchServices: LibraryV2MatchService[];
  profileName: string | null;
  columns: LibraryV2TrackTableColumns;
  showAllProviders: boolean;
  selected: boolean;
  onToggleSelect: (() => void) | undefined;
  onAction: ActionHandler;
}) {
  const missing = track.file_status === 'missing';
  const label = track.title ?? `Track ${track.track_number ?? '?'}`;
  const entity: Lib2EntityRef = { ...entityBase, ...(track.id ? { trackId: track.id } : {}) };
  const [detailTab, setDetailTab] = useState<TrackDetailTab | null>(null);
  return (
    <tr className={missing ? styles.missingRow : styles.staticRow}>
      <td className={styles.colCheckbox}>
        {onToggleSelect ? (
          <input
            type="checkbox"
            checked={selected}
            aria-label={`Select ${label}`}
            onChange={onToggleSelect}
          />
        ) : null}
      </td>
      <td>
        <MonitorToggle
          entity="tracks"
          id={track.id}
          monitored={track.monitored}
          albumId={entityBase.albumId}
          trackNumber={track.track_number ?? undefined}
          discNumber={track.disc_number ?? undefined}
          title={track.title ?? undefined}
        />
      </td>
      <td className={styles.colNum}>{track.track_number ?? '—'}</td>
      {columns.disc ? <td className={styles.colNum}>{track.disc_number ?? '—'}</td> : null}
      <td>
        {/* Legacy parity: present/missing shown inline right after the title. */}
        <span className={styles.trackTitleCell}>
          <span className={missing ? styles.muted : undefined}>{label}</span>
          <InlineFileStatus status={track.file_status} />
        </span>
      </td>
      {columns.artists ? <td>{track.artists.map((a) => a.name).join(', ')}</td> : null}
      {columns.duration ? (
        <td className={styles.colDuration}>{formatDuration(track.duration)}</td>
      ) : null}
      {columns.bpm ? <td className={styles.colBpm}>{track.bpm ?? '—'}</td> : null}
      {columns.match ? (
        <td>
          {matchServices.length > 0 ? (
            <MatchChips
              entityType="track"
              entityName={`${track.artists.map((a) => a.name).join(' ')} ${track.title ?? ''}`.trim()}
              services={matchServices}
              abbreviated
              showAll={showAllProviders}
            />
          ) : (
            <span className={styles.muted}>—</span>
          )}
        </td>
      ) : null}
      {columns.quality ? (
        <td className={styles.qualityText}>
          <span className={styles.qualityCellRow}>
            <QualityDisplay file={track.file} />
            <TrackQualityProfileBadge track={track} />
            {profileName ? (
              <span className={styles.qualityProfileBadge} title="Quality profile for this track">
                <SvgIcon name="star" />
                {profileName}
              </span>
            ) : null}
            <TrackVerificationBadge file={track.file} />
          </span>
        </td>
      ) : null}
      {columns.features ? (
        <td>
          {!missing && track.file ? (
            <span className={styles.featuresDisplay}>
              <TrackReplayGainBadge track={track} />
              <TrackLyricsBadge track={track} onOpenLyrics={() => setDetailTab('lyrics')} />
            </span>
          ) : (
            <span className={styles.muted}>—</span>
          )}
        </td>
      ) : null}
      {columns.metadata ? (
        <td>
          {track.id && !missing ? (
            track.metadata_gaps.length === 0 ? (
              <span className={styles.statusOk} title={metadataGapsTooltip(track.metadata_gaps)}>
                tags ✓
              </span>
            ) : (
              <span className={styles.statusWarn} title={metadataGapsTooltip(track.metadata_gaps)}>
                {track.metadata_gaps.length} tag gaps
              </span>
            )
          ) : (
            <span className={styles.muted}>—</span>
          )}
        </td>
      ) : null}
      {columns.file_path ? (
        <td className={styles.filePathCell} title={track.file?.path ?? undefined}>
          {track.file?.path ?? <span className={styles.muted}>—</span>}
        </td>
      ) : null}
      {columns.play ? (
        <td className={styles.colPlay}>
          <TrackPlayButton
            track={track}
            albumId={entityBase.albumId}
            albumTitle={albumTitle}
            artistName={track.artists.map((a) => a.name).join(', ')}
          />
        </td>
      ) : null}
      <td className={styles.trackActions}>
        <IconActionButton
          icon="automatic"
          title="Automatic Search — search missing/upgradable for this track"
          disabled={!track.id}
          onClick={() => onAction(`Search: ${label} (${albumTitle})`, entity)}
        />
        <IconActionButton
          icon="interactive"
          title="Interactive Search — pick the source yourself"
          disabled={!track.id}
          onClick={() => onAction(`Interactive Search: ${label} (${albumTitle})`, entity)}
        />
        {track.id ? (
          <TrackDetailButton
            track={track}
            albumTitle={albumTitle}
            openTab={detailTab}
            onOpenTab={setDetailTab}
            onClose={() => setDetailTab(null)}
          />
        ) : null}
      </td>
    </tr>
  );
}

/** H1: reuses the Legacy player as-is via the shell bridge (`playLibraryTrack`)
 *  instead of building a new player — library-v2 and Legacy share one
 *  `window`/media bar, so this is the same call Legacy's own row play button
 *  makes. Opt-in column (§36), disabled when there's no file to play. */
export function TrackPlayButton({
  track,
  albumId,
  albumTitle,
  artistName,
}: {
  track: LibraryV2Track;
  albumId: number | undefined;
  albumTitle: string;
  artistName: string;
}) {
  const trackId = track.id;
  const filePath = track.file?.path ?? null;
  const canPlay = trackId != null && filePath != null;
  return (
    <IconActionButton
      icon="play"
      title={canPlay ? 'Play track' : 'No file available'}
      disabled={!canPlay}
      onClick={() => {
        if (trackId == null || filePath == null) return;
        void getShellBridge()?.playLibraryTrack(
          {
            id: trackId,
            title: track.title ?? 'Unknown Track',
            file_path: filePath,
            bitrate: track.file?.bitrate ?? null,
            artist_id: track.artists[0]?.id ?? null,
            album_id: albumId ?? null,
          },
          albumTitle,
          artistName,
        );
      }}
    />
  );
}

/** RG badge (deep-dive B3): always rendered — green when present, grey when
 *  missing and clickable to analyze + write it on the spot. Replaces the
 *  separate ReplayGain action button; a `mutation.isError` note surfaces
 *  inline instead of a silent failed icon. */
export function TrackReplayGainBadge({ track }: { track: LibraryV2Track }) {
  const queryClient = useQueryClient();
  const hasRg = Boolean(track.file?.has_replaygain);
  const mutation = useMutation({
    mutationFn: () => analyzeLibraryV2TrackReplayGain(track.id as number),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
  });
  if (hasRg) {
    return (
      <span
        className={`${styles.featureTag} ${styles.featureRg}`}
        title="ReplayGain is written to this track"
      >
        RG
      </span>
    );
  }
  return (
    <button
      type="button"
      className={`${styles.featureTag} ${styles.featureMissing}`}
      disabled={mutation.isPending || !track.id}
      title={
        mutation.isError
          ? mutationErrorMessage(mutation.error, 'ReplayGain analysis failed')
          : 'Analyze + write ReplayGain for this track'
      }
      onClick={() => mutation.mutate()}
    >
      {mutation.isPending ? '…' : 'RG'}
    </button>
  );
}

/** LR badge (deep-dive B3): green + present opens the Lyrics tab of the track
 *  detail modal; grey + missing fetches lyrics from LRClib on the spot. */
export function TrackLyricsBadge({
  track,
  onOpenLyrics,
}: {
  track: LibraryV2Track;
  onOpenLyrics: () => void;
}) {
  const queryClient = useQueryClient();
  const hasLyrics = Boolean(track.file?.has_lyrics);
  const mutation = useMutation({
    mutationFn: () => fetchLibraryV2TrackLyrics(track.id as number),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
  });
  if (hasLyrics) {
    return (
      <button
        type="button"
        className={`${styles.featureTag} ${styles.featureLr}`}
        title="Lyrics are embedded in this track — click to view"
        onClick={onOpenLyrics}
      >
        LR
      </button>
    );
  }
  return (
    <button
      type="button"
      className={`${styles.featureTag} ${styles.featureMissing}`}
      disabled={mutation.isPending || !track.id}
      title={
        mutation.isError
          ? mutationErrorMessage(mutation.error, 'Lyrics fetch failed')
          : 'Fetch lyrics from LRClib for this track'
      }
      onClick={() => mutation.mutate()}
    >
      {mutation.isPending ? '…' : 'LR'}
    </button>
  );
}

/** Legacy parity: present/missing indicator that sits inline after the title. */
function InlineFileStatus({ status }: { status: LibraryV2Track['file_status'] }) {
  if (status === 'duplicate_single')
    return <span className={styles.inlineDuplicate}>also on album</span>;
  return null;
}

/** Per-track details, consolidated behind one button: Quality profile (the
 *  default/first tab — the most common reason to open this), Metadata edit,
 *  and Info (source/download history). Keeps the row from getting crowded
 *  with a separate icon per action. ``openTab``/``onOpenTab`` are lifted to
 *  the row so the LR badge (deep-dive B3) can jump straight to the Lyrics
 *  tab of the SAME modal instead of opening a second one. */
function TrackDetailButton({
  track,
  albumTitle,
  openTab,
  onOpenTab,
  onClose,
}: {
  track: LibraryV2Track;
  albumTitle: string;
  openTab: TrackDetailTab | null;
  onOpenTab: (tab: TrackDetailTab) => void;
  onClose: () => void;
}) {
  if (!track.id) return null;
  return (
    <>
      <IconActionButton
        icon="info"
        title="Track details — quality profile, metadata, source info"
        onClick={() => onOpenTab('quality')}
      />
      {openTab ? (
        <TrackDetailModal
          key={openTab}
          track={track}
          albumTitle={albumTitle}
          initialTab={openTab}
          onClose={onClose}
        />
      ) : null}
    </>
  );
}

type TrackDetailTab = 'quality' | 'metadata' | 'tags' | 'lyrics' | 'info';

const TRACK_DETAIL_TAB_LABELS: Record<TrackDetailTab, string> = {
  quality: 'Quality',
  metadata: 'Metadata',
  tags: 'Tags',
  lyrics: 'Lyrics',
  info: 'Info',
};

function TrackDetailModal({
  track,
  albumTitle,
  initialTab = 'quality',
  onClose,
}: {
  track: LibraryV2Track;
  albumTitle: string;
  initialTab?: TrackDetailTab;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<TrackDetailTab>(initialTab);
  const trackId = track.id as number; // TrackDetailButton only renders when track.id is set
  // Tags + Lyrics share one live file read; fetch once, lazily, on first visit
  // to either tab (avoids a mutagen file read for every track detail open).
  const fileTagsQuery = useQuery(
    libraryV2TrackFileTagsQueryOptions(trackId, tab === 'tags' || tab === 'lyrics'),
  );
  return (
    <ModalShell title={track.title ?? albumTitle} detail onClose={onClose}>
      <div className={styles.detailTabs}>
        {(['quality', 'metadata', 'tags', 'lyrics', 'info'] as const).map((t) => (
          <button
            key={t}
            type="button"
            className={`${styles.detailTab} ${tab === t ? styles.detailTabActive : ''}`}
            onClick={() => setTab(t)}
          >
            {TRACK_DETAIL_TAB_LABELS[t]}
          </button>
        ))}
      </div>
      <div className={styles.tabBody}>
        {tab === 'quality' ? (
          <QualityProfilePicker
            entity="tracks"
            id={trackId}
            currentProfileId={track.quality_profile_id}
            onSaved={onClose}
          />
        ) : null}
        {tab === 'metadata' ? <TrackMetadataForm track={track} onSaved={onClose} /> : null}
        {tab === 'tags' ? <TrackTagsPanel query={fileTagsQuery} trackId={trackId} /> : null}
        {tab === 'lyrics' ? <TrackLyricsPanel query={fileTagsQuery} /> : null}
        {tab === 'info' ? (
          <TrackInfoPanel
            trackId={trackId}
            trackTitle={track.title ?? albumTitle}
            trackArtist={track.artists.map((a) => a.name).join(', ')}
            file={track.file}
          />
        ) : null}
      </div>
    </ModalShell>
  );
}

function TrackMetadataForm({ track, onSaved }: { track: LibraryV2Track; onSaved: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(track.title ?? '');
  const [trackNumber, setTrackNumber] = useState(
    track.track_number === null ? '' : String(track.track_number),
  );
  const [discNumber, setDiscNumber] = useState(
    track.disc_number === null ? '' : String(track.disc_number),
  );
  const [bpm, setBpm] = useState(track.bpm === null ? '' : String(track.bpm));
  const [explicitFlag, setExplicitFlag] = useState<'' | 'yes' | 'no'>(
    track.explicit === true ? 'yes' : track.explicit === false ? 'no' : '',
  );
  const [style, setStyle] = useState(track.style ?? '');
  const [mood, setMood] = useState(track.mood ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { values, valid } = computeTrackEditValues(
    {
      title: track.title,
      track_number: track.track_number,
      disc_number: track.disc_number,
      bpm: track.bpm,
      explicit: track.explicit,
      style: track.style,
      mood: track.mood,
    },
    { title, trackNumber, discNumber, bpm, explicitFlag, style, mood },
  );
  const overrides = track.user_overrides ?? {};
  const resettable = [
    'title',
    'track_number',
    'disc_number',
    'bpm',
    'explicit',
    'style',
    'mood',
  ].filter((field) => field in overrides);

  async function save(valuesToSet: Record<string, unknown>, clear: string[] = []) {
    if (!track.id) return;
    setBusy(true);
    setError(null);
    try {
      await updateLibraryV2MetadataOverrides('track', track.id, valuesToSet, clear);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onSaved();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Edit failed');
      setBusy(false);
    }
  }

  return (
    <>
      <div className={styles.editRow}>
        <label htmlFor="lib2-track-title">Title</label>
        <input
          id="lib2-track-title"
          className={styles.searchInput}
          value={title}
          disabled={busy}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-track-number">Track number</label>
        <input
          id="lib2-track-number"
          className={styles.searchInput}
          type="number"
          min={0}
          value={trackNumber}
          disabled={busy}
          onChange={(event) => setTrackNumber(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-track-disc">Disc number</label>
        <input
          id="lib2-track-disc"
          className={styles.searchInput}
          type="number"
          min={0}
          value={discNumber}
          disabled={busy}
          onChange={(event) => setDiscNumber(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-track-bpm">BPM</label>
        <input
          id="lib2-track-bpm"
          className={styles.searchInput}
          type="number"
          min={0}
          step="0.1"
          value={bpm}
          disabled={busy}
          onChange={(event) => setBpm(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-track-explicit">Explicit</label>
        <select
          id="lib2-track-explicit"
          className={styles.select}
          value={explicitFlag}
          disabled={busy}
          onChange={(e) => setExplicitFlag(e.target.value as '' | 'yes' | 'no')}
        >
          <option value="">Unknown</option>
          <option value="yes">Explicit</option>
          <option value="no">Clean</option>
        </select>
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-track-style">Style</label>
        <input
          id="lib2-track-style"
          className={styles.searchInput}
          value={style}
          disabled={busy}
          onChange={(event) => setStyle(event.target.value)}
        />
      </div>
      <div className={styles.editRow}>
        <label htmlFor="lib2-track-mood">Mood</label>
        <input
          id="lib2-track-mood"
          className={styles.searchInput}
          value={mood}
          disabled={busy}
          onChange={(event) => setMood(event.target.value)}
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
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={busy || !valid || Object.keys(values).length === 0}
          onClick={() => void save(values)}
        >
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
      {track.id && track.file ? <TrackWriteTagsButton trackId={track.id} /> : null}
    </>
  );
}

/** §18.2: write this track's library metadata into its file tags on demand
 *  (legacy `col-writetag` parity). Reuses the same bulk write endpoint +
 *  polling helper as RetagModal, scoped to a single track. */
function TrackWriteTagsButton({ trackId }: { trackId: number }) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async () => {
      setMessage('Writing tags…');
      const jobId = await writeLibraryV2Tags([trackId]);
      const jobError = await awaitBulkJob(queryClient, jobId);
      if (jobError) throw new Error(jobError);
    },
    onSuccess: () => setMessage('Tags written to file.'),
    onError: (err) => setMessage(mutationErrorMessage(err, 'Write failed')),
  });
  return (
    <div className={styles.formDivider}>
      <ActionButton
        icon="retag"
        label={mutation.isPending ? 'Writing…' : 'Write Tags to File'}
        title="Write this track's library metadata into the audio file's tags"
        busy={mutation.isPending}
        onClick={() => mutation.mutate()}
      />
      {message ? (
        <span className={mutation.isError ? styles.sourceInfoError : styles.muted}>{message}</span>
      ) : null}
    </div>
  );
}

// --- Tags + Lyrics tabs: live embedded-tag inspector (§18.1) ---------------
// Mirrors the legacy Audit Trail modal's tag grid/lyrics render
// (webui/static/wishlist-tools.js: _renderEmbeddedTagsGrid / _renderLyricsBody)
// against the same `read_embedded_tags` shape, ported to React.

const FILE_TAG_LABELS: Record<string, string> = {
  title: 'Title',
  artist: 'Artist',
  artists: 'All Artists',
  albumartist: 'Album Artist',
  album_artist: 'Album Artist',
  album: 'Album',
  date: 'Date',
  year: 'Year',
  originaldate: 'Original Date',
  genre: 'Genre',
  mood: 'Mood',
  style: 'Style',
  tracknumber: 'Track #',
  tracktotal: 'Total Tracks',
  discnumber: 'Disc #',
  totaldiscs: 'Total Discs',
  bpm: 'BPM',
  isrc: 'ISRC',
  barcode: 'Barcode',
  catalognumber: 'Catalog #',
  asin: 'ASIN',
  copyright: 'Copyright',
  publisher: 'Publisher',
  language: 'Language',
  script: 'Script',
  media: 'Media',
  releasetype: 'Release Type',
  releasestatus: 'Release Status',
  releasecountry: 'Country',
  composer: 'Composer',
  performer: 'Performer',
  quality: 'Quality',
  replaygain_track_gain: 'Track Gain',
  replaygain_track_peak: 'Track Peak',
  replaygain_album_gain: 'Album Gain',
  replaygain_album_peak: 'Album Peak',
};

const FILE_TAG_TRACK_KEYS = [
  'title',
  'artist',
  'artists',
  'tracknumber',
  'tracktotal',
  'discnumber',
  'totaldiscs',
  'bpm',
  'isrc',
];
const FILE_TAG_ALBUM_KEYS = [
  'album',
  'album_artist',
  'albumartist',
  'date',
  'year',
  'originaldate',
  'genre',
  'mood',
  'style',
  'copyright',
  'publisher',
  'language',
  'script',
  'media',
  'releasetype',
  'releasestatus',
  'releasecountry',
  'barcode',
  'catalognumber',
  'asin',
];
const FILE_TAG_REPLAYGAIN_KEYS = [
  'replaygain_track_gain',
  'replaygain_track_peak',
  'replaygain_album_gain',
  'replaygain_album_peak',
];
const FILE_TAG_LYRICS_KEYS = ['lyrics', 'unsyncedlyrics'];
const FILE_TAG_SOURCE_SERVICES = [
  { name: 'MusicBrainz', prefix: 'musicbrainz_' },
  { name: 'Spotify', prefix: 'spotify_' },
  { name: 'Tidal', prefix: 'tidal_' },
  { name: 'Deezer', prefix: 'deezer_' },
  { name: 'AudioDB', prefix: 'audiodb_' },
  { name: 'iTunes', prefix: 'itunes_' },
  { name: 'JioSaavn', prefix: 'jiosaavn_' },
  { name: 'Genius', prefix: 'genius_' },
  { name: 'Last.fm', prefix: 'lastfm_' },
];

function fileTagLabel(key: string): string {
  if (FILE_TAG_LABELS[key]) return FILE_TAG_LABELS[key];
  return key
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

function isSourceIdTagKey(key: string): boolean {
  return /(_id|_url)$/.test(key) || key.startsWith('musicbrainz_');
}

type FileTagsQuery = UseQueryResult<LibraryV2FileTags>;

function TrackTagsPanel({ query, trackId }: { query: FileTagsQuery; trackId: number }) {
  const queryClient = useQueryClient();
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState<string>('');
  const [isAdding, setIsAdding] = useState(false);
  const [newKey, setNewKey] = useState('');
  const [newValue, setNewValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  const editMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      editTrackFileTag(trackId, key, value),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...LIBRARY_V2_QUERY_KEY, 'track-file-tags', trackId],
      });
      setEditingKey(null);
      setIsAdding(false);
      setNewKey('');
      setNewValue('');
      setError(null);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : 'Failed to save tag');
    },
  });

  if (query.isLoading) {
    return <div className={styles.inlineLoading}>Reading tags from file…</div>;
  }
  if (query.isError) {
    return (
      <p className={styles.sourceInfoError}>
        {query.error instanceof Error ? query.error.message : 'Could not read file tags.'}
      </p>
    );
  }
  const data = query.data;
  if (!data || data.available === false) {
    return <p>{data?.reason || 'File tags not available.'}</p>;
  }
  const tags = data.tags ?? {};
  const buckets: {
    track: [string, string][];
    album: [string, string][];
    replaygain: [string, string][];
    source: Record<string, [string, string][]>;
    other: [string, string][];
  } = {
    track: [],
    album: [],
    replaygain: [],
    source: {},
    other: [],
  };
  Object.keys(tags)
    .sort()
    .forEach((key) => {
      const value = tags[key];
      if (!value || FILE_TAG_LYRICS_KEYS.includes(key)) return;
      if (FILE_TAG_TRACK_KEYS.includes(key)) buckets.track.push([key, value]);
      else if (FILE_TAG_ALBUM_KEYS.includes(key)) buckets.album.push([key, value]);
      else if (FILE_TAG_REPLAYGAIN_KEYS.includes(key)) buckets.replaygain.push([key, value]);
      else if (isSourceIdTagKey(key)) {
        const svc = FILE_TAG_SOURCE_SERVICES.find((s) => key.startsWith(s.prefix));
        const slot = svc ? svc.name : 'Other Sources';
        (buckets.source[slot] ??= []).push([key, value]);
      } else {
        buckets.other.push([key, value]);
      }
    });

  const handleStartEdit = (key: string, value: string) => {
    setEditingKey(key);
    setEditingValue(value);
    setError(null);
  };

  const handleSave = (key: string, value: string) => {
    const k = key.trim();
    if (!k) {
      setError('Tag key cannot be empty');
      return;
    }
    editMutation.mutate({ key: k, value });
  };

  const section = (title: string, entries: [string, string][]) =>
    entries.length === 0 ? null : (
      <div key={title}>
        <p className={styles.sourceInfoHistory}>{title}</p>
        <div className={styles.sourceInfoBody}>
          {entries.map(([key, value]) => {
            const isEditing = editingKey === key;
            if (isEditing) {
              return (
                <div key={key} className={styles.tagEditInline}>
                  <div className={styles.tagEditHeader}>
                    <span className={styles.tagEditLabel}>{fileTagLabel(key)}</span>
                  </div>
                  <div className={styles.tagEditForm}>
                    <input
                      type="text"
                      className={styles.tagEditInput}
                      value={editingValue}
                      onChange={(e) => setEditingValue(e.target.value)}
                      disabled={editMutation.isPending}
                      autoFocus
                    />
                    <div className={styles.tagEditActions}>
                      <button
                        type="button"
                        className={styles.btnTagSave}
                        disabled={editMutation.isPending}
                        onClick={() => handleSave(key, editingValue)}
                      >
                        {editMutation.isPending ? 'Saving…' : 'Save'}
                      </button>
                      <button
                        type="button"
                        className={styles.btnTagCancel}
                        disabled={editMutation.isPending}
                        onClick={() => setEditingKey(null)}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        className={styles.btnTagDelete}
                        disabled={editMutation.isPending}
                        onClick={() => handleSave(key, '')}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </div>
              );
            }
            return (
              <div
                key={key}
                className={`${styles.sourceInfoRow} ${styles.tagRowClickable}`}
                title="Click to edit tag"
                onClick={() => handleStartEdit(key, value)}
              >
                <span className={styles.sourceInfoLabel}>{fileTagLabel(key)}</span>
                <span className={styles.sourceInfoValue}>
                  {value}
                  <span className={styles.editIndicator}>
                    <SvgIcon name="edit" />
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      </div>
    );

  return (
    <div className={styles.sourceInfoBody}>
      {error ? (
        <div className={styles.searchError} style={{ margin: '8px 0' }}>
          {error}
        </div>
      ) : null}
      <SourceInfoRow label="Format" value={data.format ?? '—'} />
      {data.bitrate ? (
        <SourceInfoRow label="Bitrate" value={`${Math.round(data.bitrate / 1000)} kbps`} />
      ) : null}
      {data.duration ? (
        <SourceInfoRow
          label="Duration"
          value={`${Math.floor(data.duration / 60)}:${String(Math.round(data.duration % 60)).padStart(2, '0')}`}
        />
      ) : null}
      <SourceInfoRow label="Cover Art" value={data.has_picture ? 'Embedded' : 'None'} />
      {section('Track', buckets.track)}
      {section('Album', buckets.album)}
      {section('ReplayGain', buckets.replaygain)}
      {[...FILE_TAG_SOURCE_SERVICES.map((s) => s.name), 'Other Sources'].map((name) =>
        section(name, buckets.source[name] ?? []),
      )}
      {section('Other', buckets.other)}
      {buckets.track.length +
        buckets.album.length +
        buckets.replaygain.length +
        buckets.other.length ===
        0 && Object.keys(buckets.source).length === 0 ? (
        <p className={styles.muted}>No readable tags embedded in this file.</p>
      ) : null}

      {isAdding ? (
        <div className={styles.tagAddPanel}>
          <div className={styles.tagAddTitle}>Add Custom Tag</div>
          <div className={styles.tagAddInputs}>
            <input
              type="text"
              placeholder="Tag name (e.g. genre, bpm)"
              className={`${styles.tagEditInput} ${styles.tagAddInputKey}`}
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              disabled={editMutation.isPending}
            />
            <input
              type="text"
              placeholder="Value"
              className={`${styles.tagEditInput} ${styles.tagAddInputValue}`}
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              disabled={editMutation.isPending}
            />
          </div>
          <div
            className={styles.tagEditActions}
            style={{ justifyContent: 'flex-end', marginTop: '4px' }}
          >
            <button
              type="button"
              className={styles.btnTagSave}
              disabled={editMutation.isPending || !newKey.trim() || !newValue.trim()}
              onClick={() => handleSave(newKey, newValue)}
            >
              {editMutation.isPending ? 'Adding…' : 'Add Tag'}
            </button>
            <button
              type="button"
              className={styles.btnTagCancel}
              disabled={editMutation.isPending}
              onClick={() => {
                setIsAdding(false);
                setError(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className={styles.tagAddBtnContainer}>
          <button
            type="button"
            className={styles.btnTagAdd}
            onClick={() => {
              setIsAdding(true);
              setError(null);
            }}
          >
            <span>+</span> Add Custom Tag
          </button>
        </div>
      )}
    </div>
  );
}

function TrackLyricsPanel({ query }: { query: FileTagsQuery }) {
  if (query.isLoading) {
    return <div className={styles.inlineLoading}>Reading lyrics from file…</div>;
  }
  if (query.isError) {
    return (
      <p className={styles.sourceInfoError}>
        {query.error instanceof Error ? query.error.message : 'Could not read file tags.'}
      </p>
    );
  }
  const data = query.data;
  if (!data || data.available === false) {
    return <p>{data?.reason || 'File tags not available.'}</p>;
  }
  const text = data.tags?.lyrics || data.tags?.unsyncedlyrics || '';
  if (!text.trim()) {
    return <p className={styles.muted}>No lyrics embedded in this file.</p>;
  }
  return <div className={styles.lyricsText}>{text}</div>;
}

const SOURCE_SERVICE_LABELS: Record<string, string> = {
  soulseek: 'Soulseek',
  youtube: 'YouTube',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  hifi: 'HiFi',
  deezer: 'Deezer',
  lidarr: 'Lidarr',
  amazon: 'Amazon Music',
  soundcloud: 'SoundCloud',
  auto_import: 'Auto-Import',
  staging: 'Staging',
  torrent: 'Torrent',
  usenet: 'Usenet',
};

function sourceServiceLabel(service: string | null): string {
  if (!service) return 'Unknown';
  return SOURCE_SERVICE_LABELS[service] ?? service;
}

function baseFileName(name: string | null): string {
  if (!name) return 'Unknown';
  return name.replace(/\\/g, '/').split('/').pop() || name;
}

function SourceInfoRow({
  label,
  value,
  mono,
  danger,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
  danger?: boolean;
}) {
  return (
    <div className={styles.sourceInfoRow}>
      <span className={styles.sourceInfoLabel}>{label}</span>
      <span
        className={`${styles.sourceInfoValue} ${mono ? styles.sourceInfoMono : ''}`}
        style={danger ? { color: 'rgb(248, 113, 113)' } : undefined}
      >
        {value}
      </span>
    </div>
  );
}

const MANUAL_SKIP_CHECK_LABELS: Record<string, string> = {
  acoustid: 'AcoustID',
  quality: 'Quality gate',
};

/** §18.3: what checks this file went through — the verification badge's own
 *  tooltip already spells out the AcoustID pass/skip/bypass result, so this
 *  panel adds the piece the badge can't show: which checks were explicitly,
 *  manually overridden, when, and why. */
function TrackLifecycleSection({
  file,
  manualSkips,
}: {
  file: LibraryV2TrackFile | null | undefined;
  manualSkips: LibraryV2ManualSkip[];
}) {
  if (!file?.verification_status && manualSkips.length === 0) return null;
  return (
    <div className={styles.sourceInfoBody}>
      {file?.verification_status ? (
        <SourceInfoRow label="Verification" value={<TrackVerificationBadge file={file} />} />
      ) : null}
      {manualSkips.map((skip) => (
        <SourceInfoRow
          key={skip.id}
          label="Manual override"
          value={`${skip.skipped_checks.map((c) => MANUAL_SKIP_CHECK_LABELS[c] ?? c).join(', ') || 'unknown check'} skipped${skip.created_at ? ` — ${skip.created_at.slice(0, 16).replace('T', ' ')}` : ''}`}
        />
      ))}
    </div>
  );
}

/** Info tab: verification/lifecycle summary + current source (with blacklist)
 *  + the full download history — every past provenance record for this
 *  track, not just the latest. */
function TrackInfoPanel({
  trackId,
  trackTitle,
  trackArtist,
  file,
}: {
  trackId: number;
  trackTitle: string;
  trackArtist: string;
  file: LibraryV2TrackFile | null | undefined;
}) {
  const query = useQuery(libraryV2TrackSourceInfoQueryOptions(trackId, true));
  const rows = query.data?.downloads ?? [];
  const manualSkips = query.data?.manual_skips ?? [];
  const dl = rows[0];
  const blacklist = useMutation({
    mutationFn: () =>
      blacklistLibraryV2Source({
        track_title: dl?.track_title || trackTitle,
        track_artist: dl?.track_artist || trackArtist,
        blocked_filename: dl?.source_filename || '',
        blocked_username: dl?.source_username || '',
      }),
  });

  const lifecycle = <TrackLifecycleSection file={file} manualSkips={manualSkips} />;

  if (query.isLoading) {
    return (
      <>
        {lifecycle}
        <div className={styles.inlineLoading}>Loading source info…</div>
      </>
    );
  }
  if (!dl) {
    return (
      <>
        {lifecycle}
        <p>
          No download source data for this track yet. Source tracking starts with new downloads.
        </p>
      </>
    );
  }

  const audioParts = [
    dl.bit_depth ? `${dl.bit_depth}-bit` : null,
    dl.sample_rate ? `${(dl.sample_rate / 1000).toFixed(1)} kHz` : null,
    dl.bitrate ? `${Math.round(dl.bitrate / 1000)} kbps` : null,
  ].filter(Boolean);

  return (
    <div className={styles.sourceInfoBody}>
      {lifecycle}
      <SourceInfoRow label="Service" value={sourceServiceLabel(dl.source_service)} />
      {dl.source_service === 'soulseek' && dl.source_username ? (
        <SourceInfoRow label="User" value={dl.source_username} mono />
      ) : null}
      <SourceInfoRow label="Original File" value={baseFileName(dl.source_filename)} mono />
      {dl.source_size ? (
        <SourceInfoRow label="Size" value={`${(dl.source_size / 1048576).toFixed(1)} MB`} />
      ) : null}
      {dl.audio_quality ? <SourceInfoRow label="Quality" value={dl.audio_quality} /> : null}
      {audioParts.length ? <SourceInfoRow label="Audio" value={audioParts.join(' · ')} /> : null}
      {dl.created_at ? (
        <SourceInfoRow label="Downloaded" value={dl.created_at.slice(0, 16).replace('T', ' ')} />
      ) : null}
      {dl.status && dl.status !== 'completed' ? (
        <SourceInfoRow label="Status" value={dl.status} danger />
      ) : null}
      {dl.source_username && dl.source_filename ? (
        <div className={styles.modalActions}>
          <ActionButton
            icon="delete"
            tone="danger"
            busy={blacklist.isPending}
            disabled={blacklist.isSuccess}
            label={blacklist.isSuccess ? 'Blacklisted' : 'Blacklist This Source'}
            title="Skip this source in future downloads"
            onClick={() => blacklist.mutate()}
          />
        </div>
      ) : null}
      {blacklist.isError ? (
        <p className={styles.sourceInfoError} role="alert">
          {mutationErrorMessage(blacklist.error, 'Failed to blacklist source')}
        </p>
      ) : null}
      {rows.length > 1 ? (
        <div className={styles.trackHistoryWrap}>
          <p className={styles.sourceInfoHistory}>History — {rows.length} download records</p>
          <table className={styles.trackTable}>
            <thead>
              <tr>
                <th>Date</th>
                <th>Service</th>
                <th>User</th>
                <th>File</th>
                <th>Quality</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.id ?? i}>
                  <td className={styles.muted}>
                    {r.created_at ? r.created_at.slice(0, 16).replace('T', ' ') : '—'}
                  </td>
                  <td>{sourceServiceLabel(r.source_service)}</td>
                  <td className={styles.sourceInfoMono}>{r.source_username ?? '—'}</td>
                  <td title={r.source_filename ?? undefined}>{baseFileName(r.source_filename)}</td>
                  <td className={styles.qualityText}>
                    {[
                      r.bit_depth ? `${r.bit_depth}-bit` : null,
                      r.sample_rate ? `${(r.sample_rate / 1000).toFixed(1)} kHz` : null,
                      r.bitrate ? `${Math.round(r.bitrate / 1000)} kbps` : null,
                    ]
                      .filter(Boolean)
                      .join(' · ') || '—'}
                  </td>
                  <td>{r.status ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

export function TrackQualityProfileBadge({ track }: { track: LibraryV2Track }) {
  if (!track.file) return null;
  if (track.meets_profile === false) {
    return (
      <span className={styles.qBelow} title="Below the album's quality profile">
        below profile
      </span>
    );
  }
  if (track.upgrade_candidate === true) {
    return (
      <span className={styles.qUpgrade} title="A higher-quality version may be available">
        upgrade ↑
      </span>
    );
  }
  if (track.meets_profile === null) {
    return (
      <span className={styles.qUnknown} title="Scan the file to evaluate its quality profile">
        quality unknown
      </span>
    );
  }
  return null;
}

function BackLink({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" className={styles.backLink} onClick={onClick}>
      {children}
    </button>
  );
}

export async function waitForLibraryV2Import(
  maxPolls = 600,
  pollIntervalMs = 1000,
): Promise<Awaited<ReturnType<typeof fetchLibraryV2ImportStatus>>> {
  for (let i = 0; i < maxPolls; i += 1) {
    const state = await fetchLibraryV2ImportStatus();
    if (!state.running) return state;
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  throw new Error('Timed out waiting for the library import');
}

/** Deep-dive B7: the manual global upgrade scan used to live in every
 *  artist's toolbar (misleadingly, since it always scanned the whole
 *  catalog). Scoped Automatic Search (C1) covers per-artist/-album/-track
 *  upgrades now, so the global variant only makes sense here, at the
 *  library-overview level, next to Import. */
function UpgradeScanButton() {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function runScan() {
    setBusy(true);
    setMessage('Scanning…');
    try {
      const jobId = await startLibraryV2UpgradeScan();
      const error = await awaitBulkJob(queryClient, jobId);
      setMessage(error ? `Failed: ${error}` : 'Upgrade scan finished — candidates queued.');
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Upgrade scan failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className={styles.importWrap}>
      <button
        type="button"
        className={styles.btnGhost}
        disabled={busy}
        title="Scan the entire Library v2 catalog and queue monitored tracks below their quality-profile cutoff"
        onClick={() => void runScan()}
      >
        {busy ? 'Scanning…' : 'Search Upgrades'}
      </button>
      {message ? <span className={styles.importMsg}>{message}</span> : null}
    </span>
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
      const state = await waitForLibraryV2Import();
      setMessage(state.error ? `Failed: ${state.error}` : 'Imported — refreshing…');
      if (!state.error) window.location.reload();
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
