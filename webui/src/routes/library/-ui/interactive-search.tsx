import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { DialogFrame, DialogHeader } from '@/components/dialog';

import type { LibraryV2QualityProfile, LibraryV2RankedTarget } from '../-library-v2.types';

import {
  fetchLibraryV2AlbumHistory,
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
  const kbps = bitrate ? (bitrate > 5000 ? Math.round(bitrate / 1000) : bitrate) : null;
  return {
    fmt,
    kbps,
    sampleRate: r.sample_rate ?? firstTrackNumber(r, 'sample_rate'),
    bitDepth: r.bit_depth ?? firstTrackNumber(r, 'bit_depth'),
  };
}

function cutoffIndex(profile: LibraryV2QualityProfile): number {
  return profile.upgrade_policy === 'until_cutoff' ? profile.upgrade_cutoff_index : 0;
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

type SortKey = 'source' | 'title' | 'quality' | 'size' | 'age' | 'availability';

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
      if (meta.grabs != null) return meta.grabs;
      if (meta.seeders != null) return meta.seeders;
      return (r.free_upload_slots ?? 0) * 100 - (r.queue_length ?? 0);
    }
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
  const kbps = bitrate ? (bitrate > 5000 ? Math.round(bitrate / 1000) : bitrate) : null;
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
  hifi: 'HiFi',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  youtube: 'YouTube',
  deezer_dl: 'Deezer',
  soundcloud: 'SoundCloud',
  amazon: 'Amazon',
  lidarr: 'Lidarr',
};

/** The download source (Soulseek / Usenet / HiFi / …) for the Source column. */
function sourceLabel(r: SourceSearchResult): string {
  const u = (r.username ?? '').toLowerCase();
  return SOURCE_LABELS[u] ?? 'Soulseek';
}

/** Coarse source family for badge coloring (usenet/torrent/streaming/p2p). */
function sourceTone(r: SourceSearchResult): 'usenet' | 'torrent' | 'stream' | 'p2p' {
  const u = (r.username ?? '').toLowerCase();
  if (u === 'usenet') return 'usenet';
  if (u === 'torrent') return 'torrent';
  return SOURCE_LABELS[u] ? 'stream' : 'p2p';
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
  const u = (r.username ?? '').toLowerCase();
  return SOURCE_LABELS[u] ? '' : (r.username ?? '');
}

/** Source-appropriate availability metric (peers don't apply to Usenet, grabs
 *  don't apply to Soulseek), so each source shows what's meaningful for it. */
function availabilityCell(r: SourceSearchResult): string {
  const u = (r.username ?? '').toLowerCase();
  const meta = effMeta(r);
  if (u === 'usenet') {
    const parts: string[] = [];
    if (meta.grabs != null) parts.push(`${meta.grabs} grabs`);
    if (meta.seeders != null) parts.push(`${meta.seeders} seeders`);
    return parts.join(' · ') || '—';
  }
  if (SOURCE_LABELS[u]) return 'instant'; // streaming sources (HiFi/Tidal/…)
  // Soulseek peer: free slots + queue length.
  const slots = r.free_upload_slots ?? 0;
  const queue = r.queue_length ?? 0;
  return queue ? `${slots} slots · ${queue} queued` : `${slots} slots`;
}

type GrabState = 'pending' | 'verifying' | 'done' | 'error';

/** dd28-06: one source that failed while others succeeded. Kept separately
 *  from `error` because it must not replace the results that DID arrive. */
interface SourceFailure {
  name: string;
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

const GRAB_OUTCOME_POLL_INTERVAL_MS = 4_000;
const GRAB_OUTCOME_MAX_POLLS = 30; // ~2 minutes — a best-effort window, not a guarantee

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
  const [grabbed, setGrabbed] = useState<Record<string, GrabState>>({});
  const [grabErrors, setGrabErrors] = useState<Record<string, string>>({});
  const [qualityCheck, setQualityCheck] = useState(true);
  const [acoustidCheck, setAcoustidCheck] = useState(true);
  const [cutoffOnly, setCutoffOnly] = useState(false);
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
      setLoading(false);
      setError('Enter something to search for — this entity has no usable title.');
      return;
    }
    const sources = [...activeSources];
    setLoading(true);
    setError(null);
    setResults([]);
    setSourceFailures([]);
    try {
      if (sources.length <= 1) {
        const all = await searchSources(q, sources[0]?.name, entity);
        if (runSequence === runSequenceRef.current) setResults(all);
        return;
      }

      // Render each source as soon as it answers. A slow/disconnected source
      // must not hide fast Usenet/Soulseek results for its full 90s timeout
      // (iss27-14).
      const merged: SourceSearchResult[] = [];
      const failed: SourceFailure[] = [];
      await Promise.all(
        sources.map(async (source) => {
          try {
            const sourceResults = await searchSources(q, source.name, entity);
            merged.push(...sourceResults);
            if (runSequence === runSequenceRef.current && sourceResults.length > 0) {
              setResults((current) => [...current, ...sourceResults]);
            }
          } catch (caught) {
            failed.push({
              name: source.name,
              displayName: source.display_name,
              message:
                caught instanceof Error && caught.name === 'TimeoutError'
                  ? 'timed out'
                  : caught instanceof Error
                    ? caught.message
                    : 'failed',
            });
          }
        }),
      );
      // dd28-06: a per-source failure used to be reported only when EVERY
      // source came back empty. With Soulseek answering, a Usenet timeout or
      // 500 was invisible — so "Usenet never finds anything" looked like a
      // property of Usenet rather than a broken call. Surface the failures
      // regardless; they are a warning next to the results, not an error
      // replacing them.
      if (runSequence === runSequenceRef.current) setSourceFailures(failed);
      if (merged.length === 0 && failed.length > 0) {
        throw new Error(`Search failed for ${failed.map((f) => f.displayName).join(', ')}`);
      }
    } catch (e) {
      if (runSequence === runSequenceRef.current) {
        setError(e instanceof Error ? e.message : 'Search failed');
      }
    } finally {
      if (runSequence === runSequenceRef.current) setLoading(false);
    }
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
    setGrabbed((g) => ({ ...g, [key]: 'pending' }));
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
      // outcome (quarantine, import) via that entity's pipeline history;
      // anything else has nothing to poll and is "done" once dispatched.
      if (entity?.trackId || entity?.albumId) {
        setGrabbed((g) => ({ ...g, [key]: 'verifying' }));
        void watchGrabOutcome(entity, sinceMs, key);
      } else {
        setGrabbed((g) => ({ ...g, [key]: 'done' }));
      }
    } catch (caught) {
      setGrabbed((g) => ({ ...g, [key]: 'error' }));
      setGrabErrors((errors) => ({
        ...errors,
        [key]:
          caught instanceof Error && caught.message.trim() ? caught.message : 'Download failed',
      }));
    }
  }

  /** Polls the grabbed entity's merged pipeline history until a terminal
   *  outcome shows up (or the window times out) — surfaces a quarantine/
   *  failure right in this modal instead of leaving a stale "Grabbed ✓" up
   *  when the file never actually made it into the library. */
  async function watchGrabOutcome(entity: Lib2EntityRef, sinceMs: number, key: string) {
    const fetchHistory = entity.trackId
      ? () => fetchLibraryV2TrackHistory(entity.trackId!)
      : entity.albumId
        ? () => fetchLibraryV2AlbumHistory(entity.albumId!)
        : null;
    if (!fetchHistory) return;
    for (let attempt = 0; attempt < GRAB_OUTCOME_MAX_POLLS; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, GRAB_OUTCOME_POLL_INTERVAL_MS));
      if (cancelledRef.current) return;
      let history: LibraryV2HistoryEntry[];
      try {
        history = await fetchHistory();
      } catch {
        continue; // transient — history is a best-effort signal, keep trying
      }
      if (cancelledRef.current) return;
      const outcome = classifyGrabOutcome(history, sinceMs);
      if (outcome.status === 'failed') {
        setGrabbed((g) => ({ ...g, [key]: 'error' }));
        setGrabErrors((errors) => ({ ...errors, [key]: outcome.message }));
        return;
      }
      if (outcome.status === 'imported') {
        setGrabbed((g) => ({ ...g, [key]: 'done' }));
        // Autolink has committed the new file before the imported history
        // event is written. Refresh active album/artist queries immediately;
        // otherwise the table can keep showing "Missing" until a manual
        // Refresh & Scan even though the DB row already exists (§35).
        void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
        return;
      }
    }
    // Timed out with no terminal signal — likely still downloading (a large
    // Usenet grab can outlast this window). Leave it as a plain "Grabbed ✓"
    // rather than stalling the button forever; the track/album row's own
    // live queue badge (docs §73) covers longer-running progress.
    if (!cancelledRef.current) {
      setGrabbed((g) => ({ ...g, [key]: 'done' }));
    }
  }

  return (
    <DialogFrame
      open
      initialFocus={searchInputRef}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      className={`${styles.modal} ${styles.modalWide}`}
    >
      <DialogHeader title="Interactive Search" closeLabel="Close" />

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
            <table className={styles.trackTable}>
              <thead>
                <tr>
                  <SortTh label="Source" k="source" className={styles.isSource} />
                  <SortTh label="Title" k="title" />
                  <th className={styles.isArtist}>Artist</th>
                  <SortTh label="Quality" k="quality" className={styles.isQuality} />
                  <SortTh label="Size" k="size" className={styles.colNum} />
                  <SortTh label="Age" k="age" className={styles.colNum} />
                  <SortTh label="Availability" k="availability" className={styles.isAvail} />
                  <th className={styles.isGrab}></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => {
                  const key = resultKey(r);
                  const state = grabbed[key];
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
                      <td title={r.filename}>
                        <span className={styles.isTitle}>{resultTitle(r)}</span>
                        {isAlbum ? (
                          <span className={styles.albumResultBadge}>
                            album · {r.track_count ?? r.tracks?.length ?? '?'} tracks
                          </span>
                        ) : null}
                      </td>
                      <td>{r.artist ?? '—'}</td>
                      <td className={styles.qualityText}>
                        <span className={styles.qualityCellRow}>
                          {resultQuality(r)}
                          <ProfileBadge result={r} profile={effectiveProfile} />
                        </span>
                      </td>
                      <td className={styles.colNum}>{fmtBytes(resultSize(r))}</td>
                      <td className={styles.colNum} title={effMeta(r).publish_date ?? undefined}>
                        {ageText(effMeta(r).publish_date)}
                      </td>
                      <td className={styles.isAvailCell}>{availabilityCell(r)}</td>
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
                              !canWrite ||
                              wrongScope ||
                              state === 'pending' ||
                              state === 'done' ||
                              state === 'verifying'
                            }
                            onClick={() => void grab(r)}
                          >
                            {wrongScope
                              ? 'Album result'
                              : state === 'done'
                                ? 'Grabbed ✓'
                                : state === 'pending'
                                  ? '…'
                                  : state === 'verifying'
                                    ? 'Verifying…'
                                    : state === 'error'
                                      ? 'Retry'
                                      : 'Download'}
                          </button>
                          {state === 'error' ? (
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
        Downloads run through the normal SoulSync pipeline (staging → processing → tagging) and are
        linked into the v2 library automatically once the file lands — no manual “Refresh &amp;
        Scan” needed.
      </div>
    </DialogFrame>
  );
}
