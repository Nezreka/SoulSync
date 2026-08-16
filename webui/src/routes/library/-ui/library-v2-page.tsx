import { Tooltip } from '@base-ui/react/tooltip';
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useNavigate as useRouterNavigate } from '@tanstack/react-router';
import {
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { DialogFrame, DialogHeader } from '@/components/dialog';
import { thumb } from '@/platform/artwork-thumb';
import { getShellBridge } from '@/platform/shell/bridge';
import { useReactPageShell } from '@/platform/shell/route-controllers';

import {
  analyzeLibraryV2TrackReplayGain,
  blacklistLibraryV2Source,
  fetchArtistDiscographyGapFill,
  fetchArtistHeroStats,
  fetchArtistTopTracks,
  fetchLibraryV2DiscoveryTrackStatus,
  fetchProviderArtistDetail,
  materializeLibraryV2DiscoveryArtist,
  materializeLibraryV2DiscoveryTrack,
  resolveLibraryV2DiscoveryArtist,
  type ArtistTopTrack,
  type ProviderRelease,
  bulkMonitorLibraryV2Releases,
  clearLibraryV2EntityMatch,
  deleteLibraryV2Entity,
  deleteLibraryV2Files,
  editLibraryV2Artist,
  editTrackFileTag,
  enrichLibraryV2Entity,
  fetchLibraryV2AlbumHistory,
  fetchLibraryV2ArtistDeletePreview,
  fetchLibraryV2ArtistHistory,
  fetchLibraryV2ArtistSettings,
  fetchLibraryV2MatchArtistReleases,
  fetchLibraryV2Artists,
  fetchLibraryV2ArtistTrackFiles,
  fetchLibraryV2Duplicates,
  fetchLibraryV2FileDeletePreview,
  fetchLibraryV2JobStatus,
  fillLibraryV2TagGaps,
  fetchLibraryV2TrackHistory,
  fetchLibraryV2TrackLyrics,
  LIBRARY_V2_ALBUM_TYPES,
  LIBRARY_V2_QUERY_KEY,
  invalidateLibraryV2,
  libraryV2AlbumMatchStatusQueryOptions,
  libraryV2AlbumQueryOptions,
  libraryV2ArtistAliasesQueryOptions,
  libraryV2ArtistMatchStatusQueryOptions,
  libraryV2ArtistQueryOptions,
  libraryV2ArtistsQueryOptions,
  libraryV2EnabledQueryOptions,
  libraryV2ImportStatusQueryOptions,
  libraryV2MirrorStatusQueryOptions,
  libraryV2QualityProfilesQueryOptions,
  libraryV2QueueStatusQueryOptions,
  libraryV2TrackFileTagsQueryOptions,
  libraryV2TrackSourceInfoQueryOptions,
  libraryV2UiPreferencesQueryOptions,
  libraryV2WantedQueryOptions,
  linkLibraryV2ArtistAlias,
  manualMatchLibraryV2Entity,
  materializeLibraryV2MissingTrack,
  moveLibraryV2TrackFile,
  processWishlist,
  removeLibraryV2FileRecords,
  searchLibraryV2MatchService,
  refreshLibraryV2,
  startLibraryV2DiscographyRefresh,
  reconcileUnmappedArtists,
  reconcileWishlist,
  retryLibraryV2Mirror,
  runRepairJob,
  setLibraryV2Monitored,
  setLibraryV2QualityProfile,
  startLibraryV2AlbumReplayGain,
  startLibraryV2Import,
  startLibraryV2ScopedSearch,
  startLibraryV2UpgradeScan,
  unlinkLibraryV2ArtistAlias,
  unlinkLibraryV2Duplicate,
  updateLibraryV2MetadataOverrides,
  updateLibraryV2ArtistSettings,
  updateLibraryV2UiPreferences,
  writeLibraryV2Tags,
  type Lib2EntityRef,
  type LibraryV2AlbumType,
  type LibraryV2ArtistTrackFile,
  type LibraryV2HistoryCategory,
  type LibraryV2MatchRelease,
  type LibraryV2MatchSearchResult,
} from '../-library-v2.api';
import { useLibraryChanged } from '../-library-v2.live';
import {
  LIBRARY_V2_WANTED_KINDS,
  type LibraryV2AlbumDetail,
  type LibraryV2AlbumSummary,
  type LibraryV2ArtistDetail,
  type LibraryV2ArtistSettings,
  type LibraryV2ArtistSummary,
  type LibraryV2ArtistTableColumns,
  type LibraryV2FileTags,
  type LibraryV2ImportState,
  type LibraryV2DiscographyStats,
  type LibraryV2JobState,
  type LibraryV2ManualSkip,
  type LibraryV2MatchService,
  type LibraryV2QualityProfileSource,
  type LibraryV2QueueStatusEntry,
  type LibraryV2Track,
  type LibraryV2TrackFile,
  type LibraryV2TrackTableColumns,
  type LibraryV2WantedKind,
  type LibraryV2WantedRow,
} from '../-library-v2.types';
import { computeTrackEditValues } from '../-metadata-edit';
import { Route } from '../route';
import { AlbumArtPickerModal, ArtistImagePickerModal } from './art-picker-modal';
import { parseArtworkTarget, watchPendingArtwork } from './artwork-pending';
import {
  classifyReleaseContent,
  DEFAULT_DISCOGRAPHY_FILTERS,
  passesDiscographyFilters,
  type DiscographyFilterState,
  type DiscographyOwnership,
} from './discography-filters';
import { InteractiveSearchModal } from './interactive-search';
import styles from './library-v2-page.module.css';
import { QualityProfileModal, QualityProfilePicker } from './quality-profile-modal';
import { ReassignModal } from './reassign-modal';
import { AlbumReorganizeModal, ArtistReorganizeAllModal } from './reorganize-modal';
import { RetagModal } from './retag-modal';

/** Row/toolbar action dispatch: the label drives the behaviour, the optional
 *  entity ref carries WHICH lib2 track/album the action is for so grabs keep
 *  their entity + quality-profile context (audit P1-16/P1-17). */
type ActionHandler = (action: string, entity?: Lib2EntityRef) => void;

function trackProgress(present: number, total: number): string {
  return `${present}/${total}`;
}

function qualityProfileSourceLabel(source?: LibraryV2QualityProfileSource): string {
  if (source === 'track') return 'Track';
  if (source === 'album') return 'Album';
  if (source === 'artist') return 'Artist';
  return 'App default';
}

function profileLabel(name: string, source?: LibraryV2QualityProfileSource): string {
  const srcLabel = qualityProfileSourceLabel(source);
  if (srcLabel === 'App default') {
    return name;
  }
  return `${name} (${srcLabel})`;
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

export function AlbumSizeBadge({ bytes }: { bytes: number }) {
  return (
    <span className={styles.albumSizeBadge} title="Size on disk">
      <SvgIcon name="folder" />
      {formatFileSize(bytes)}
    </span>
  );
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

/** An untitled track's display label falls back to "Track <n>"/"Track ?"
 *  (see the track-row `label` in `TrackRow`) — that placeholder makes a
 *  guaranteed-empty search query, so it must never be sent to search. */
const PLACEHOLDER_TRACK_LABEL_RE = /^Track\s+(\?|\d+)$/;

/** Strips a trailing `(...)` group from `s`, matching parens by depth instead
 *  of a flat `[^)]*` regex — an album/track title with its own parens (e.g.
 *  "Freed from Desire (feat. Indiiana)") would otherwise hide the true end of
 *  the wrapping "(album)" context group, so the regex found no match at all
 *  and left the ENTIRE tail — track title and duplicated album context —
 *  in the query. Returns the group's inner text (or null if the string
 *  doesn't end in a balanced parenthesized group) plus the remaining prefix. */
function splitTrailingParenGroup(s: string): {
  rest: string;
  group: string | null;
} {
  const trimmed = s.replace(/\s+$/, '');
  if (!trimmed.endsWith(')')) return { rest: s.trim(), group: null };
  let depth = 0;
  for (let i = trimmed.length - 1; i >= 0; i -= 1) {
    if (trimmed[i] === ')') depth += 1;
    else if (trimmed[i] === '(') {
      depth -= 1;
      if (depth === 0) {
        return {
          rest: trimmed.slice(0, i).trim(),
          group: trimmed.slice(i + 1, -1).trim(),
        };
      }
    }
  }
  return { rest: s.trim(), group: null }; // unbalanced — leave untouched
}

/** Build a source-search query from an artist name + an action label.
 *
 *  Track-scoped labels carry an appended album context — "Title (Album)" — and
 *  that context has to come back off, or the query repeats the album name.
 *  Album-scoped labels are just "Album", where a trailing parenthesized group
 *  is *part of the title* ("OK Computer (OKNOTOK 1997 2017)", "Definitely
 *  Maybe (Remastered)"). dd28-35: stripping unconditionally threw away exactly
 *  the edition words that pick the right release. ``entity`` disambiguates —
 *  only a track-scoped search has a trackId, so only it gets the strip.
 *
 *  Falls back to the album context (iss27-01) when the title is really just
 *  the untitled-track placeholder.
 *
 *  dd28-36: a title that is *entirely* one parenthesized group ("(Untitled)")
 *  would otherwise reduce to the bare artist name — or, with no artist, to an
 *  empty query the search silently refuses to send. Keep the original tail
 *  whenever removing the group would leave nothing behind. */
export function buildSearchQuery(
  artistName: string,
  action: string,
  entity?: Lib2EntityRef,
): string {
  const idx = action.indexOf(': ');
  if (idx === -1) return artistName; // artist-level search
  const tail = action.slice(idx + 2);
  const trackScoped = entity?.trackId !== undefined;
  const { rest: withoutAlbum, group: album } = trackScoped
    ? splitTrailingParenGroup(tail)
    : { rest: tail.trim(), group: null };
  const stripped = withoutAlbum.replace(/\s*-\s*missing\s*$/i, '').trim();
  const rest = stripped || tail.replace(/\s*-\s*missing\s*$/i, '').trim();
  const finalRest = album && PLACEHOLDER_TRACK_LABEL_RE.test(rest) ? album : rest;
  return `${artistName} ${finalRest}`.trim();
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
  server:
    'M5 4h14a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z M5 13h14a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2z M7 7h.01 M7 16h.01 M11 7h7 M11 16h7',
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

const MEDIA_SERVER_LABELS: Record<string, string> = {
  emby: 'Emby',
  jellyfin: 'Jellyfin',
  navidrome: 'Navidrome',
  plex: 'Plex',
};

function mediaServerLabel(source: string): string {
  const normalized = source.trim().toLowerCase();
  return (
    MEDIA_SERVER_LABELS[normalized] ??
    normalized
      .split(/[_-]+/)
      .filter(Boolean)
      .map((part) => `${part[0]?.toUpperCase() ?? ''}${part.slice(1)}`)
      .join(' ')
  );
}

function naturalList(values: string[]): string {
  if (values.length <= 1) return values[0] ?? '';
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(', ')}, and ${values.at(-1)}`;
}

/** A single quiet signal for one or more positive media-server mappings.
 * Provider names stay out of the layout and are available on hover and to
 * assistive technology. The count only appears when more than one server
 * independently recognised the entity. */
function MediaServerRecognitionBadge({ sources }: { sources: string[] | undefined }) {
  const labels = Array.from(
    new Set((sources ?? []).map(mediaServerLabel).filter((source) => source.length > 0)),
  );
  if (labels.length === 0) return null;
  const description = `Recognised by ${naturalList(labels)}`;
  return (
    <span
      className={styles.mediaServerRecognitionBadge}
      aria-label={description}
      title={description}
    >
      <SvgIcon name="server" />
      <span className={styles.mediaServerRecognitionCheck} aria-hidden="true">
        ✓
      </span>
      {labels.length > 1 ? (
        <span className={styles.mediaServerRecognitionCount} aria-hidden="true">
          {labels.length}
        </span>
      ) : null}
    </span>
  );
}

/** Quality display: format and resolution (bit depth + sample rate). */
function QualityDisplay({ file }: { file: LibraryV2Track['file'] | null | undefined }) {
  const prefsQuery = useQuery(libraryV2UiPreferencesQueryOptions());
  if (!file) {
    return <span className={styles.qualityPlaceholderDash}>—</span>;
  }

  const showFormat = prefsQuery.data?.track_table.quality_show_format ?? true;
  const showResolution = prefsQuery.data?.track_table.quality_show_resolution ?? true;

  const fmt = showFormat ? (file.format ?? '').toUpperCase() || null : null;
  const bitDepth = showResolution && file.bit_depth ? `${file.bit_depth}bit` : null;
  const sampleRate =
    showResolution && file.sample_rate
      ? `${Number((file.sample_rate / 1000).toFixed(file.sample_rate % 1000 === 0 ? 0 : 1))}kHz`
      : null;
  const resolution = [bitDepth, sampleRate].filter(Boolean).join('/');
  const formatBadge = [fmt, resolution || null].filter(Boolean).join(' · ');

  if (!formatBadge) return null;

  return (
    <span className={styles.qualityDisplay}>
      <span className={styles.qualityTag}>{formatBadge}</span>
    </span>
  );
}

/** Bitrate display: kbps value. */
function BitrateDisplay({ file }: { file: LibraryV2Track['file'] | null | undefined }) {
  const prefsQuery = useQuery(libraryV2UiPreferencesQueryOptions());
  if (!file) {
    return <span className={styles.qualityPlaceholderDash}>—</span>;
  }

  const showBitrate = prefsQuery.data?.track_table.quality_show_bitrate ?? true;
  if (!showBitrate) return null;

  const kbps = file.bitrate
    ? file.bitrate > 5000
      ? Math.round(file.bitrate / 1000)
      : file.bitrate
    : null;

  if (!kbps) return null;

  return (
    <span className={styles.qualityDisplay}>
      <span className={styles.qualityTag}>{kbps} kbps</span>
    </span>
  );
}

// --- shared building blocks --------------------------------------------------

function useNavigate() {
  return useRouterNavigate({ from: Route.fullPath });
}

const LOCAL_ARTWORK_PREFIX = '/api/library/v2/artwork/';

/** Set (never duplicate) the `v` cache-bust parameter on an artwork URL. */
function withCacheBust(url: string, version: number): string {
  const [path, query = ''] = url.split('?');
  const params = new URLSearchParams(query);
  params.set('v', String(version));
  return `${path}?${params.toString()}`;
}

/** Cover/poster image with a graceful placeholder when no artwork resolves.
 *  ``thumb`` requests the small resized variant for fast list rendering. */
export function Artwork({
  src,
  alt,
  className,
  thumb: useThumbnail,
  remote,
}: {
  src: string;
  alt: string;
  className: string;
  thumb?: boolean;
  /** ldp-07: the provider CDN cover for this entity. Painted instead of the
   *  placeholder while a cold local build is still running, so a first visit
   *  shows real artwork at CDN speed exactly like the legacy page did. */
  remote?: string | null;
}) {
  // Only SoulSync's artwork endpoint understands ``size=thumb``. Appending it
  // to Spotify/Deezer/CDN URLs (the previous behavior) can invalidate signed
  // URLs; it also produced ``...?v=123?size=thumb`` for cache-busted local art.
  const sized = useThumbnail ? thumb(src, 'card') : src;
  const local = Boolean(sized) && sized.startsWith(LOCAL_ARTWORK_PREFIX);
  const base =
    sized && useThumbnail && local ? `${sized}${sized.includes('?') ? '&' : '?'}size=thumb` : sized;
  // rev25-12: this state is keyed to the base it belongs to and reset the
  // instant `base` changes — during render, not in a `[base]` effect. An
  // effect fires only after this render already committed, so a src change
  // (e.g. a list refetch landing a fresh `?v=`) used to commit one frame that
  // still carried the previous base's cache-bust suffix on the new URL,
  // forcing every such change to load the image twice. Adjusting state during
  // render (React's documented pattern for this) means the corrected value is
  // what actually gets painted, never a transient wrong one.
  const [state, setState] = useState({
    base,
    failed: false,
    remoteFailed: false,
    version: 0,
  });
  if (state.base !== base) setState({ base, failed: false, remoteFailed: false, version: 0 });
  const failed = state.base === base && state.failed;
  const remoteFailed = state.base === base && state.remoteFailed;
  const version = state.base === base ? state.version : 0;
  // A falsy `base` must stay falsy: `''` plus a suffix produces the truthy
  // string `'?v=1'`, which `<img>` resolves against the current document — a
  // broken-image flash — instead of the placeholder. `v` is *replaced*, not
  // appended: the list response already ships one for cached covers.
  const url = base && version ? withCacheBust(base, version) : base;
  // rev25-02: a cold cover 404s while the server builds it in the background.
  // An `<img>` cannot read `X-Artwork-Pending`, so the wait is driven by the
  // server: subscribe until the build is reported ready (render it) or
  // unavailable (the placeholder is final), instead of a fixed retry ladder
  // that expired before slow builds finished.
  useEffect(() => {
    if (!failed || !local) return;
    const target = parseArtworkTarget(base);
    if (!target) return;
    return watchPendingArtwork(target.kind, target.id, (readyVersion) => {
      if (readyVersion == null) return;
      setState((current) =>
        current.base === base
          ? {
              base,
              failed: false,
              remoteFailed: current.remoteFailed,
              version: readyVersion,
            }
          : current,
      );
    });
  }, [failed, local, base]);
  // The local copy stays the long-term truth (a manual cover pick, an embedded
  // cover, offline/NAS); the CDN url only stands in for the wait. Its own
  // failure is tracked separately, or falling back would re-trigger itself.
  const usingRemote = failed && !remoteFailed && Boolean(remote) && remote !== url;
  const shown = usingRemote ? (remote as string) : url;
  const handleError = () => {
    const field = usingRemote ? 'remoteFailed' : 'failed';
    setState((current) => (current.base === base ? { ...current, [field]: true } : current));
  };
  if (!shown || (failed && !usingRemote)) {
    return (
      <div className={`${className} ${styles.artPlaceholder}`} aria-label={alt}>
        ♪
      </div>
    );
  }
  return (
    <img
      className={className}
      src={shown}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={handleError}
    />
  );
}

function useMonitorMutation() {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
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
      if (!canWrite) throw new Error('Library changes require the admin profile');
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

function QueryFailure({
  error,
  fallback,
  retry,
}: {
  error: unknown;
  fallback: string;
  retry: () => void;
}) {
  return (
    <div className={styles.searchError} role="alert">
      {mutationErrorMessage(error, fallback)}{' '}
      <button type="button" className={styles.inlineRetry} onClick={retry}>
        Try again
      </button>
    </div>
  );
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
  const canWrite = useLibraryV2CanWrite();
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
            : entity === 'artists' && monitored
              ? 'On the Watchlist — click to remove this artist (files and explicitly monitored tracks stay untouched)'
              : entity === 'artists'
                ? 'Not on the Watchlist — click to monitor this artist and enable Artist Settings'
                : monitored
                  ? 'Monitored — click to stop'
                  : 'Not monitored — click to monitor'
        }
        data-requires-write=""
        disabled={mutation.isPending || !canWrite}
        onClick={() => {
          if (!canWrite) return;
          mutation.mutate({
            entity,
            id,
            monitored: nextMonitored,
            albumId,
            trackNumber,
            discNumber,
            title,
          });
        }}
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

/**
 * Whether this profile may mutate the catalogue (iss29-C10).
 *
 * `_guard` in api/library_v2.py rejects EVERY non-GET from a non-admin profile
 * — the lib2 monitored columns are global, so a second profile's write would
 * overwrite the admin's state (P0-02). The UI knew nothing about that, so a
 * read-only profile was offered the full toolbar and every button answered 403
 * on click. Missing capability data must fail closed.
 */
export const LibraryV2CanWriteContext = createContext(false);

export function useLibraryV2CanWrite(): boolean {
  return useContext(LibraryV2CanWriteContext);
}

export function ActionButton({
  icon,
  label,
  onClick,
  title,
  busy,
  disabled,
  requiresWrite = true,
  tone = 'default',
}: {
  icon: IconName;
  label: ReactNode;
  onClick: () => void;
  title?: string;
  busy?: boolean;
  disabled?: boolean;
  requiresWrite?: boolean;
  tone?: 'default' | 'danger';
}) {
  const canWrite = useLibraryV2CanWrite();
  const writeBlocked = requiresWrite && !canWrite;
  return (
    <button
      type="button"
      className={`${styles.toolButton} ${tone === 'danger' ? styles.toolDanger : ''}`}
      data-requires-write={requiresWrite ? '' : undefined}
      disabled={busy || disabled || writeBlocked}
      title={writeBlocked ? 'Library changes require the admin profile' : title}
      onClick={() => {
        if (!writeBlocked) onClick();
      }}
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
  requiresWrite = false,
  tone = 'default',
}: {
  icon: IconName;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  requiresWrite?: boolean;
  tone?: 'default' | 'danger';
}) {
  const canWrite = useLibraryV2CanWrite();
  const writeBlocked = requiresWrite && !canWrite;
  return (
    <button
      type="button"
      className={`${styles.iconAction} ${tone === 'danger' ? styles.toolDanger : ''}`}
      aria-label={title}
      title={writeBlocked ? 'Library changes require the admin profile' : title}
      data-requires-write={requiresWrite ? '' : undefined}
      disabled={disabled || writeBlocked}
      onClick={(e) => {
        e.stopPropagation();
        if (!writeBlocked) onClick();
      }}
    >
      <SvgIcon name={icon} />
    </button>
  );
}

export function ArtistRefreshButton({ artistId }: { artistId: number }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async () => {
      const jobId = await refreshLibraryV2('artists', artistId);
      const state = await awaitBulkJobState(queryClient, jobId);
      if (state.error) throw new Error(state.error);
      return state;
    },
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
        title="Re-read files on disk: existence, audio quality and embedded tags. Provider metadata is unchanged."
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
  match,
  settings,
  onClose,
  children,
}: {
  title: string;
  wide?: boolean;
  /** Fixed width+height (tab body scrolls internally) so tabbed content
   *  (track/album detail modals) doesn't resize/jump when switching tabs. */
  detail?: boolean;
  /** Roomier matching surface for identity cards + release context. */
  match?: boolean;
  /** Viewport-bound, vertically scrolling table-settings surface. */
  settings?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <DialogFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      className={`${styles.modal} ${wide ? styles.modalWide : ''} ${detail ? styles.modalDetail : ''} ${match ? styles.modalMatch : ''} ${settings ? styles.modalSettings : ''}`}
    >
      <DialogHeader title={title} closeLabel="Close" />
      {children}
    </DialogFrame>
  );
}

// --- metadata match chips (legacy Enhanced-View parity) ---------------------

function matchChipClass(status: string): string {
  if (status === 'matched') return styles.matchMatched;
  if (status === 'not_found') return styles.matchNotFound;
  return styles.matchPending;
}

/** §52.5/§56.2: only Spotify/Deezer artist search results and the live
 *  artist_stats lookup carry these — the shared backend convention treats 0
 *  as "not provided", not a real value, so this returns null rather than
 *  printing "0 followers". */
function formatMatchStat(result: { followers?: number; popularity?: number }): string | null {
  const parts: string[] = [];
  if (result.followers) parts.push(`${formatCompactNumber(result.followers)} followers`);
  if (result.popularity) parts.push(`${result.popularity} popularity`);
  return parts.length ? parts.join(' · ') : null;
}

function formatCompactNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, '')}K`;
  return value.toLocaleString('en-US');
}

function matchOriginLabel(origin?: LibraryV2MatchService['match_origin']): string | null {
  if (origin === 'manual') return 'Manual match';
  if (origin === 'automatic') return 'Automatic match';
  if (origin === 'legacy') return 'Legacy match';
  return null;
}

const WATCHLIST_MATCH_PROVIDERS = new Set([
  'spotify',
  'itunes',
  'deezer',
  'discogs',
  'amazon',
  'musicbrainz',
]);

function MatchReleaseStrip({ albums }: { albums: LibraryV2MatchRelease[] }) {
  if (!albums.length) return null;
  return (
    <div className={styles.matchReleaseStrip} aria-label="Albums from this artist match">
      {albums.map((album, index) => (
        <div key={album.id || `${album.title}-${index}`} className={styles.matchReleaseCard}>
          <Artwork src={album.image || ''} alt={album.title} className={styles.matchReleaseCover} />
          <span className={styles.matchReleaseTitle} title={album.title}>
            {album.title}
          </span>
          <span className={styles.matchReleaseMeta}>
            {[album.album_type, formatReleaseDate(album.release_date)]
              .filter(Boolean)
              .join(' · ') || 'Release'}
          </span>
        </div>
      ))}
    </div>
  );
}

function MatchArtistReleaseContext({
  result,
  service,
  autoLoad,
}: {
  result: LibraryV2MatchSearchResult;
  service: string;
  autoLoad: boolean;
}) {
  const [open, setOpen] = useState(autoLoad);
  const provider = result.provider || service;
  const query = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'match-release-preview', provider, result.id],
    queryFn: () =>
      fetchLibraryV2MatchArtistReleases({
        service: provider,
        artist_id: result.id,
        artist_name: result.name,
        limit: 6,
      }),
    enabled: open,
    staleTime: 10 * 60 * 1000,
    retry: false,
  });

  if (!open) {
    return (
      <button type="button" className={styles.matchReleaseToggle} onClick={() => setOpen(true)}>
        Preview albums
      </button>
    );
  }
  if (query.isLoading) {
    return <span className={styles.matchReleaseHint}>Loading album context…</span>;
  }
  if (query.isError) {
    return <span className={styles.matchReleaseHint}>Album context unavailable right now.</span>;
  }
  if (query.data?.supported === false) {
    return <span className={styles.matchReleaseHint}>This provider has no album preview.</span>;
  }
  if (!query.data?.albums.length) {
    return <span className={styles.matchReleaseHint}>No albums returned for this identity.</span>;
  }
  return <MatchReleaseStrip albums={query.data.albums} />;
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
  entityImage,
  artistReleases = [],
  watchlistRowId,
}: {
  entityType: 'artist' | 'album' | 'track';
  entityName: string;
  services: LibraryV2MatchService[];
  abbreviated?: boolean;
  /** B5 opt-in override: show every provider chip, including ones this
   *  instance never configured (A8's default hides those as noise). */
  showAll?: boolean;
  entityImage?: string | null;
  artistReleases?: LibraryV2AlbumSummary[];
  watchlistRowId?: number;
}) {
  const canWrite = useLibraryV2CanWrite();
  const prefsQuery = useQuery(libraryV2UiPreferencesQueryOptions());
  const [active, setActive] = useState<LibraryV2MatchService | null>(null);
  // A8: hide chips for providers nobody configured on this instance — a
  // permanently grey Tidal/Qobuz/… row was pure noise. `available` is
  // `undefined` for older cached responses, which reads as available.
  // Also support manual exclusion via visible_match_providers preference.
  const visibleProviders = prefsQuery.data?.track_table.visible_match_providers ?? {};
  const visible = services.filter((s) => {
    if (visibleProviders[s.service] === false) return false;
    return showAll ? true : s.available !== false;
  });
  if (!visible.length) return null;
  return (
    <div className={abbreviated ? styles.trackMatchChips : styles.matchChips}>
      {visible.map((s) => {
        const details = [
          s.external_id ? `id: ${s.external_id}` : 'no id',
          s.last_attempted ? `last: ${s.last_attempted.slice(0, 16).replace('T', ' ')}` : null,
          s.legacy_entity_id != null || s.library_v2_entity_id != null
            ? 'click to (re)match'
            : null,
          matchOriginLabel(s.match_origin),
        ]
          .filter(Boolean)
          .join(' · ');
        const tip = `${s.label}: ${s.status} (${details})`;
        return (
          <button
            key={s.service}
            type="button"
            className={`${styles.matchChip} ${abbreviated ? styles.trackMatchChip : ''} ${matchChipClass(s.status)}`}
            title={canWrite ? tip : 'Library changes require the admin profile'}
            data-requires-write=""
            disabled={!canWrite || (s.legacy_entity_id == null && s.library_v2_entity_id == null)}
            onClick={() => {
              if (canWrite) setActive(s);
            }}
          >
            <span>{abbreviated ? getServiceAbbreviation(s.service) : s.label}</span>
          </button>
        );
      })}
      {active && (active.legacy_entity_id != null || active.library_v2_entity_id != null) ? (
        <ManualMatchModal
          entityType={entityType}
          entityName={entityName}
          service={active}
          entityImage={entityImage}
          artistReleases={artistReleases}
          watchlistRowId={watchlistRowId}
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
      label = 'Verified';
      tooltip = 'AcoustID fingerprint matched the expected track';
      break;
    case 'human_verified':
      className = styles.verificationHuman;
      label = 'Human verified';
      tooltip = 'Human verified: you approved this file, skipping AcoustID';
      break;
    case 'force_imported':
      className = styles.verificationForced;
      label = 'Bypassed';
      tooltip = 'Force-imported: AcoustID check bypassed (accepted version-mismatch fallback)';
      break;
    case 'unverified':
      className = styles.verificationUnverified;
      label = 'Unverified';
      tooltip = 'Imported but not hard-confirmed: AcoustID could not verify this file';
      break;
    default:
      return null;
  }
  // A7/C4: the concrete AcoustID reason, when the pipeline captured one —
  // otherwise the tooltip only states the outcome, not why.
  const detail = file.pipeline_result?.acoustid_message;
  if (detail) tooltip = `${tooltip} (${detail})`;
  return (
    <span className={`${styles.verificationBadge} ${className}`} title={tooltip}>
      {label}
    </span>
  );
}

/** iss28-01: compact effective Check summary. Human approval wins over the
 * raw AcoustID skip because it explains why the technical check was bypassed. */
export function TrackCheckBadge({ file }: { file: LibraryV2TrackFile | null }) {
  if (!file) return <span className={styles.muted}>—</span>;
  const detail = file.pipeline_result?.acoustid_message;
  if (file.verification_status === 'human_verified') {
    return (
      <span
        className={`${styles.verificationBadge} ${styles.verificationHuman}`}
        title={
          detail
            ? `Human verified; AcoustID detail: ${detail}`
            : 'Human verified: this file was explicitly approved'
        }
      >
        Human verified
      </span>
    );
  }
  if (file.acoustid_status === 'pass') {
    return (
      <span
        className={`${styles.verificationBadge} ${styles.verificationVerified}`}
        title={detail ? `AcoustID check passed: ${detail}` : 'AcoustID fingerprint check passed'}
      >
        Verified
      </span>
    );
  }
  if (file.acoustid_status === 'skip' || file.verification_status === 'force_imported') {
    return (
      <span
        className={`${styles.verificationBadge} ${styles.verificationForced}`}
        title={
          detail
            ? `Check skipped: ${detail}`
            : file.verification_status === 'force_imported'
              ? 'Check skipped by force/retry import'
              : 'AcoustID check was skipped'
        }
      >
        Skipped
      </span>
    );
  }
  return (
    <span
      className={`${styles.verificationBadge} ${styles.verificationUnverified}`}
      title={
        detail ? `Not scanned: ${detail}` : 'No completed AcoustID check is recorded for this file'
      }
    >
      Not scanned
    </span>
  );
}

const QUALITY_FALLBACK_LABELS: Record<string, string> = {
  downsample: 'Hi-Res downsampled',
  lossy_copy: 'Lossy copy created',
};

function ManualMatchModal({
  entityType,
  entityName,
  service,
  entityImage,
  artistReleases,
  watchlistRowId,
  onClose,
}: {
  entityType: 'artist' | 'album' | 'track';
  entityName: string;
  service: LibraryV2MatchService;
  entityImage?: string | null;
  artistReleases: LibraryV2AlbumSummary[];
  watchlistRowId?: number;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  const [query, setQuery] = useState(entityName);
  const search = useMutation({
    mutationFn: () => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      return searchLibraryV2MatchService({
        service: service.service,
        entity_type: entityType,
        query,
      });
    },
  });
  const apply = useMutation({
    mutationFn: (result: LibraryV2MatchSearchResult) => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      const resultService = result.provider || service.service;
      return manualMatchLibraryV2Entity({
        entity_type: entityType,
        legacy_entity_id: service.legacy_entity_id as number | string,
        library_v2_entity_id: service.library_v2_entity_id,
        service: resultService,
        service_id: result.id,
        ...(entityType === 'artist' &&
        watchlistRowId &&
        WATCHLIST_MATCH_PROVIDERS.has(resultService)
          ? { watchlist_row_id: watchlistRowId }
          : {}),
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    },
  });
  const clear = useMutation({
    mutationFn: () => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      return clearLibraryV2EntityMatch({
        entity_type: entityType,
        legacy_entity_id: service.legacy_entity_id as number | string,
        library_v2_entity_id: service.library_v2_entity_id,
        service: service.service,
        ...(entityType === 'artist' &&
        watchlistRowId &&
        WATCHLIST_MATCH_PROVIDERS.has(service.service)
          ? { watchlist_row_id: watchlistRowId }
          : {}),
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onClose();
    },
  });
  const results = search.data ?? [];
  const currentReleases: LibraryV2MatchRelease[] = artistReleases.slice(0, 6).map((album) => ({
    id: String(album.id),
    title: album.title,
    image: album.image_url,
    release_date: album.release_date,
    album_type: album.album_type,
    total_tracks: album.track_count,
  }));
  return (
    <ModalShell title={`Match ${entityType} on ${service.label}`} match onClose={onClose}>
      {service.status === 'matched' && service.external_id ? (
        <section className={styles.currentMatchCard}>
          <Artwork
            src={entityImage || ''}
            alt={entityName}
            className={`${styles.currentMatchImage} ${entityType === 'artist' ? styles.matchArtistImage : ''}`}
          />
          <div className={styles.currentMatchBody}>
            <div className={styles.currentMatchEyebrow}>
              Current {service.label} identity
              {matchOriginLabel(service.match_origin) ? (
                <span className={styles.matchProvenancePill} data-origin={service.match_origin}>
                  {matchOriginLabel(service.match_origin)}
                </span>
              ) : null}
            </div>
            <strong className={styles.currentMatchName}>{entityName}</strong>
            <button
              type="button"
              className={styles.matchIdButton}
              title="Copy provider ID"
              onClick={() => void navigator.clipboard?.writeText(service.external_id || '')}
            >
              {service.external_id}
              <span>Copy</span>
            </button>
            {entityType === 'artist' && currentReleases.length ? (
              <div className={styles.currentMatchReleases}>
                <span>Library release context</span>
                <MatchReleaseStrip albums={currentReleases} />
              </div>
            ) : null}
          </div>
          <button
            type="button"
            className={styles.btnDangerGhost}
            data-requires-write=""
            disabled={clear.isPending || !canWrite}
            onClick={() => {
              if (window.confirm(`Clear the current ${service.label} match?`)) clear.mutate();
            }}
          >
            {clear.isPending ? 'Clearing…' : 'Clear match'}
          </button>
        </section>
      ) : (
        <div className={styles.matchNoCurrent}>No current {service.label} identity is linked.</div>
      )}
      <div className={styles.matchSearchRow}>
        <input
          className={styles.searchInput}
          value={query}
          disabled={apply.isPending}
          placeholder={`Search ${service.label}…`}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && canWrite) search.mutate();
          }}
        />
        <button
          type="button"
          className={styles.btnPrimary}
          data-requires-write=""
          disabled={search.isPending || !query.trim() || !canWrite}
          onClick={() => {
            if (canWrite) search.mutate();
          }}
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
      {clear.isError ? (
        <div className={styles.searchError}>
          {mutationErrorMessage(clear.error, 'Clear match failed')}
        </div>
      ) : null}
      <div className={styles.matchResults}>
        {search.isSuccess && results.length === 0 ? (
          <div className={styles.inlineLoading}>No results — try a different search.</div>
        ) : null}
        {results.map((r, index) => {
          const resultProvider = r.provider || service.service;
          const isCurrent =
            resultProvider === service.service &&
            String(r.id) === String(service.external_id || '');
          return (
            <div
              key={`${resultProvider}:${r.id}`}
              className={`${styles.matchResultRow} ${isCurrent ? styles.matchResultCurrent : ''}`}
            >
              <Artwork
                src={r.image || ''}
                alt={r.name || 'Unknown'}
                className={`${styles.matchResultImage} ${entityType === 'artist' ? styles.matchArtistImage : ''}`}
              />
              <div className={styles.matchResultInfo}>
                <span className={styles.matchResultHeading}>
                  <span className={styles.matchResultName}>{r.name || 'Unknown'}</span>
                  <span className={styles.matchProviderPill}>{resultProvider}</span>
                  {isCurrent ? <span className={styles.matchCurrentPill}>Current</span> : null}
                </span>
                {r.extra ? <span className={styles.matchResultExtra}>{r.extra}</span> : null}
                {formatMatchStat(r) ? (
                  <span className={styles.matchResultExtra}>{formatMatchStat(r)}</span>
                ) : null}
                <button
                  type="button"
                  className={styles.matchResultId}
                  title="Copy provider ID"
                  onClick={() => void navigator.clipboard?.writeText(r.id)}
                >
                  ID: {r.id}
                </button>
                {entityType === 'artist' ? (
                  <MatchArtistReleaseContext
                    result={r}
                    service={service.service}
                    autoLoad={index < 3}
                  />
                ) : null}
              </div>
              <button
                type="button"
                className={styles.btnPrimary}
                data-requires-write=""
                disabled={apply.isPending || isCurrent || !canWrite}
                onClick={() => apply.mutate(r)}
              >
                {isCurrent ? 'Current' : apply.isPending ? 'Matching…' : 'Use this match'}
              </button>
            </div>
          );
        })}
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
  const canWrite = useLibraryV2CanWrite();
  const mutation = useMutation({
    mutationFn: async (service: string) => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
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
        data-requires-write=""
        disabled={mutation.isPending || !canWrite}
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
          data-requires-write=""
          disabled={mutation.isPending || !canWrite}
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

/** Fetches an artist's provider match chips and carries the rich local context
 * into the current-match card. */
function ArtistMatchChips({
  artist,
  watchlistRowId,
  abbreviated,
}: {
  artist: LibraryV2ArtistDetail;
  watchlistRowId?: number;
  /** ldp-05: the rich hero puts these in the legacy badge row under the
   *  name, where short service codes read like the logo row they replace. */
  abbreviated?: boolean;
}) {
  const query = useQuery(libraryV2ArtistMatchStatusQueryOptions(artist.id));
  if (!query.data?.services.length) return null;
  return (
    <MatchChips
      entityType="artist"
      entityName={artist.name}
      entityImage={artist.image_url}
      artistReleases={[...artist.albums, ...(artist.eps ?? []), ...artist.singles]}
      services={query.data.services}
      watchlistRowId={watchlistRowId}
      abbreviated={abbreviated}
    />
  );
}

/** §40: alias-group chips on the artist header + a "Link alias" action.
 *  ``artistId`` is always the CANONICAL id here (get_artist redirects an
 *  alias id's detail response to its canonical — see docs §24.4), so the
 *  rendered chips are exactly its linked aliases. Deliberately minimal (no
 *  suggestion/recovery UX) — that is §41's separate, larger scope. */
export function ArtistAliases({ artistId, artistName }: { artistId: number; artistName: string }) {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  const [linking, setLinking] = useState(false);
  const query = useQuery(libraryV2ArtistAliasesQueryOptions(artistId));
  const unlink = useMutation({
    mutationFn: (aliasId: number) => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      return unlinkLibraryV2ArtistAlias(aliasId);
    },
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
            data-requires-write=""
            disabled={unlink.isPending || !canWrite}
            onClick={() => {
              if (canWrite) unlink.mutate(m.id);
            }}
          >
            ✕
          </button>
        </span>
      ))}
      {unlink.isError ? (
        <span className={styles.mutationError} role="alert">
          <span>{mutationErrorMessage(unlink.error, 'Unlink failed')}</span>
          <button
            type="button"
            className={styles.inlineRetry}
            data-requires-write=""
            disabled={unlink.isPending || unlink.variables == null || !canWrite}
            onClick={() => {
              if (unlink.variables != null) unlink.mutate(unlink.variables);
            }}
          >
            Retry
          </button>
        </span>
      ) : null}
      <button
        type="button"
        className={styles.aliasLinkButton}
        data-requires-write=""
        disabled={!canWrite}
        title="Link another artist row in your library as an alias of this one (same real artist, different provider identity)"
        onClick={() => {
          if (canWrite) setLinking(true);
        }}
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
  const canWrite = useLibraryV2CanWrite();
  const [query, setQuery] = useState('');
  const search = useMutation({
    mutationFn: (q: string) =>
      fetchLibraryV2Artists({ q, sort: 'name', page: 1, monitored: 'all' }),
  });
  const link = useMutation({
    mutationFn: (aliasOfId: number) => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      return linkLibraryV2ArtistAlias(artistId, aliasOfId);
    },
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
              data-requires-write=""
              disabled={link.isPending || !canWrite}
              onClick={() => {
                if (canWrite) link.mutate(a.id);
              }}
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
  const canWrite = useLibraryV2CanWrite();
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
    mutationFn: (value: 'all' | 'none' | 'new') => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      return editLibraryV2Artist(artistId, value);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
    onMutate: (value) => {
      const previous = newItems;
      setNewItems(value);
      return { previous };
    },
    onError: (_error, _value, context) => {
      setNewItems(context?.previous ?? initialNewItems);
    },
  });

  function saveFutureReleases(value: 'all' | 'none' | 'new') {
    futureReleasesMutation.mutate(value);
  }

  async function apply(scope: 'all' | 'missing', monitored: boolean, label: string) {
    if (!canWrite) return;
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
            data-requires-write=""
            disabled={busy !== null || !canWrite}
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
            data-requires-write=""
            disabled={!canWrite}
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
          data-requires-write=""
          disabled={futureReleasesMutation.isPending || !canWrite}
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
            data-requires-write=""
            disabled={!canWrite}
            onClick={() =>
              futureReleasesMutation.mutate(futureReleasesMutation.variables ?? newItems)
            }
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

/** §52.3/§52.4: one Artist Settings surface over the existing Watchlist row.
 * Quality remains the app-wide profile system; release filters, lookback,
 * preferred provider and auto-download are written to `watchlist_artists`;
 * existing release monitoring stays a separate, clearly-labelled action. */
export function ArtistSettingsModal({
  artist,
  onClose,
}: {
  artist: LibraryV2ArtistDetail;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  const settingsQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-settings', artist.id],
    queryFn: () => fetchLibraryV2ArtistSettings(artist.id),
  });
  const [draft, setDraft] = useState<LibraryV2ArtistSettings | null>(null);
  const [bulkBusy, setBulkBusy] = useState<string | null>(null);
  const [bulkMessage, setBulkMessage] = useState<{
    tone: 'ok' | 'error';
    text: string;
  } | null>(null);

  useEffect(() => {
    if (settingsQuery.data?.settings) setDraft(settingsQuery.data.settings);
  }, [settingsQuery.data?.settings]);

  const save = useMutation({
    mutationFn: (value: LibraryV2ArtistSettings) => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      return updateLibraryV2ArtistSettings(artist.id, value);
    },
    onSuccess: async (response) => {
      setDraft(response.settings);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    },
  });

  async function applyExistingReleaseStrategy(
    scope: 'all' | 'missing',
    monitored: boolean,
    label: string,
  ) {
    if (!canWrite) return;
    setBulkBusy(label);
    setBulkMessage(null);
    try {
      const jobId = await bulkMonitorLibraryV2Releases(artist.id, scope, monitored);
      const error = await awaitBulkJob(queryClient, jobId);
      if (error) throw new Error(error);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      setBulkMessage({ tone: 'ok', text: `${label} applied.` });
    } catch (caught) {
      setBulkMessage({
        tone: 'error',
        text: mutationErrorMessage(caught, 'Existing-release monitoring failed'),
      });
    } finally {
      setBulkBusy(null);
    }
  }

  function setBoolean(field: keyof LibraryV2ArtistSettings, checked: boolean) {
    setDraft((current) => (current ? { ...current, [field]: checked } : current));
  }

  const sourceOptions = settingsQuery.data?.metadata_sources ?? [];
  const providerIds = draft
    ? Object.entries(draft.provider_ids).filter((entry): entry is [string, string] =>
        Boolean(entry[1]),
      )
    : [];

  return (
    <ModalShell title="Artist Settings" wide onClose={onClose}>
      {settingsQuery.isError ? (
        <div className={styles.mutationError} role="alert">
          {mutationErrorMessage(settingsQuery.error, 'Artist settings could not be loaded')}
        </div>
      ) : settingsQuery.isLoading || !draft ? (
        <div className={styles.inlineLoading}>Loading Watchlist Artist Settings…</div>
      ) : (
        <>
          <section className={styles.artistSettingsSection}>
            <h4>Watchlist identity</h4>
            <div className={styles.artistSettingsIdentity}>
              <Artwork
                src={
                  artist.image_url ||
                  settingsQuery.data?.artist_stats?.image_url ||
                  draft.watchlist_image_url ||
                  ''
                }
                alt={settingsQuery.data?.artist_stats?.name || draft.watchlist_name || artist.name}
                className={styles.artistSettingsPhoto}
                thumb
              />
              <div className={styles.artistSettingsIdentityBody}>
                <strong className={styles.artistSettingsIdentityName}>
                  {settingsQuery.data?.artist_stats?.name || draft.watchlist_name || artist.name}
                </strong>
                <span className={styles.muted}>
                  This is the artist currently linked to the admin Watchlist.
                </span>
                {(settingsQuery.data?.artist_stats?.genres?.length
                  ? settingsQuery.data.artist_stats.genres
                  : artist.genres
                ).length > 0 ? (
                  <span className={styles.artistSettingsGenres}>
                    {(settingsQuery.data?.artist_stats?.genres?.length
                      ? settingsQuery.data.artist_stats.genres
                      : artist.genres
                    ).join(' · ')}
                  </span>
                ) : null}
                {settingsQuery.data?.artist_stats &&
                formatMatchStat(settingsQuery.data.artist_stats) ? (
                  <span className={styles.muted}>
                    {formatMatchStat(settingsQuery.data.artist_stats)}
                  </span>
                ) : null}
                {providerIds.length > 0 ? (
                  <div className={styles.artistSettingsProviderIds}>
                    {providerIds.map(([provider, id]) => (
                      <button
                        type="button"
                        key={provider}
                        title={`Copy ${provider} ID: ${id}`}
                        onClick={() => void navigator.clipboard?.writeText(id)}
                      >
                        <strong>{provider}</strong>
                        <span>{id}</span>
                        <small>Copy</small>
                      </button>
                    ))}
                  </div>
                ) : null}
                <ArtistMatchChips artist={artist} watchlistRowId={draft.watchlist_row_id} />
                {[...artist.albums, ...(artist.eps ?? []), ...artist.singles].length ? (
                  <div className={styles.artistSettingsReleaseContext}>
                    <span>Release context in Library v2</span>
                    <MatchReleaseStrip
                      albums={[...artist.albums, ...(artist.eps ?? []), ...artist.singles]
                        .slice(0, 6)
                        .map((album) => ({
                          id: String(album.id),
                          title: album.title,
                          image: album.image_url,
                          release_date: album.release_date,
                          album_type: album.album_type,
                          total_tracks: album.track_count,
                        }))}
                    />
                  </div>
                ) : null}
              </div>
            </div>
          </section>

          <section className={styles.artistSettingsSection}>
            <h4>Quality profile</h4>
            <p className={styles.muted}>
              Quality controls allowed downloads and upgrades. It does not enable monitoring.
            </p>
            <QualityProfilePicker
              entity="artists"
              id={artist.id}
              currentProfileId={artist.quality_profile?.id ?? 1}
              currentProfileSource={artist.quality_profile_source}
              currentProfileExplicit={artist.quality_profile_explicit}
            />
          </section>

          <section className={styles.artistSettingsSection}>
            <h4>Future releases</h4>
            <p className={styles.muted}>
              These are the existing Watchlist scanner settings. They decide what is discovered and
              whether newly discovered releases enter the download pipeline.
            </p>
            <label className={styles.artistSettingsToggle}>
              <input
                type="checkbox"
                checked={draft.auto_download}
                onChange={(event) => setBoolean('auto_download', event.target.checked)}
              />
              <span>
                <strong>Auto-download new releases</strong>
                <small>Off means follow/discover only; releases are not added to Wanted.</small>
              </span>
            </label>
            <fieldset className={styles.artistSettingsFieldset}>
              <legend>Release types</legend>
              {(
                [
                  ['include_albums', 'Albums'],
                  ['include_eps', 'EPs'],
                  ['include_singles', 'Singles'],
                  ['include_live', 'Live'],
                  ['include_remixes', 'Remixes'],
                  ['include_acoustic', 'Acoustic'],
                  ['include_compilations', 'Compilations'],
                  ['include_instrumentals', 'Instrumentals'],
                ] as const
              ).map(([field, label]) => (
                <label key={field} className={styles.checkOption}>
                  <input
                    type="checkbox"
                    checked={draft[field]}
                    onChange={(event) => setBoolean(field, event.target.checked)}
                  />
                  {label}
                </label>
              ))}
            </fieldset>
            <div className={styles.artistSettingsGrid}>
              <label>
                <span>Discovery lookback</span>
                <select
                  className={styles.select}
                  value={draft.lookback_days == null ? '' : String(draft.lookback_days)}
                  onChange={(event) =>
                    setDraft((current) =>
                      current
                        ? {
                            ...current,
                            lookback_days:
                              event.target.value === '' ? null : Number(event.target.value),
                          }
                        : current,
                    )
                  }
                >
                  <option value="">Use global setting</option>
                  <option value="0">From now on</option>
                  <option value="7">Last 7 days</option>
                  <option value="30">Last 30 days</option>
                  <option value="90">Last 90 days</option>
                  <option value="365">Last year</option>
                </select>
              </label>
              <label>
                <span>Preferred metadata provider</span>
                <select
                  className={styles.select}
                  value={draft.preferred_metadata_source ?? ''}
                  onChange={(event) =>
                    setDraft((current) =>
                      current
                        ? {
                            ...current,
                            preferred_metadata_source: event.target.value || null,
                          }
                        : current,
                    )
                  }
                >
                  <option value="">
                    App default
                    {settingsQuery.data?.global_metadata_source
                      ? ` (${settingsQuery.data.global_metadata_source})`
                      : ''}
                  </option>
                  {sourceOptions.map((source) => (
                    <option key={source} value={source}>
                      {source.charAt(0).toUpperCase() + source.slice(1)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Library-v2 discography rule</span>
                <select
                  className={styles.select}
                  value={draft.monitor_new_items}
                  onChange={(event) =>
                    setDraft((current) =>
                      current
                        ? {
                            ...current,
                            monitor_new_items: event.target.value as 'all' | 'new' | 'none',
                          }
                        : current,
                    )
                  }
                >
                  <option value="all">Monitor newly discovered releases</option>
                  <option value="new">Only releases newer than the last sync</option>
                  <option value="none">Do not monitor newly discovered releases</option>
                </select>
              </label>
            </div>
            {save.isError ? (
              <div className={styles.mutationError} role="alert">
                {mutationErrorMessage(save.error, 'Artist settings could not be saved')}
              </div>
            ) : save.isSuccess ? (
              <div className={styles.mutationSuccess} role="status">
                Watchlist Artist Settings saved.
              </div>
            ) : null}
            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnPrimary}
                data-requires-write=""
                disabled={save.isPending || !canWrite}
                onClick={() => {
                  if (canWrite) save.mutate(draft);
                }}
              >
                {save.isPending ? 'Saving…' : 'Save future-release settings'}
              </button>
            </div>
          </section>

          <section className={styles.artistSettingsSection}>
            <h4>Existing releases and tracks</h4>
            <p className={styles.muted}>
              These actions change Wanted state for items already shown in Library v2. They do not
              change the Watchlist bookmark or quality profile.
            </p>
            <div className={styles.artistSettingsActions}>
              <button
                type="button"
                className={styles.toolButton}
                data-requires-write=""
                disabled={bulkBusy !== null || !canWrite}
                onClick={() =>
                  void applyExistingReleaseStrategy('all', true, 'Monitor all existing releases')
                }
              >
                {bulkBusy === 'Monitor all existing releases' ? 'Applying…' : 'Monitor all'}
              </button>
              <button
                type="button"
                className={styles.toolButton}
                data-requires-write=""
                disabled={bulkBusy !== null || !canWrite}
                onClick={() =>
                  void applyExistingReleaseStrategy('missing', true, 'Monitor missing releases')
                }
              >
                {bulkBusy === 'Monitor missing releases' ? 'Applying…' : 'Monitor missing only'}
              </button>
              <button
                type="button"
                className={styles.btnDanger}
                data-requires-write=""
                disabled={bulkBusy !== null || !canWrite}
                onClick={() =>
                  void applyExistingReleaseStrategy('all', false, 'Unmonitor existing releases')
                }
              >
                {bulkBusy === 'Unmonitor existing releases' ? 'Applying…' : 'Unmonitor all'}
              </button>
            </div>
            {bulkMessage ? (
              <div
                className={
                  bulkMessage.tone === 'ok' ? styles.mutationSuccess : styles.mutationError
                }
                role={bulkMessage.tone === 'ok' ? 'status' : 'alert'}
              >
                {bulkMessage.text}
              </div>
            ) : null}
          </section>
        </>
      )}
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
  maintenance: 'Maintenance',
  info: 'Info',
};

/** Merged pipeline history for one artist or album — grabs, imports,
 *  quarantine, catalog moves and physical deletes, not just raw downloads
 *  (§A6/C3 artist scope; §52.9 album scope reuses the same resolver). */
function HistoryModal({
  scope,
  entityId,
  onClose,
}: {
  scope: 'artist' | 'album';
  entityId: number;
  onClose: () => void;
}) {
  const historyQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'history', scope, entityId],
    queryFn: () =>
      scope === 'album'
        ? fetchLibraryV2AlbumHistory(entityId)
        : fetchLibraryV2ArtistHistory(entityId),
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
        ) : historyQuery.isError ? (
          // iss29-C04: "no recorded history" is a claim about the journals.
          // A failed fetch knows nothing about them.
          <div className={styles.inlineLoading}>
            {mutationErrorMessage(historyQuery.error, 'History could not be loaded.')}
          </div>
        ) : rows.length === 0 ? (
          <div className={styles.inlineLoading}>No recorded history for this {scope} yet.</div>
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

/** Artist/album delete shortcuts use the same two-mode file-removal dialog
 * as Manage Tracks. Whole-entity removal happens only after the selected
 * file command succeeds, so a partial disk failure never orphans live files. */
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
  return (
    <UnifiedFileRemovalDialog
      entity={entity}
      eid={id}
      title={title}
      removeWholeEntity
      onDone={onDone}
      onCancel={onClose}
    />
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
  quality_profile_source?: LibraryV2QualityProfileSource;
  quality_profile_explicit?: boolean;
  /** Context for the reassign modal — who this album is filed under today,
   *  and its cover for the modal hero. Optional: the menu works without them. */
  artist_name?: string | null;
  image_url?: string | null;
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
            currentProfileSource={album.quality_profile_source}
            currentProfileExplicit={album.quality_profile_explicit}
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
      {error ? (
        <div className={styles.searchError} role="alert">
          {error}
        </div>
      ) : null}
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
export function AlbumOverflowMenu({
  album,
  onDeleted,
}: {
  album: AlbumDetailTarget;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  const [open, setOpen] = useState(false);
  const [showSubmenu, setShowSubmenu] = useState(false);
  const [showRetag, setShowRetag] = useState(false);
  const [showReorganize, setShowReorganize] = useState(false);
  const [showReassign, setShowReassign] = useState(false);
  const [showArtPicker, setShowArtPicker] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const replaygain = useMutation({
    mutationFn: async () => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      const jobId = await startLibraryV2AlbumReplayGain(album.id);
      const jobError = await awaitBulkJob(queryClient, jobId);
      if (jobError) throw new Error(jobError);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      window.showToast?.('Album ReplayGain analyzed and written.', 'success');
    },
    onError: (error) => {
      window.showToast?.(mutationErrorMessage(error, 'ReplayGain analysis failed'), 'error');
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
            data-requires-write=""
            disabled={!canWrite}
            onClick={() => {
              if (!canWrite) return;
              setShowRetag(true);
              setOpen(false);
            }}
          >
            Preview retag
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            data-requires-write=""
            disabled={replaygain.isPending || !canWrite}
            onClick={() => {
              if (!canWrite) return;
              replaygain.mutate();
              setOpen(false);
            }}
          >
            {replaygain.isPending ? 'Analyzing ReplayGain…' : 'Analyze ReplayGain'}
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            data-requires-write=""
            disabled={!canWrite}
            onClick={() => {
              if (!canWrite) return;
              setShowReorganize(true);
              setOpen(false);
            }}
          >
            Reorganize
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            data-requires-write=""
            disabled={!canWrite}
            onClick={() => {
              if (!canWrite) return;
              setShowReassign(true);
              setOpen(false);
            }}
          >
            Reassign to another artist…
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            data-requires-write=""
            disabled={!canWrite}
            onClick={() => {
              if (!canWrite) return;
              setShowArtPicker(true);
              setOpen(false);
            }}
          >
            Change cover
          </button>
          <button
            type="button"
            className={styles.overflowMenuItem}
            onClick={() => {
              setShowHistory(true);
              setOpen(false);
            }}
          >
            History
          </button>
          <div
            className={styles.submenuContainer}
            onMouseEnter={() => {
              if (canWrite) setShowSubmenu(true);
            }}
            onMouseLeave={() => setShowSubmenu(false)}
          >
            <button
              type="button"
              className={styles.overflowMenuItem}
              data-requires-write=""
              disabled={!canWrite}
              onClick={(e) => {
                e.stopPropagation();
                if (!canWrite) return;
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
            data-requires-write=""
            disabled={!canWrite}
            onClick={() => {
              if (!canWrite) return;
              setShowDelete(true);
              setOpen(false);
            }}
          >
            Delete
          </button>
        </div>
      ) : null}
      {replaygain.isError ? (
        <span className={styles.mutationError} role="alert">
          <span>{mutationErrorMessage(replaygain.error, 'ReplayGain analysis failed')}</span>
          <button
            type="button"
            className={styles.inlineRetry}
            data-requires-write=""
            disabled={replaygain.isPending || !canWrite}
            onClick={() => {
              if (canWrite) replaygain.mutate();
            }}
          >
            Retry
          </button>
        </span>
      ) : null}
      {showHistory ? (
        <HistoryModal scope="album" entityId={album.id} onClose={() => setShowHistory(false)} />
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
      {showReassign ? (
        <ReassignModal
          albumId={album.id}
          albumTitle={album.title}
          currentArtist={album.artist_name || 'its current artist'}
          imageUrl={album.image_url ?? undefined}
          onClose={() => setShowReassign(false)}
          // Nothing has moved yet — the files are staged with a hint and the
          // import pipeline does the rest. Refresh anyway: the album's own
          // rows change the moment that import lands, and a stale view here
          // is what makes a user click Reassign a second time.
          onApplied={() => {
            void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
          }}
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

/** Existing repair workers exposed with explicit user-facing scope. */
const MAINTENANCE_JOBS: Array<{
  id: string;
  label: string;
  desc: string;
  scope: 'artist' | 'library';
}> = [
  {
    id: 'metadata_gap_filler',
    label: 'Find Missing Metadata',
    desc: 'Find missing identifiers and metadata fields for this artist’s tracks.',
    scope: 'artist',
  },
  {
    id: 'album_tag_consistency',
    label: 'Check Album Tags',
    desc: 'Find inconsistent album artist, year and artwork tags for this artist.',
    scope: 'artist',
  },
  {
    id: 'quality_upgrade_scan',
    label: 'Find Quality Upgrades',
    desc: 'Queue monitored tracks across the library that are below their profile cutoff.',
    scope: 'library',
  },
];

async function awaitMaintenanceResult(
  jobId: string,
): Promise<Record<string, number | string | null>> {
  for (let i = 0; i < 900; i += 1) {
    const state = await fetchLibraryV2JobStatus(jobId);
    if (!state.running) {
      if (state.error) throw new Error(state.error);
      if (!state.result) throw new Error('Job finished without a result');
      return state.result;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error('Timed out waiting for the job');
}

export function MaintenanceModal({
  artistId,
  artistName,
  onClose,
}: {
  artistId: number;
  artistName: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  const [state, setState] = useState<Record<string, 'queued' | 'error'>>({});
  const [reconcile, setReconcile] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [reconcileResult, setReconcileResult] = useState<string | null>(null);
  const [wishlist, setWishlist] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [wishlistResult, setWishlistResult] = useState<string | null>(null);

  const runReconcile = () => {
    if (!canWrite) return;
    setReconcile('running');
    setReconcileResult(null);
    void reconcileUnmappedArtists()
      .then(awaitMaintenanceResult)
      .then((r) => {
        setReconcile('done');
        setReconcileResult(
          `Scanned ${r.scanned ?? 0} · matched ${r.matched ?? 0} · split ${r.split ?? 0} · still unmatched ${r.unmatched ?? 0}`,
        );
        void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      })
      .catch((e) => {
        setReconcile('error');
        setReconcileResult(e instanceof Error ? e.message : 'Reconcile failed');
      });
  };

  const runWishlistReconcile = () => {
    if (!canWrite) return;
    setWishlist('running');
    setWishlistResult(null);
    void reconcileWishlist()
      .then(awaitMaintenanceResult)
      .then((r) => {
        setWishlist('done');
        setWishlistResult(
          `${r.wanted ?? 0} wanted · ${r.wishlisted ?? 0} in wishlist · ${r.mirrored ?? 0} synced`,
        );
        void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      })
      .catch((e) => {
        setWishlist('error');
        setWishlistResult(e instanceof Error ? e.message : 'Reconcile failed');
      });
  };

  return (
    <ModalShell title="Library Health & Repair" wide onClose={onClose}>
      <p className={styles.qpSubtitle}>
        Review and repair catalog links, monitoring and file metadata. Every tool states whether it
        is limited to <strong>{artistName}</strong> or scans the entire library. Progress remains
        visible under Stats → Repair jobs.
      </p>
      <div className={styles.maintenanceGrid}>
        <section className={styles.maintenanceSection}>
          <div className={styles.maintenanceSectionHeader}>
            <div>
              <strong>Catalog & monitoring</strong>
              <span>Repair cross-system identities and acquisition intent.</span>
            </div>
            <span className={styles.maintenanceScopeBadge}>Entire library</span>
          </div>
          <div className={styles.qpList}>
            <button
              type="button"
              className={styles.qpOption}
              data-requires-write=""
              disabled={reconcile === 'running' || !canWrite}
              onClick={runReconcile}
            >
              <span className={styles.qpName}>
                Match Unmapped Artists
                {reconcile === 'running' ? <span className={styles.statusOk}>running…</span> : null}
                {reconcile === 'done' ? <span className={styles.statusOk}>done</span> : null}
                {reconcile === 'error' ? <span className={styles.statusWarn}>failed</span> : null}
              </span>
              <span className={styles.qpDesc}>
                Resolve provider identities and split genuine collaboration names into their real
                artists.
                {reconcileResult ? ` — ${reconcileResult}` : ''}
              </span>
            </button>
            <button
              type="button"
              className={styles.qpOption}
              data-requires-write=""
              disabled={wishlist === 'running' || !canWrite}
              onClick={runWishlistReconcile}
            >
              <span className={styles.qpName}>
                Synchronize Wanted & Wishlist
                {wishlist === 'running' ? <span className={styles.statusOk}>running…</span> : null}
                {wishlist === 'done' ? <span className={styles.statusOk}>done</span> : null}
                {wishlist === 'error' ? <span className={styles.statusWarn}>failed</span> : null}
              </span>
              <span className={styles.qpDesc}>
                Re-add wanted missing tracks, prune tracks no longer wanted and preserve deliberate
                cancels.
                {wishlistResult ? ` — ${wishlistResult}` : ''}
              </span>
            </button>
          </div>
        </section>

        {(['artist', 'library'] as const).map((scope) => (
          <section key={scope} className={styles.maintenanceSection}>
            <div className={styles.maintenanceSectionHeader}>
              <div>
                <strong>{scope === 'artist' ? 'Artist files & tags' : 'Library-wide scans'}</strong>
                <span>
                  {scope === 'artist'
                    ? `Only files linked to ${artistName}.`
                    : 'Potentially checks every monitored catalog entry.'}
                </span>
              </div>
              <span
                className={`${styles.maintenanceScopeBadge} ${
                  scope === 'artist' ? styles.maintenanceScopeArtist : ''
                }`}
              >
                {scope === 'artist' ? 'This artist' : 'Entire library'}
              </span>
            </div>
            <div className={styles.qpList}>
              {MAINTENANCE_JOBS.filter((job) => job.scope === scope).map((job) => (
                <button
                  key={job.id}
                  type="button"
                  className={styles.qpOption}
                  data-requires-write=""
                  disabled={state[job.id] === 'queued' || !canWrite}
                  onClick={() => {
                    if (!canWrite) return;
                    void runRepairJob(
                      job.id,
                      job.scope === 'artist' ? { id: artistId, name: artistName } : undefined,
                    )
                      .then(() => setState((s) => ({ ...s, [job.id]: 'queued' })))
                      .catch(() => setState((s) => ({ ...s, [job.id]: 'error' })));
                  }}
                >
                  <span className={styles.qpName}>
                    {job.label}
                    {state[job.id] === 'queued' ? (
                      <span className={styles.statusOk}>queued</span>
                    ) : null}
                    {state[job.id] === 'error' ? (
                      <span className={styles.statusWarn}>failed to queue</span>
                    ) : null}
                  </span>
                  <span className={styles.qpDesc}>{job.desc}</span>
                </button>
              ))}
            </div>
          </section>
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

export function ManageTracksDuplicatesTab({ artistId }: { artistId: number }) {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
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
    if (!canWrite) return;
    withBusy(trackId, unlinkLibraryV2Duplicate(trackId));
  }

  function moveFile(fromTrackId: number, toTrackId: number) {
    if (!canWrite) return;
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
        other version (disk untouched — run Rename/Reorganize after). Physical files you no longer
        need can then be removed from the <strong>Files</strong> tab.
      </p>
      {rowError ? <div className={styles.searchError}>{rowError}</div> : null}
      <div className={styles.resultsWrap}>
        {dupesQuery.isLoading ? (
          <div className={styles.inlineLoading}>Scanning for duplicates…</div>
        ) : dupesQuery.isError ? (
          <QueryFailure
            error={dupesQuery.error}
            fallback="Could not check for duplicate tracks."
            retry={() => void dupesQuery.refetch()}
          />
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
                        data-requires-write=""
                        disabled={busyTracks.has(p.single.track_id) || !canWrite}
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
                        data-requires-write=""
                        disabled={busyTracks.has(p.album.track_id) || !canWrite}
                        title="Attach the album's file to the single version instead (file stays on disk; the album track stops being wanted)"
                        onClick={() => moveFile(p.album.track_id, p.single.track_id)}
                      >
                        {busyTracks.has(p.album.track_id) ? '…' : 'Move → single'}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className={styles.toolButton}
                      data-requires-write=""
                      disabled={busyTracks.has(p.single.track_id) || !canWrite}
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
export function ArtistFilesTab({ artistId }: { artistId: number }) {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
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
      ) : filesQuery.isError ? (
        <QueryFailure
          error={filesQuery.error}
          fallback="Could not load artist files."
          retry={() => void filesQuery.refetch()}
        />
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
          data-requires-write=""
          disabled={selected.size === 0 || !canWrite}
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
            void queryClient.invalidateQueries({
              queryKey: LIBRARY_V2_QUERY_KEY,
            });
          }}
          onCancel={() => setConfirming(false)}
        />
      ) : null}
    </>
  );
}

/** Shared §52.11 dialog used by Manage Tracks and artist/album shortcuts. */
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
  return (
    <UnifiedFileRemovalDialog
      entity={entity}
      eid={eid}
      fileIds={fileIds}
      onDone={onDone}
      onCancel={onCancel}
    />
  );
}

function middleEllipsis(path: string, max = 76): string {
  if (path.length <= max) return path;
  const side = Math.floor((max - 1) / 2);
  return `${path.slice(0, side)}…${path.slice(-side)}`;
}

export function UnifiedFileRemovalDialog({
  entity,
  eid,
  fileIds,
  title,
  removeWholeEntity = false,
  onDone,
  onCancel,
}: {
  entity: 'artists' | 'albums';
  eid: number;
  fileIds?: number[];
  title?: string;
  removeWholeEntity?: boolean;
  onDone: () => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'database_only' | 'permanent'>('database_only');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revealedPaths, setRevealedPaths] = useState<Set<string>>(() => new Set());
  const entityImpact = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'delete-preview', entity, eid],
    queryFn: () => fetchLibraryV2ArtistDeletePreview(eid),
    enabled: removeWholeEntity && entity === 'artists',
  });
  const preview = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'file-delete-preview', entity, eid, fileIds],
    queryFn: () => fetchLibraryV2FileDeletePreview(entity, eid, fileIds),
  });
  const physical = preview.data;
  const trackCount = new Set((physical?.files ?? []).flatMap((file) => file.track_ids ?? [])).size;
  const physicalReady = Boolean(physical && physical.file_count > 0 && physical.unsafe_count === 0);
  const canExecute = Boolean(
    physical && !busy && (mode === 'database_only' || (physicalReady && confirmed)),
  );

  async function execute() {
    if (!physical) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === 'database_only') {
        if (physical.file_count > 0) {
          await removeLibraryV2FileRecords(entity, eid, fileIds);
        }
        if (removeWholeEntity) {
          await deleteLibraryV2Entity(entity, eid);
        }
      } else {
        const operation = await deleteLibraryV2Files(entity, eid, physical.preview_token, fileIds);
        if (operation.status !== 'completed') {
          throw new Error(
            `Permanent deletion was ${operation.status}; the library entry was kept for review.`,
          );
        }
        if (removeWholeEntity) {
          await deleteLibraryV2Entity(entity, eid);
        }
      }
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      onDone();
    } catch (caught) {
      setError(mutationErrorMessage(caught, 'File removal failed'));
    } finally {
      setBusy(false);
    }
  }

  const heading = removeWholeEntity
    ? `Remove ${entity === 'artists' ? 'artist' : 'album'}`
    : 'Remove selected files';
  const subject = title || physical?.title || `${fileIds?.length ?? 0} selected files`;

  return (
    <ModalShell title={heading} wide onClose={onCancel}>
      <p>
        Choose what should happen to <strong>{subject}</strong>. Monitoring and Wanted state are
        recalculated after the file records change.
      </p>
      {preview.isLoading ? <p className={styles.muted}>Building file summary…</p> : null}
      {preview.isError ? (
        <div className={styles.mutationError} role="alert">
          {mutationErrorMessage(preview.error, 'File summary could not be loaded')}
        </div>
      ) : null}
      {physical ? (
        <>
          <div className={styles.fileRemovalSummary}>
            <span>
              <strong>{trackCount}</strong>
              {removeWholeEntity ? ' file-linked tracks' : ' tracks'}
            </span>
            <span>
              <strong>{physical.file_count}</strong> files
            </span>
            <span>
              <strong>{formatFileSize(physical.total_size)}</strong> total
            </span>
          </div>
          {physical.files.length > 0 ? (
            <details className={styles.fileRemovalPaths}>
              <summary>Review paths ({physical.files.length})</summary>
              <ul className={styles.fileDeleteList}>
                {physical.files.map((file) => {
                  const path = file.path ?? file.stored_paths[0] ?? 'Unresolved file';
                  const pathRevealed = revealedPaths.has(path);
                  return (
                    <li key={path || file.file_ids.join('-')}>
                      <span className={styles.fileRemovalPathRow}>
                        <span
                          className={pathRevealed ? styles.fileRemovalPathRevealed : undefined}
                          title={path}
                        >
                          {pathRevealed ? path : middleEllipsis(path)}
                        </span>
                        <button
                          type="button"
                          className={styles.pathCopyButton}
                          title={pathRevealed ? 'Collapse full path' : 'Reveal full path'}
                          aria-label={pathRevealed ? 'Collapse full path' : 'Reveal full path'}
                          onClick={() =>
                            setRevealedPaths((current) => {
                              const next = new Set(current);
                              if (next.has(path)) next.delete(path);
                              else next.add(path);
                              return next;
                            })
                          }
                        >
                          {pathRevealed ? 'Hide' : 'Reveal'}
                        </button>
                        <button
                          type="button"
                          className={styles.pathCopyButton}
                          title="Copy full path"
                          aria-label="Copy full path"
                          onClick={() => void navigator.clipboard?.writeText(path)}
                        >
                          Copy
                        </button>
                      </span>
                      <small>
                        {file.album_title ? `${file.album_title} · ` : ''}
                        {formatFileSize(file.size ?? 0)}
                        {!file.deletable ? ` · permanent delete blocked: ${file.reason}` : ''}
                      </small>
                    </li>
                  );
                })}
              </ul>
            </details>
          ) : (
            <p className={styles.muted}>No linked files remain. Database removal is still safe.</p>
          )}
        </>
      ) : null}
      {entityImpact.data ? (
        <p className={styles.muted}>
          Removing the artist also removes {entityImpact.data.albums} owned release
          {entityImpact.data.albums === 1 ? '' : 's'} and {entityImpact.data.tracks} catalog track
          {entityImpact.data.tracks === 1 ? '' : 's'}.
          {entityImpact.data.detached_albums > 0
            ? ` ${entityImpact.data.detached_albums} featured ${
                entityImpact.data.detached_albums === 1 ? 'appearance stays' : 'appearances stay'
              } in the library.`
            : ''}
        </p>
      ) : null}

      <div className={styles.fileRemovalChoices}>
        <label
          className={`${styles.fileRemovalChoice} ${
            mode === 'database_only' ? styles.fileRemovalChoiceActive : ''
          }`}
        >
          <input
            type="radio"
            name="file-removal-mode"
            checked={mode === 'database_only'}
            onChange={() => {
              setMode('database_only');
              setConfirmed(false);
            }}
          />
          <span>
            <strong>Remove from library database only</strong>
            <small>Keep every physical file on disk.</small>
          </span>
        </label>
        <label
          className={`${styles.fileRemovalChoice} ${
            mode === 'permanent' ? styles.fileRemovalChoiceDanger : ''
          }`}
        >
          <input
            type="radio"
            name="file-removal-mode"
            checked={mode === 'permanent'}
            disabled={!physicalReady}
            onChange={() => setMode('permanent')}
          />
          <span>
            <strong>Permanently delete files</strong>
            <small>Remove the library records and delete the corresponding disk files.</small>
          </span>
        </label>
      </div>
      {physical && physical.unsafe_count > 0 ? (
        <div className={styles.mutationError} role="alert">
          Permanent deletion is blocked for {physical.unsafe_count} unsafe or unresolved file
          {physical.unsafe_count === 1 ? '' : 's'}. Database-only removal remains available.
        </div>
      ) : null}
      {mode === 'permanent' ? (
        <label className={styles.fileDeleteConfirm}>
          <input
            type="checkbox"
            checked={confirmed}
            disabled={!physicalReady || busy}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          I understand this permanently deletes the selected files from disk.
        </label>
      ) : null}
      {error ? (
        <div className={styles.mutationError} role="alert">
          {error}
        </div>
      ) : null}
      <div className={styles.modalActions}>
        <button type="button" className={styles.btnGhost} disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className={mode === 'permanent' ? styles.btnDanger : styles.btnPrimary}
          disabled={!canExecute}
          onClick={() => void execute()}
        >
          {busy
            ? 'Working…'
            : mode === 'permanent'
              ? 'Permanently delete'
              : 'Remove from library database'}
        </button>
      </div>
    </ModalShell>
  );
}

// --- page root ---------------------------------------------------------------

export function LibraryV2Page() {
  useReactPageShell('library');
  useLibraryChanged();
  const search = Route.useSearch();
  const enabledQuery = useQuery(libraryV2EnabledQueryOptions());

  if (enabledQuery.isError) {
    return (
      <div className={styles.page}>
        <div className={styles.emptyState} role="alert">
          <h2>Library availability could not be verified</h2>
          <p>{mutationErrorMessage(enabledQuery.error, 'The availability check failed.')}</p>
          <button type="button" onClick={() => void enabledQuery.refetch()}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (enabledQuery.data?.enabled === false) {
    return (
      <div className={styles.page}>
        <div className={styles.emptyState}>
          <h2>Library is unavailable</h2>
          <p>This profile does not have Library access, or the server is still starting.</p>
        </div>
      </div>
    );
  }

  // ldp-01: `<source>:<provider id>` — an artist that may not be in the
  // catalogue at all. Split on the FIRST colon only; provider ids can contain
  // one (MusicBrainz URLs, Bandcamp slugs).
  const discoverSplit = search.discover ? search.discover.indexOf(':') : -1;
  const discover =
    search.discover && discoverSplit > 0
      ? {
          source: search.discover.slice(0, discoverSplit),
          providerId: search.discover.slice(discoverSplit + 1),
          name: search.discoverName ?? '',
        }
      : null;

  const canWrite = enabledQuery.data?.canWrite === true;

  return (
    <LibraryV2CanWriteContext.Provider value={canWrite}>
      <MirrorStatusBanner />
      {!canWrite ? (
        <div className={styles.emptyState}>
          Read-only: library changes require the admin profile.
        </div>
      ) : null}
      {search.album ? (
        <AlbumDetailView albumId={search.album} />
      ) : discover && !search.artist ? (
        <DiscoveryArtistView
          source={discover.source}
          providerId={discover.providerId}
          name={discover.name}
        />
      ) : search.artist ? (
        <ArtistDetailView artistId={search.artist} />
      ) : search.section === 'wanted' ? (
        <WantedIndexView />
      ) : (
        <ArtistIndexView />
      )}
    </LibraryV2CanWriteContext.Provider>
  );
}

/** Split-brain guard (audit P0-04): monitor changes mirror into the legacy
 *  wishlist through a transactional outbox. When ops are stuck or failed,
 *  say so — the UI must not show "monitored" while the pipeline never
 *  learned about it. */
export function MirrorStatusBanner() {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  const statusQuery = useQuery(libraryV2MirrorStatusQueryOptions());
  const retry = useMutation({
    mutationFn: () => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      return retryLibraryV2Mirror();
    },
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: [...LIBRARY_V2_QUERY_KEY, 'mirror-status'],
      }),
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
        data-requires-write=""
        disabled={retry.isPending || !canWrite}
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
  const artistFilter = useUrlSyncedFilter(
    search.q,
    (value) => void navigate({ search: (prev) => ({ ...prev, q: value, page: 1 }) }),
  );

  // Only fetched for the table view (D6) — the card grid doesn't use either.
  const isTableView = search.view === 'table';
  const profilesQuery = useQuery({
    ...libraryV2QualityProfilesQueryOptions(),
    enabled: isTableView,
  });
  const prefsQuery = useQuery({
    ...libraryV2UiPreferencesQueryOptions(),
    enabled: isTableView,
  });
  const profileNameById = new Map((profilesQuery.data ?? []).map((p) => [p.id, p.name]));
  const artistTableColumns = prefsQuery.data?.artist_table.columns ?? {
    quality_profile: false,
    genres: false,
    added: false,
    size: false,
  };
  const artistColumnOrder = mergeColumnOrder<keyof LibraryV2ArtistTableColumns>(
    prefsQuery.data?.artist_table.column_order,
    ['quality_profile', 'genres', 'added', 'size'],
  );

  // rev25-06/rev25-11: the size roll-up is requested explicitly by whichever
  // view can actually render it, not derived from the preference server-side
  // — so toggling the column is part of the query key and refetches, instead
  // of rendering "—" from an already-cached total_size_bytes: 0 payload.
  const artistsQuery = useQuery(
    libraryV2ArtistsQueryOptions({
      q: search.q,
      sort: search.sort,
      page: search.page,
      monitored: search.monitored,
      includeSize: isTableView && Boolean(artistTableColumns.size),
    }),
  );

  const artists = artistsQuery.data?.artists ?? [];
  const pagination = artistsQuery.data?.pagination;
  // iss29-C03: a FAILED fetch is not an empty library. `retry: 1` means that
  // after the retry `isLoading` is false and `data` undefined, which used to
  // render "Your library is empty — Import library" to a user with 900 artists
  // and offer them a full re-import. `AlbumDetailView` already reads `isError`;
  // this was the inconsistency, not the house style.
  const isEmpty =
    !artistsQuery.isLoading && !artistsQuery.isError && artists.length === 0 && !search.q;

  // showLibraryDownloadsSection (shared-helpers.js) renders the per-artist
  // download bubbles on the library page. It is bound to `artistDownloadBubbles`
  // — module state in core.js, fed by download events, not by page load — so it
  // cannot move in here. It appends into this host, which is rendered with NO
  // React children so the vanilla function owns the subtree outright and React
  // never reconciles it away. Kept from the vanilla library page that this page
  // replaced; tests/test_artist_bubble_hydrate_hardening.py guards the seam.
  const downloadsHost = useRef<HTMLDivElement>(null);
  useEffect(() => {
    window.showLibraryDownloadsSection?.();
  }, []);

  return (
    <div className={styles.page}>
      <header className={`${styles.header} library-header`}>
        <div>
          <h1 className={styles.title}>Library</h1>
          <p className={styles.subtitle}>
            {pagination ? `${pagination.total_count} artists` : 'Experimental library manager'}
          </p>
        </div>
        <div className={styles.headerActions}>
          <GlobalAutomaticSearchButton />
          <ImportButton hasArtists={artists.length > 0} />
        </div>
      </header>

      <div className={styles.toolbar}>
        <LibrarySectionTabs />
        <input
          id="library-search-input"
          className={styles.searchInput}
          type="text"
          placeholder="Filter artists…"
          value={artistFilter.value}
          onChange={(e) => artistFilter.onChange(e.target.value)}
        />
        <select
          id="watchlist-filter"
          className={styles.select}
          value={search.monitored}
          onChange={(e) =>
            void navigate({
              search: (p) => ({
                ...p,
                monitored: e.target.value as typeof p.monitored,
                page: 1,
              }),
            })
          }
        >
          <option value="all">All</option>
          <option value="monitored">Monitored</option>
          <option value="unmonitored">Unmonitored</option>
        </select>
        <select
          id="library-sort"
          className={styles.select}
          value={search.sort}
          onChange={(e) =>
            void navigate({
              search: (p) => ({
                ...p,
                sort: e.target.value as typeof p.sort,
                page: 1,
              }),
            })
          }
        >
          <option value="name">Name</option>
          <option value="added">Recently added</option>
          <option value="albums">Album count</option>
          <option value="tracks">Track count</option>
        </select>
        <div className={styles.viewToggle} id="library-view-toggle">
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
        {isTableView ? (
          <ArtistTableOptionsMenu columns={artistTableColumns} columnOrder={artistColumnOrder} />
        ) : null}
      </div>

      {/* Host for the vanilla download bubbles — see the note above. Sits
          directly above the artist list, where the vanilla page put it. */}
      <div ref={downloadsHost} data-library-downloads-host="" />

      {artistsQuery.isLoading ? (
        <div className={styles.loading}>Loading…</div>
      ) : artistsQuery.isError ? (
        // iss29-C03: say the list could not be loaded. Anything else here is a
        // statement about the user's library derived from a failed request.
        <div className={styles.emptyState}>
          <h2>Could not load your library</h2>
          <p>{mutationErrorMessage(artistsQuery.error, 'The library list failed to load.')}</p>
          <button type="button" onClick={() => void artistsQuery.refetch()}>
            Try again
          </button>
        </div>
      ) : isEmpty ? (
        <LibraryEmptyState />
      ) : search.view === 'table' ? (
        <ArtistTable
          artists={artists}
          columns={artistTableColumns}
          columnOrder={artistColumnOrder}
          profileNameById={profileNameById}
        />
      ) : (
        <ArtistCards artists={artists} />
      )}

      {pagination && pagination.total_pages > 1 ? (
        <div className={styles.pagination} id="library-pagination">
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

// --- Library sections -------------------------------------------------------

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
        className={search.section === 'wanted' ? styles.viewActive : ''}
        onClick={() =>
          void navigate({
            search: (previous) => ({
              ...previous,
              section: 'wanted',
              q: '',
              artist: undefined,
              album: undefined,
              page: 1,
            }),
          })
        }
      >
        Wanted
      </button>
    </div>
  );
}

// --- wanted (§64 I2; library-wide Missing / Cutoff Unmet, Lidarr-style) ----

const WANTED_KIND_LABELS: Record<LibraryV2WantedKind, string> = {
  missing: 'Missing',
  cutoff_unmet: 'Cutoff Unmet',
};

/** Same format/resolution summary as `QualityDisplay`, standalone — a wanted
 *  row's `file` only carries the handful of quality fields the backend
 *  evaluated against, not the full `LibraryV2TrackFile` shape. */
export function formatWantedFileQuality(file: LibraryV2WantedRow['file']): string | null {
  if (!file) return null;
  const fmt = (file.format ?? '').toUpperCase() || null;
  const bitDepth = file.bit_depth ? `${file.bit_depth}bit` : null;
  const sampleRate = file.sample_rate
    ? `${Number((file.sample_rate / 1000).toFixed(file.sample_rate % 1000 === 0 ? 0 : 1))}kHz`
    : null;
  const bitrate =
    !bitDepth && !sampleRate && file.bitrate ? `${Math.round(file.bitrate)}kbps` : null;
  const resolution = [bitDepth, sampleRate, bitrate].filter(Boolean).join('/');
  return [fmt, resolution || null].filter(Boolean).join(' · ') || null;
}

function WantedIndexView() {
  const navigate = useNavigate();
  const canWrite = useLibraryV2CanWrite();
  const search = Route.useSearch();
  const wantedFilter = useUrlSyncedFilter(
    search.q,
    (value) => void navigate({ search: (p) => ({ ...p, q: value, page: 1 }) }),
  );
  // dd28-16: one shared banner + a run-sequence guard, so a slower earlier
  // search can no longer overwrite a newer one's result.
  const { banner, setBanner, busy: searchBusy, runScoped } = useScopedSearchBanner();
  const wantedQuery = useQuery(
    libraryV2WantedQueryOptions({
      q: search.q,
      page: search.page,
      wantedKind: search.wantedKind,
    }),
  );
  const rows = wantedQuery.data?.tracks ?? [];
  const pagination = wantedQuery.data?.pagination;

  function setKind(kind: LibraryV2WantedKind) {
    void navigate({ search: (p) => ({ ...p, wantedKind: kind, page: 1 }) });
  }

  function runSearch(trackId: number) {
    if (searchBusy || !canWrite) return;
    runScoped('tracks', trackId);
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Library</h1>
          <p className={styles.subtitle}>
            {pagination
              ? `${pagination.total_count} ${WANTED_KIND_LABELS[search.wantedKind].toLowerCase()} track${pagination.total_count === 1 ? '' : 's'}`
              : 'Wanted tracks across the whole library'}
          </p>
        </div>
      </header>

      {banner ? (
        <div className={`${styles.grabBanner} ${styles[`grab_${banner.tone}`]}`}>
          <span>{banner.text}</span>
          <button type="button" className={styles.grabBannerClose} onClick={() => setBanner(null)}>
            ×
          </button>
        </div>
      ) : null}

      <div className={styles.toolbar}>
        <LibrarySectionTabs />
        <div className={styles.viewToggle} aria-label="Wanted kind">
          {LIBRARY_V2_WANTED_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              className={search.wantedKind === kind ? styles.viewActive : ''}
              onClick={() => setKind(kind)}
            >
              {WANTED_KIND_LABELS[kind]}
            </button>
          ))}
        </div>
        <input
          className={styles.searchInput}
          type="text"
          placeholder="Filter by track, album or artist…"
          value={wantedFilter.value}
          onChange={(e) => wantedFilter.onChange(e.target.value)}
        />
      </div>

      {wantedQuery.isLoading ? (
        <div className={styles.loading}>Loading…</div>
      ) : wantedQuery.isError ? (
        // iss29-C05: this screen is consulted to decide whether Automatic
        // Search has anything to do. Claiming "everything you want is already
        // on disk" off the back of a failed request is a factual statement
        // about the library derived from no data at all.
        <div className={styles.emptyState}>
          <h2>Could not load this list</h2>
          <p>{mutationErrorMessage(wantedQuery.error, 'The wanted list failed to load.')}</p>
          <button type="button" onClick={() => void wantedQuery.refetch()}>
            Try again
          </button>
        </div>
      ) : rows.length === 0 ? (
        <div className={styles.emptyState}>
          <h2>Nothing here</h2>
          <p>
            {search.wantedKind === 'missing'
              ? 'Every monitored track you want is already on disk.'
              : 'Every monitored track on disk already meets its quality profile.'}
          </p>
        </div>
      ) : (
        <table className={styles.trackTable}>
          <thead>
            <tr>
              <th>Artist</th>
              <th>Album</th>
              <th>Track</th>
              {search.wantedKind === 'cutoff_unmet' ? <th>Quality</th> : null}
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row: LibraryV2WantedRow) => (
              <tr key={row.track_id}>
                <td>
                  <button
                    type="button"
                    className={styles.linkButton}
                    onClick={() =>
                      void navigate({
                        search: (p) => ({
                          ...p,
                          artist: row.artist.id,
                          album: undefined,
                          releases: undefined,
                        }),
                      })
                    }
                  >
                    {row.artist.name}
                  </button>
                </td>
                <td>
                  <button
                    type="button"
                    className={styles.linkButton}
                    onClick={() =>
                      void navigate({
                        search: (p) => ({ ...p, album: row.album.id }),
                      })
                    }
                  >
                    {row.album.title}
                  </button>
                </td>
                <td>{row.title}</td>
                {search.wantedKind === 'cutoff_unmet' ? (
                  <td>{formatWantedFileQuality(row.file) ?? '—'}</td>
                ) : null}
                <td className={styles.trackActions}>
                  <IconActionButton
                    icon="automatic"
                    title="Search this track"
                    requiresWrite
                    // dd28-16: one banner is shared by every row, so a second
                    // search must not start until the first has reported.
                    disabled={searchBusy}
                    onClick={() => runSearch(row.track_id)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
          remote={artist.remote_image_url}
          alt={artist.name}
          className={styles.artistThumb}
          thumb
        />
        {artist.media_server_sources?.length ? (
          <span className={styles.mediaServerBadges}>
            <MediaServerRecognitionBadge sources={artist.media_server_sources} />
          </span>
        ) : null}
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
    <div className={styles.cardGrid} id="library-artists-grid">
      {artists.map((artist) => (
        <ArtistCard
          key={artist.id}
          artist={artist}
          onOpen={(artistId) => void navigate({ search: (p) => openArtistSearch(p, artistId) })}
        />
      ))}
    </div>
  );
}

function ArtistTable({
  artists,
  columns,
  columnOrder,
  profileNameById,
}: {
  artists: LibraryV2ArtistSummary[];
  columns: LibraryV2ArtistTableColumns;
  columnOrder: (keyof LibraryV2ArtistTableColumns)[];
  profileNameById: Map<number, string>;
}) {
  const navigate = useNavigate();

  const defaultOrder: (keyof LibraryV2ArtistTableColumns)[] = [
    'quality_profile',
    'genres',
    'added',
    'size',
  ];
  const orderedKeys = Array.from(
    new Set([
      ...columnOrder.filter(
        (key) => key === 'quality_profile' || key === 'genres' || key === 'added' || key === 'size',
      ),
      ...defaultOrder,
    ]),
  ) as (keyof LibraryV2ArtistTableColumns)[];

  const renderHeaderCell = (key: keyof LibraryV2ArtistTableColumns) => {
    if (!columns[key]) return null;
    switch (key) {
      case 'quality_profile':
        return <th key="quality_profile">Quality Profile</th>;
      case 'genres':
        return <th key="genres">Genre</th>;
      case 'added':
        return <th key="added">Added</th>;
      case 'size':
        return (
          <th key="size" className={styles.colNum}>
            Size
          </th>
        );
      default:
        return null;
    }
  };

  const renderBodyCell = (
    artist: LibraryV2ArtistSummary,
    key: keyof LibraryV2ArtistTableColumns,
  ) => {
    if (!columns[key]) return null;
    switch (key) {
      case 'quality_profile':
        return (
          <td key="quality_profile">
            {profileNameById.has(artist.quality_profile_id)
              ? profileLabel(
                  profileNameById.get(artist.quality_profile_id) as string,
                  artist.quality_profile_source,
                )
              : '—'}
          </td>
        );
      case 'genres':
        return <td key="genres">{artist.genres.join(', ') || '—'}</td>;
      case 'added':
        return <td key="added">{formatReleaseDate(artist.added_at) ?? '—'}</td>;
      case 'size':
        return (
          <td key="size" className={styles.colNum}>
            {artist.total_size_bytes > 0 ? formatFileSize(artist.total_size_bytes) : '—'}
          </td>
        );
      default:
        return null;
    }
  };

  return (
    <table className={styles.table} id="library-artists-grid">
      <thead>
        <tr>
          <th className={styles.colMonitor}>Mon.</th>
          <th>Artist</th>
          <th className={styles.colNum}>Albums</th>
          <th className={styles.colNum}>Singles</th>
          <th className={styles.colNum}>Tracks</th>
          <th className={styles.colNum}>Missing</th>
          {orderedKeys.map(renderHeaderCell)}
        </tr>
      </thead>
      <tbody>
        {artists.map((artist) => (
          <tr
            key={artist.id}
            className={styles.tableRow}
            onClick={() => void navigate({ search: (p) => openArtistSearch(p, artist.id) })}
          >
            <td>
              <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
            </td>
            <td>
              <span className={styles.cellArtist}>
                <Artwork
                  src={artist.image_url ?? ''}
                  remote={artist.remote_image_url}
                  alt={artist.name}
                  className={styles.rowThumb}
                  thumb
                />
                <span>{artist.name}</span>
                <MediaServerRecognitionBadge sources={artist.media_server_sources} />
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
            {orderedKeys.map((key) => renderBodyCell(artist, key))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- artist detail (Lidarr-style: expandable album/single tables) ------------

function AlbumDetailView({ albumId }: { albumId: number }) {
  const navigate = useNavigate();
  const canWrite = useLibraryV2CanWrite();
  const previousArtist = Route.useSearch().artist;
  // `resolve` materializes the provider tracklist for a release that has no
  // track rows yet. The inline expand always did this; opening the same
  // release as its own page did not, so a discography-only release (a
  // compilation nobody owns) rendered an empty track list. The server-side
  // guard makes it a no-op for every release that already has tracks.
  const albumQuery = useQuery(libraryV2AlbumQueryOptions(albumId, { resolve: true }));
  const album = albumQuery.data;
  const [modalAction, setModalAction] = useState<{
    action: string;
    entity?: Lib2EntityRef;
  } | null>(null);
  // dd28-16: see useScopedSearchBanner.
  const {
    banner: grabBanner,
    setBanner: setGrabBanner,
    busy: searchBusy,
    runScoped,
  } = useScopedSearchBanner();

  function handleAction(action: string, entity?: Lib2EntityRef) {
    if (!canWrite) return;
    if (INTERACTIVE_RE.test(action)) {
      setModalAction({ action, entity });
      return;
    }
    if (!SCOPED_SEARCH_RE.test(action)) return;
    if (searchBusy) return;
    const scope = entity?.trackId
      ? { entity: 'tracks' as const, id: entity.trackId }
      : { entity: 'albums' as const, id: albumId };
    runScoped(scope.entity, scope.id);
  }

  // Back must return to the view you left. Clearing `releases` here dropped
  // the user out of All Releases / Discover View into My Library on every
  // back click, so getting back to the release they came from took three
  // more clicks. The view settings are only reset when back leads to a
  // DIFFERENT artist than the URL was showing — then they describe someone
  // else's page and carrying them over would be the surprise instead.
  const goBack = () => {
    const artistId = album?.primary_artist?.id;
    const switchesArtist = Boolean(artistId && previousArtist && artistId !== previousArtist);
    return navigate({
      search: (previous) => ({
        ...previous,
        album: undefined,
        artist: artistId ?? previous.artist,
        ...(switchesArtist
          ? {
              releases: 'library' as const,
              releaseView: 'table' as const,
              header: 'compact' as const,
            }
          : {}),
      }),
    });
  };

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
                    quality_profile_source: album.quality_profile_source,
                    quality_profile_explicit: album.quality_profile_explicit,
                    artist_name: album.primary_artist?.name,
                    image_url: album.image_url,
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
                  {album.quality_profile
                    ? profileLabel(album.quality_profile.name, album.quality_profile_source)
                    : 'No quality profile'}
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
                {album.total_size_bytes > 0 ? (
                  <span className={styles.detailLabel}>
                    <SvgIcon name="folder" />
                    {formatFileSize(album.total_size_bytes)}
                  </span>
                ) : null}
              </div>
              {album.genres.length > 0 ? (
                <p className={styles.genres}>{album.genres.join(', ')}</p>
              ) : null}
            </div>
          </header>
          <AlbumTrackTable albumId={album.id} onAction={handleAction} />
          {modalAction && INTERACTIVE_RE.test(modalAction.action) ? (
            <InteractiveSearchModal
              initialQuery={buildSearchQuery(
                album.primary_artist?.name ?? '',
                modalAction.action,
                modalAction.entity,
              )}
              qualityProfile={album.quality_profile}
              entity={modalAction.entity}
              canWrite={canWrite}
              onClose={() => setModalAction(null)}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

/**
 * A debounced filter box that stays in step with the URL (iss29-B09).
 *
 * The inputs used to be uncontrolled (`defaultValue`), so React never touched
 * them again after mount: a browser Back — or any other navigation that changed
 * `q` — updated the results while the box kept showing the old text, and the
 * next keystroke re-applied that stale text. Local state keeps typing
 * responsive; the effect resyncs whenever the URL moves on its own.
 */
function useUrlSyncedFilter(
  urlValue: string | undefined,
  apply: (value: string) => void,
  delayMs = 300,
): { value: string; onChange: (next: string) => void } {
  const [value, setValue] = useState(urlValue ?? '');
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    setValue(urlValue ?? '');
  }, [urlValue]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return {
    value,
    onChange: (next: string) => {
      setValue(next);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => apply(next), delayMs);
    },
  };
}

/** Filter for the release toggle: "My Library" keeps owned or wanted releases;
 *  "All Releases" shows the full provider discography. */
/** ldp-05: which artist view you land on depends on where you came from.
 *  Opening an artist from inside Library V2 always starts in the V2 shape —
 *  My Library, table, compact header — regardless of what the previous
 *  artist's URL happened to carry. Coming from search is the opposite case
 *  and is set by `DISCOVERY_ARTIST_VIEW` below. */
function openArtistSearch<T extends Record<string, unknown>>(previous: T, artistId: number) {
  return {
    ...previous,
    artist: artistId,
    album: undefined,
    releases: 'library' as const,
    releaseView: 'table' as const,
    header: 'compact' as const,
  };
}

/** Arriving from a search result: the full discography, in the card view, with
 *  the rich header — i.e. exactly what the legacy artist page showed, so the
 *  switch to Library V2 is not something a user has to notice. */
const DISCOVERY_ARTIST_VIEW = {
  releases: 'all' as const,
  releaseView: 'cards' as const,
  header: 'rich' as const,
};

function visibleReleases(
  entries: LibraryV2AlbumSummary[],
  mode: 'library' | 'all',
): LibraryV2AlbumSummary[] {
  if (mode === 'all') return entries;
  // Guide §5: "My Library" is `origin='library' OR monitored`. A wanted TRACK
  // counts as much as a monitored release — bookmarking one top track used to
  // write a wishlist row the user could then not find anywhere in the library.
  return entries.filter(
    (e) => e.origin !== 'discography' || e.monitored || (e.monitored_tracks ?? 0) > 0,
  );
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

/** B4: artist-toolbar decluttering — Preview Retag/Reorganize All/repair tools/
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
        title="Preview Retag, Reorganize All, Library Health & Repair, Manual Import, Enrich"
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
            Library Health & Repair
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

// --- ldp-01…ldp-07: legacy artist hero, card grid and discovery mode --------
//
// Ported, not re-imagined (issues §28.5). The markup and every class name come
// straight from the legacy artist page (`webui/index.html:4565-4655` and
// `4676ff`) so `style.css` — which the React app already loads, unscoped —
// dresses these components exactly as it dressed the originals. What changes
// is only the plumbing: DOM mutation becomes React state, `getElementById`
// becomes props/queries, and legacy action names become V2 semantics.

/** Legacy's lazy background loader (`core.js:225-239`): covers only start
 *  downloading once their card is within 200px of the viewport. Reuses the
 *  shared IntersectionObserver when the legacy bundle is present, and paints
 *  directly when it is not, so this survives the legacy page's removal. */
function useLazyBackgrounds(ref: { current: HTMLElement | null }, key: unknown) {
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const observe = (globalThis as { observeLazyBackgrounds?: (c: Element) => void })
      .observeLazyBackgrounds;
    if (observe) {
      observe(root);
      return;
    }
    root.querySelectorAll<HTMLElement>('[data-bg-src]').forEach((el) => {
      el.style.backgroundImage = `url('${el.dataset.bgSrc}')`;
    });
  }, [ref, key]);
}

function releaseYear(releaseDate: string | null | undefined, year: number | null | undefined) {
  const fromDate = /^(\d{4})/.exec(String(releaseDate ?? ''))?.[1];
  const parsed = Number(fromDate ?? year ?? 0);
  return parsed > 1900 && parsed <= new Date().getFullYear() + 1 ? String(parsed) : '';
}

/** One card, in the shape both sources can produce: a catalogue release and a
 *  provider-only release from the discovery endpoint. */
interface DiscographyCard {
  key: string;
  /** Only a catalogue release has one; a provider-only release does not. */
  albumId?: number;
  monitored: boolean;
  /** #1067: set only on a "+ Other sources" card — the source that lists it. */
  gapSource?: string;
  title: string;
  albumType: string;
  year: string;
  imageUrl: string;
  explicit: boolean;
  /** `null` while ownership is undetermined — legacy's "checking" state. */
  owned: boolean | null;
  ownedTracks: number;
  totalTracks: number;
}

function catalogueCard(album: LibraryV2AlbumSummary): DiscographyCard {
  return {
    key: `lib2-${album.id}`,
    albumId: album.id,
    monitored: album.monitored,
    title: album.title,
    albumType: album.album_type,
    year: releaseYear(album.release_date, album.year),
    // ldp-07: a pure discography row already carries the provider CDN url
    // here; an owned release carries its local cached cover.
    imageUrl: album.image_url ?? album.remote_image_url ?? '',
    explicit: album.explicit === true,
    owned: album.tracks_present > 0,
    ownedTracks: album.tracks_present,
    totalTracks: album.track_count,
  };
}

function providerCard(release: ProviderRelease): DiscographyCard {
  const completion = release.track_completion ?? null;
  return {
    key: `provider-${release.id}`,
    monitored: false,
    title: release.title || release.name || '',
    albumType: release.album_type || 'album',
    year: releaseYear(release.release_date, release.year),
    imageUrl: release.image_url ?? '',
    explicit: release.explicit === true,
    owned: release.owned ?? null,
    ownedTracks: completion?.owned_tracks ?? 0,
    totalTracks: completion?.total_tracks ?? 0,
  };
}

/** The completion badge pinned to a card's corner (`library.js:2181-2220`). */
function completionOverlay(card: DiscographyCard): {
  cls: string;
  label: string;
} {
  if (card.owned === null) return { cls: 'checking', label: 'Checking…' };
  if (!card.owned) return { cls: 'missing', label: 'Missing' };
  const missing = Math.max(0, card.totalTracks - card.ownedTracks);
  if (missing === 0 || card.totalTracks === 0) return { cls: 'completed', label: '✓ Owned' };
  const pct = Math.round((card.ownedTracks / card.totalTracks) * 100);
  return {
    cls: pct >= 75 ? 'nearly_complete' : 'partial',
    label: `${card.ownedTracks}/${card.totalTracks}`,
  };
}

/** ldp-03: the legacy tile grid, as the alternative to the V2 track table. */
function ReleaseCardGrid({
  cards,
  onOpen,
  openTitle,
  showOwnership = true,
}: {
  cards: DiscographyCard[];
  onOpen?: (card: DiscographyCard) => void;
  openTitle?: string;
  /** Off for a provider artist: there is no library to compare against, so
   *  legacy omitted the completion badge entirely rather than claim every
   *  release is "Checking…" forever (`library.js:2178`). */
  showOwnership?: boolean;
}) {
  const gridRef = useRef<HTMLDivElement>(null);
  useLazyBackgrounds(gridRef, cards.map((c) => c.key).join('|'));
  if (cards.length === 0) return null;
  return (
    <div className="releases-grid" ref={gridRef}>
      {cards.map((card) => {
        const flags = classifyReleaseContent({
          title: card.title,
          album_type: card.albumType,
        });
        const overlay = showOwnership ? completionOverlay(card) : null;
        const state =
          !showOwnership || card.owned ? '' : card.owned === null ? ' checking' : ' missing';
        return (
          <div
            key={card.key}
            className={`release-card album-card${state}`}
            data-is-live={String(flags.isLive)}
            data-is-compilation={String(flags.isCompilation)}
            data-is-featured={String(flags.isFeatured)}
            title={onOpen ? openTitle : card.title}
            onClick={onOpen ? () => onOpen(card) : undefined}
          >
            <div className="album-card-image" data-bg-src={card.imageUrl || undefined} />
            {card.albumId ? (
              <div
                className={styles.cardMonitor}
                title="Bookmark this release"
                onClick={(e) => e.stopPropagation()}
              >
                <MonitorToggle entity="albums" id={card.albumId} monitored={card.monitored} />
              </div>
            ) : null}
            {overlay ? (
              <div className={`completion-overlay ${overlay.cls}`}>
                <span className="completion-status">{overlay.label}</span>
              </div>
            ) : null}
            <div className="album-card-content">
              <div className="album-card-name" title={card.title}>
                {card.title}
                {card.explicit ? <span className="explicit-badge">E</span> : null}
              </div>
              {card.year ? <div className="album-card-year">{card.year}</div> : null}
            </div>
            {card.gapSource ? (
              <div
                className="gapfill-source-badge"
                title={`Only listed on ${card.gapSource} — opens and downloads from there`}
              >
                {card.gapSource}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** The legacy section wrapper — and it is load-bearing, not decoration:
 *  `.release-card` carries a fixed `height: 300px` that only
 *  `.discography-sections .release-card { height: fit-content }` cancels.
 *  Rendering the cards outside this ancestry left every card 300px tall while
 *  `.album-card`'s `aspect-ratio: 1` fought it, so the covers overlapped. */
function DiscographySections({ children }: { children: ReactNode }) {
  return <div className="discography-sections">{children}</div>;
}

function DiscographySection({
  title,
  stats,
  children,
}: {
  title: string;
  stats?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="discography-section">
      <div className="section-header">
        <h3>{title}</h3>
        {stats ? <div className="section-stats">{stats}</div> : null}
      </div>
      {children}
    </div>
  );
}

const CATEGORY_LABELS = {
  albums: 'Albums',
  eps: 'EPs',
  singles: 'Singles',
} as const;
const CONTENT_LABELS = {
  live: 'Live',
  compilations: 'Compilations',
  featured: 'Featured',
} as const;
const OWNERSHIP_LABELS = {
  all: 'All',
  owned: 'Owned',
  missing: 'Missing',
} as const;

/** ldp-04: Show / Include / Status, straight from `index.html:4676ff`. */
function DiscographyFilterBar({
  state,
  onChange,
  otherSources,
  onToggleOtherSources,
  otherSourcesBusy,
}: {
  state: DiscographyFilterState;
  onChange: (next: DiscographyFilterState) => void;
  /** #1067 "+ Other sources": omitted when there is no provider id to ask
   *  with, because the endpoint refuses to name-search on purpose. */
  otherSources?: boolean;
  onToggleOtherSources?: () => void;
  otherSourcesBusy?: boolean;
}) {
  return (
    <div className="discography-filters">
      <div className="filter-group">
        <span className="filter-label">Show</span>
        {(Object.keys(CATEGORY_LABELS) as (keyof typeof CATEGORY_LABELS)[]).map((key) => (
          <button
            key={key}
            type="button"
            className={`discography-filter-btn${state.categories[key] ? ' active' : ''}`}
            onClick={() =>
              onChange({
                ...state,
                categories: {
                  ...state.categories,
                  [key]: !state.categories[key],
                },
              })
            }
          >
            {CATEGORY_LABELS[key]}
          </button>
        ))}
      </div>
      <div className="filter-divider" />
      <div className="filter-group">
        <span className="filter-label">Include</span>
        {(Object.keys(CONTENT_LABELS) as (keyof typeof CONTENT_LABELS)[]).map((key) => (
          <button
            key={key}
            type="button"
            className={`discography-filter-btn${state.content[key] ? ' active' : ''}`}
            onClick={() =>
              onChange({
                ...state,
                content: { ...state.content, [key]: !state.content[key] },
              })
            }
          >
            {CONTENT_LABELS[key]}
          </button>
        ))}
      </div>
      <div className="filter-divider" />
      <div className="filter-group">
        <span className="filter-label">Status</span>
        {(Object.keys(OWNERSHIP_LABELS) as DiscographyOwnership[]).map((key) => (
          <button
            key={key}
            type="button"
            className={`discography-filter-btn${state.ownership === key ? ' active' : ''}`}
            onClick={() => onChange({ ...state, ownership: key })}
          >
            {OWNERSHIP_LABELS[key]}
          </button>
        ))}
      </div>
      {onToggleOtherSources ? (
        <>
          <div className="filter-divider" />
          <div className="filter-group">
            <span className="filter-label">Sources</span>
            <button
              type="button"
              className={`discography-filter-btn${otherSources ? ' active' : ''}`}
              title="Also list releases your other metadata sources know about — each is marked with the source that has it (#1067)"
              onClick={onToggleOtherSources}
            >
              {otherSourcesBusy ? 'Loading…' : '+ Other sources'}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** ldp-05/ldp-06: the legacy hero's Top Tracks column. The row action is
 *  Bookmark with V2 monitoring semantics — never legacy's "Add to Wishlist"
 *  and never "Download": bookmarking states intent, the pipeline decides when
 *  to act on it (guide §2.2). */
type BookmarkState = { status: 'busy' | 'done' } | { status: 'error'; message: string };

function TopTracksSidebar({
  artistName,
  providerId,
  source,
}: {
  artistName: string;
  providerId: string | null;
  source: string;
}) {
  const queryClient = useQueryClient();
  const [bookmarked, setBookmarked] = useState<Record<string, BookmarkState>>({});
  const topTracks = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'top-tracks', providerId, artistName],
    queryFn: () => fetchArtistTopTracks({ providerId, name: artistName }),
    enabled: Boolean(artistName),
    staleTime: 5 * 60_000,
  });
  const tracks = topTracks.data?.tracks ?? [];
  // Legacy offered its row action only on the provider pass, and for a real
  // reason: a Last.fm row is a name and a playcount, with no album and no
  // ids. Bookmarking one of those invented an album named after the track
  // that matched nothing, so its tracklist could never resolve. Those rows
  // stay display-only here too.
  const bookmarkable = topTracks.data?.kind === 'provider';
  // Identify against the provider that actually answered, not against the id
  // namespace the artist row happens to carry.
  const trackSource = topTracks.data?.source || source;
  const trackArtistId = topTracks.data?.resolvedArtistId ?? providerId;
  const titles = tracks.map((t) => t.name);
  // The tick is a fact about the library, not about this component's
  // lifetime: without this the bookmark looked applied until the next reload.
  const status = useQuery({
    queryKey: [
      ...LIBRARY_V2_QUERY_KEY,
      'top-track-status',
      trackSource,
      trackArtistId,
      artistName,
      titles,
    ],
    queryFn: () =>
      fetchLibraryV2DiscoveryTrackStatus({
        source: trackSource,
        artistName,
        artistProviderId: trackArtistId,
        titles,
      }),
    enabled: bookmarkable && titles.length > 0,
  });
  if (topTracks.isLoading || tracks.length === 0) return null;

  function stateOf(track: ArtistTopTrack): BookmarkState | undefined {
    const local = bookmarked[track.name];
    if (local) return local;
    return status.data?.[track.name]?.monitored ? { status: 'done' } : undefined;
  }

  async function bookmark(track: ArtistTopTrack) {
    setBookmarked((s) => ({ ...s, [track.name]: { status: 'busy' } }));
    try {
      const trackId = await materializeLibraryV2DiscoveryTrack({
        source: trackSource,
        artistName,
        artistProviderId: trackArtistId,
        trackTitle: track.name,
        trackProviderId: track.id ?? null,
        albumTitle: track.album?.name ?? null,
        albumProviderId: track.album?.id ?? null,
      });
      await setLibraryV2Monitored('tracks', trackId, true);
      setBookmarked((s) => ({ ...s, [track.name]: { status: 'done' } }));
      // Not awaited: refreshing the whole Library V2 cache is a page-wide
      // refetch, and blocking the tick on it made bookmarking a top-ten list
      // feel like ten page loads. The rows are independent, so the next
      // bookmark can start immediately.
      void invalidateLibraryV2(queryClient);
    } catch (error) {
      setBookmarked((s) => ({
        ...s,
        [track.name]: {
          status: 'error',
          message: mutationErrorMessage(error, 'Bookmark failed'),
        },
      }));
    }
  }

  return (
    <div className="artist-hero-right">
      <div className="hero-sidebar-title">
        {topTracks.data?.kind === 'lastfm' ? 'Popular on Last.fm' : 'Top Tracks'}
      </div>
      <div className="hero-top-tracks">
        {tracks.map((track, index) => {
          const state = stateOf(track);
          return (
            <div className="hero-top-track" key={`${track.name}-${index}`}>
              <span className="hero-top-track-num">{index + 1}</span>
              <span className="hero-top-track-name" title={track.name}>
                {track.name}
              </span>
              {track.playcount ? (
                <span className="hero-top-track-plays">{formatCompactNumber(track.playcount)}</span>
              ) : null}
              {bookmarkable ? (
                <button
                  type="button"
                  className="hero-top-track-download"
                  disabled={state?.status === 'busy' || state?.status === 'done'}
                  title={
                    state?.status === 'error'
                      ? state.message
                      : state?.status === 'done'
                        ? 'Bookmarked — this track is now wanted'
                        : 'Bookmark — mark this track as wanted'
                  }
                  onClick={(e) => {
                    e.stopPropagation();
                    void bookmark(track);
                  }}
                >
                  {state?.status === 'done' ? '✓' : <SvgIcon name="monitor" filled />}
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** ldp-05: the legacy hero, kept to its original vertical footprint. Used for
 *  both a catalogue artist (`header=rich`) and a discovery artist, which is
 *  the whole point — the two must be indistinguishable to a user arriving
 *  from search. */
/** ldp-05: the legacy hero's enrichment rings — per provider, the share of
 *  this artist's tracks that actually carry that provider's id. Ported from
 *  `renderArtistEnrichmentCoverage` (`library.js:1188`) including its class
 *  names and SVG geometry, so `style.css` renders it identically. */
const ENRICH_SERVICES_COVERAGE: Array<{
  name: string;
  key: string;
  color: string;
}> = [
  { name: 'Spotify', key: 'spotify', color: '#1db954' },
  { name: 'MusicBrainz', key: 'musicbrainz', color: '#ba55d3' },
  { name: 'Deezer', key: 'deezer', color: '#a238ff' },
  { name: 'Last.fm', key: 'lastfm', color: '#d51007' },
  { name: 'iTunes', key: 'itunes', color: '#fc3c44' },
  { name: 'AudioDB', key: 'audiodb', color: '#1a9fff' },
  { name: 'Discogs', key: 'discogs', color: '#D4A574' },
  { name: 'Genius', key: 'genius', color: '#ffff64' },
  { name: 'Tidal', key: 'tidal', color: '#00ffff' },
  { name: 'Qobuz', key: 'qobuz', color: '#4285f4' },
  { name: 'Bandcamp', key: 'bandcamp', color: '#1da0c3' },
];

const RING_RADIUS = 20;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

function EnrichmentCoverage({ coverage }: { coverage: Record<string, number> }) {
  if (!coverage.total_tracks) return null;
  return (
    <div className="artist-enrichment-coverage">
      <div className="artist-enrich-title">Enrichment Coverage</div>
      <div className="artist-enrich-grid">
        {ENRICH_SERVICES_COVERAGE.map((service) => {
          const pct = coverage[service.key] ?? 0;
          const offset = RING_CIRCUMFERENCE - (RING_CIRCUMFERENCE * pct) / 100;
          return (
            <div className="artist-enrich-circle" key={service.key}>
              <div className="artist-enrich-ring">
                <svg viewBox="0 0 48 48">
                  <circle className="ring-bg" cx="24" cy="24" r={RING_RADIUS} />
                  <circle
                    className="ring-fill"
                    cx="24"
                    cy="24"
                    r={RING_RADIUS}
                    stroke={service.color}
                    strokeDasharray={RING_CIRCUMFERENCE.toFixed(1)}
                    strokeDashoffset={offset.toFixed(1)}
                  />
                </svg>
                <span className="ring-pct">{Math.round(pct)}</span>
              </div>
              <span className="artist-enrich-label">{service.name}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** ldp-05: the legacy hero, structurally identical to
 *  `index.html:4565-4655` — image column, info column, top-tracks column —
 *  because that is what `style.css` lays out. Only the plumbing differs.
 *
 *  The image is deliberately NOT the plain 160px `.artist-image`: legacy
 *  overrode that with `#artist-hero-section #artist-detail-image { width:100% }`
 *  so the portrait fills its `max-width: min(38%, 460px)` column. An id
 *  selector cannot be reused here, so `styles.heroImage` reproduces it.
 *
 *  `styles.heroImageBox` gives that column a width of its own. Without one it
 *  took the photo's intrinsic width, so the header's proportions changed from
 *  artist to artist depending on what the provider shipped. */
function LegacyArtistHero({
  name,
  imageUrl,
  remoteImageUrl,
  genres,
  bio,
  listeners,
  playcount,
  followers,
  sections,
  providerId,
  source,
  badges,
  actions,
  coverage,
  onPickImage,
}: {
  name: string;
  imageUrl: string;
  remoteImageUrl?: string | null;
  genres: string[];
  bio: string | null;
  listeners: number | null;
  playcount: number | null;
  /** Provider follower count — the one figure that still resolves on an
   *  instance with no Last.fm key. Shares the existing stat row, so the
   *  header does not grow vertically. */
  followers?: number | null;
  sections: Array<{ label: string; owned: number; total: number }>;
  providerId: string | null;
  source: string;
  /** Provider chips — the V2 equivalent of legacy's service logo row. */
  badges?: ReactNode;
  actions?: ReactNode;
  coverage?: Record<string, number>;
  onPickImage?: () => void;
}) {
  const [expandedBio, setExpandedBio] = useState(false);
  // Legacy shipped the bio as raw HTML with Last.fm's trailing link; strip the
  // markup rather than render it — an artist bio is not a trusted template.
  const cleanBio = (bio ?? '')
    .replace(/<a\b[^>]*>.*?<\/a>/gi, '')
    .replace(/<[^>]+>/g, '')
    .trim();
  return (
    <div className="artist-hero-section">
      <div className="artist-hero-content">
        <div
          className={`artist-image-container ${styles.heroImageBox}`}
          title={onPickImage ? 'Change artist photo' : undefined}
          onClick={onPickImage}
        >
          <Artwork
            src={imageUrl}
            remote={remoteImageUrl}
            alt={name}
            className={`artist-image ${styles.heroImage}`}
          />
          {onPickImage ? (
            <div className={`artist-image-edit-overlay ${styles.heroImageOverlay}`}>
              <SvgIcon name="cover" />
              <span>Change photo</span>
            </div>
          ) : null}
        </div>
        <div className="artist-info">
          <div className="artist-hero-identity">
            <h1 className="artist-name">{name}</h1>
            {badges ? <div className="artist-hero-badges">{badges}</div> : null}
          </div>
          {actions ? <div className="artist-hero-actions">{actions}</div> : null}
          {genres.length > 0 ? (
            <div className="artist-genres-container">{genres.join(', ')}</div>
          ) : null}
          {cleanBio ? (
            <div className={`artist-hero-bio${expandedBio ? ' expanded' : ''}`}>
              <span className="bio-text">{cleanBio}</span>
              <span
                className="artist-hero-bio-toggle"
                onClick={() => setExpandedBio((v) => !v)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && setExpandedBio((v) => !v)}
              >
                {expandedBio ? 'Show less' : 'Read more'}
              </span>
            </div>
          ) : null}
          {listeners || playcount || followers ? (
            <div className="artist-hero-numbers">
              {listeners ? (
                <div className="artist-hero-stat">
                  <span className="hero-stat-value">{formatCompactNumber(listeners)}</span>
                  <span className="hero-stat-label">listeners</span>
                </div>
              ) : null}
              {playcount ? (
                <div className="artist-hero-stat">
                  <span className="hero-stat-value">{formatCompactNumber(playcount)}</span>
                  <span className="hero-stat-label">plays</span>
                </div>
              ) : null}
              {followers ? (
                <div className="artist-hero-stat">
                  <span className="hero-stat-value">{formatCompactNumber(followers)}</span>
                  <span className="hero-stat-label">followers</span>
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="collection-overview">
            {sections.map((section) => (
              <div className="collection-category" key={section.label}>
                <span className="category-label">{section.label}</span>
                <div className="completion-bar">
                  <div
                    className="completion-fill"
                    style={{
                      width: `${section.total ? clampPercent((section.owned / section.total) * 100) : 0}%`,
                    }}
                  />
                </div>
                <span className="category-stats">
                  {section.owned}/{section.total}
                </span>
              </div>
            ))}
          </div>
          {coverage ? <EnrichmentCoverage coverage={coverage} /> : null}
        </div>
        <TopTracksSidebar artistName={name} providerId={providerId} source={source} />
      </div>
    </div>
  );
}

/** #1067 "+ Other sources": releases only OTHER configured metadata sources
 *  list, appended to the normal sections with a source badge. Opt-in and
 *  lazy — it costs one provider round trip per extra source. */
function useOtherSources(input: {
  providerId: string | null;
  artistName: string;
  baseSource?: string | null;
}) {
  const [enabled, setEnabled] = useState(false);
  const query = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'gap-fill', input.providerId, input.artistName],
    queryFn: () =>
      fetchArtistDiscographyGapFill({
        providerId: input.providerId ?? '',
        artistName: input.artistName,
        baseSource: input.baseSource,
      }),
    enabled: enabled && Boolean(input.providerId) && Boolean(input.artistName),
    staleTime: 10 * 60_000,
  });
  const byBucket = (bucket: 'album' | 'ep' | 'single'): DiscographyCard[] =>
    enabled
      ? (query.data ?? [])
          .filter((r) => r._bucket === bucket)
          .map((r) => ({ ...providerCard(r), gapSource: r.gap_source }))
      : [];
  return {
    available: Boolean(input.providerId) && Boolean(input.artistName),
    enabled,
    busy: query.isFetching,
    toggle: () => setEnabled((v) => !v),
    byBucket,
  };
}

/** Everything the two "All Releases" render modes share: one filter bar, one
 *  set of visible releases (ldp-03/ldp-04 both apply in either mode). */
function useDiscographyFilters() {
  const [filters, setFilters] = useState<DiscographyFilterState>(DEFAULT_DISCOGRAPHY_FILTERS);
  return { filters, setFilters };
}

function visibleCards(cards: DiscographyCard[], filters: DiscographyFilterState) {
  return cards.filter((card) =>
    passesDiscographyFilters(
      { title: card.title, album_type: card.albumType },
      filters,
      card.owned,
    ),
  );
}

/** ldp-01/ldp-02: an artist with no catalogue row at all, rendered from
 *  provider data alone. Read-only by decision (issues §28.6 question 1): the
 *  catalogue row is created the moment the user bookmarks the artist or opens
 *  one of its releases, never just for looking. */
type GroupLabel = 'Albums' | 'EPs' | 'Singles';

function DiscoveryArtistView({
  source,
  providerId,
  name,
}: {
  source: string;
  providerId: string;
  name: string;
}) {
  const navigate = useNavigate();
  const { filters, setFilters } = useDiscographyFilters();
  const [adopting, setAdopting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const otherSources = useOtherSources({
    providerId,
    artistName: name,
    baseSource: source,
  });

  const existing = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'discovery-resolve', source, providerId, name],
    queryFn: () => resolveLibraryV2DiscoveryArtist({ source, providerId, name }),
  });
  const knownArtistId = existing.data ?? null;
  const detail = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'discovery-detail', source, providerId, name],
    queryFn: () => fetchProviderArtistDetail({ source, providerId, name }),
    enabled: existing.isSuccess && knownArtistId === null,
    staleTime: 5 * 60_000,
  });

  /** Arriving at an artist we already have is not discovery — hand straight
   *  over to the real Library V2 page, with the rich header preselected
   *  because the user came from a search result (ldp-05). */
  useEffect(() => {
    if (!knownArtistId) return;
    void navigate({
      search: (p) => ({
        ...p,
        ...DISCOVERY_ARTIST_VIEW,
        artist: knownArtistId,
        discover: undefined,
        discoverName: undefined,
      }),
      replace: true,
    });
  }, [knownArtistId, navigate]);

  /** The one write this view can perform: adopt the artist into the catalogue
   *  and continue on the normal page, where every V2 action already works.
   *  `monitor` separates the two reasons to adopt — Bookmark states intent and
   *  monitors (ldp-06), opening a release only needs the entity to exist. */
  async function adopt({ monitor }: { monitor: boolean }) {
    if (adopting) return;
    setAdopting(true);
    setError(null);
    try {
      const artistId = await materializeLibraryV2DiscoveryArtist({
        source,
        providerId,
        name,
      });
      if (monitor) await setLibraryV2Monitored('artists', artistId, true);
      await navigate({
        search: (p) => ({
          ...p,
          ...DISCOVERY_ARTIST_VIEW,
          artist: artistId,
          discover: undefined,
          discoverName: undefined,
        }),
        replace: true,
      });
    } catch (e) {
      setError(mutationErrorMessage(e, 'Could not add this artist'));
      setAdopting(false);
    }
  }

  if (existing.isLoading || knownArtistId) {
    return <div className={styles.loading}>Loading…</div>;
  }
  if (detail.isLoading) {
    return <div className={styles.loading}>Loading Artist Discography…</div>;
  }
  if (existing.isError || detail.isError || !detail.data) {
    return (
      <div className={styles.page}>
        <BackLink
          onClick={() =>
            void navigate({
              search: (p) => ({
                ...p,
                discover: undefined,
                discoverName: undefined,
              }),
            })
          }
        >
          ← All artists
        </BackLink>
        <div className={styles.emptyState}>
          <h2>Could not load this artist</h2>
          <p>
            {mutationErrorMessage(
              existing.error ?? detail.error,
              'The metadata source did not answer.',
            )}
          </p>
        </div>
      </div>
    );
  }

  const { artist, discography } = detail.data;
  const groups: Array<[GroupLabel, ProviderRelease[]]> = [
    ['Albums', discography.albums ?? []],
    ['EPs', discography.eps ?? []],
    ['Singles', discography.singles ?? []],
  ];
  const categoryOf = {
    Albums: 'albums',
    EPs: 'eps',
    Singles: 'singles',
  } as const;
  const bucketOf = { Albums: 'album', EPs: 'ep', Singles: 'single' } as const;

  return (
    <div className={styles.page}>
      <BackLink
        onClick={() =>
          void navigate({
            search: (p) => ({
              ...p,
              discover: undefined,
              discoverName: undefined,
            }),
          })
        }
      >
        ← All artists
      </BackLink>
      {error ? <div className={`${styles.grabBanner} ${styles.grab_err}`}>{error}</div> : null}
      <LegacyArtistHero
        name={artist.name || name}
        imageUrl={artist.image_url ?? ''}
        genres={artist.genres ?? []}
        bio={artist.lastfm_bio ?? null}
        listeners={artist.lastfm_listeners ?? null}
        playcount={artist.lastfm_playcount ?? null}
        followers={artist.followers ?? null}
        sections={groups.map(([label, releases]) => ({
          label,
          owned: 0,
          total: releases.length,
        }))}
        providerId={providerId}
        source={source}
        actions={
          <ActionButton
            icon="monitor"
            label={adopting ? 'Adding…' : 'Bookmark artist'}
            title="Add this artist to your library and monitor them"
            busy={adopting}
            onClick={() => void adopt({ monitor: true })}
          />
        }
      />
      <DiscographyFilterBar
        state={filters}
        onChange={setFilters}
        otherSources={otherSources.enabled}
        otherSourcesBusy={otherSources.busy}
        onToggleOtherSources={otherSources.available ? otherSources.toggle : undefined}
      />
      <DiscographySections>
        {groups.map(([label, releases]) => {
          if (!filters.categories[categoryOf[label as keyof typeof categoryOf]]) return null;
          const cards = visibleCards(
            [
              ...releases.map(providerCard),
              ...otherSources.byBucket(bucketOf[label as GroupLabel]),
            ],
            filters,
          );
          if (cards.length === 0) return null;
          return (
            <DiscographySection
              key={label}
              title={label}
              stats={<span>{cards.length} releases</span>}
            >
              <ReleaseCardGrid
                cards={cards}
                showOwnership={false}
                openTitle={`Add ${artist.name || name} to your library to manage this release`}
                onOpen={() => void adopt({ monitor: false })}
              />
            </DiscographySection>
          );
        })}
      </DiscographySections>
    </div>
  );
}

/** ldp-05: the rich hero for an artist that IS in the catalogue. Same layout
 *  as the discovery hero — that is the point, a user arriving from search
 *  must not be able to tell which of the two they are looking at. Bio and
 *  stats come from the same Last.fm lookup the legacy hero used; the V2 match
 *  chips stay (ldp-08) instead of legacy's metadata-source panel. */
function CatalogueArtistHero({
  artist,
  onOpenSettings,
  onPickImage,
  headerToggle,
}: {
  artist: LibraryV2ArtistDetail;
  onOpenSettings: () => void;
  onPickImage: () => void;
  headerToggle: ReactNode;
}) {
  const matchStatus = useQuery(libraryV2ArtistMatchStatusQueryOptions(artist.id));
  const providerIds = artist.provider_ids ?? {};
  const info = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'hero-stats', artist.name],
    queryFn: () =>
      fetchArtistHeroStats({
        name: artist.name,
        spotifyId: providerIds.spotify,
        deezerId: providerIds.deezer,
      }),
    enabled: Boolean(artist.name),
    staleTime: 30 * 60_000,
  });
  // Only Spotify and Deezer expose a popularity ranking at all; for anything
  // else the sidebar falls through to Last.fm by name, exactly like legacy.
  const source = providerIds.spotify ? 'spotify' : providerIds.deezer ? 'deezer' : '';
  const countOwned = (label: string, entries: LibraryV2AlbumSummary[]) => ({
    label,
    owned: entries.filter((e) => e.tracks_present > 0).length,
    total: entries.length,
  });
  return (
    <LegacyArtistHero
      name={artist.name}
      imageUrl={artist.image_url ?? ''}
      remoteImageUrl={artist.remote_image_url}
      genres={artist.genres}
      bio={artist.summary || info.data?.bio || null}
      listeners={info.data?.listeners ?? null}
      playcount={info.data?.playcount ?? null}
      followers={info.data?.followers ?? null}
      sections={[
        countOwned('Albums', artist.albums),
        countOwned('EPs', artist.eps ?? []),
        countOwned('Singles', artist.singles),
      ]}
      providerId={providerIds.spotify ?? providerIds.deezer ?? null}
      source={source}
      onPickImage={onPickImage}
      // ldp-08 stays: the V2 match chips are the metadata-source display, not
      // legacy's panel. The enrichment rings underneath are the part the user
      // asked for on top — they answer a different question (how many of this
      // artist's TRACKS a provider actually knows).
      badges={<ArtistMatchChips artist={artist} abbreviated />}
      coverage={matchStatus.data?.enrichmentCoverage}
      actions={
        <>
          <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
          {artist.monitored ? (
            <IconActionButton
              icon="settings"
              title="Artist Settings — Watchlist, future releases, quality and provider match"
              requiresWrite
              onClick={onOpenSettings}
            />
          ) : null}
          {headerToggle}
          <ArtistAliases artistId={artist.id} artistName={artist.name} />
        </>
      }
    />
  );
}

function ArtistDetailView({ artistId }: { artistId: number }) {
  const navigate = useNavigate();
  const canWrite = useLibraryV2CanWrite();
  const search = Route.useSearch();
  const releasesMode = search.releases;
  // ldp-03/ldp-04/ldp-05: how `All Releases` renders and how rich the header
  // is are view settings, carried in the URL next to `releases` and `view`
  // like every other Library V2 view setting.
  const releaseView = search.releaseView;
  const headerMode = search.header;
  const { filters, setFilters } = useDiscographyFilters();
  const artistQuery = useQuery(libraryV2ArtistQueryOptions(artistId));
  const artistProviderIds = artistQuery.data?.provider_ids ?? {};
  const otherSources = useOtherSources({
    providerId: artistProviderIds.spotify ?? artistProviderIds.deezer ?? null,
    artistName: artistQuery.data?.name ?? '',
  });
  const queueStatusQuery = useQuery(libraryV2QueueStatusQueryOptions('artists', artistId));
  useRefreshLibraryWhenQueueDrains(Object.keys(queueStatusQuery.data?.tracks ?? {}).length);
  const artist = artistQuery.data;
  const [discographyBusy, setDiscographyBusy] = useState(false);
  const [modalAction, setModalAction] = useState<{
    action: string;
    entity?: Lib2EntityRef;
  } | null>(null);
  const [showArtistSettings, setShowArtistSettings] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showMaintenance, setShowMaintenance] = useState(false);
  const [showManageTracks, setShowManageTracks] = useState(false);
  const [showReorganizeAll, setShowReorganizeAll] = useState(false);
  const [showEditArtist, setShowEditArtist] = useState(false);
  const [showArtPicker, setShowArtPicker] = useState(false);
  const [showUnmonitoredProfile, setShowUnmonitoredProfile] = useState(false);
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
  // dd28-16: the discography refresh writes into the SAME banner as the
  // scoped searches, so it has to share their run-sequence guard too.
  const {
    banner: grabBanner,
    setBanner: setGrabBanner,
    busy: searchBusy,
    run: runBannerTask,
    publish: publishBanner,
    runScoped,
  } = useScopedSearchBanner();
  const queryClient = useQueryClient();
  const artistName = artist?.name ?? '';
  const attemptedDiscographyFetchRef = useRef(false);

  async function updateDiscography() {
    setDiscographyBusy(true);
    await runBannerTask(async ({ sequence }) => {
      publishBanner(sequence, {
        tone: 'busy',
        text: 'Fetching full discography…',
      });
      try {
        // A provider catalogue walk runs as a background job: polling it means
        // a slow provider shows as "still running" instead of a timeout on
        // work the server is going to finish anyway.
        const jobId = await startLibraryV2DiscographyRefresh(artistId);
        const state = await awaitBulkJobState(queryClient, jobId);
        if (state.error) throw new Error(state.error);
        const stats = (state.result ?? {}) as Partial<LibraryV2DiscographyStats>;
        publishBanner(sequence, {
          tone: 'ok',
          text: `Discography updated from ${stats.source ?? 'provider'}: ${stats.added ?? 0} new, ${stats.enriched ?? 0} matched.`,
        });
      } catch (e) {
        publishBanner(sequence, {
          tone: 'err',
          text: e instanceof Error ? e.message : 'Discography refresh failed',
        });
      }
    });
    setDiscographyBusy(false);
  }

  function setReleasesMode(mode: 'library' | 'all') {
    void navigate({ search: (p) => ({ ...p, releases: mode }) });
  }

  /** ldp-04: the filter bar governs `All Releases` in BOTH render modes, so
   *  switching Table ↔ Legacy never silently changes which releases are on
   *  screen. `My Library` is untouched by it, exactly as specified. */
  function releasesOf(
    entries: LibraryV2AlbumSummary[],
    category: 'albums' | 'eps' | 'singles',
  ): LibraryV2AlbumSummary[] {
    const base = visibleReleases(entries, releasesMode);
    if (releasesMode !== 'all') return base;
    if (!filters.categories[category]) return [];
    return base.filter((album) =>
      passesDiscographyFilters(
        { title: album.title, album_type: album.album_type },
        filters,
        album.tracks_present > 0,
      ),
    );
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
    if (!canWrite) return;
    if (INTERACTIVE_RE.test(action)) {
      setModalAction({ action, entity });
      return;
    }
    if (SCOPED_SEARCH_RE.test(action)) {
      if (searchBusy) return;
      const scope = resolveSearchScope(entity, artistId);
      runScoped(scope.entity, scope.id);
    }
  }

  /** ldp-05: one switch, in both header shapes. Compact stays the default;
   *  arriving from a search result preselects the rich hero. */
  const headerToggle = (
    <IconActionButton
      icon={headerMode === 'rich' ? 'collapse' : 'expand'}
      title={
        headerMode === 'rich'
          ? 'Compact header'
          : 'Rich header — bio, listeners, plays and top tracks'
      }
      onClick={() =>
        void navigate({
          search: (p) => ({
            ...p,
            header: headerMode === 'rich' ? 'compact' : 'rich',
          }),
        })
      }
    />
  );

  return (
    <div className={styles.page}>
      <BackLink onClick={() => void navigate({ search: (p) => ({ ...p, artist: undefined }) })}>
        ← All artists
      </BackLink>
      {artistQuery.isError ? (
        // iss29-C04: without this branch `isLoading` false + `artist` undefined
        // fell through to "Loading…" forever — a stale bookmark to a deleted
        // artist (404) span the page for good, with no message and no retry.
        <div className={styles.emptyState}>
          <h2>Artist not found</h2>
          <p>{mutationErrorMessage(artistQuery.error, 'This artist could not be loaded.')}</p>
        </div>
      ) : artistQuery.isLoading || !artist ? (
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
                // dd28-16: a double click double-POSTed; the server answered
                // 409 (job already running) and the client rendered that as
                // "Search failed" over a search that was in fact running.
                busy={searchBusy}
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
                  setRetagTarget({
                    entity: 'artists',
                    id: artistId,
                    title: artist.name,
                  })
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
                requiresWrite={false}
                onClick={() => setShowManageTracks(true)}
              />
              <ActionButton
                icon="history"
                label="History"
                title="Recent downloads recorded for this artist"
                requiresWrite={false}
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
              {!artist.monitored ? (
                <ActionButton
                  icon="star"
                  label={`Profile: ${
                    artist.quality_profile
                      ? profileLabel(artist.quality_profile.name, artist.quality_profile_source)
                      : 'None'
                  }`}
                  title="Set quality independently; bookmark the artist to unlock Watchlist settings"
                  onClick={() => setShowUnmonitoredProfile(true)}
                />
              ) : null}
              <ActionButton
                icon="delete"
                label="Delete"
                tone="danger"
                title="Remove this artist and choose whether linked files stay on disk"
                onClick={() =>
                  setDeleteTarget({
                    entity: 'artists',
                    id: artistId,
                    title: artist.name,
                  })
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

          {headerMode === 'rich' ? (
            <CatalogueArtistHero
              artist={artist}
              onOpenSettings={() => setShowArtistSettings(true)}
              onPickImage={() => setShowArtPicker(true)}
              headerToggle={headerToggle}
            />
          ) : (
            <header className={styles.detailHeader}>
              <Artwork
                src={artist.image_url ?? ''}
                remote={artist.remote_image_url}
                alt={artist.name}
                className={styles.detailThumb}
              />
              <div className={styles.detailMeta}>
                <div className={styles.detailTitleRow}>
                  <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
                  <h1 className={styles.title}>{artist.name}</h1>
                  {artist.monitored ? (
                    <IconActionButton
                      icon="settings"
                      title="Artist Settings — Watchlist, future releases, quality and provider match"
                      requiresWrite
                      onClick={() => setShowArtistSettings(true)}
                    />
                  ) : null}
                  {headerToggle}
                </div>
                <p className={styles.subtitle}>
                  {artist.album_count} albums · {artist.single_count} singles
                  {artist.monitored ? ' · Monitored (watchlist)' : ''}
                </p>
                <div className={styles.detailLabels}>
                  <span className={`${styles.detailLabel} ${styles.labelProfile}`}>
                    <SvgIcon name="star" />
                    {artist.quality_profile
                      ? profileLabel(artist.quality_profile.name, artist.quality_profile_source)
                      : 'No quality profile'}
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
                  {artist.total_size_bytes > 0 ? (
                    <span className={styles.detailLabel}>
                      <SvgIcon name="folder" />
                      {formatFileSize(artist.total_size_bytes)}
                    </span>
                  ) : null}
                  <MediaServerRecognitionBadge sources={artist.media_server_sources} />
                </div>
                {artist.genres.length > 0 ? (
                  <p className={styles.genres}>{artist.genres.join(', ')}</p>
                ) : null}
                <ArtistMatchChips artist={artist} />
                <ArtistAliases artistId={artist.id} artistName={artist.name} />
              </div>
            </header>
          )}

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
            {releasesMode === 'all' ? (
              <div className={styles.releasesToggle}>
                <button
                  type="button"
                  className={releaseView === 'table' ? styles.viewActive : ''}
                  onClick={() =>
                    void navigate({
                      search: (p) => ({ ...p, releaseView: 'table' }),
                    })
                  }
                >
                  Table View
                </button>
                <button
                  type="button"
                  className={releaseView === 'cards' ? styles.viewActive : ''}
                  onClick={() =>
                    void navigate({
                      search: (p) => ({ ...p, releaseView: 'cards' }),
                    })
                  }
                >
                  Discover View
                </button>
              </div>
            ) : null}
            <span className={styles.releasesHint}>
              {releasesMode === 'all'
                ? 'Full discography from the metadata provider — monitor a release to add it to Wanted.'
                : 'Releases in your library (plus monitored ones).'}
            </span>
          </div>

          {releasesMode === 'all' ? (
            <DiscographyFilterBar
              state={filters}
              onChange={setFilters}
              otherSources={otherSources.enabled}
              otherSourcesBusy={otherSources.busy}
              onToggleOtherSources={otherSources.available ? otherSources.toggle : undefined}
            />
          ) : null}

          {releasesMode === 'all' && releaseView === 'cards' ? (
            <DiscographySections>
              {(
                [
                  ['Albums', releasesOf(artist.albums, 'albums')],
                  ['EPs', releasesOf(artist.eps ?? [], 'eps')],
                  ['Singles', releasesOf(artist.singles, 'singles')],
                ] as Array<[GroupLabel, LibraryV2AlbumSummary[]]>
              ).map(([title, entries]) => {
                const cards = [
                  ...entries.map(catalogueCard),
                  ...otherSources.byBucket(
                    ({ Albums: 'album', EPs: 'ep', Singles: 'single' } as const)[
                      title as GroupLabel
                    ],
                  ),
                ];
                if (cards.length === 0) return null;
                return (
                  <DiscographySection
                    key={title}
                    title={title}
                    stats={
                      <>
                        <span>{entries.filter((e) => e.tracks_present > 0).length} owned</span>
                        <span>{entries.filter((e) => e.tracks_present === 0).length} missing</span>
                      </>
                    }
                  >
                    <ReleaseCardGrid
                      cards={cards}
                      openTitle="Open release"
                      onOpen={(card) =>
                        card.albumId
                          ? void navigate({
                              search: (p) => ({ ...p, album: card.albumId }),
                            })
                          : undefined
                      }
                    />
                  </DiscographySection>
                );
              })}
            </DiscographySections>
          ) : (
            <>
              <AlbumGroup
                title="Albums"
                albums={releasesOf(artist.albums, 'albums')}
                artistId={artistId}
                artistName={artist.name}
                scope="albums"
                queueStatusByAlbum={queueStatusQuery.data?.albums ?? {}}
                queueStatusTracks={queueStatusQuery.data?.tracks ?? {}}
                onAction={handleAction}
              />
              <AlbumGroup
                title="EPs"
                albums={releasesOf(artist.eps ?? [], 'eps')}
                artistId={artistId}
                artistName={artist.name}
                scope="eps"
                queueStatusByAlbum={queueStatusQuery.data?.albums ?? {}}
                queueStatusTracks={queueStatusQuery.data?.tracks ?? {}}
                onAction={handleAction}
              />
              <AlbumGroup
                title="Singles"
                albums={releasesOf(artist.singles, 'singles')}
                artistId={artistId}
                artistName={artist.name}
                scope="singles"
                queueStatusByAlbum={queueStatusQuery.data?.albums ?? {}}
                queueStatusTracks={queueStatusQuery.data?.tracks ?? {}}
                onAction={handleAction}
              />
            </>
          )}
          {modalAction && INTERACTIVE_RE.test(modalAction.action) ? (
            <InteractiveSearchModal
              initialQuery={buildSearchQuery(artist.name, modalAction.action, modalAction.entity)}
              qualityProfile={artist.quality_profile}
              entity={modalAction.entity}
              canWrite={canWrite}
              onClose={() => setModalAction(null)}
            />
          ) : null}
          {showArtistSettings ? (
            <ArtistSettingsModal artist={artist} onClose={() => setShowArtistSettings(false)} />
          ) : null}
          {showHistory ? (
            <HistoryModal
              scope="artist"
              entityId={artistId}
              onClose={() => setShowHistory(false)}
            />
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
          {showUnmonitoredProfile ? (
            <QualityProfileModal
              entity="artists"
              id={artist.id}
              currentProfileId={artist.quality_profile?.id ?? 1}
              currentProfileSource={artist.quality_profile_source}
              currentProfileExplicit={artist.quality_profile_explicit}
              title={artist.name}
              onClose={() => setShowUnmonitoredProfile(false)}
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
        </>
      )}
    </div>
  );
}

/** Poll the background bulk-job status until it settles, then refresh. */
async function awaitBulkJobState(
  queryClient: ReturnType<typeof useQueryClient>,
  jobId: string,
  isCurrent?: () => boolean,
): Promise<LibraryV2JobState> {
  let polls = 0;
  for (;;) {
    // §27 side finding: without this the poll loop kept running after the user
    // navigated away mid-refresh. A leak rather than a data risk, but there is
    // no reason to keep asking once nobody is listening.
    if (isCurrent && !isCurrent()) {
      return { running: false } as LibraryV2JobState;
    }
    const state = await fetchLibraryV2JobStatus(jobId);
    if (!state.running) {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      return state;
    }
    polls += 1;
    // A long-running server job stays running. After five minutes back off to
    // reduce traffic, but never manufacture a terminal failure client-side.
    const delayMs = polls < 300 ? 1000 : 5000;
    await new Promise((r) => setTimeout(r, delayMs));
  }
}

/** Poll the background bulk-job status until it settles, then refresh. */
async function awaitBulkJob(
  queryClient: ReturnType<typeof useQueryClient>,
  jobId: string,
): Promise<string | null> {
  const state = await awaitBulkJobState(queryClient, jobId);
  return state.error;
}

/** Deep-dive C1: run the scoped Automatic Search endpoint for exactly one
 *  artist/album/track and report a banner-ready outcome. Replaces the old
 *  client-side best-pick heuristic (A4) — the server does the searching,
 *  candidate-walking and grabbing through the normal wishlist pipeline. */
async function runScopedSearch(
  queryClient: ReturnType<typeof useQueryClient>,
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
  isCurrent?: () => boolean,
): Promise<{ tone: 'ok' | 'err'; text: string }> {
  try {
    const jobId = await startLibraryV2ScopedSearch(entity, id);
    const state = await awaitBulkJobState(queryClient, jobId, isCurrent);
    if (state.error) return { tone: 'err', text: `Search failed: ${state.error}` };
    const dispatchError = state.result?.dispatch_error;
    if (dispatchError) return { tone: 'err', text: `Search failed: ${dispatchError}` };
    return {
      tone: 'ok',
      text: 'Search started for the monitored missing/upgradable tracks in scope — progress on the Downloads page.',
    };
  } catch (e) {
    return {
      tone: 'err',
      text: e instanceof Error ? e.message : 'Search failed',
    };
  }
}

type ScopedSearchBanner = { tone: 'busy' | 'ok' | 'err'; text: string } | null;

/** Shared owner of the scoped-search banner (dd28-16).
 *
 *  Every "Automatic Search"/"Search" button wrote into one shared banner via a
 *  bare `void runScopedSearch(...).then(setBanner)`. Searching track A and then
 *  track B meant A's slower result landed last and overwrote B's — the user
 *  read the wrong outcome, up to an "ok" sitting over a real failure. The
 *  buttons were not disabled while a request was in flight either, so a double
 *  click double-POSTed; the server is idempotent (409 from the job registry)
 *  but the client rendered that 409 as "Search failed: …" over a search that
 *  was in fact running.
 *
 *  Interactive Search already solved this with a run-sequence ref; this is the
 *  same guard, shared by every scoped-search caller (and by the discography
 *  refresh, which writes into the same banner).
 */
function useScopedSearchBanner() {
  const queryClient = useQueryClient();
  const [banner, setBanner] = useState<ScopedSearchBanner>(null);
  const [busy, setBusy] = useState(false);
  const runSequenceRef = useRef(0);
  const mountedRef = useRef(true);
  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );

  /** Publish a banner only if this is still the newest run and we're mounted. */
  function publish(sequence: number, next: ScopedSearchBanner) {
    if (!mountedRef.current || sequence !== runSequenceRef.current) return;
    setBanner(next);
  }

  async function run<T>(
    task: (signal: { sequence: number; isCurrent: () => boolean }) => Promise<T>,
  ): Promise<void> {
    const sequence = ++runSequenceRef.current;
    setBusy(true);
    try {
      await task({
        sequence,
        isCurrent: () => mountedRef.current && sequence === runSequenceRef.current,
      });
    } finally {
      if (mountedRef.current && sequence === runSequenceRef.current) setBusy(false);
    }
  }

  function runScoped(entity: 'artists' | 'albums' | 'tracks', id: number) {
    void run(async ({ sequence, isCurrent }) => {
      publish(sequence, { tone: 'busy', text: 'Searching…' });
      const result = await runScopedSearch(queryClient, entity, id, isCurrent);
      publish(sequence, result);
    });
  }

  return { banner, setBanner, busy, run, publish, runScoped };
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
  const canWrite = useLibraryV2CanWrite();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const targetMonitored = !allMonitored;

  async function apply() {
    if (!canWrite) return;
    setBusy(true);
    setError(null);
    try {
      const jobId = await bulkMonitorLibraryV2Releases(artistId, scope, targetMonitored, albumIds);
      const jobError = await awaitBulkJob(queryClient, jobId);
      if (jobError) throw new Error(jobError);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
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
        data-requires-write=""
        disabled={busy || !canWrite}
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
  artistName,
  scope,
  queueStatusByAlbum,
  queueStatusTracks,
  onAction,
}: {
  title: string;
  albums: LibraryV2AlbumSummary[];
  artistId: number;
  artistName: string;
  scope: 'albums' | 'eps' | 'singles';
  queueStatusByAlbum: Record<number, number>;
  queueStatusTracks: Record<number, LibraryV2QueueStatusEntry>;
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
          <AlbumBlock
            key={album.id}
            album={album}
            artistName={artistName}
            activeDownloads={queueStatusByAlbum[album.id] ?? 0}
            queueStatusTracks={queueStatusTracks}
            onAction={onAction}
          />
        ))}
      </div>
    </section>
  );
}

function AlbumBlock({
  album,
  artistName,
  activeDownloads,
  onAction,
  queueStatusTracks,
}: {
  album: LibraryV2AlbumSummary;
  /** Who the album is filed under today — the reassign modal says so, and the
   *  summary row itself has no artist on it. */
  artistName: string;
  activeDownloads: number;
  onAction: ActionHandler;
  queueStatusTracks: Record<number, LibraryV2QueueStatusEntry>;
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
          remote={album.remote_image_url}
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
              void navigate({
                search: (previous) => ({ ...previous, album: album.id }),
              });
            }}
          >
            {album.title}
          </button>
          <span className={styles.albumHeadBadges}>
            <span className={styles.albumTypeBadge}>{album.album_type}</span>
            <AlbumSizeBadge bytes={album.total_size_bytes} />
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
        {activeDownloads > 0 ? (
          <span
            className={styles.queueStatusPill}
            title={`${activeDownloads} track(s) currently in the download pipeline`}
          >
            <SvgIcon name="download" />
            {activeDownloads} downloading
          </span>
        ) : null}
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
            requiresWrite
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
            requiresWrite
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
              quality_profile_source: album.quality_profile_source,
              quality_profile_explicit: album.quality_profile_explicit,
              artist_name: artistName,
              image_url: album.image_url,
            }}
          />
        </span>
      </div>
      {/* Always ask; the server decides. Tying this to `unowned` meant a
          release the user owns nothing of but that is flagged origin='library'
          — every release a track bookmark created — never fetched its
          tracklist, so it stayed a one-track album. The endpoint's own guard
          is a single row read and only calls a provider when the tracklist is
          genuinely incomplete. */}
      {open ? (
        <AlbumTrackTable
          albumId={album.id}
          resolve
          onAction={onAction}
          queueStatusTracks={queueStatusTracks}
        />
      ) : null}
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
  verification: false,
  acoustid: true,
  file_size: false,
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
  verification: 'Verification',
  acoustid: 'Check',
  file_size: 'File size',
  file_path: 'File path',
  play: 'Play button',
};

type TrackSortKey = 'number' | 'title' | 'duration' | 'bpm' | 'file_size';
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
      case 'file_size':
        return t.file?.size ?? -1;
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

const MIN_COLUMN_WIDTH = 1;
const LEGACY_PIXEL_WIDTH_THRESHOLD = 100;
const CHECKBOX_COLUMN_WIDTH = 28;
const MONITOR_COLUMN_WIDTH = 30;
const ACTION_COLUMN_WIDTH = 80;
const UTILITY_COLUMN_WIDTH = CHECKBOX_COLUMN_WIDTH + MONITOR_COLUMN_WIDTH + ACTION_COLUMN_WIDTH;

const DEFAULT_COLUMN_WEIGHTS: Record<string, number> = {
  number: 3,
  title: 24,
  play: 5,
  disc: 5,
  artists: 14,
  duration: 8,
  bpm: 6,
  match: 12,
  // iss28-06: this one column contains three deliberately aligned sub-cells.
  quality: 40,
  features: 9,
  metadata: 10,
  verification: 9,
  acoustid: 9,
  file_size: 8,
  file_path: 20,
};

/** A saved width is the user's preferred share of the table, not permission
 * to crush a compact value below the space it needs. Text-heavy columns stay
 * deliberately flexible because titles, artist names and paths can be
 * arbitrarily long; fixed-format values and controls get a readable floor.
 * The floor only affects the current render and is never persisted. */
const RESPONSIVE_COLUMN_MIN_WIDTHS: Record<string, number> = {
  number: 42,
  title: 112,
  play: 48,
  disc: 52,
  artists: 96,
  duration: 76,
  bpm: 56,
  match: 82,
  // The widest Quality sub-cell is 140px; include the table cell padding.
  quality: 164,
  features: 82,
  metadata: 104,
  verification: 112,
  acoustid: 112,
  file_size: 84,
  file_path: 120,
};

const DEFAULT_RESPONSIVE_COLUMN_MIN_WIDTH = 72;

/** iss28-02: clamp a relative weight, not a CSS-pixel width. The old
 * preference values remain usable because normalization treats any positive
 * number as a weight. */
export function clampColumnWidth(width: number): number {
  if (!Number.isFinite(width)) return MIN_COLUMN_WIDTH;
  return Math.max(MIN_COLUMN_WIDTH, Math.round(width * 1000) / 1000);
}

export function normalizeColumnWidths(
  keys: string[],
  stored: Record<string, number | null> = {},
): Record<string, number> {
  const uniqueKeys = Array.from(new Set(keys));
  if (uniqueKeys.length === 0) return {};
  if (uniqueKeys.length === 1) return { [uniqueKeys[0]]: 100 };
  const legacyPixels = uniqueKeys.some(
    (key) =>
      typeof stored[key] === 'number' &&
      Number.isFinite(stored[key]) &&
      (stored[key] as number) > LEGACY_PIXEL_WIDTH_THRESHOLD,
  );
  const storedValuesAreComplete =
    uniqueKeys.every(
      (key) =>
        typeof stored[key] === 'number' &&
        Number.isFinite(stored[key]) &&
        (stored[key] as number) > 0,
    ) && !legacyPixels;
  const defaultNumberWidth =
    uniqueKeys.includes('number') && !storedValuesAreComplete
      ? Math.min(DEFAULT_COLUMN_WEIGHTS.number, 100 - MIN_COLUMN_WIDTH * (uniqueKeys.length - 1))
      : null;
  const raw = Object.fromEntries(
    uniqueKeys.map((key) => {
      const saved = stored[key];
      return [
        key,
        typeof saved === 'number' && Number.isFinite(saved) && saved > 0
          ? saved
          : (DEFAULT_COLUMN_WEIGHTS[key] ?? 10) * (legacyPixels ? 10 : 1),
      ];
    }),
  );
  const result: Record<string, number> = {};
  let remainingKeys = uniqueKeys.filter((key) => key !== 'number' || defaultNumberWidth == null);
  let remainingWeight = 100 - (defaultNumberWidth ?? 0);
  if (defaultNumberWidth != null) result.number = defaultNumberWidth;

  // Lower-bounded proportional allocation ("water filling"). There is no
  // artificial maximum: a user may give any column all space that remains
  // after its neighbours reach their minimum.
  while (remainingKeys.length > 0) {
    if (remainingKeys.length === 1) {
      result[remainingKeys[0]] = remainingWeight;
      break;
    }
    const rawTotal = remainingKeys.reduce((sum, key) => sum + raw[key], 0);
    let constrained: { key: string; weight: number } | null = null;
    for (const key of remainingKeys) {
      const candidate = (raw[key] / rawTotal) * remainingWeight;
      if (candidate < MIN_COLUMN_WIDTH) {
        constrained = { key, weight: MIN_COLUMN_WIDTH };
        break;
      }
    }
    if (!constrained) {
      for (const key of remainingKeys) {
        result[key] = (raw[key] / rawTotal) * remainingWeight;
      }
      break;
    }
    result[constrained.key] = constrained.weight;
    remainingWeight -= constrained.weight;
    remainingKeys = remainingKeys.filter((key) => key !== constrained.key);
  }

  const rounded = Object.fromEntries(
    uniqueKeys.map((key) => [key, Math.round(result[key] * 1000) / 1000]),
  );
  const roundedTotal = Object.values(rounded).reduce((sum, weight) => sum + weight, 0);
  const correction = Math.round((100 - roundedTotal) * 1000) / 1000;
  const correctionKey =
    uniqueKeys.find((key) =>
      correction >= 0 ? true : rounded[key] + correction >= MIN_COLUMN_WIDTH,
    ) ?? uniqueKeys[uniqueKeys.length - 1];
  rounded[correctionKey] = Math.round((rounded[correctionKey] + correction) * 1000) / 1000;
  return rounded;
}

export function resizeColumnWidths(
  widths: Record<string, number>,
  keys: string[],
  key: string,
  deltaPercent: number,
): Record<string, number> {
  const index = keys.indexOf(key);
  if (index < 0 || index >= keys.length - 1 || !Number.isFinite(deltaPercent)) return widths;
  const neighbour = keys[index + 1];
  const pairTotal = widths[key] + widths[neighbour];
  const minimum = Math.min(MIN_COLUMN_WIDTH, pairTotal / 2);
  const nextCurrent = Math.max(minimum, Math.min(pairTotal - minimum, widths[key] + deltaPercent));
  return {
    ...widths,
    [key]: Math.round(nextCurrent * 1000) / 1000,
    [neighbour]: Math.round((pairTotal - nextCurrent) * 1000) / 1000,
  };
}

/** Resolve the preferred percentages against the current data area.
 *
 * With enough room the result is the exact saved proportion. As the table
 * narrows, columns that would hide a fixed-format value stop shrinking at
 * their semantic pixel minimum. The remaining room is redistributed across
 * columns that still have slack, preserving their relative preference. If a
 * viewport is narrower than all minima combined, the minima scale together;
 * clipping is then unavoidable, but the table still stays inside its card. */
export function resolveResponsiveColumnWidths(
  keys: string[],
  weights: Record<string, number>,
  availableWidth: number,
): Record<string, number> {
  const uniqueKeys = Array.from(new Set(keys));
  if (uniqueKeys.length === 0) return {};
  if (!Number.isFinite(availableWidth) || availableWidth <= 0) {
    return Object.fromEntries(uniqueKeys.map((key) => [key, 0]));
  }
  if (uniqueKeys.length === 1) return { [uniqueKeys[0]]: availableWidth };

  const rawWeights = Object.fromEntries(
    uniqueKeys.map((key) => {
      const weight = weights[key];
      return [
        key,
        typeof weight === 'number' && Number.isFinite(weight) && weight > 0 ? weight : 1,
      ];
    }),
  );
  const rawMinimums = Object.fromEntries(
    uniqueKeys.map((key) => [
      key,
      RESPONSIVE_COLUMN_MIN_WIDTHS[key] ?? DEFAULT_RESPONSIVE_COLUMN_MIN_WIDTH,
    ]),
  );
  const minimumTotal = Object.values(rawMinimums).reduce((sum, width) => sum + width, 0);
  const minimumScale = minimumTotal > availableWidth ? availableWidth / minimumTotal : 1;
  const minimums = Object.fromEntries(
    uniqueKeys.map((key) => [key, rawMinimums[key] * minimumScale]),
  );

  const result: Record<string, number> = {};
  let remainingKeys = [...uniqueKeys];
  let remainingWidth = availableWidth;

  // Pixel-space water filling: pin every column whose proportional target is
  // below its readable floor, then repeat with the columns that still flex.
  while (remainingKeys.length > 0) {
    if (remainingKeys.length === 1) {
      result[remainingKeys[0]] = remainingWidth;
      break;
    }
    const weightTotal = remainingKeys.reduce((sum, key) => sum + rawWeights[key], 0);
    const constrainedKey = remainingKeys.find(
      (key) => (rawWeights[key] / weightTotal) * remainingWidth < minimums[key],
    );
    if (constrainedKey == null) {
      for (const key of remainingKeys) {
        result[key] = (rawWeights[key] / weightTotal) * remainingWidth;
      }
      break;
    }
    result[constrainedKey] = minimums[constrainedKey];
    remainingWidth -= minimums[constrainedKey];
    remainingKeys = remainingKeys.filter((key) => key !== constrainedKey);
  }

  const rounded = Object.fromEntries(
    uniqueKeys.map((key) => [key, Math.round(result[key] * 1000) / 1000]),
  );
  const roundedTotal = Object.values(rounded).reduce((sum, width) => sum + width, 0);
  const correction = Math.round((availableWidth - roundedTotal) * 1000) / 1000;
  const correctionKey =
    uniqueKeys.find((key) => rounded[key] + correction >= minimums[key] - 0.001) ??
    uniqueKeys[uniqueKeys.length - 1];
  rounded[correctionKey] = Math.round((rounded[correctionKey] + correction) * 1000) / 1000;
  return rounded;
}

function dataColumnWidth(
  key: string,
  weight: number,
  responsiveWidths: Record<string, number> | null,
): string {
  const responsiveWidth = responsiveWidths?.[key];
  return responsiveWidth == null ? `${weight}%` : `${responsiveWidth}px`;
}

function measureTrackTableWidth(element: HTMLDivElement | null): number | null {
  if (!element) return null;
  const rectWidth = element.getBoundingClientRect().width;
  const borderBoxWidth = element.clientWidth || rectWidth;
  if (!Number.isFinite(borderBoxWidth) || borderBoxWidth <= 0) return null;
  const style = window.getComputedStyle(element);
  const paddingLeft = Number.parseFloat(style.paddingLeft) || 0;
  const paddingRight = Number.parseFloat(style.paddingRight) || 0;
  const contentWidth = borderBoxWidth - paddingLeft - paddingRight;
  return contentWidth > 0 ? contentWidth : null;
}

/** Preference lists are leaf values in the JSON merge. When a new column is
 * introduced, an older stored list therefore does not contain it. Append all
 * missing defaults exactly once so new columns remain discoverable. */
export function mergeColumnOrder<K extends string>(stored: K[] | undefined, defaults: K[]): K[] {
  const allowed = new Set(defaults);
  return Array.from(new Set([...(stored ?? []).filter((key) => allowed.has(key)), ...defaults]));
}

function ResizableHeaderCell({
  columnKey,
  resizeLabel = columnKey,
  resizeKey = columnKey,
  handleSide = 'right',
  onResizeStart,
  onResize,
  onResizeReset,
  onKeyboardResize,
  resizable = true,
  className,
  children,
}: {
  columnKey: string;
  resizeLabel?: string;
  resizeKey?: string;
  handleSide?: 'left' | 'right';
  onResizeStart: (key: string) => void;
  onResize: (key: string, deltaPixels: number, phase: 'preview' | 'commit' | 'cancel') => void;
  onResizeReset: () => void;
  onKeyboardResize: (key: string, deltaPercent: number) => void;
  resizable?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const drag = useRef<{
    pointerId: number;
    startX: number;
  } | null>(null);

  function finishResize(event: ReactPointerEvent<HTMLSpanElement>, phase: 'commit' | 'cancel') {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    onResize(resizeKey, event.clientX - active.startX, phase);
  }

  return (
    <th className={className}>
      <span className={styles.resizableHeaderContent}>{children}</span>
      {resizable ? (
        <span
          role="separator"
          aria-label={`Resize ${resizeLabel} column`}
          aria-orientation="vertical"
          className={`${styles.columnResizeHandle} ${
            handleSide === 'left' ? styles.columnResizeHandleLeft : ''
          }`}
          onDoubleClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            drag.current = null;
            onResizeReset();
          }}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            event.stopPropagation();
            drag.current = {
              pointerId: event.pointerId,
              startX: event.clientX,
            };
            onResizeStart(resizeKey);
            event.currentTarget.setPointerCapture?.(event.pointerId);
          }}
          onPointerMove={(event) => {
            const active = drag.current;
            if (!active || active.pointerId !== event.pointerId) return;
            onResize(resizeKey, event.clientX - active.startX, 'preview');
          }}
          onPointerUp={(event) => finishResize(event, 'commit')}
          onPointerCancel={(event) => finishResize(event, 'cancel')}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
            event.preventDefault();
            onKeyboardResize(resizeKey, event.key === 'ArrowRight' ? 1 : -1);
          }}
          tabIndex={0}
          title="Drag to resize adjacent columns · double-click to reset layout"
        />
      ) : null}
    </th>
  );
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSort,
  onResizeStart,
  onResize,
  onResizeReset,
  onKeyboardResize,
  resizable,
  resizeKey,
  handleSide,
  className,
}: {
  label: string;
  sortKey: TrackSortKey;
  sort: TrackSort | null;
  onSort: (key: TrackSortKey) => void;
  onResizeStart: (key: string) => void;
  onResize: (key: string, deltaPixels: number, phase: 'preview' | 'commit' | 'cancel') => void;
  onResizeReset: () => void;
  onKeyboardResize: (key: string, deltaPercent: number) => void;
  resizable?: boolean;
  resizeKey?: string;
  handleSide?: 'left' | 'right';
  className?: string;
}) {
  const active = sort?.key === sortKey;
  return (
    <ResizableHeaderCell
      columnKey={sortKey}
      onResizeStart={onResizeStart}
      onResize={onResize}
      onResizeReset={onResizeReset}
      onKeyboardResize={onKeyboardResize}
      resizable={resizable}
      resizeKey={resizeKey}
      handleSide={handleSide}
      className={className}
    >
      <button type="button" className={styles.sortableHeader} onClick={() => onSort(sortKey)}>
        {label}
        {active ? (
          <span aria-hidden="true" className={styles.sortIndicator}>
            {sort?.dir === 'asc' ? '▲' : '▼'}
          </span>
        ) : null}
      </button>
    </ResizableHeaderCell>
  );
}

/** B5: gear popover to pick which optional columns show and whether to show
 *  every match-provider chip (vs. A8's default of only configured
 *  providers). Persisted server-side so picks survive a reload. */
function useUiPreferencesMutation() {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  // dd28-45: column resizing fires one mutation per drag settle, and the
  // responses are not ordered. Writing whatever arrives last into the cache
  // meant a slower older response could overwrite a newer one — the column
  // width visibly "sprang back". Stamp each request and ignore any answer that
  // is not the newest one this component instance issued.
  const sequenceRef = useRef(0);
  const settledRef = useRef(0);
  return useMutation({
    mutationFn: async (patch: Parameters<typeof updateLibraryV2UiPreferences>[0]) => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      const sequence = ++sequenceRef.current;
      const preferences = await updateLibraryV2UiPreferences(patch);
      return { sequence, preferences };
    },
    onSuccess: ({ sequence, preferences }) => {
      if (sequence < settledRef.current) return;
      settledRef.current = sequence;
      queryClient.setQueryData([...LIBRARY_V2_QUERY_KEY, 'ui-preferences'], preferences);
    },
    /**
     * iss29-C09: no consumer of this mutation renders `error`, so a rejected
     * write vanished completely — the toggle stayed where the user put it,
     * nothing was persisted, and the next page load quietly reverted it. M-12
     * is documented as implemented, which is what made the silence
     * indistinguishable from success.
     *
     * These are preferences, not library state, so a toast is the right
     * weight: it says the choice did not stick without interrupting the work.
     * Refetching restores what the server actually holds, so the UI stops
     * showing a value that only exists on this client.
     */
    onError: (error) => {
      const message = mutationErrorMessage(error, 'Could not save your view preferences');
      if (typeof window !== 'undefined' && typeof window.showToast === 'function') {
        window.showToast(message, 'error');
      } else {
        console.error('[library-v2] ui preferences update failed:', message);
      }
      void queryClient.invalidateQueries({
        queryKey: [...LIBRARY_V2_QUERY_KEY, 'ui-preferences'],
      });
    },
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
  columnOrder,
  onReorder,
  extra,
}: {
  title: string;
  columnLabels: Record<K, string>;
  columns: Record<K, boolean>;
  onToggle: (key: K) => void;
  columnOrder?: K[];
  onReorder?: (newOrder: K[]) => void;
  extra?: ReactNode;
}) {
  const [open, setOpen] = useState(false);

  const columnKeys = columnOrder ?? (Object.keys(columnLabels) as K[]);

  return (
    <span className={styles.overflowWrap} onClick={(e) => e.stopPropagation()}>
      <IconActionButton
        icon="settings"
        title={title}
        requiresWrite
        onClick={() => setOpen((v) => !v)}
      />
      {open ? (
        <ModalShell title={title} settings onClose={() => setOpen(false)}>
          <div className={styles.tableOptionsDialogBody}>
            <div className={styles.tableOptionsLayout}>
              <section className={styles.tableOptionsSection}>
                <div className={styles.tableOptionsGroupLabel}>Visible columns</div>
                <div className={styles.tableOptionsColumnGrid}>
                  {columnKeys.map((key, index) => (
                    <div key={key} className={styles.tableOptionsRow}>
                      {onReorder && columnOrder ? (
                        <div className={styles.tableOptionsReorder}>
                          <button
                            type="button"
                            title="Move up"
                            disabled={index === 0}
                            onClick={() => {
                              const nextOrder = [...columnOrder];
                              const temp = nextOrder[index];
                              nextOrder[index] = nextOrder[index - 1];
                              nextOrder[index - 1] = temp;
                              onReorder(nextOrder);
                            }}
                            className={styles.reorderBtn}
                          >
                            ▲
                          </button>
                          <button
                            type="button"
                            title="Move down"
                            disabled={index === columnKeys.length - 1}
                            onClick={() => {
                              const nextOrder = [...columnOrder];
                              const temp = nextOrder[index];
                              nextOrder[index] = nextOrder[index + 1];
                              nextOrder[index + 1] = temp;
                              onReorder(nextOrder);
                            }}
                            className={styles.reorderBtn}
                          >
                            ▼
                          </button>
                        </div>
                      ) : null}
                      <label className={styles.tableOptionsItem}>
                        <input
                          type="checkbox"
                          checked={columns[key]}
                          onChange={() => onToggle(key)}
                        />
                        {columnLabels[key]}
                      </label>
                    </div>
                  ))}
                </div>
              </section>
              {extra}
            </div>
          </div>
        </ModalShell>
      ) : null}
    </span>
  );
}

function TrackTableOptionsMenu({
  columns,
  columnOrder,
  showAllProviders,
  availableProviders,
  columnWidths,
  onResetColumnWidths,
}: {
  columns: LibraryV2TrackTableColumns;
  columnOrder: (keyof LibraryV2TrackTableColumns)[];
  showAllProviders: boolean;
  availableProviders?: Set<string> | null;
  columnWidths: Record<string, number | null>;
  onResetColumnWidths: () => void;
}) {
  const mutation = useUiPreferencesMutation();
  const prefsQuery = useQuery(libraryV2UiPreferencesQueryOptions());
  const visibleProviders = prefsQuery.data?.track_table.visible_match_providers ?? {};
  const qualityShowFormat = prefsQuery.data?.track_table.quality_show_format ?? true;
  const qualityShowResolution = prefsQuery.data?.track_table.quality_show_resolution ?? true;
  const qualityShowBitrate = prefsQuery.data?.track_table.quality_show_bitrate ?? true;

  const MATCH_PROVIDERS = [
    { key: 'spotify', label: 'Spotify' },
    { key: 'musicbrainz', label: 'MusicBrainz' },
    { key: 'deezer', label: 'Deezer' },
    { key: 'itunes', label: 'iTunes' },
    { key: 'audiodb', label: 'AudioDB' },
    { key: 'discogs', label: 'Discogs' },
    { key: 'lastfm', label: 'Last.fm' },
    { key: 'genius', label: 'Genius' },
    { key: 'tidal', label: 'Tidal' },
    { key: 'qobuz', label: 'Qobuz' },
    { key: 'amazon', label: 'Amazon' },
    { key: 'jiosaavn', label: 'JioSaavn' },
    { key: 'bandcamp', label: 'Bandcamp' },
  ];

  return (
    <ColumnsOptionsMenu
      title="Table options — columns & match providers"
      columnLabels={TRACK_TABLE_COLUMN_LABELS}
      columns={columns}
      onToggle={(key) => mutation.mutate({ track_table: { columns: { [key]: !columns[key] } } })}
      columnOrder={columnOrder}
      onReorder={(newOrder) => mutation.mutate({ track_table: { column_order: newOrder } })}
      extra={
        <div className={styles.tableOptionsExtraGrid}>
          <section className={styles.tableOptionsSection}>
            <div className={styles.tableOptionsGroupLabel}>Quality & sizing</div>
            <label className={styles.tableOptionsItem}>
              <input
                type="checkbox"
                checked={qualityShowFormat}
                onChange={() =>
                  mutation.mutate({
                    track_table: { quality_show_format: !qualityShowFormat },
                  })
                }
              />
              Show format
            </label>
            <label className={styles.tableOptionsItem}>
              <input
                type="checkbox"
                checked={qualityShowResolution}
                onChange={() =>
                  mutation.mutate({
                    track_table: {
                      quality_show_resolution: !qualityShowResolution,
                    },
                  })
                }
              />
              Show resolution
            </label>
            <label className={styles.tableOptionsItem}>
              <input
                type="checkbox"
                checked={qualityShowBitrate}
                onChange={() =>
                  mutation.mutate({
                    track_table: { quality_show_bitrate: !qualityShowBitrate },
                  })
                }
              />
              Show bitrate
            </label>
            <button
              type="button"
              className={styles.tableOptionsReset}
              disabled={!Object.values(columnWidths).some((width) => width != null)}
              onClick={onResetColumnWidths}
            >
              Reset column widths
            </button>
          </section>

          <section className={styles.tableOptionsSection}>
            <div className={styles.tableOptionsGroupLabel}>Match providers</div>
            <label className={styles.tableOptionsItem}>
              <input
                type="checkbox"
                checked={showAllProviders}
                onChange={() =>
                  mutation.mutate({
                    track_table: {
                      show_all_match_providers: !showAllProviders,
                    },
                  })
                }
              />
              Show every provider
            </label>
            <div className={styles.tableOptionsProviderGrid}>
              {MATCH_PROVIDERS.filter(
                (provider) => !availableProviders || availableProviders.has(provider.key),
              ).map((provider) => {
                const isVisible = visibleProviders[provider.key] ?? true;
                return (
                  <label key={provider.key} className={styles.tableOptionsItem}>
                    <input
                      type="checkbox"
                      checked={isVisible}
                      onChange={() =>
                        mutation.mutate({
                          track_table: {
                            visible_match_providers: {
                              ...visibleProviders,
                              [provider.key]: !isVisible,
                            },
                          },
                        })
                      }
                    />
                    {provider.label}
                  </label>
                );
              })}
            </div>
          </section>
        </div>
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
  size: 'Size',
};

function ArtistTableOptionsMenu({
  columns,
  columnOrder,
}: {
  columns: LibraryV2ArtistTableColumns;
  columnOrder: (keyof LibraryV2ArtistTableColumns)[];
}) {
  const mutation = useUiPreferencesMutation();
  return (
    <ColumnsOptionsMenu
      title="Table options — columns"
      columnLabels={ARTIST_TABLE_COLUMN_LABELS}
      columns={columns}
      onToggle={(key) => mutation.mutate({ artist_table: { columns: { [key]: !columns[key] } } })}
      columnOrder={columnOrder}
      onReorder={(newOrder) => mutation.mutate({ artist_table: { column_order: newOrder } })}
    />
  );
}

/** B6 bulk action bar for the track table's row-selection checkboxes.
 *  Deliberate reuse-first: Monitor/ReplayGain fan out the existing
 *  single-track mutations with Promise.all (no new backend), Write Tags
 *  calls the already-multi-track /tags/write job, and Delete reuses the
 *  same ADR-05 file_ids-scoped flow C2 built for the artist Files tab —
 *  just scoped to this album's selected tracks instead of the whole artist. */
export function TrackTableBulkBar({
  albumId,
  tracks,
  onClear,
}: {
  albumId: number;
  tracks: LibraryV2Track[];
  onClear: () => void;
}) {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState<{
    label: string;
    ids: number[];
    apply: (id: number) => Promise<unknown>;
  } | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [showBulkEdit, setShowBulkEdit] = useState(false);

  const trackIds = tracks.filter((t) => t.id != null).map((t) => t.id as number);
  const fileIds = tracks.map((t) => t.file?.file_id).filter((id): id is number => id != null);
  const bulkProfilesQuery = useQuery(libraryV2QualityProfilesQueryOptions());

  type Settled = {
    succeeded: number[];
    failed: Array<{ id: number; error: string }>;
  };

  async function run(label: string, fn: () => Promise<void | Settled>) {
    if (!canWrite) return;
    setBusy(label);
    setError(null);
    try {
      const result = await fn();
      if (!result || result.succeeded.length > 0) {
        await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      }
      if (result?.failed.length) {
        const failedIds = result.failed.map(({ id }) => id);
        setError(
          `${result.succeeded.length} succeeded; ${failedIds.length} failed (track IDs: ${failedIds.join(', ')}).`,
        );
      } else {
        setRetry(null);
      }
    } catch (e) {
      setError(mutationErrorMessage(e, `${label} failed`));
    } finally {
      setBusy(null);
    }
  }

  /**
   * Fan a per-track mutation out over the selection and report what actually
   * happened (iss29-C07).
   *
   * `Promise.all` rejects on the FIRST failure, so one unwritable track made
   * the bar say "Monitor failed" even though the other 39 had been applied —
   * and the user's only cue to re-check was the row state itself. It also
   * abandoned the remaining calls mid-flight. `allSettled` runs them all and
   * the message distinguishes total from partial failure.
   */
  async function fanOut(
    label: string,
    ids: number[],
    apply: (id: number) => Promise<unknown>,
  ): Promise<Settled> {
    const outcomes = await Promise.allSettled(ids.map((id) => apply(id)));
    const failed = outcomes.flatMap((outcome, index) =>
      outcome.status === 'rejected'
        ? [
            {
              id: ids[index]!,
              error: mutationErrorMessage(outcome.reason, 'unknown error'),
            },
          ]
        : [],
    );
    const succeeded = ids.filter((_, index) => outcomes[index]?.status === 'fulfilled');
    setRetry(failed.length ? { label, ids: failed.map(({ id }) => id), apply } : null);
    return { succeeded, failed };
  }

  return (
    <div className={styles.bulkBar}>
      <span className={styles.bulkBarCount}>{tracks.length} selected</span>
      <button
        type="button"
        className={styles.bulkBarButton}
        data-requires-write=""
        disabled={busy !== null || !canWrite}
        onClick={() =>
          void run('Monitor', () =>
            fanOut('Monitor', trackIds, (id) => setLibraryV2Monitored('tracks', id, true)),
          )
        }
      >
        {busy === 'Monitor' ? 'Monitoring…' : 'Monitor'}
      </button>
      <button
        type="button"
        className={styles.bulkBarButton}
        data-requires-write=""
        disabled={busy !== null || !canWrite}
        onClick={() =>
          void run('Unmonitor', () =>
            fanOut('Unmonitor', trackIds, (id) => setLibraryV2Monitored('tracks', id, false)),
          )
        }
      >
        {busy === 'Unmonitor' ? 'Unmonitoring…' : 'Unmonitor'}
      </button>
      <button
        type="button"
        className={styles.bulkBarButton}
        data-requires-write=""
        disabled={busy !== null || trackIds.length === 0 || !canWrite}
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
        data-requires-write=""
        disabled={busy !== null || trackIds.length === 0 || !canWrite}
        onClick={() =>
          void run('ReplayGain', () =>
            fanOut('ReplayGain', trackIds, (id) => analyzeLibraryV2TrackReplayGain(id)),
          )
        }
      >
        {busy === 'ReplayGain' ? 'Analyzing…' : 'ReplayGain'}
      </button>
      {/* UI-04 / iss29-C08: bulk quality-profile assignment. The backend and
          the single-track path both existed; the bulk bar was simply missing
          the control, so assigning a profile to 40 selected tracks meant 40
          trips through the per-row picker. "Inherit" posts `inherit: true`,
          the same payload the picker's inherit option sends. */}
      <label className={styles.bulkBarSelect}>
        <span className={styles.srOnly}>Quality profile</span>
        <select
          aria-label="Quality profile for the selected tracks"
          data-requires-write=""
          disabled={busy !== null || trackIds.length === 0 || !canWrite}
          value=""
          onChange={(event) => {
            const raw = event.target.value;
            event.target.value = '';
            if (!raw) return;
            const profileId = raw === 'inherit' ? null : Number(raw);
            void run('Quality profile', () =>
              // A track has no children to cascade to, and §52.3 keeps the
              // profile choice orthogonal to wanted/monitoring intent.
              fanOut('Quality profile', trackIds, (id) =>
                setLibraryV2QualityProfile('tracks', id, profileId, false, false),
              ),
            );
          }}
        >
          <option value="">{busy === 'Quality profile' ? 'Applying…' : 'Quality profile…'}</option>
          <option value="inherit">Inherit from album</option>
          {(bulkProfilesQuery.data ?? []).map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className={styles.bulkBarButton}
        data-requires-write=""
        disabled={busy !== null || trackIds.length === 0 || !canWrite}
        onClick={() => setShowBulkEdit(true)}
      >
        Bulk edit…
      </button>
      <button
        type="button"
        className={`${styles.bulkBarButton} ${styles.bulkBarButtonDanger}`}
        data-requires-write=""
        disabled={busy !== null || fileIds.length === 0 || !canWrite}
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
          {retry ? (
            <button
              type="button"
              className={styles.inlineRetry}
              disabled={busy !== null || !canWrite}
              onClick={() =>
                void run(`Retry ${retry.label}`, () => fanOut(retry.label, retry.ids, retry.apply))
              }
            >
              Retry failed {retry.ids.length}
            </button>
          ) : null}
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
            void queryClient.invalidateQueries({
              queryKey: LIBRARY_V2_QUERY_KEY,
            });
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
  const canWrite = useLibraryV2CanWrite();
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
  const [pendingTrackIds, setPendingTrackIds] = useState(trackIds);

  const parsedBpm = bpm.trim() === '' ? null : Number(bpm);
  const bpmValid =
    !applyBpm || (parsedBpm !== null && Number.isFinite(parsedBpm) && parsedBpm >= 0);
  const nothingSelected = !applyStyle && !applyMood && !applyBpm && !applyExplicit;

  async function save() {
    if (!canWrite) return;
    setBusy(true);
    setError(null);
    const values: Record<string, unknown> = {};
    if (applyStyle) values.style = style.trim() || null;
    if (applyMood) values.mood = mood.trim() || null;
    if (applyBpm) values.bpm = parsedBpm;
    if (applyExplicit) values.explicit = explicitFlag === 'yes';
    const outcomes = await Promise.allSettled(
      pendingTrackIds.map((id) => updateLibraryV2MetadataOverrides('track', id, values)),
    );
    const failedIds = pendingTrackIds.filter((_, index) => outcomes[index]?.status === 'rejected');
    const succeededIds = pendingTrackIds.filter(
      (_, index) => outcomes[index]?.status === 'fulfilled',
    );
    if (succeededIds.length) {
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    }
    if (failedIds.length) {
      setPendingTrackIds(failedIds);
      setError(
        `Updated ${succeededIds.length} track(s)${succeededIds.length ? ` (${succeededIds.join(', ')})` : ''}; failed ${failedIds.length} (${failedIds.join(', ')}). Retry only sends the failed tracks.`,
      );
      setBusy(false);
      return;
    }
    onSaved();
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
      {error ? (
        <div className={styles.searchError} role="alert">
          {error}
        </div>
      ) : null}
      <div className={styles.modalActions}>
        <button type="button" className={styles.btnGhost} disabled={busy} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={styles.btnPrimary}
          data-requires-write=""
          disabled={busy || nothingSelected || !bpmValid || !canWrite}
          onClick={() => void save()}
        >
          {busy
            ? 'Saving…'
            : `${pendingTrackIds.length < trackIds.length ? 'Retry' : 'Apply to'} ${pendingTrackIds.length} track${pendingTrackIds.length === 1 ? '' : 's'}`}
        </button>
      </div>
    </ModalShell>
  );
}

export function AlbumTrackTable({
  albumId,
  resolve,
  onAction,
  queueStatusTracks,
}: {
  albumId: number;
  /** Discography-only releases materialize their provider tracklist on expand. */
  resolve?: boolean;
  onAction: ActionHandler;
  /**
   * find22-15 / iss29-C06: the per-track queue map, handed down from the ONE
   * artist-scope poll. Every expanded album on an artist page used to mount its
   * own 3s poll — six open blocks came to ~140 requests/min, each running
   * `entity_track_ids` plus a queue scan against the single-writer SQLite
   * database that is this project's known bottleneck — for tracks the
   * artist-wide response already contained. Absent (the standalone album page,
   * which has no artist-scope query) this component still polls for itself.
   */
  queueStatusTracks?: Record<number, LibraryV2QueueStatusEntry>;
}) {
  const canWrite = useLibraryV2CanWrite();
  const albumQuery = useQuery(libraryV2AlbumQueryOptions(albumId, { resolve }));
  const matchQuery = useQuery(libraryV2AlbumMatchStatusQueryOptions(albumId));
  const profilesQuery = useQuery(libraryV2QualityProfilesQueryOptions());
  const prefsQuery = useQuery(libraryV2UiPreferencesQueryOptions());
  const ownQueueStatusQuery = useQuery({
    ...libraryV2QueueStatusQueryOptions('albums', albumId),
    enabled: queueStatusTracks === undefined && albumId > 0,
  });
  const queueTracks = queueStatusTracks ?? ownQueueStatusQuery.data?.tracks ?? {};
  const preferencesMutation = useUiPreferencesMutation();
  useRefreshLibraryWhenQueueDrains(Object.keys(queueTracks).length);
  const album = albumQuery.data;
  const [sort, setSort] = useState<TrackSort | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const columns = prefsQuery.data?.track_table.columns ?? DEFAULT_TRACK_TABLE_COLUMNS;
  const showAllProviders = prefsQuery.data?.track_table.show_all_match_providers ?? false;
  const columnWidths = prefsQuery.data?.track_table.column_widths ?? {};
  const defaultOrder: (keyof LibraryV2TrackTableColumns)[] = [
    'play',
    'disc',
    'artists',
    'duration',
    'bpm',
    'match',
    'quality',
    'features',
    'metadata',
    'verification',
    'acoustid',
    'file_size',
    'file_path',
  ];
  const orderedKeys = mergeColumnOrder(prefsQuery.data?.track_table.column_order, defaultOrder);
  const visibleColumnKeys = ['number', 'title', ...orderedKeys.filter((key) => columns[key])];
  const visibleColumnsSignature = visibleColumnKeys.join('|');
  const persistedWidthsSignature = visibleColumnKeys
    .map((key) => `${key}:${columnWidths[key] ?? ''}`)
    .join('|');
  const [columnWeights, setColumnWeights] = useState<Record<string, number>>(() =>
    normalizeColumnWidths(visibleColumnKeys, columnWidths),
  );
  const [tableWrapElement, setTableWrapElement] = useState<HTMLDivElement | null>(null);
  const [tableWidth, setTableWidth] = useState<number | null>(null);
  const resizeSnapshot = useRef<{
    key: string;
    widths: Record<string, number>;
  } | null>(null);

  useLayoutEffect(() => {
    const element = tableWrapElement;
    if (!element) return;
    const measure = () => {
      const next = measureTrackTableWidth(element);
      setTableWidth((current) => (current === next ? current : next));
    };
    measure();
    const observer =
      typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => measure());
    observer?.observe(element);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [tableWrapElement]);

  useEffect(() => {
    if (resizeSnapshot.current) return;
    setColumnWeights(normalizeColumnWidths(visibleColumnKeys, columnWidths));
    // Signatures deliberately represent the leaf preference values. Arrays
    // and merged preference objects are recreated while queries settle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleColumnsSignature, persistedWidthsSignature]);

  const responsiveColumnWidths =
    tableWidth != null && tableWidth > UTILITY_COLUMN_WIDTH
      ? resolveResponsiveColumnWidths(
          visibleColumnKeys,
          columnWeights,
          tableWidth - UTILITY_COLUMN_WIDTH,
        )
      : null;

  const availableProviders = useMemo(() => {
    if (!matchQuery.data) return null;
    const set = new Set<string>();
    for (const s of matchQuery.data.album) {
      if (s.available !== false) set.add(s.service);
    }
    for (const trackServices of Object.values(matchQuery.data.tracks)) {
      for (const s of trackServices) {
        if (s.available !== false) set.add(s.service);
      }
    }
    return set;
  }, [matchQuery.data]);
  if (albumQuery.isError) {
    // iss29-C04: an expanded album whose fetch failed used to sit on
    // "Loading tracks…" permanently, because `isLoading` is false once the
    // retry is spent and `album` stays undefined.
    return (
      <div className={styles.inlineLoading}>
        {mutationErrorMessage(albumQuery.error, 'Tracks could not be loaded.')}
      </div>
    );
  }
  if (albumQuery.isLoading || !album) {
    return <div className={styles.inlineLoading}>Loading tracks…</div>;
  }
  const albumMatch = matchQuery.data?.album ?? [];
  const trackMatch = matchQuery.data?.tracks ?? {};
  const profileNameById = new Map((profilesQuery.data ?? []).map((p) => [p.id, p.name]));

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

  function persistColumnWidths(widths: Record<string, number>) {
    preferencesMutation.mutate({
      track_table: {
        column_widths: {
          ...columnWidths,
          ...widths,
        },
      },
    });
  }

  function startColumnResize(key: string) {
    resizeSnapshot.current = { key, widths: columnWeights };
  }

  function resizeColumn(key: string, deltaPixels: number, phase: 'preview' | 'commit' | 'cancel') {
    const snapshot = resizeSnapshot.current;
    if (!snapshot || snapshot.key !== key) return;
    if (phase === 'cancel') {
      setColumnWeights(snapshot.widths);
      resizeSnapshot.current = null;
      return;
    }
    const liveTableWidth = measureTrackTableWidth(tableWrapElement) ?? tableWidth ?? 1;
    const dataWidth = Math.max(1, liveTableWidth - UTILITY_COLUMN_WIDTH);
    const deltaPercent = (deltaPixels / dataWidth) * 100;
    const next = resizeColumnWidths(snapshot.widths, visibleColumnKeys, key, deltaPercent);
    setColumnWeights(next);
    if (phase === 'commit') {
      resizeSnapshot.current = null;
      persistColumnWidths(next);
    }
  }

  function resizeColumnByKeyboard(key: string, deltaPercent: number) {
    const next = resizeColumnWidths(columnWeights, visibleColumnKeys, key, deltaPercent);
    setColumnWeights(next);
    persistColumnWidths(next);
  }

  function resetColumnLayout() {
    const next = normalizeColumnWidths(visibleColumnKeys);
    resizeSnapshot.current = null;
    setColumnWeights(next);
    preferencesMutation.mutate({
      track_table: {
        column_widths: Object.fromEntries(
          Array.from(new Set([...Object.keys(columnWidths), ...visibleColumnKeys])).map((key) => [
            key,
            null,
          ]),
        ),
      },
    });
  }

  function headerResizeProps(key: string) {
    const index = visibleColumnKeys.indexOf(key);
    const last = index === visibleColumnKeys.length - 1;
    return {
      onResizeStart: startColumnResize,
      onResize: resizeColumn,
      onResizeReset: resetColumnLayout,
      onKeyboardResize: resizeColumnByKeyboard,
      resizable: canWrite && visibleColumnKeys.length > 1,
      resizeKey: last ? visibleColumnKeys[index - 1] : key,
      handleSide: (last ? 'left' : 'right') as 'left' | 'right',
    };
  }

  const renderHeaderCell = (key: keyof LibraryV2TrackTableColumns) => {
    if (!columns[key]) return null;
    switch (key) {
      case 'disc':
        return (
          <ResizableHeaderCell
            key="disc"
            columnKey="disc"
            {...headerResizeProps('disc')}
            className={styles.colNum}
          >
            Disc
          </ResizableHeaderCell>
        );
      case 'artists':
        return (
          <ResizableHeaderCell key="artists" columnKey="artists" {...headerResizeProps('artists')}>
            Artists
          </ResizableHeaderCell>
        );
      case 'duration':
        return (
          <SortableHeader
            key="duration"
            className={styles.colDuration}
            label="Duration"
            sortKey="duration"
            sort={sort}
            onSort={toggleSort}
            {...headerResizeProps('duration')}
          />
        );
      case 'bpm':
        return (
          <SortableHeader
            key="bpm"
            className={styles.colBpm}
            label="BPM"
            sortKey="bpm"
            sort={sort}
            onSort={toggleSort}
            {...headerResizeProps('bpm')}
          />
        );
      case 'match':
        return (
          <ResizableHeaderCell key="match" columnKey="match" {...headerResizeProps('match')}>
            Match
          </ResizableHeaderCell>
        );
      case 'quality':
        return (
          <ResizableHeaderCell key="quality" columnKey="quality" {...headerResizeProps('quality')}>
            Quality
          </ResizableHeaderCell>
        );
      case 'features':
        return (
          <ResizableHeaderCell
            key="features"
            columnKey="features"
            {...headerResizeProps('features')}
            className={styles.colFeatures}
          >
            Features
          </ResizableHeaderCell>
        );
      case 'metadata':
        return (
          <ResizableHeaderCell
            key="metadata"
            columnKey="metadata"
            {...headerResizeProps('metadata')}
          >
            Metadata
          </ResizableHeaderCell>
        );
      case 'verification':
        return (
          <ResizableHeaderCell
            key="verification"
            columnKey="verification"
            {...headerResizeProps('verification')}
          >
            Verification
          </ResizableHeaderCell>
        );
      case 'acoustid':
        return (
          <ResizableHeaderCell
            key="acoustid"
            columnKey="acoustid"
            resizeLabel="Check"
            {...headerResizeProps('acoustid')}
          >
            Check
          </ResizableHeaderCell>
        );
      case 'file_size':
        return (
          <SortableHeader
            key="file_size"
            label="File size"
            sortKey="file_size"
            sort={sort}
            onSort={toggleSort}
            {...headerResizeProps('file_size')}
          />
        );
      case 'file_path':
        return (
          <ResizableHeaderCell
            key="file_path"
            columnKey="file_path"
            {...headerResizeProps('file_path')}
          >
            File
          </ResizableHeaderCell>
        );
      case 'play':
        return (
          <ResizableHeaderCell
            key="play"
            columnKey="play"
            {...headerResizeProps('play')}
            className={styles.colPlay}
          >
            <span className={styles.srOnly}>Play</span>
          </ResizableHeaderCell>
        );
      default:
        return null;
    }
  };

  return (
    <div ref={setTableWrapElement} className={styles.trackTableWrap}>
      <div className={styles.trackTableToolbar}>
        {albumMatch.length > 0 ? (
          <div className={styles.albumMatchRow}>
            <span className={styles.albumMatchLabel}>Matched via</span>
            <MatchChips
              entityType="album"
              entityName={album.title}
              entityImage={album.image_url}
              services={albumMatch}
              showAll={showAllProviders}
            />
          </div>
        ) : (
          <span />
        )}
        <TrackTableOptionsMenu
          columns={columns}
          columnOrder={orderedKeys}
          showAllProviders={showAllProviders}
          availableProviders={availableProviders}
          columnWidths={columnWidths}
          onResetColumnWidths={resetColumnLayout}
        />
      </div>
      {selected.size > 0 ? (
        <TrackTableBulkBar
          albumId={albumId}
          tracks={selectedTracks}
          onClear={() => setSelected(new Set())}
        />
      ) : null}
      <table className={styles.trackTable}>
        <colgroup>
          <col style={{ width: `${CHECKBOX_COLUMN_WIDTH}px` }} />
          <col style={{ width: `${MONITOR_COLUMN_WIDTH}px` }} />
          {visibleColumnKeys.map((key) => (
            <col
              key={key}
              style={{
                width: dataColumnWidth(key, columnWeights[key] ?? 0, responsiveColumnWidths),
              }}
            />
          ))}
          <col style={{ width: `${ACTION_COLUMN_WIDTH}px` }} />
        </colgroup>
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
              {...headerResizeProps('number')}
            />
            <SortableHeader
              label="Title"
              sortKey="title"
              sort={sort}
              onSort={toggleSort}
              {...headerResizeProps('title')}
            />
            {orderedKeys.map(renderHeaderCell)}
            <th className={styles.colActions}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {sortedTracks.map((track, i) => (
            <TrackRow
              key={track.id ?? `missing-${i}`}
              track={track}
              albumTitle={album.title}
              entityBase={{
                albumId: album.id,
                qualityProfileId: album.quality_profile?.id,
              }}
              matchServices={track.id ? (trackMatch[track.id] ?? []) : []}
              profileName={profileNameById.get(track.quality_profile_id) ?? null}
              columns={columns}
              columnOrder={orderedKeys}
              columnWidths={columnWeights}
              responsiveColumnWidths={responsiveColumnWidths}
              showAllProviders={showAllProviders}
              selected={track.id != null && selected.has(track.id)}
              onToggleSelect={
                track.id != null ? () => toggleSelected(track.id as number) : undefined
              }
              onAction={onAction}
              queueStatus={track.id != null ? queueTracks[track.id] : undefined}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Keep a page honest when a grab finishes after its search modal was closed.
 * Queue polling is already active on artist/album detail pages; use its
 * active→empty edge to refetch the committed autolink result exactly once. */
function useRefreshLibraryWhenQueueDrains(activeCount: number) {
  const queryClient = useQueryClient();
  const hadActiveQueue = useRef(false);
  useEffect(() => {
    if (activeCount > 0) {
      hadActiveQueue.current = true;
      return;
    }
    if (!hadActiveQueue.current) return;
    hadActiveQueue.current = false;
    void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
  }, [activeCount, queryClient]);
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

function metadataTagBreakdown(gaps: string[]) {
  const present = METADATA_TAG_ORDER.filter((tag) => !gaps.includes(tag)).map(
    (tag) => METADATA_TAG_LABELS[tag],
  );
  const missing = gaps.map((tag) => METADATA_TAG_LABELS[tag] ?? tag);
  return { present, missing };
}

function MetadataTagsTooltip({ gaps, hint }: { gaps: string[]; hint: string }) {
  const { present, missing } = metadataTagBreakdown(gaps);
  return (
    <Tooltip.Portal>
      <Tooltip.Positioner
        className={styles.metadataTagsTooltipPositioner}
        sideOffset={8}
        collisionPadding={8}
      >
        <Tooltip.Popup role="tooltip" className={styles.metadataTagsTooltip}>
          {present.length > 0 ? (
            <div className={styles.metadataTagsTooltipGroup}>
              <strong>Present tags</strong>
              <span className={styles.metadataTagsPresent}>
                {present.map((label) => `✓ ${label}`).join(' · ')}
              </span>
            </div>
          ) : null}
          {missing.length > 0 ? (
            <div className={styles.metadataTagsTooltipGroup}>
              <strong>Missing tags</strong>
              <span className={styles.metadataTagsMissing}>
                {missing.map((label) => `✗ ${label}`).join(' · ')}
              </span>
            </div>
          ) : null}
          <span className={styles.metadataTagsTooltipHint}>{hint}</span>
        </Tooltip.Popup>
      </Tooltip.Positioner>
    </Tooltip.Portal>
  );
}

/** LV2-TAG-STATUS-01/02: the tag-gap cell must not claim "tags ✓" before the
 *  canonical tag reader has actually scanned this file — an unscanned or
 *  unreadable file shows an explicit, non-actionable state instead of a
 *  possibly-false empty gap list. Once genuinely scanned, "tags ✓" opens the
 *  Tags detail tab and "N tag gaps" is clickable to write this track's
 *  library metadata into its file tags on the spot (same job/endpoint as
 *  TrackWriteTagsButton, scoped to just this track) — never optimistic; the
 *  cell only shows "tags ✓" again once the server confirms it post-write. */
export function TrackMetadataGapsCell({
  track,
  onOpenTags,
}: {
  track: LibraryV2Track;
  onOpenTags: () => void;
}) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async () => {
      // iss27-02: unlike the plain write-tags button, a tag-gap click first
      // re-queries providers for whatever the catalogue is missing, so a
      // field the catalogue never had (not just one it already had) gets a
      // real chance to be filled before the file write.
      const jobId = await fillLibraryV2TagGaps(track.id as number);
      const state = await awaitBulkJobState(queryClient, jobId);
      if (state.error) throw new Error(state.error);
      return {
        written: Number(state.result?.written ?? 0),
        enrichedFrom: (state.result?.enriched_from as string | null | undefined) ?? null,
      };
    },
    onSuccess: ({ written, enrichedFrom }) => {
      // The endpoint reports success even when it wrote to no file — a gap the
      // catalogue itself cannot fill (no genre stored on the album) leaves
      // nothing to write. Claiming "Tags written" there is what made the same
      // gaps look permanent after an apparently successful click (T-03).
      if (written > 0) {
        window.showToast?.(
          enrichedFrom
            ? `Refetched from ${enrichedFrom} and wrote tags to file.`
            : 'Tags written to file.',
          'success',
        );
      } else {
        window.showToast?.('Nothing to write — no configured provider has these tags yet.', 'info');
      }
    },
    onError: (error) => {
      window.showToast?.(mutationErrorMessage(error, 'Fill tag gaps failed'), 'error');
    },
  });

  const scanStatus = track.metadata_scan_status ?? 'scanned';
  if (scanStatus === 'pending') {
    return (
      <span className={styles.muted} title="File not yet scanned for tags — run Refresh &amp; Scan">
        scan pending
      </span>
    );
  }
  if (scanStatus === 'unreadable') {
    return (
      <span
        className={styles.statusWarn}
        title="The file's tags could not be read on the last scan"
      >
        unreadable
      </span>
    );
  }
  if (track.metadata_gaps.length === 0) {
    return (
      <Tooltip.Root>
        <Tooltip.Trigger
          type="button"
          delay={150}
          className={styles.statusOk}
          onClick={(e) => {
            e.stopPropagation();
            onOpenTags();
          }}
        >
          tags ✓
        </Tooltip.Trigger>
        <MetadataTagsTooltip gaps={track.metadata_gaps} hint="Click to inspect the file tags." />
      </Tooltip.Root>
    );
  }
  const hint = mutation.isError
    ? mutationErrorMessage(mutation.error, 'Write tags failed')
    : 'Click to fetch the missing metadata and write these tags to the file.';
  return (
    <Tooltip.Root>
      <Tooltip.Trigger
        type="button"
        delay={150}
        className={styles.statusWarn}
        disabled={mutation.isPending || !track.id}
        onClick={(e) => {
          e.stopPropagation();
          mutation.mutate();
        }}
      >
        {mutation.isPending ? '…' : `${track.metadata_gaps.length} tag gaps`}
      </Tooltip.Trigger>
      <MetadataTagsTooltip gaps={track.metadata_gaps} hint={hint} />
    </Tooltip.Root>
  );
}

function TrackRow({
  track,
  albumTitle,
  entityBase,
  matchServices,
  profileName,
  columns,
  columnOrder,
  columnWidths,
  responsiveColumnWidths,
  showAllProviders,
  selected,
  onToggleSelect,
  onAction,
  queueStatus,
}: {
  track: LibraryV2Track;
  albumTitle: string;
  entityBase: Lib2EntityRef;
  matchServices: LibraryV2MatchService[];
  profileName: string | null;
  columns: LibraryV2TrackTableColumns;
  columnOrder: (keyof LibraryV2TrackTableColumns)[];
  columnWidths: Record<string, number | null>;
  responsiveColumnWidths: Record<string, number> | null;
  showAllProviders: boolean;
  selected: boolean;
  onToggleSelect: (() => void) | undefined;
  onAction: ActionHandler;
  /** §73/I6: this track's live queue-status entry, if any is in flight. */
  queueStatus?: LibraryV2QueueStatusEntry;
}) {
  const prefsQuery = useQuery(libraryV2UiPreferencesQueryOptions());
  const missing = track.file_status === 'missing';
  const label = track.title ?? `Track ${track.track_number ?? '?'}`;
  const entity: Lib2EntityRef = {
    ...entityBase,
    ...(track.id ? { trackId: track.id } : {}),
  };
  const [detailTab, setDetailTab] = useState<TrackDetailTab | null>(null);
  const widthStyle = (key: string) => {
    const raw = columnWidths[key];
    if (raw == null) return undefined;
    return {
      width: dataColumnWidth(key, raw, responsiveColumnWidths),
    };
  };

  const renderBodyCell = (key: keyof LibraryV2TrackTableColumns) => {
    if (!columns[key]) return null;
    switch (key) {
      case 'disc':
        return (
          <td key="disc" className={styles.colNum} style={widthStyle('disc')}>
            {track.disc_number ?? '—'}
          </td>
        );
      case 'artists':
        return (
          <td key="artists" style={widthStyle('artists')}>
            {track.artists.map((a) => a.name).join(', ')}
          </td>
        );
      case 'duration':
        return (
          <td key="duration" className={styles.colDuration} style={widthStyle('duration')}>
            {formatDuration(track.duration)}
          </td>
        );
      case 'bpm':
        return (
          <td key="bpm" className={styles.colBpm} style={widthStyle('bpm')}>
            {track.bpm ?? '—'}
          </td>
        );
      case 'match':
        return (
          <td key="match" style={widthStyle('match')}>
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
        );
      case 'quality': {
        const showFormat = prefsQuery.data?.track_table.quality_show_format ?? true;
        const showResolution = prefsQuery.data?.track_table.quality_show_resolution ?? true;
        const showBitrate = prefsQuery.data?.track_table.quality_show_bitrate ?? true;
        const showFormatCol = showFormat || showResolution;

        return (
          <td key="quality" className={styles.qualityText} style={widthStyle('quality')}>
            <span className={styles.qualityCellRow}>
              {/* 1. Format & Resolution */}
              {showFormatCol ? (
                <div className={styles.qualityColFormat}>
                  <QualityDisplay file={track.file} />
                </div>
              ) : null}

              {/* 2. Bitrate */}
              {showBitrate ? (
                <div className={styles.qualityColBitrate}>
                  <BitrateDisplay file={track.file} />
                </div>
              ) : null}

              {/* 3. Quality Profile (color-coded based on quality status) */}
              <div className={styles.qualityColProfile}>
                {profileName ? (
                  <span
                    className={`${styles.qualityProfileBadge} ${
                      track.meets_profile === false
                        ? styles.qpBelow
                        : track.upgrade_candidate === true
                          ? styles.qpUpgrade
                          : track.meets_profile === null && track.file
                            ? styles.qpUnknown
                            : styles.qpDefault
                    }`}
                    title={
                      track.meets_profile === false
                        ? `Quality profile: ${profileLabel(profileName, track.quality_profile_source)} · Below profile`
                        : track.upgrade_candidate === true
                          ? `Quality profile: ${profileLabel(profileName, track.quality_profile_source)} · Upgrade candidate available`
                          : track.meets_profile === null && track.file
                            ? `Quality profile: ${profileLabel(profileName, track.quality_profile_source)} · Quality unknown - scan to evaluate`
                            : `Quality profile: ${profileLabel(profileName, track.quality_profile_source)} · Meets profile`
                    }
                  >
                    <SvgIcon name="star" />
                    {profileLabel(profileName, track.quality_profile_source)}
                  </span>
                ) : null}
              </div>
            </span>
          </td>
        );
      }
      case 'features':
        return (
          <td key="features" style={widthStyle('features')}>
            {!missing && track.file ? (
              <span className={styles.featuresDisplay}>
                <TrackReplayGainBadge track={track} />
                <TrackLyricsBadge track={track} onOpenLyrics={() => setDetailTab('lyrics')} />
              </span>
            ) : (
              <span className={styles.muted}>—</span>
            )}
          </td>
        );
      case 'metadata':
        return (
          <td key="metadata" style={widthStyle('metadata')}>
            {track.id && !missing ? (
              <TrackMetadataGapsCell track={track} onOpenTags={() => setDetailTab('tags')} />
            ) : (
              <span className={styles.muted}>—</span>
            )}
          </td>
        );
      case 'verification':
        return (
          <td key="verification" style={widthStyle('verification')}>
            {!missing && track.file?.verification_status ? (
              <TrackVerificationBadge file={track.file} />
            ) : (
              // A file with no recorded provenance is a real, actionable state
              // (T-09), not an empty cell: it says "nothing in this library
              // knows how this file was checked".
              <span
                className={styles.muted}
                title={
                  missing || !track.file
                    ? 'No file'
                    : 'No verification recorded — run Refresh & Scan to adopt the file’s own tag, or the AcoustID Scanner to check it'
                }
              >
                —
              </span>
            )}
          </td>
        );
      case 'acoustid':
        return (
          <td key="acoustid" style={widthStyle('acoustid')}>
            {missing ? (
              <span className={styles.muted}>—</span>
            ) : (
              <TrackCheckBadge file={track.file} />
            )}
          </td>
        );
      case 'file_path':
        return (
          <td
            key="file_path"
            className={styles.filePathCell}
            style={widthStyle('file_path')}
            title={track.file?.path ?? undefined}
          >
            {track.file?.path ?? <span className={styles.muted}>—</span>}
          </td>
        );
      case 'file_size':
        return (
          <td key="file_size" className={styles.fileSizeCell} style={widthStyle('file_size')}>
            {track.file?.size == null ? (
              <span className={styles.muted}>—</span>
            ) : (
              formatFileSize(track.file.size)
            )}
          </td>
        );
      case 'play':
        return (
          <td key="play" className={styles.colPlay} style={widthStyle('play')}>
            <TrackPlayButton
              track={track}
              albumTitle={albumTitle}
              artistName={track.artists.map((a) => a.name).join(', ')}
            />
          </td>
        );
      default:
        return null;
    }
  };

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
      <td className={styles.colMonitor}>
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
      <td className={styles.colNum} style={widthStyle('number')}>
        {track.track_number ?? '—'}
      </td>
      <td style={widthStyle('title')}>
        {/* Legacy parity: present/missing shown inline right after the title. */}
        <span className={styles.trackTitleCell}>
          <span className={missing ? styles.muted : undefined}>{label}</span>
          <MediaServerRecognitionBadge sources={track.media_server_sources} />
          <InlineFileStatus status={track.file_status} />
          <QueueStatusBadge status={queueStatus} />
        </span>
      </td>
      {columnOrder.map(renderBodyCell)}
      <td className={styles.trackActions}>
        <IconActionButton
          icon="automatic"
          title="Automatic Search — search missing/upgradable for this track"
          requiresWrite
          disabled={!track.id}
          onClick={() => onAction(`Search: ${label} (${albumTitle})`, entity)}
        />
        <IconActionButton
          icon="interactive"
          title="Interactive Search — pick the source yourself"
          requiresWrite
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
  albumTitle,
  artistName,
}: {
  track: LibraryV2Track;
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
            id: track.server_track_id ?? track.legacy_track_id ?? null,
            lib2_track_id: trackId,
            legacy_track_id: track.legacy_track_id ?? null,
            server_track_id: track.server_track_id ?? null,
            title: track.title ?? 'Unknown Track',
            file_path: filePath,
            bitrate: track.file?.bitrate ?? null,
            artist_id: null,
            // iss29-B08: the V2 artist, so the player's "Go to artist" can
            // route back into this page instead of staying disabled.
            lib2_artist_id:
              track.artists?.find((a) => a.role === 'primary')?.id ??
              track.artists?.[0]?.id ??
              null,
            // Legacy ids, and library-v2 only holds lib2 ones. Feeding a lib2
            // album id into a legacy slot is the H-14 confusion; the player
            // gets the display strings below instead.
            album_id: null,
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
  const canWrite = useLibraryV2CanWrite();
  const hasRg = Boolean(track.file?.has_replaygain);
  const mutation = useMutation({
    mutationFn: () => analyzeLibraryV2TrackReplayGain(track.id as number),
    onSuccess: (gainDb) => {
      void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      const gainStr = gainDb != null ? ` (${gainDb > 0 ? '+' : ''}${gainDb.toFixed(1)} dB)` : '';
      window.showToast?.(`ReplayGain analyzed and written${gainStr}.`, 'success');
    },
    onError: (error) => {
      window.showToast?.(mutationErrorMessage(error, 'ReplayGain analysis failed'), 'error');
    },
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
      data-requires-write=""
      disabled={mutation.isPending || !track.id || !canWrite}
      title={
        mutation.isError
          ? mutationErrorMessage(mutation.error, 'ReplayGain analysis failed')
          : 'Analyze + write ReplayGain for this track'
      }
      onClick={(e) => {
        e.stopPropagation();
        if (canWrite) mutation.mutate();
      }}
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
  const canWrite = useLibraryV2CanWrite();
  const hasLyrics = Boolean(track.file?.has_lyrics);
  const mutation = useMutation({
    mutationFn: () => fetchLibraryV2TrackLyrics(track.id as number),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      window.showToast?.('Lyrics fetched and embedded.', 'success');
    },
    onError: (error) => {
      window.showToast?.(mutationErrorMessage(error, 'Lyrics fetch failed'), 'error');
    },
  });
  if (hasLyrics) {
    return (
      <button
        type="button"
        className={`${styles.featureTag} ${styles.featureLr}`}
        title="Lyrics are embedded in this track — click to view"
        onClick={(e) => {
          e.stopPropagation();
          onOpenLyrics();
        }}
      >
        LR
      </button>
    );
  }
  return (
    <button
      type="button"
      className={`${styles.featureTag} ${styles.featureMissing}`}
      data-requires-write=""
      disabled={mutation.isPending || !track.id || !canWrite}
      title={
        mutation.isError
          ? mutationErrorMessage(mutation.error, 'Lyrics fetch failed')
          : 'Fetch lyrics from LRClib for this track'
      }
      onClick={(e) => {
        e.stopPropagation();
        if (canWrite) mutation.mutate();
      }}
    >
      {mutation.isPending ? '…' : 'LR'}
    </button>
  );
}

/** Legacy parity: present/missing indicator that sits inline after the title. */
function InlineFileStatus({ status }: { status: LibraryV2Track['file_status'] }) {
  if (status === 'duplicate_single')
    return <span className={styles.inlineDuplicate}>also on album</span>;
  if (status === 'missing_suspected')
    return (
      <span
        className={styles.inlineMissingSuspected}
        title="The file was absent during one healthy storage scan. A second scan is required before it is treated as missing."
      >
        checking missing
      </span>
    );
  return null;
}

/** §73/I6: live download-queue badge for one track row. Active-only —
 *  renders nothing once the track has no in-flight entry, matching the
 *  existing quality/verification badges that already cover completed and
 *  failed outcomes. */
const QUEUE_STATUS_LABELS: Record<LibraryV2QueueStatusEntry['status'], string> = {
  queued: 'Queued',
  searching: 'Searching',
  downloading: 'Downloading',
  processing: 'Processing',
};

function QueueStatusBadge({ status }: { status: LibraryV2QueueStatusEntry | undefined }) {
  if (!status) return null;
  const label = QUEUE_STATUS_LABELS[status.status];
  const text = status.status === 'downloading' ? `${label} ${status.progress_pct}%` : label;
  return (
    <span className={styles.queueStatusBadge} title={text}>
      <SvgIcon name="download" />
      {text}
    </span>
  );
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
        icon="edit"
        title="Edit track — quality profile, metadata, tags, lyrics and pipeline info"
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
            currentProfileSource={track.quality_profile_source}
            currentProfileExplicit={track.quality_profile_explicit}
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
const FILE_TAG_DUPLICATE_KEYS = new Set(['quality']);
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
  { name: 'Beatport', prefix: 'beatport_' },
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

export interface GroupedFileTags {
  track: [string, string][];
  album: [string, string][];
  replaygain: [string, string][];
  source: Record<string, [string, string][]>;
  other: [string, string][];
}

/** Keep the Track Detail tag inspector on the same grouping contract as the
 * quarantine/download-audit inspector. Lyrics have their own tab and values
 * already represented by the file-info strip (currently `quality`) are not
 * repeated as an opaque catch-all row. */
export function groupFileTags(tags: Record<string, string>): GroupedFileTags {
  const buckets: GroupedFileTags = {
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
      if (!value || FILE_TAG_LYRICS_KEYS.includes(key) || FILE_TAG_DUPLICATE_KEYS.has(key)) return;
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
  return buckets;
}

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
      // dd28-46: the track row's tag-gap cell reads `track.metadata_gaps` from
      // the ALBUM query, not from this panel's query — so a successful manual
      // tag write left the row still claiming "N tag gaps" until something
      // else happened to refetch. Invalidating the whole namespace is the same
      // thing every other write in this file does.
      void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
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
  const buckets = groupFileTags(tags);

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

  const section = (title: string, entries: [string, string][], compact = false) =>
    entries.length === 0 ? null : (
      <section key={title} className={compact ? styles.fileTagSourceCard : styles.fileTagGroup}>
        <h4 className={styles.fileTagGroupTitle}>{title}</h4>
        <div>
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
                className={`${styles.fileTagRow} ${styles.tagRowClickable}`}
                title="Click to edit tag"
                onClick={() => handleStartEdit(key, value)}
              >
                <span className={styles.fileTagKey}>{fileTagLabel(key)}</span>
                <span className={styles.fileTagValue}>
                  {value}
                  <span className={styles.editIndicator}>
                    <SvgIcon name="edit" />
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      </section>
    );

  const sourceSections = [...FILE_TAG_SOURCE_SERVICES.map((s) => s.name), 'Other Sources']
    .map((name) => section(name, buckets.source[name] ?? [], true))
    .filter(Boolean);

  return (
    <div>
      {error ? (
        <div className={styles.searchError} style={{ margin: '8px 0' }}>
          {error}
        </div>
      ) : null}
      <div className={styles.fileTagChips} aria-label="File properties">
        {data.format ? <span className={styles.fileTagChip}>{data.format}</span> : null}
        {data.bitrate ? (
          <span className={styles.fileTagChip}>{Math.round(data.bitrate / 1000)} kbps</span>
        ) : null}
        {data.duration ? (
          <span className={styles.fileTagChip}>
            {Math.floor(data.duration / 60)}:
            {String(Math.round(data.duration % 60)).padStart(2, '0')}
          </span>
        ) : null}
        <span className={styles.fileTagChip}>Cover {data.has_picture ? '✓' : '—'}</span>
      </div>
      <div className={styles.fileTagGrid}>
        {section('Track', buckets.track)}
        {section('Album', buckets.album)}
        {section('ReplayGain', buckets.replaygain)}
        {section('Other', buckets.other)}
      </div>
      {sourceSections.length > 0 ? (
        <section className={`${styles.fileTagGroup} ${styles.fileTagSources}`}>
          <h4 className={styles.fileTagGroupTitle}>Source IDs</h4>
          <div className={styles.fileTagSourceGrid}>{sourceSections}</div>
        </section>
      ) : null}
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
  const fallbacks = file?.pipeline_result?.quality_fallback ?? [];
  if (!file?.verification_status && manualSkips.length === 0 && fallbacks.length === 0) {
    return null;
  }
  return (
    <div className={styles.sourceInfoBody}>
      {file?.verification_status ? (
        <SourceInfoRow label="Verification" value={<TrackVerificationBadge file={file} />} />
      ) : null}
      {fallbacks.length > 0 ? (
        <SourceInfoRow
          label="Quality gate"
          value={fallbacks.map((f) => QUALITY_FALLBACK_LABELS[f] ?? f).join(', ')}
        />
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

/** §52.9: chronological search→grab→quality→quarantine→import timeline for
 *  one track (`core.library2.history_feed.scoped_history`, scope='track').
 *  Unlike the download-source list above, this also surfaces attempts that
 *  were quarantined or failed before ever producing a `lib2_track_files`
 *  row — the gap the Info tab left open per §52.9/§52.10. */
export function TrackPipelineTimeline({ trackId }: { trackId: number }) {
  const query = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'track-history', trackId],
    queryFn: () => fetchLibraryV2TrackHistory(trackId),
  });
  const rows = query.data ?? [];
  if (query.isLoading) {
    return <div className={styles.inlineLoading}>Loading pipeline history…</div>;
  }
  if (query.isError) {
    return (
      <QueryFailure
        error={query.error}
        fallback="Could not load pipeline history."
        retry={() => void query.refetch()}
      />
    );
  }
  if (rows.length === 0) return null;
  // Oldest first — a pipeline reads top-to-bottom like the journey it is;
  // the backend returns newest-first for the flat artist History table.
  const chronological = [...rows].reverse();
  return (
    <div className={styles.trackHistoryWrap}>
      <p className={styles.sourceInfoHistory}>
        Pipeline — {chronological.length} event
        {chronological.length === 1 ? '' : 's'}
      </p>
      <ul className={styles.pipelineTimeline}>
        {chronological.map((h, i) => (
          <li key={i} className={styles.pipelineTimelineItem}>
            <div className={styles.pipelineTimelineHead}>
              <span className={styles.sourceBadge} data-tone={h.category}>
                {h.title ?? h.event_type}
              </span>
              {h.status ? (
                <span className={styles.pipelineStatus} data-status={h.status}>
                  {h.status.replace('_', ' ')}
                </span>
              ) : null}
              <span className={styles.pipelineTimelineDate}>
                {h.date ? h.date.slice(0, 16).replace('T', ' ') : '—'}
              </span>
            </div>
            {h.detail ? <div className={styles.pipelineTimelineDetail}>{h.detail}</div> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Info tab: verification/lifecycle summary + current source (with blacklist)
 *  + the full download history — every past provenance record for this
 *  track, not just the latest. */
export function TrackInfoPanel({
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

  const lifecycle = (
    <>
      <TrackLifecycleSection file={file} manualSkips={manualSkips} />
      <TrackPipelineTimeline trackId={trackId} />
    </>
  );

  if (query.isLoading) {
    return (
      <>
        {lifecycle}
        <div className={styles.inlineLoading}>Loading source info…</div>
      </>
    );
  }
  if (query.isError) {
    return (
      <>
        {lifecycle}
        <QueryFailure
          error={query.error}
          fallback="Could not load download source data."
          retry={() => void query.refetch()}
        />
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
      <span className={styles.qualityStatusDotRed} title="Below the album's quality profile" />
    );
  }
  if (track.upgrade_candidate === true) {
    return (
      <span
        className={styles.qualityStatusDotOrange}
        title="A higher-quality version may be available (upgrade candidate)"
      />
    );
  }
  if (track.meets_profile === null) {
    return (
      <span
        className={styles.qualityStatusDotBlue}
        title="Quality unknown (scan the file to evaluate)"
      />
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

const IMPORT_STAGE_LABELS: Record<string, string> = {
  starting: 'Starting import',
  artists: 'Importing artists',
  albums: 'Importing albums',
  tracks: 'Importing tracks',
  tracklists: 'Resolving tracklists',
  tags: 'Reading file tags',
  artwork: 'Caching artwork',
  done: 'Finishing import',
};

export function describeLibraryV2ImportProgress(state: LibraryV2ImportState): string {
  const label = IMPORT_STAGE_LABELS[state.stage ?? ''] ?? 'Importing library';
  if (!Number.isFinite(state.total) || state.total <= 0) return `${label}…`;
  const total = Math.max(0, Math.round(state.total));
  const current = Math.min(total, Math.max(0, Math.round(state.current)));
  const percent = clampPercent((current / total) * 100);
  return `${label} · ${current}/${total} · ${percent}%`;
}

export function describeLibraryV2ImportCompletion(state: LibraryV2ImportState): string {
  const stats = state.stats;
  if (!stats) return 'Import complete.';
  const counts = [
    ['artist', 'artists', stats.artists],
    ['album', 'albums', stats.albums],
    ['track', 'tracks', stats.tracks],
  ] as const;
  if (counts.some(([, , value]) => typeof value !== 'number')) return 'Import complete.';
  const summary = counts
    .map(([singular, plural, value]) => `${value} ${value === 1 ? singular : plural}`)
    .join(' · ');
  return `Import complete — ${summary}.`;
}

/** How the automatic migration is doing, or null when it has nothing to say.
 *
 * An upgrading installation migrates itself in the background, so a user can
 * open this page while it is still running. Without this the page would just
 * look empty, and a failed migration would look like an empty library forever.
 */
export function describeLibraryV2Migration(
  state: LibraryV2ImportState | undefined,
): { tone: 'busy' | 'error'; text: string } | null {
  const bootstrap = state?.bootstrap;
  if (!bootstrap) return null;
  if (bootstrap.status === 'running') {
    const label = IMPORT_STAGE_LABELS[bootstrap.stage ?? ''] ?? 'Migrating your library';
    const total = Math.max(0, Math.round(bootstrap.total));
    if (total <= 0) return { tone: 'busy', text: `${label}…` };
    const current = Math.min(total, Math.max(0, Math.round(bootstrap.current)));
    const percent = clampPercent((current / total) * 100);
    return {
      tone: 'busy',
      text: `Migrating your library · ${label} · ${current}/${total} · ${percent}%`,
    };
  }
  if (bootstrap.status === 'failed') {
    return {
      tone: 'error',
      text:
        `Migrating your library failed: ${bootstrap.last_error || 'unknown error'}. ` +
        'It retries on its own; you can also start it here.',
    };
  }
  return null;
}

export function describeLibraryV2ArtworkCacheProgress(state: LibraryV2ImportState): string {
  const cache = state.artwork_cache;
  if (!Number.isFinite(cache.total) || cache.total <= 0) {
    return 'Library ready to browse · Caching artwork in the background…';
  }
  const total = Math.max(0, Math.round(cache.total));
  const current = Math.min(total, Math.max(0, Math.round(cache.current)));
  const percent = clampPercent((current / total) * 100);
  return `Library ready to browse · Caching artwork in the background · ${current}/${total} · ${percent}%`;
}

/** F-13 global Automatic Search action. Upgrade candidates must be mirrored first,
 * then the existing Wishlist processor sees one complete missing+upgrade
 * queue. Starting the processor first creates a race where the just-enqueued
 * upgrades can miss the active cycle. */
export function GlobalAutomaticSearchButton() {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function runSearch() {
    if (!canWrite) return;
    setBusy(true);
    setMessage('Finding missing tracks and quality upgrades…');
    let upgradesQueued = false;
    try {
      const jobId = await startLibraryV2UpgradeScan();
      const error = await awaitBulkJob(queryClient, jobId);
      if (error) throw new Error(error);
      upgradesQueued = true;
      setMessage('Upgrades queued · starting Wishlist processing…');
      const started = await processWishlist();
      setMessage(`${started} Missing tracks and quality upgrades are queued.`);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } catch (e) {
      const detail = e instanceof Error ? e.message : 'Automatic Search failed';
      setMessage(
        upgradesQueued ? `Upgrades queued, but Wishlist processing failed: ${detail}` : detail,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className={styles.automaticSearchWrap}>
      <button
        type="button"
        className={`${styles.btnGhost} ${styles.automaticSearchButton}`}
        data-requires-write=""
        disabled={busy || !canWrite}
        title="Search all monitored missing tracks and all files below their quality-profile cutoff"
        onClick={() => void runSearch()}
      >
        <SvgIcon name="automatic" />
        Automatic Search
      </button>
      {message ? (
        <span className={styles.automaticSearchMessage} role="status">
          {message}
        </span>
      ) : null}
    </span>
  );
}

/** What an empty library actually means right now.
 *
 * On an installation that just upgraded, "empty" almost always means "the
 * migration has not reached this table yet" — telling that user to press
 * Import would be wrong (the button is refused while the migration holds the
 * lock) and alarming. Only a library that genuinely has nothing to migrate
 * gets the call to action.
 */
export function LibraryEmptyState({ pollIntervalMs = 1000 }: { pollIntervalMs?: number }) {
  const importQuery = useQuery(libraryV2ImportStatusQueryOptions(pollIntervalMs));
  const bootstrap = importQuery.data?.bootstrap;
  const migrating = bootstrap?.status === 'running';
  const failed = bootstrap?.status === 'failed';

  return (
    <div className={styles.emptyState}>
      <h2>
        {migrating
          ? 'Migrating your library…'
          : failed
            ? 'Your library could not be migrated'
            : 'Your library is empty'}
      </h2>
      <p>
        {migrating
          ? 'This runs by itself and continues where it left off after a restart. ' +
            'Artists appear here as they are migrated.'
          : failed
            ? 'It retries on its own, and continues from where it stopped rather ' +
              'than starting over. You can also start it here.'
            : 'Import your existing library to populate the manager.'}
      </p>
      <ImportButton hasArtists={false} prominent />
    </div>
  );
}

export function ImportButton({
  hasArtists,
  prominent,
  pollIntervalMs = 1000,
}: {
  hasArtists: boolean;
  prominent?: boolean;
  pollIntervalMs?: number;
}) {
  const queryClient = useQueryClient();
  const canWrite = useLibraryV2CanWrite();
  const importQuery = useQuery(libraryV2ImportStatusQueryOptions(pollIntervalMs));
  const observedRunning = useRef(false);
  const [message, setMessage] = useState<string | null>(null);
  const startImport = useMutation({
    mutationFn: () => {
      if (!canWrite) throw new Error('Library changes require the admin profile');
      return startLibraryV2Import(false);
    },
    onMutate: () => setMessage(null),
    onSuccess: () => {
      observedRunning.current = true;
      return queryClient.invalidateQueries({
        queryKey: [...LIBRARY_V2_QUERY_KEY, 'import-status'],
      });
    },
    onError: (error) => setMessage(mutationErrorMessage(error, 'Import failed')),
  });

  const importState = importQuery.data;
  const running = importState?.running === true;
  const artworkRunning = importState?.artwork_cache.running === true;
  // The persisted migration may be running in a worker this browser session
  // never started, in which case pressing Import would only be refused.
  const migration = describeLibraryV2Migration(importState);
  const migrating = migration?.tone === 'busy';
  const busy = startImport.isPending || running || migrating;

  useEffect(() => {
    if (!importState) return;
    if (importState.running) {
      observedRunning.current = true;
      return;
    }
    if (!observedRunning.current) return;
    observedRunning.current = false;
    if (importState.error || importState.stage === 'failed') {
      setMessage(`Failed: ${importState.error || 'Import failed'}`);
      return;
    }
    setMessage(describeLibraryV2ImportCompletion(importState));
    void invalidateLibraryV2(queryClient);
  }, [importState, queryClient]);

  const statusMessage = running
    ? describeLibraryV2ImportProgress(importState)
    : startImport.isPending
      ? 'Starting import…'
      : migration
        ? migration.text
        : artworkRunning && importState
          ? `${message ? `${message} ` : ''}${describeLibraryV2ArtworkCacheProgress(importState)}`
          : importState?.artwork_cache.error
            ? `${message ? `${message} ` : 'Library ready to browse. '}Artwork caching failed; covers will load on demand.`
            : message;
  const progress =
    running && importState.total > 0
      ? clampPercent((importState.current / importState.total) * 100)
      : migrating && importState?.bootstrap && importState.bootstrap.total > 0
        ? clampPercent((importState.bootstrap.current / importState.bootstrap.total) * 100)
        : artworkRunning && importState.artwork_cache.total > 0
          ? clampPercent(
              (importState.artwork_cache.current / importState.artwork_cache.total) * 100,
            )
          : null;

  // Once lib2 contains artists it is the live catalogue, not a mirror that
  // needs an ongoing "Re-import" action. Keep this component mounted while an
  // import/migration is active so its progress remains visible, and keep the
  // empty-state trigger below for installations that genuinely have nothing.
  // Completion/artwork/error text may still be useful without presenting a
  // misleading action that starts the legacy bootstrap again.
  const hideImportAction = hasArtists && !running && !migrating && !startImport.isPending;
  const showStatusOnly = hideImportAction && Boolean(statusMessage);

  if (hideImportAction && !showStatusOnly) return null;

  return (
    <span className={styles.importWrap}>
      {!hideImportAction ? (
        <button
          type="button"
          className={prominent ? styles.btnPrimary : styles.btnGhost}
          data-requires-write=""
          disabled={busy || artworkRunning || !canWrite}
          title={migrating ? 'Your library is being migrated in the background' : undefined}
          onClick={() => {
            if (canWrite) startImport.mutate();
          }}
        >
          {migrating ? 'Migrating…' : busy ? 'Importing…' : 'Import library'}
        </button>
      ) : null}
      {statusMessage ? (
        <span className={styles.importStatus} role="status" aria-live="polite">
          <span className={styles.importMsg}>{statusMessage}</span>
          {progress !== null ? (
            <progress
              className={styles.importProgress}
              max={100}
              value={progress}
              aria-label={statusMessage}
            />
          ) : null}
          {(running || artworkRunning) && importQuery.isError ? (
            <span className={styles.importPollError}>Status unavailable; retrying…</span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}
