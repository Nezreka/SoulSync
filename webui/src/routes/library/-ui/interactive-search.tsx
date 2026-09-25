import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { DialogFrame, DialogHeader } from '@/components/dialog';

import type {
  LibraryV2QualityProfile,
  LibraryV2QueueStatusEntry,
  LibraryV2RankedTarget,
} from '../-library-v2.types';

import { bitrateKbps } from '../-bitrate';
import {
  fetchLibraryV2AlbumHistory,
  fetchLibraryV2QueueStatus,
  fetchLibraryV2TrackHistory,
  LIBRARY_V2_QUERY_KEY,
  libraryV2QualityProfilesQueryOptions,
  listSearchSources,
  rankSearchResultQuality,
  searchSources,
  startSourceDownload,
  type Lib2EntityRef,
  type LibraryV2HistoryEntry,
  type SourceSearchResult,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';

/** Extract the numeric quality facts a result exposes (source-aware: many
 *  sources only know some of these — missing facts must not fail a target). */
function resultFacts(r: SourceSearchResult): {
  fmt: string;
  kbps: number | null;
  sampleRate: number | null;
  bitDepth: number | null;
} {
  const fmt = ((r.result_type === 'album' ? r.dominant_quality : r.quality) ?? '').toLowerCase();
  const bitrate = r.bitrate ?? firstTrackNumber(r, 'bitrate');
  const kbps = bitrateKbps(bitrate, fmt);
  return {
    fmt,
    kbps,
    sampleRate: r.sample_rate ?? firstTrackNumber(r, 'sample_rate'),
    bitDepth: r.bit_depth ?? firstTrackNumber(r, 'bit_depth'),
  };
}

function cutoffIndex(profile: LibraryV2QualityProfile): number {
  if (profile.upgrade_policy === 'until_cutoff') return profile.upgrade_cutoff_index;
  if (profile.upgrade_policy === 'until_top') return 0;
  return Math.max(0, profile.ranked_targets.length - 1);
}

/** Deep-dive D3: results below the profile's cutoff never get grabbed
 *  automatically (Lidarr hides them by default too) — but a result with no
 *  judgeable quality facts stays visible either way, matching
 *  `profileTargetRank`'s "never falsely reject" rule. */
function meetsCutoffOnly(r: SourceSearchResult, profile: LibraryV2QualityProfile): boolean {
  if (profile.ranked_targets.length === 0) return true;
  const rank = profileTargetRank(r, profile.ranked_targets);
  return rank === null || rank <= cutoffIndex(profile);
}

/** Client-side PREVIEW of how the pipeline's quality check will see a result:
 *  the index of the best ranked target it plausibly satisfies, or null when
 *  the source doesn't expose enough facts to judge (never falsely reject).
 *  The authoritative check still runs in the import pipeline. */
function profileTargetRank(r: SourceSearchResult, targets: LibraryV2RankedTarget[]): number | null {
  if (targets.length === 0) return null;
  const { fmt, kbps, sampleRate, bitDepth } = resultFacts(r);
  if (!fmt) return null; // source exposes no quality info — don't judge
  for (let i = 0; i < targets.length; i += 1) {
    const t = targets[i];
    if (t.format && !fmt.includes(t.format.toLowerCase())) continue;
    // Only enforce numeric facts the result actually exposes.
    if (t.bit_depth && bitDepth !== null && bitDepth < t.bit_depth) continue;
    if (t.min_sample_rate && sampleRate !== null && sampleRate < t.min_sample_rate) continue;
    if (t.min_bitrate && kbps !== null && kbps < t.min_bitrate) continue;
    // Hi-res targets need positive evidence, not just absence of counter-evidence.
    if (t.bit_depth && t.bit_depth > 16 && bitDepth === null) continue;
    return i;
  }
  return targets.length; // judged, and no target matched
}

/** Lidarr-style release age: "3d", "8mo", "2.1y" — usenet retention at a glance. */
function ageText(publishDate?: string | null): string {
  if (!publishDate) return '—';
  const then = Date.parse(publishDate);
  if (Number.isNaN(then)) return '—';
  const days = Math.max(0, (Date.now() - then) / 86_400_000);
  if (days < 1) return '<1d';
  if (days < 60) return `${Math.round(days)}d`;
  if (days < 365) return `${Math.round(days / 30.4)}mo`;
  return `${(days / 365.25).toFixed(1)}y`;
}

function ageDays(publishDate?: string | null): number {
  if (!publishDate) return Number.POSITIVE_INFINITY;
  const then = Date.parse(publishDate);
  if (Number.isNaN(then)) return Number.POSITIVE_INFINITY;
  return (Date.now() - then) / 86_400_000;
}

type SortKey = 'source' | 'title' | 'quality' | 'size' | 'age' | 'availability' | 'grabs';

function sortValue(r: SourceSearchResult, key: SortKey): number | string {
  switch (key) {
    case 'source':
      return sourceLabel(r);
    case 'title':
      return resultTitle(r).toLowerCase();
    case 'quality':
      return rankSearchResultQuality(r);
    case 'size':
      return resultSize(r) ?? 0;
    case 'age':
      return ageDays(effMeta(r).publish_date);
    case 'availability': {
      const meta = effMeta(r);
      if (meta.seeders != null) return meta.seeders;
      return (r.free_upload_slots ?? 0) * 100 - (r.queue_length ?? 0);
    }
    case 'grabs':
      return effMeta(r).grabs ?? 0;
  }
}

function fmtBytes(n?: number | null): string {
  if (!n || n <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function baseName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function resultTitle(r: SourceSearchResult): string {
  const rawRelease = r.release_title ?? effMeta(r).release_title;
  if (rawRelease) return rawRelease;
  if (r.result_type === 'album') return r.album_title || baseName(r.album_path ?? '') || '—';
  return r.title ?? baseName(r.filename);
}

function resultSize(r: SourceSearchResult): number | null | undefined {
  return r.result_type === 'album' ? r.total_size : r.size;
}

function firstTrackNumber(
  r: SourceSearchResult,
  key: 'bit_depth' | 'sample_rate' | 'bitrate',
): number | null {
  for (const track of r.tracks ?? []) {
    const value = track[key];
    if (typeof value === 'number' && value > 0) return value;
  }
  return null;
}

function resultQuality(r: SourceSearchResult) {
  const fmt = ((r.result_type === 'album' ? r.dominant_quality : r.quality) ?? '').toUpperCase();
  const bitrate = r.bitrate ?? firstTrackNumber(r, 'bitrate');
  const rawSampleRate = r.sample_rate ?? firstTrackNumber(r, 'sample_rate');
  const rawBitDepth = r.bit_depth ?? firstTrackNumber(r, 'bit_depth');
  const kbps = bitrateKbps(bitrate, fmt);
  const bitDepth = rawBitDepth ? `${rawBitDepth} Bit` : null;
  const sampleRate = rawSampleRate
    ? `${Number((rawSampleRate / 1000).toFixed(rawSampleRate % 1000 === 0 ? 0 : 1))} kHz`
    : null;
  const resolution = [bitDepth, sampleRate].filter(Boolean).join(' ');

  if (!fmt && !resolution && !kbps) {
    return <span className={styles.qualityMissing}>—</span>;
  }

  return (
    <span className={styles.qualityDisplay}>
      {fmt && <span className={styles.qualityTag}>{fmt}</span>}
      {resolution && <span className={styles.qualityTag}>{resolution}</span>}
      {kbps && <span className={styles.qualityTag}>{kbps} kbps</span>}
    </span>
  );
}

function resultKey(r: SourceSearchResult): string {
  return `${r.username}::${r.result_type === 'album' ? (r.album_path ?? r.album_title) : r.filename}`;
}

const SOURCE_LABELS: Record<string, string> = {
  usenet: 'Usenet',
  torrent: 'Torrent',
  soulseek: 'Soulseek',
  hifi: 'HiFi',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  youtube: 'YouTube',
  deezer: 'Deezer',
  deezer_dl: 'Deezer',
  soundcloud: 'SoundCloud',
  amazon: 'Amazon',
  lidarr: 'Lidarr',
};

/** The download source (Soulseek / Usenet / HiFi / …) for the Source column. */
function sourceLabel(r: SourceSearchResult): string {
  const source = (r.source ?? r.username ?? '').toLowerCase();
  return SOURCE_LABELS[source] ?? 'Soulseek';
}

/** Coarse source family for badge coloring (usenet/torrent/streaming/p2p). */
function sourceTone(r: SourceSearchResult): 'usenet' | 'torrent' | 'stream' | 'p2p' {
  const source = (r.source ?? r.username ?? '').toLowerCase();
  if (source === 'usenet') return 'usenet';
  if (source === 'torrent') return 'torrent';
  if (source !== 'soulseek' && SOURCE_LABELS[source]) return 'stream';
  return 'p2p';
}

/** Source metadata (indexer/grabs) — album results carry it on their first track. */
function effMeta(r: SourceSearchResult): NonNullable<SourceSearchResult['_source_metadata']> {
  if (r._source_metadata) return r._source_metadata;
  const t0 = r.tracks?.[0] as
    | { _source_metadata?: SourceSearchResult['_source_metadata'] }
    | undefined;
  return t0?._source_metadata ?? {};
}

/** The peer (Soulseek) or indexer (Usenet) detail. */
function sourceDetail(r: SourceSearchResult): string {
  const meta = effMeta(r);
  if (meta.indexer) return meta.indexer;
  const source = (r.source ?? '').toLowerCase();
  return source === 'soulseek' || !source ? (r.username ?? '') : '';
}

function peerCell(r: SourceSearchResult): string {
  const source = (r.source ?? r.username ?? '').toLowerCase();
  const meta = effMeta(r);
  if (source === 'torrent') {
    if (meta.seeders == null) return '—';
    return meta.leechers != null
      ? `${meta.seeders} seeders · ${meta.leechers} leechers`
      : `${meta.seeders} seeders`;
  }
  if (source === 'usenet') return '—';
  if (source !== 'soulseek' && SOURCE_LABELS[source]) return 'instant';
  // Soulseek peer: free slots + queue length.
  const slots = r.free_upload_slots ?? 0;
  const queue = r.queue_length ?? 0;
  return queue ? `${slots} slots · ${queue} queued` : `${slots} slots`;
}

function grabsCell(r: SourceSearchResult): string {
  const grabs = effMeta(r).grabs;
  return grabs == null ? '—' : grabs.toLocaleString();
}

type GrabState =
  | 'pending'
  | 'searching'
  | 'queued'
  | 'downloading'
  | 'processing'
  | 'verifying'
  | 'done'
  | 'started'
  | 'error';

interface SourceSearchProgress {
  displayName: string;
  phase: 'searching' | 'complete' | 'error';
  resultCount: number;
  elapsedMs?: number;
  message?: string;
}

type GrabLiveStatus = { state: GrabState; progress?: number };
type SearchColumn = 'artist' | 'size' | 'age' | 'peers' | 'grabs';
type SearchColumnVisibility = Record<SearchColumn, boolean>;

const SEARCH_COLUMNS_KEY = 'soulsync.libraryV2.interactiveSearch.columns.v1';
const DEFAULT_SEARCH_COLUMNS: SearchColumnVisibility = {
  artist: true,
  size: true,
  age: true,
  peers: true,
  grabs: true,
};

function loadSearchColumns(): SearchColumnVisibility {
  try {
    const stored = JSON.parse(
      localStorage.getItem(SEARCH_COLUMNS_KEY) ?? '{}',
    ) as Partial<SearchColumnVisibility>;
    return { ...DEFAULT_SEARCH_COLUMNS, ...stored };
  } catch {
    return DEFAULT_SEARCH_COLUMNS;
  }
}

function firstQueueEntry(entries: Record<number, LibraryV2QueueStatusEntry>) {
  return Object.values(entries)[0];
}

function queueGrabStatus(entry: LibraryV2QueueStatusEntry): GrabLiveStatus {
  return { state: entry.status, progress: entry.progress_pct };
}

function grabLabel(status?: GrabLiveStatus): string {
  if (!status) return 'Download';
  if (status.state === 'downloading') return `Downloading ${status.progress ?? 0}%`;
  return {
    pending: 'Starting…',
    searching: 'Searching…',
    queued: 'Queued',
    processing: 'Processing…',
    verifying: 'Verifying…',
    done: 'Imported ✓',
    started: 'Started ✓',
    error: 'Retry',
  }[status.state];
}

/** dd28-06: one source that failed while others succeeded. Kept separately
 *  from `error` because it must not replace the results that DID arrive. */
interface SourceFailure {
  displayName: string;
  message: string;
}

/** A grab only dispatches a download — the real outcome (quarantined,
 *  imported, still running) lands later via the async import pipeline. The
 *  user's ask: if it still gets quarantined despite Quality/AcoustID check
 *  being off, say so right in this window instead of silently leaving
 *  "Grabbed ✓" up when the file never actually made it into the library. */
export type GrabOutcome =
  | { status: 'failed'; message: string }
  | { status: 'imported' }
  | { status: 'pending' };

/** Pure classifier over the entity's merged pipeline history (already used
 *  by the History tab) — no polling/timing concerns, so it's cheap to unit
 *  test exhaustively. `sinceMs` filters to events from THIS grab, not an
 *  earlier one for the same track/album (a 10s slack absorbs client/server
 *  clock skew and request latency). */
export function classifyGrabOutcome(
  history: LibraryV2HistoryEntry[],
  sinceMs: number,
): GrabOutcome {
  const fresh = history.filter((e) => {
    const t = e.date ? Date.parse(e.date) : NaN;
    return Number.isFinite(t) && t >= sinceMs - 10_000;
  });
  const failure = fresh.find((e) => e.category === 'quarantined' || e.category === 'failed');
  if (failure) {
    const message = failure.detail
      ? `${failure.title ?? 'Failed'}: ${failure.detail}`
      : (failure.title ?? 'Download failed');
    return { status: 'failed', message };
  }
  if (fresh.some((e) => e.category === 'imported')) return { status: 'imported' };
  return { status: 'pending' };
}

const GRAB_OUTCOME_POLL_INTERVAL_MS = 2_000;

export function sortSourceSearchResults(
  results: SourceSearchResult[],
  key: SortKey,
  direction: 1 | -1,
): SourceSearchResult[] {
  const copy = [...results];
  copy.sort((a, b) => {
    const va = sortValue(a, key);
    const vb = sortValue(b, key);
    let cmp: number;
    if (key === 'age') {
      const aUnknown = !Number.isFinite(va);
      const bUnknown = !Number.isFinite(vb);
      if (aUnknown !== bUnknown) return aUnknown ? 1 : -1;
      cmp = aUnknown ? 0 : Number(va) - Number(vb);
    } else {
      cmp =
        typeof va === 'string' || typeof vb === 'string'
          ? String(va).localeCompare(String(vb))
          : va - vb;
    }
    if (cmp !== 0) return cmp * direction;
    // Stable tiebreak: better quality first, then larger size.
    return (
      rankSearchResultQuality(b) - rankSearchResultQuality(a) ||
      (resultSize(b) ?? 0) - (resultSize(a) ?? 0)
    );
  });
  return copy;
}

/** Preview badge: how this result measures against the entity's quality
 *  profile. Informative only — the pipeline runs the authoritative check. */
function ProfileBadge({
  result,
  profile,
}: {
  result: SourceSearchResult;
  profile?: LibraryV2QualityProfile | null;
}) {
  if (!profile || profile.ranked_targets.length === 0) return null;
  const rank = profileTargetRank(result, profile.ranked_targets);
  if (rank === null) return null; // source exposes no judgeable quality info
  const targets = profile.ranked_targets;
  if (rank >= targets.length) {
    return (
      <span
        className={styles.qBelow}
        title={`Matches none of "${profile.name}"'s targets — the pipeline will likely reject or quarantine it`}
      >
        below profile
      </span>
    );
  }
  const cutoff = cutoffIndex(profile);
  const label = targets[rank]?.label ?? `target #${rank + 1}`;
  if (rank <= cutoff) {
    return (
      <span className={styles.qMeets} title={`Matches "${label}" — satisfies the profile's cutoff`}>
        meets cutoff
      </span>
    );
  }
  return (
    <span
      className={styles.qAcceptable}
      title={`Matches "${label}" — acceptable, but below the upgrade cutoff`}
    >
      acceptable
    </span>
  );
}

/** Lidarr-style interactive search: search every configured SoulSync source for a
 *  release, pick one, and send it through the download pipeline. When the
 *  target entity's quality profile is provided, each result gets a preview
 *  badge of how it measures against the profile's ranked targets. */
export function InteractiveSearchModal({
  initialQuery,
  qualityProfile,
  entity,
  canWrite,
  onClose,
}: {
  initialQuery: string;
  /** The artist's profile — fallback when the action has no album context. */
  qualityProfile?: LibraryV2QualityProfile | null;
  /** Which lib2 entity grabs from this window act for. Sent with every grab
   *  so the pipeline keeps entity + profile context (audit P1-16). */
  entity?: Lib2EntityRef;
  canWrite: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState(initialQuery);
  // An empty set means the explicit "All sources" choice. Once a user clicks
  // a source chip, the set becomes the exact subset to search. This makes a
  // click on "Usenet" select Usenet instead of silently excluding it
  // (iss27-12).
  const [selectedSources, setSelectedSources] = useState<Set<string>>(new Set());
  // Album/track actions use the ALBUM's own profile for the preview badge,
  // not the artist's (audit P1-17). The authoritative profile is resolved
  // server-side from the entity ids on grab either way.
  const profilesQuery = useQuery({
    ...libraryV2QualityProfilesQueryOptions(),
    enabled: entity?.qualityProfileId != null,
  });
  const searchSourcesQuery = useQuery({
    queryKey: ['library-v2', 'download-search-sources'],
    queryFn: listSearchSources,
    staleTime: 60_000,
  });
  const effectiveProfile =
    entity?.qualityProfileId != null
      ? (profilesQuery.data?.find((p) => p.id === entity.qualityProfileId) ?? qualityProfile)
      : qualityProfile;
  const [results, setResults] = useState<SourceSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // dd28-06: per-source failures that did NOT blank the whole result set.
  const [sourceFailures, setSourceFailures] = useState<SourceFailure[]>([]);
  const [sourceProgress, setSourceProgress] = useState<Record<string, SourceSearchProgress>>({});
  const [grabbed, setGrabbed] = useState<Record<string, GrabLiveStatus>>({});
  const [grabErrors, setGrabErrors] = useState<Record<string, string>>({});
  const [qualityCheck, setQualityCheck] = useState(true);
  const [acoustidCheck, setAcoustidCheck] = useState(true);
  const [cutoffOnly, setCutoffOnly] = useState(false);
  const [columns, setColumns] = useState<SearchColumnVisibility>(loadSearchColumns);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'quality', dir: -1 });
  // Stops in-flight outcome polls from touching state after the modal closes
  // (the user is done watching, and the component is about to unmount).
  const cancelledRef = useRef(false);
  const runSequenceRef = useRef(0);
  const searchInputRef = useRef<HTMLInputElement>(null);
  useEffect(
    () => () => {
      cancelledRef.current = true;
    },
    [],
  );

  const sorted = useMemo(
    () => sortSourceSearchResults(results, sort.key, sort.dir),
    [results, sort],
  );
  const canFilterByCutoff = !!effectiveProfile && effectiveProfile.ranked_targets.length > 0;
  const filtered = useMemo(
    () =>
      cutoffOnly && effectiveProfile
        ? sorted.filter((r) => meetsCutoffOnly(r, effectiveProfile))
        : sorted,
    [sorted, cutoffOnly, effectiveProfile],
  );
  const progressItems = useMemo(() => Object.entries(sourceProgress), [sourceProgress]);
  const showPeers =
    columns.peers &&
    filtered.some((r) => {
      const source = (r.source ?? r.username ?? '').toLowerCase();
      return source === 'torrent' || source === 'soulseek' || !SOURCE_LABELS[source];
    });
  const showGrabs = columns.grabs && filtered.some((r) => effMeta(r).grabs != null);

  useEffect(() => {
    try {
      localStorage.setItem(SEARCH_COLUMNS_KEY, JSON.stringify(columns));
    } catch {
      // Ignore unavailable preference storage.
    }
  }, [columns]);

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: -1 }));
  }

  function SortTh({ label, k, className }: { label: string; k: SortKey; className?: string }) {
    const active = sort.key === k;
    return (
      <th
        className={`${className ?? ''} ${styles.sortableTh}`}
        aria-sort={active ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}
        onClick={() => toggleSort(k)}
      >
        {label}
        {active ? <span className={styles.sortArrow}>{sort.dir === 1 ? '▲' : '▼'}</span> : null}
      </th>
    );
  }

  const allSources = searchSourcesQuery.data?.sources ?? [];
  const activeSources =
    selectedSources.size === 0 ? allSources : allSources.filter((s) => selectedSources.has(s.name));

  /** Select an exact source subset. Selecting every source normalizes back to
   *  "All sources"; removing the last explicit source does the same. */
  function toggleSource(name: string) {
    setSelectedSources((previous) => {
      if (previous.size === 0) return new Set([name]);
      const next = new Set(previous);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next.size === 0 || next.size === allSources.length ? new Set() : next;
    });
  }

  /** iss27-01: with every source active (the default) and more than one
   *  source configured, search every enabled source in parallel and merge
   *  the results — a single source (or the orchestrator's single-source
   *  mode) no longer silently stands in for "all sources". One source
   *  failing (timeout, disconnected) doesn't blank the whole result set.
   *  Narrowing the chip row to a subset searches exactly that subset. */
  async function run(q: string) {
    const runSequence = ++runSequenceRef.current;
    // dd28-36: bailing out silently made an empty query look like a completed
    // search with no hits. Say what actually happened so the user can type one.
    if (!q.trim()) {
      setResults([]);
      setSourceFailures([]);
      setSourceProgress({});
      setLoading(false);
      setError('Enter something to search for — this entity has no usable title.');
      return;
    }
    const sources = [...activeSources];
    const targets =
      sources.length > 0 ? sources : [{ name: '__current__', display_name: 'Current source' }];
    setLoading(true);
    setError(null);
    setResults([]);
    setSourceFailures([]);
    setSourceProgress(
      Object.fromEntries(
        targets.map((source) => [
          source.name,
          {
            displayName: source.display_name,
            phase: 'searching' as const,
            resultCount: 0,
          },
        ]),
      ),
    );

    const merged: SourceSearchResult[] = [];
    const failed: SourceFailure[] = [];
    await Promise.all(
      targets.map(async (source) => {
        const sourceStartedAt = Date.now();
        try {
          const sourceResults = await searchSources(
            q,
            source.name === '__current__' ? undefined : source.name,
            entity,
          );
          merged.push(...sourceResults);
          if (runSequence !== runSequenceRef.current) return;
          if (sourceResults.length > 0) {
            setResults((current) => [...current, ...sourceResults]);
          }
          setSourceProgress((current) => ({
            ...current,
            [source.name]: {
              displayName: source.display_name,
              phase: 'complete',
              resultCount: sourceResults.length,
              elapsedMs: Date.now() - sourceStartedAt,
            },
          }));
        } catch (caught) {
          const message =
            caught instanceof Error && caught.name === 'TimeoutError'
              ? 'timed out'
              : caught instanceof Error
                ? caught.message
                : 'failed';
          failed.push({
            displayName: source.display_name,
            message,
          });
          if (runSequence !== runSequenceRef.current) return;
          setSourceProgress((current) => ({
            ...current,
            [source.name]: {
              displayName: source.display_name,
              phase: 'error',
              resultCount: 0,
              elapsedMs: Date.now() - sourceStartedAt,
              message,
            },
          }));
        }
      }),
    );

    if (runSequence !== runSequenceRef.current) return;
    setSourceFailures(failed);
    if (merged.length === 0 && failed.length > 0) {
      setError(`Search failed for ${failed.map((failure) => failure.displayName).join(', ')}`);
    }
    setLoading(false);
  }

  // Auto-run once with the prefilled context query — deferred until the
  // source list has settled so the very first run already benefits from
  // the all-sources fan-out above instead of racing it.
  const autoRanRef = useRef(false);
  useEffect(() => {
    if (autoRanRef.current || searchSourcesQuery.isLoading) return;
    autoRanRef.current = true;
    void run(initialQuery);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchSourcesQuery.isLoading]);

  async function grab(r: SourceSearchResult) {
    if (!canWrite || (entity?.trackId && r.result_type === 'album')) return;
    // §52.12.4: candidates outside the quality profile are shown (ProfileBadge)
    // but only downloadable via a separate, explicitly confirmed Force action
    // when the user has turned the Quality check off — the pipeline audits the
    // override (core/library2/manual_skips.py) once dispatched.
    if (!qualityCheck && effectiveProfile && effectiveProfile.ranked_targets.length > 0) {
      const rank = profileTargetRank(r, effectiveProfile.ranked_targets);
      const belowProfile = rank === effectiveProfile.ranked_targets.length;
      if (belowProfile) {
        const confirmed = window.confirm(
          `"${resultTitle(r)}" is below "${effectiveProfile.name}"'s quality profile. ` +
            'Quality check is off, so this will force-download it anyway. This override ' +
            'is recorded. Continue?',
        );
        if (!confirmed) return;
      }
    }
    // Must match the key the row renders with (resultKey) — album results
    // have no filename, so a filename-based key would never update the button.
    const key = resultKey(r);
    setGrabbed((g) => ({ ...g, [key]: { state: 'pending' } }));
    setGrabErrors((errors) => {
      const next = { ...errors };
      delete next[key];
      return next;
    });
    const sinceMs = Date.now();
    try {
      await startSourceDownload(r, { qualityCheck, skipAcoustid: !acoustidCheck }, entity);
      // Dispatch succeeded — that only means the download STARTED. Only a
      // grab naming a library entity can be watched through to its real
      // outcome (quarantine, import) via that entity's pipeline history.
      if (entity?.trackId || entity?.albumId) {
        setGrabbed((g) => ({ ...g, [key]: { state: 'verifying' } }));
        void watchGrabOutcome(entity, sinceMs, key);
      } else {
        setGrabbed((g) => ({ ...g, [key]: { state: 'started' } }));
      }
    } catch (caught) {
      setGrabbed((g) => ({ ...g, [key]: { state: 'error' } }));
      setGrabErrors((errors) => ({
        ...errors,
        [key]:
          caught instanceof Error && caught.message.trim() ? caught.message : 'Download failed',
      }));
    }
  }

  /** Polls the grabbed entity's merged pipeline history until a terminal
   *  outcome shows up or the modal closes — surfaces a quarantine/
   *  failure right in this modal instead of leaving a stale "Grabbed ✓" up
   *  when the file never actually made it into the library. */
  async function watchGrabOutcome(entity: Lib2EntityRef, sinceMs: number, key: string) {
    const fetchHistory = entity.trackId
      ? () => fetchLibraryV2TrackHistory(entity.trackId!)
      : entity.albumId
        ? () => fetchLibraryV2AlbumHistory(entity.albumId!)
        : null;
    if (!fetchHistory) return;
    while (!cancelledRef.current) {
      await new Promise((resolve) => setTimeout(resolve, GRAB_OUTCOME_POLL_INTERVAL_MS));
      if (cancelledRef.current) return;
      let history: LibraryV2HistoryEntry[] = [];
      try {
        const queueScope = entity.trackId
          ? { kind: 'tracks' as const, id: entity.trackId }
          : { kind: 'albums' as const, id: entity.albumId! };
        const [historyResult, queueResult] = await Promise.allSettled([
          fetchHistory(),
          fetchLibraryV2QueueStatus(queueScope.kind, queueScope.id),
        ]);
        if (historyResult.status === 'fulfilled') history = historyResult.value;
        if (queueResult.status === 'fulfilled') {
          const queueEntry = entity.trackId
            ? queueResult.value.tracks[entity.trackId]
            : firstQueueEntry(queueResult.value.tracks);
          setGrabbed((current) => ({
            ...current,
            [key]: queueEntry ? queueGrabStatus(queueEntry) : { state: 'verifying' },
          }));
        }
      } catch {
        continue; // transient — history is a best-effort signal, keep trying
      }
      if (cancelledRef.current) return;
      const outcome = classifyGrabOutcome(history, sinceMs);
      if (outcome.status === 'failed') {
        setGrabbed((g) => ({ ...g, [key]: { state: 'error' } }));
        setGrabErrors((errors) => ({ ...errors, [key]: outcome.message }));
        return;
      }
      if (outcome.status === 'imported') {
        setGrabbed((g) => ({ ...g, [key]: { state: 'done', progress: 100 } }));
        // Autolink has committed the new file before the imported history
        // event is written. Refresh active album/artist queries immediately;
        // otherwise the table can keep showing "Missing" until a manual
        // Refresh & Scan even though the DB row already exists (§35).
        void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
        return;
      }
    }
  }

  return (
    <DialogFrame
      open
      initialFocus={searchInputRef}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      className={`${styles.modal} ${styles.modalWide} ${styles.modalFramed}`}
    >
      <DialogHeader title="Interactive Search" closeLabel="Close interactive search" compact>
        <span className={styles.searchHeaderMeta}>
          Search sources and compare results before downloading.
        </span>
      </DialogHeader>
      <div className={styles.searchModalBody}>
        <div className={styles.searchBar}>
          <input
            ref={searchInputRef}
            className={styles.searchInput}
            aria-label="Search query"
            value={query}
            placeholder="Search query…"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void run(query);
            }}
          />
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={loading}
            onClick={() => void run(query)}
          >
            {loading ? 'Searching…' : 'Search'}
          </button>
        </div>

        {allSources.length > 1 ? (
          <div className={styles.sourceChips} role="group" aria-label="Download sources">
            <button
              type="button"
              className={styles.sourceChip}
              aria-pressed={selectedSources.size === 0}
              disabled={loading || searchSourcesQuery.isLoading}
              onClick={() => setSelectedSources(new Set())}
            >
              All sources
            </button>
            {allSources.map((source) => (
              <button
                key={source.name}
                type="button"
                className={styles.sourceChip}
                aria-pressed={selectedSources.has(source.name)}
                disabled={loading || searchSourcesQuery.isLoading}
                onClick={() => toggleSource(source.name)}
              >
                {source.display_name}
              </button>
            ))}
          </div>
        ) : null}

        {progressItems.length > 0 ? (
          <div
            className={styles.sourceProgressGrid}
            aria-label="Search client status"
            aria-live="polite"
          >
            {progressItems.map(([name, item]) => (
              <div key={name} className={styles.sourceProgressItem} data-phase={item.phase}>
                <span className={styles.sourceProgressDot} aria-hidden="true" />
                <span className={styles.sourceProgressName}>{item.displayName}</span>
                <span className={styles.sourceProgressState}>
                  {item.phase === 'searching'
                    ? 'Searching…'
                    : item.phase === 'error'
                      ? `Failed · ${item.message}`
                      : `Finished · ${item.resultCount} result${item.resultCount === 1 ? '' : 's'} · ${((item.elapsedMs ?? 0) / 1000).toFixed(1)}s`}
                </span>
              </div>
            ))}
          </div>
        ) : null}

        <div className={styles.searchOptions}>
          <label className={styles.toggleOption}>
            <input
              type="checkbox"
              className={styles.toggleInput}
              checked={qualityCheck}
              onChange={(e) => setQualityCheck(e.target.checked)}
            />
            <span className={styles.toggleSwitch} aria-hidden="true" />
            Quality check
          </label>
          <label className={styles.toggleOption}>
            <input
              type="checkbox"
              className={styles.toggleInput}
              checked={acoustidCheck}
              onChange={(e) => setAcoustidCheck(e.target.checked)}
            />
            <span className={styles.toggleSwitch} aria-hidden="true" />
            AcoustID check
          </label>
          <span className={styles.optionHint}>applied to grabs from this window</span>
          {canFilterByCutoff ? (
            <label className={styles.toggleOption}>
              <input
                type="checkbox"
                className={styles.toggleInput}
                checked={cutoffOnly}
                onChange={(e) => setCutoffOnly(e.target.checked)}
              />
              <span className={styles.toggleSwitch} aria-hidden="true" />
              Only show results meeting cutoff
            </label>
          ) : null}
        </div>

        {error ? <div className={styles.searchError}>{error}</div> : null}
        {!error && sourceFailures.length > 0 ? (
          <div className={styles.searchWarning} role="status">
            {sourceFailures.length === 1
              ? `${sourceFailures[0].displayName} could not be searched (${sourceFailures[0].message}) — these results are from the other sources only.`
              : `${sourceFailures.map((f) => `${f.displayName} (${f.message})`).join(', ')} could not be searched — these results are from the other sources only.`}
          </div>
        ) : null}

        <div className={styles.searchResultsToolbar}>
          <span>
            {filtered.length} of {results.length} results
          </span>
          <label className={styles.searchCompactSort}>
            Sort
            <select
              className={styles.select}
              aria-label="Sort search results"
              value={sort.key}
              onChange={(e) => setSort({ key: e.target.value as SortKey, dir: -1 })}
            >
              <option value="quality">Quality</option>
              <option value="title">Release</option>
              <option value="source">Source</option>
              <option value="size">Size</option>
              <option value="age">Age</option>
              <option value="availability">Peers</option>
              <option value="grabs">Grabs</option>
            </select>
            <button
              type="button"
              className={styles.toolButton}
              aria-label="Reverse search sort"
              onClick={() => setSort((s) => ({ ...s, dir: s.dir === 1 ? -1 : 1 }))}
            >
              {sort.dir === 1 ? '↑' : '↓'}
            </button>
          </label>
          <details className={styles.columnPicker}>
            <summary>Columns</summary>
            <div className={styles.columnMenu}>
              {(
                [
                  ['artist', 'Artist'],
                  ['size', 'Size'],
                  ['age', 'Age'],
                  ['peers', 'Peers / seeders'],
                  ['grabs', 'Grabs'],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={columns[key]}
                    onChange={(e) =>
                      setColumns((current) => ({ ...current, [key]: e.target.checked }))
                    }
                  />
                  {label}
                </label>
              ))}
            </div>
          </details>
        </div>

        <div className={styles.resultsWrap}>
          {loading && results.length === 0 ? (
            <div className={styles.inlineLoading}>
              {activeSources.length === 1
                ? `Searching ${activeSources[0].display_name}…`
                : activeSources.length > 1
                  ? `Searching ${activeSources.map((s) => s.display_name).join(', ')}…`
                  : 'Searching…'}
            </div>
          ) : results.length === 0 ? (
            <div className={styles.inlineLoading}>
              {error ? 'Search failed.' : 'No results — refine the query and search again.'}
            </div>
          ) : filtered.length === 0 ? (
            <div className={styles.inlineLoading}>
              No results meet{' '}
              {effectiveProfile?.name ? `"${effectiveProfile.name}"'s` : "the profile's"} cutoff —
              turn off the filter to see them all.
            </div>
          ) : (
            <>
              {loading ? (
                <div className={styles.inlineLoading} role="status">
                  Showing available results while the remaining sources are still searching…
                </div>
              ) : null}
              <table className={`${styles.trackTable} ${styles.searchResultsTable}`}>
                <thead>
                  <tr>
                    <SortTh label="Source" k="source" className={styles.isSource} />
                    <SortTh label="Release" k="title" />
                    {columns.artist ? <th className={styles.isArtist}>Artist</th> : null}
                    <SortTh label="Quality" k="quality" className={styles.isQuality} />
                    {columns.size ? (
                      <SortTh label="Size" k="size" className={styles.colNum} />
                    ) : null}
                    {columns.age ? <SortTh label="Age" k="age" className={styles.colNum} /> : null}
                    {showPeers ? (
                      <SortTh label="Peers" k="availability" className={styles.isAvail} />
                    ) : null}
                    {showGrabs ? (
                      <SortTh label="Grabs" k="grabs" className={styles.isGrabs} />
                    ) : null}
                    <th className={styles.isGrab}>Download</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r, i) => {
                    const key = resultKey(r);
                    const live = grabbed[key];
                    const isAlbum = r.result_type === 'album';
                    const wrongScope = Boolean(entity?.trackId && isAlbum);
                    return (
                      <tr key={`${key}-${i}`}>
                        <td>
                          <span className={styles.sourceBadge} data-tone={sourceTone(r)}>
                            {sourceLabel(r)}
                          </span>
                          {sourceDetail(r) ? (
                            <span className={styles.sourceDetail}>{sourceDetail(r)}</span>
                          ) : null}
                        </td>
                        <td title={resultTitle(r)}>
                          <span className={styles.isTitle}>{resultTitle(r)}</span>
                          {r.matched_track_title || r.matched_album_title ? (
                            <span className={styles.releaseMatch}>
                              Matched to {r.matched_track_title ?? r.matched_album_title}
                            </span>
                          ) : null}
                          {isAlbum ? (
                            <span className={styles.albumResultBadge}>
                              album · {r.track_count ?? r.tracks?.length ?? '?'} tracks
                            </span>
                          ) : null}
                        </td>
                        {columns.artist ? <td data-label="Artist">{r.artist ?? '—'}</td> : null}
                        <td data-label="Quality" className={styles.qualityText}>
                          <span className={styles.qualityCellRow}>
                            {resultQuality(r)}
                            <ProfileBadge result={r} profile={effectiveProfile} />
                          </span>
                        </td>
                        {columns.size ? (
                          <td data-label="Size" className={styles.colNum}>
                            {fmtBytes(resultSize(r))}
                          </td>
                        ) : null}
                        {columns.age ? (
                          <td
                            data-label="Age"
                            className={styles.colNum}
                            title={effMeta(r).publish_date ?? undefined}
                          >
                            {ageText(effMeta(r).publish_date)}
                          </td>
                        ) : null}
                        {showPeers ? (
                          <td data-label="Peers" className={styles.isAvailCell}>
                            {peerCell(r)}
                          </td>
                        ) : null}
                        {showGrabs ? (
                          <td data-label="Grabs" className={styles.isAvailCell}>
                            {grabsCell(r)}
                          </td>
                        ) : null}
                        <td>
                          <span className={styles.grabAction}>
                            <button
                              type="button"
                              className={styles.toolButton}
                              data-requires-write=""
                              title={
                                wrongScope
                                  ? 'An album result cannot be attached to one library track'
                                  : undefined
                              }
                              disabled={
                                !canWrite || wrongScope || Boolean(live && live.state !== 'error')
                              }
                              onClick={() => void grab(r)}
                            >
                              {wrongScope ? 'Album result' : grabLabel(live)}
                            </button>
                            {live?.state === 'downloading' ? (
                              <span className={styles.grabProgress} aria-hidden="true">
                                <span style={{ width: `${live.progress ?? 0}%` }} />
                              </span>
                            ) : null}
                            {live?.state === 'error' ? (
                              <span className={styles.grabError} role="alert">
                                {grabErrors[key] ?? 'Download failed'}
                              </span>
                            ) : null}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </div>

        <div className={styles.modalFootNote}>
          Downloads appear in your library automatically after processing.
        </div>
      </div>
    </DialogFrame>
  );
}
