/**
 * The findings inbox, as arithmetic.
 *
 * Everything here is pure so the hard parts — what the health number means,
 * which group the user should look at first, how wide each bar segment is —
 * can be pinned by tests instead of eyeballed in a browser.
 *
 * The problem this solves: the flat list made a user page 30-at-a-time
 * through thousands of rows with no way to see that 90% of them were one
 * safe, one-click type. Grouping turns "3,000 findings" into four decisions.
 */

import type { RepairJobRun } from './-tools.types';

// ── Shapes served by the backend ─────────────────────────────────────────────

/** One row of `GET /api/repair/findings/groups`. */
export interface FindingGroup {
  finding_type: string;
  pending: number;
  resolved: number;
  dismissed: number;
  /** Its own status, not a flavour of resolved: what the worker fixed by
   *  itself, with nobody asked. */
  auto_fixed?: number;
  total: number;
  /** Worst severity among the PENDING rows — a cleared error must not keep a
   *  group flagged red forever. */
  severity_max: string;
  last_seen?: string | null;
  job_ids?: string[];
  /** Pending rows in this group that would overwrite a value someone set by
   *  hand. Counted server-side: "apply all" and "apply all except my own
   *  edits" are two different requests, and the choice has to be made before
   *  the click — not after walking every finding's diff. */
  manual_conflicts?: number;
}

/** One row of `GET /api/repair/finding-types`. The backend owns this: the
 *  client used to keep its own list of 20 while the worker had 29 handlers,
 *  so nine working fixes had no button and two dead ends had one. */
export interface FindingTypeInfo {
  type: string;
  label: string;
  /** Null when nothing can fix this type — review only, never a button. */
  verb: string | null;
  fixable: boolean;
  destructive: boolean;
  job_ids?: string[];
}

// ── One line explaining every finding type ───────────────────────────────────

/**
 * What the type MEANS, in the user's terms, for the group row.
 *
 * The labels answer "what is this called"; these answer "why do I care". Kept
 * to one short line each — a group row is scanned, not read. Every slug in
 * the worker's FINDING_TYPE_META has an entry (a pytest guards the drift, and
 * `findingTypeBlurb` degrades to '' rather than printing a slug at the user).
 */
export const FINDING_TYPE_BLURBS: Record<string, string> = {
  dead_file: 'Library rows whose audio file is missing from disk.',
  orphan_file: 'Audio files on disk with no matching row in the library.',
  track_number_mismatch: "Tag track numbers disagree with the release's order.",
  missing_cover_art: 'Albums or artists with no artwork stored.',
  missing_lyrics: 'Tracks with no lyrics saved.',
  missing_replaygain: 'No loudness tags, so playback volume jumps between tracks.',
  replaygain_retag: 'Loudness tags written before your current target level.',
  empty_folder: 'Folders left behind with no audio inside.',
  expired_download: 'Finished downloads older than your retention window.',
  metadata_gap: 'Missing genres, years or IDs that enrichment can fill in.',
  duplicate_tracks: 'The same track stored more than once.',
  single_album_redundant: 'Singles you also own inside the full album.',
  mbid_mismatch: 'Track MusicBrainz IDs disagree with the tags on disk.',
  album_mbid_mismatch: 'Album MusicBrainz IDs disagree with the tags on disk.',
  album_tag_inconsistency: 'Tracks in one album carrying conflicting album tags.',
  incomplete_album: 'Albums missing tracks you do not own yet.',
  path_mismatch: 'Files stored outside your file-organization template.',
  missing_lossy_copy: 'Lossless tracks with no lossy copy for portable use.',
  unwanted_content: 'Live takes, interviews or spoken word you filter out.',
  unknown_artist: 'Tracks filed under an unidentified or blank artist.',
  acoustid_mismatch: 'The audio fingerprint says this is a different track.',
  quality_upgrade: 'Files below the bitrate or format your profile asks for.',
  missing_discography_track: 'Releases by your artists that are not in the library.',
  library_retag: "Files whose tags are behind the library's metadata.",
  short_preview_track: '30-second preview clips saved in place of full tracks.',
  corrupt_audio: 'Files that fail to decode — damaged or truncated audio.',
  canonical_version: 'Albums with several versions and no pinned favourite.',
  genre_cleanup: 'Genre tags that are junk, duplicated or wrongly cased.',
  comma_artist_split: 'One artist row holding several comma-separated names.',
  fake_lossless: 'FLAC upscaled from a lossy source. Review only.',
  album_needs_enrichment: 'Albums still waiting on a metadata enrichment pass.',
};

export function findingTypeBlurb(findingType: string): string {
  return FINDING_TYPE_BLURBS[findingType] || '';
}

// ── Severity ─────────────────────────────────────────────────────────────────

/** Sort order. Anything unrecognised sorts as `info` — the quietest bucket,
 *  so an unknown severity can never jump the queue ahead of real errors. */
export const SEVERITY_RANK: Record<string, number> = { error: 0, warning: 1, info: 2 };

export function severityRank(severity: string | null | undefined): number {
  return SEVERITY_RANK[severity || 'info'] ?? 2;
}

/**
 * How much one pending finding of each severity costs the health score.
 *
 * An error is a broken file; 40 missing lyrics are not a quarter of that. The
 * spread is deliberately wide so the number tracks what is actually wrong
 * rather than what is merely numerous.
 */
export const SEVERITY_WEIGHTS: Record<string, number> = { error: 1, warning: 0.25, info: 0.02 };

export function severityWeight(severity: string | null | undefined): number {
  return SEVERITY_WEIGHTS[severity || 'info'] ?? SEVERITY_WEIGHTS.info;
}

/** Weighted pending cost of one group. Uses `severity_max`, so a group whose
 *  pending rows are mixed is costed at its worst — which is the answer that
 *  errs toward telling the user something is wrong. */
export function groupWeight(group: FindingGroup): number {
  return severityWeight(group.severity_max) * Math.max(0, group.pending || 0);
}

// ── The health score ─────────────────────────────────────────────────────────

export type HealthBand = 'healthy' | 'attention' | 'unhealthy';

export interface LibraryHealth {
  score: number;
  band: HealthBand;
  /** The weighted pending cost before normalisation — what the bar segments
   *  are proportional to. */
  weighted: number;
  pending: number;
}

/**
 * `100 - weighted pending per 1,000 tracks`, clamped to 0.
 *
 * Normalising by library size is what makes the number comparable: 200 orphan
 * files in a 2,000-track library is a mess, and in a 200,000-track library it
 * is a Tuesday. A library whose track count is unknown (stats not loaded yet)
 * is scored as if it held 1,000 tracks — pessimistic, and it resolves upward
 * the moment the real count arrives rather than flashing a fake 100.
 */
export function libraryHealth(
  groups: readonly FindingGroup[],
  trackCount: number | null | undefined,
): LibraryHealth {
  let weighted = 0;
  let pending = 0;
  for (const group of groups) {
    weighted += groupWeight(group);
    pending += Math.max(0, group.pending || 0);
  }
  const per1k = Math.max(1, (trackCount || 0) / 1000);
  const score = Math.max(0, Math.round(100 - Math.min(100, weighted / per1k)));
  return { score, band: healthBand(score), weighted, pending };
}

export function healthBand(score: number): HealthBand {
  if (score >= 90) return 'healthy';
  if (score >= 70) return 'attention';
  return 'unhealthy';
}

export function healthBandLabel(band: HealthBand): string {
  if (band === 'healthy') return 'healthy';
  if (band === 'attention') return 'needs attention';
  return 'unhealthy';
}

// ── The contribution bar ─────────────────────────────────────────────────────

export interface ContributionSegment {
  findingType: string;
  label: string;
  pending: number;
  severity: string;
  /** Percent of the bar. Weighted, not raw counts — otherwise 4,000 missing
   *  lyrics would bury three corrupt files under a single-pixel sliver. */
  percent: number;
}

/**
 * One segment per type with pending rows, widest contribution first.
 *
 * `labels` comes from the served catalog so the bar and the group rows can
 * never disagree about what a type is called.
 */
export function contributionSegments(
  groups: readonly FindingGroup[],
  labelOf: (findingType: string) => string,
): ContributionSegment[] {
  const withPending = groups.filter((group) => (group.pending || 0) > 0);
  const total = withPending.reduce((sum, group) => sum + groupWeight(group), 0);
  if (total <= 0) return [];
  return withPending
    .map((group) => ({
      findingType: group.finding_type,
      label: labelOf(group.finding_type),
      pending: group.pending,
      severity: group.severity_max || 'info',
      percent: (groupWeight(group) / total) * 100,
    }))
    .sort((a, b) => b.percent - a.percent || a.findingType.localeCompare(b.findingType));
}

// ── Inbox ordering ───────────────────────────────────────────────────────────

/**
 * Worst first — but destructive types go LAST within their severity band.
 *
 * The order is a recommendation about what to click, and the safe one-click
 * groups are the ones that should be clicked first. Putting "Delete 37 files"
 * above "Apply art to 412 albums" invites the expensive decision while the
 * user is still warming up.
 */
export function sortInboxGroups(
  groups: readonly FindingGroup[],
  isDestructive: (findingType: string) => boolean,
): FindingGroup[] {
  return [...groups].sort((a, b) => {
    const bySeverity = severityRank(a.severity_max) - severityRank(b.severity_max);
    if (bySeverity !== 0) return bySeverity;
    const byRisk = Number(isDestructive(a.finding_type)) - Number(isDestructive(b.finding_type));
    if (byRisk !== 0) return byRisk;
    const byCount = (b.pending || 0) - (a.pending || 0);
    if (byCount !== 0) return byCount;
    return a.finding_type.localeCompare(b.finding_type);
  });
}

/** Which count a group row shows, following the status segmented control. */
export function groupCountForStatus(group: FindingGroup, status: string): number {
  if (status === 'resolved') return group.resolved || 0;
  if (status === 'dismissed') return group.dismissed || 0;
  if (status === 'pending') return group.pending || 0;
  if (status === 'auto_fixed') return group.auto_fixed || 0;
  return group.total || 0;
}

export interface InboxFilters {
  /** Job chip / dropdown. Matched against the group's own job_ids. */
  jobId?: string;
  severity?: string;
  status?: string;
}

/**
 * The groups the inbox actually renders.
 *
 * Filtering is client-side on purpose: the groups payload is one row per type
 * (tens of rows, not thousands), so re-querying the server for a dropdown
 * change would buy nothing and cost a round trip per keystroke.
 *
 * A group with nothing in the selected status is dropped — that is what makes
 * "Dismissed" show the dismissed groups and only those.
 */
export function visibleGroups(
  groups: readonly FindingGroup[],
  filters: InboxFilters,
): FindingGroup[] {
  return groups.filter((group) => {
    if (filters.jobId && !(group.job_ids || []).includes(filters.jobId)) return false;
    if (filters.severity && (group.severity_max || 'info') !== filters.severity) return false;
    return groupCountForStatus(group, filters.status || '') > 0;
  });
}

/**
 * How many pending findings "Fix all safe" would actually touch.
 *
 * Fixable AND non-destructive: a type with no handler would be counted and
 * then silently skipped by the run, which is how a button comes to report
 * "Fixed 0 of 1,204".
 */
export function safeFixablePending(
  groups: readonly FindingGroup[],
  infoOf: (findingType: string) => FindingTypeInfo | undefined,
): number {
  let total = 0;
  for (const group of groups) {
    const info = infoOf(group.finding_type);
    if (info?.fixable && !info.destructive) total += Math.max(0, group.pending || 0);
  }
  return total;
}

// ── History sparkline ────────────────────────────────────────────────────────

/**
 * Findings-per-run for the hero's trend line, oldest → newest.
 *
 * `/api/repair/history` returns newest first, so this reverses. Runs that
 * created nothing still count as points — a flat line at zero is the shape of
 * a library that has stopped generating work, and hiding those points would
 * draw a trend out of the few bad days.
 */
export function findingsTrend(runs: readonly RepairJobRun[], limit = 20): number[] {
  return runs
    .slice(0, limit)
    .map((run) => Math.max(0, run.findings_created || 0))
    .reverse();
}

/**
 * Trend points as an SVG polyline over a `width` × `height` box.
 *
 * A single point (or a flat series) draws a horizontal line at mid-height
 * rather than dividing by a zero range.
 */
export function sparklinePoints(values: readonly number[], width = 120, height = 28): string {
  if (values.length === 0) return '';
  if (values.length === 1) return `0,${height / 2} ${width},${height / 2}`;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min;
  const step = width / (values.length - 1);
  return values
    .map((value, index) => {
      const x = index * step;
      const y = range === 0 ? height / 2 : height - ((value - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}
