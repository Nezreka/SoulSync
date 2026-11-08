import type { LibraryV2HistoryEntry } from '../-library-v2.api';

export type HistoryGroup = { key: string; entries: LibraryV2HistoryEntry[] };

/** Runs of the same result. `byKind` folds every event of one kind together
 *  whenever it happened (a track's twelve AcoustID checks are one line);
 *  otherwise only adjacent ones from the same minute merge. */
export function groupHistoryEvents(
  entries: LibraryV2HistoryEntry[],
  { byKind = false }: { byKind?: boolean } = {},
) {
  const groups: HistoryGroup[] = [];
  const byKey = new Map<string, HistoryGroup>();
  for (const [index, event] of entries.entries()) {
    // Never merge across another operation or an unknown timestamp. Every
    // original event remains available when expanded.
    const key = JSON.stringify([
      byKind ? null : (event.date?.slice(0, 16) ?? index),
      event.category,
      event.event_type,
      event.title,
      event.source,
      event.job_id,
      event.status_basis,
    ]);
    const previous = byKind ? byKey.get(key) : groups.at(-1);
    if (previous?.key === key) previous.entries.push(event);
    else {
      const group = { key, entries: [event] };
      groups.push(group);
      byKey.set(key, group);
    }
  }
  return groups;
}

/** a quiet colour for one outcome: done, failed or in between */
export function historyTone(status: string | null | undefined) {
  const value = (status ?? '').toLowerCase();
  if (!value) return undefined;
  if (/(fail|error|reject|quarantin|block|mismatch)/.test(value)) return 'err';
  if (/^(not|no|missing|skip)/.test(value)) return 'neutral';
  if (/(complete|success|import|done|grabbed|verified|pass|\bok\b)/.test(value)) return 'ok';
  return 'neutral';
}

export interface HistoryNode {
  key: string;
  title: string;
  subtitle: string | null;
  /** newest first, like the feed */
  entries: LibraryV2HistoryEntry[];
}

/** The history as a tree: one node per track, then one per release for what
 *  happened to a release as a whole, then the rest (searches the feed can't
 *  pin to a track). Newest activity first. */
export function historyTree(entries: LibraryV2HistoryEntry[]): HistoryNode[] {
  const nodes = new Map<string, LibraryV2HistoryEntry[]>();
  for (const event of entries) {
    const key = event.track_id
      ? `track:${event.track_id}`
      : event.album_id
        ? `album:${event.album_id}`
        : 'other';
    nodes.set(key, [...(nodes.get(key) ?? []), event]);
  }
  return [...nodes].map(([key, events]) => {
    // a download names no album, a scan names both: any event that says will do
    const named = events.find((e) => e.track_title) ?? events.find((e) => e.album_title);
    if (key === 'other') {
      return { key, title: 'Searches & other activity', subtitle: null, entries: events };
    }
    return {
      key,
      title: historySubject(named ?? events[0]!),
      subtitle: key.startsWith('album:')
        ? 'Whole release'
        : (events.find((e) => e.album_title)?.album_title ?? null),
      entries: events,
    };
  });
}

const GRABBED = new Set([
  'grab_submitted',
  'force_grab',
  'manual_grab_correlated',
  'scheduled_grab_correlated',
  'client_job_adopted',
  'retry_started',
]);
const IMPORTED = new Set([
  'import_completed',
  'import_resolved_manually',
  'previous_file_replaced',
]);
const HELD = new Set(['import_file_quarantined', 'import_needs_review']);
const WRITTEN = new Set([
  'applied_tags',
  'applied_metadata',
  'applied_replaygain',
  'applied_lyrics',
  'applied_cover_art',
  'normalized_tags',
  'retagged',
  'library_retag',
  'fixed_track_number',
  'aligned_release_year',
]);
const CHECKED = new Set(['verification_status_updated', 'acoustic_id_checked', 'human_verified']);

export interface HistoryStep {
  label: string;
  tone: ReturnType<typeof historyTone>;
}

/** What a track went through, in pipeline order: grabbed → downloaded (after
 *  N tries) → imported → metadata written → the latest check. Only the steps
 *  that happened; the retries and checks themselves stay in the details. */
export function historySteps(entries: LibraryV2HistoryEntry[]): HistoryStep[] {
  const has = (types: Set<string>) => entries.some((e) => types.has(e.event_type));
  const failed = entries.filter((e) => e.event_type === 'grab_failed').length;
  const downloaded = entries.some(
    (e) =>
      e.event_type === 'grab_completed' ||
      (e.event_type === 'downloaded' && e.category === 'imported'),
  );
  const steps: HistoryStep[] = [];
  if (has(GRABBED)) steps.push({ label: 'Grabbed', tone: 'neutral' });
  if (downloaded) {
    steps.push({
      label: failed ? `Downloaded after ${failed + 1} tries` : 'Downloaded',
      tone: 'ok',
    });
  } else if (failed) {
    steps.push({
      label: failed > 1 ? `Download failed ${failed}×` : 'Download failed',
      tone: 'err',
    });
  }
  if (has(IMPORTED)) steps.push({ label: 'Imported', tone: 'ok' });
  else if (has(HELD)) steps.push({ label: 'Quarantined', tone: 'err' });
  if (has(WRITTEN)) steps.push({ label: 'Metadata written', tone: 'ok' });
  // newest first: the check that stands
  const check = entries.find((e) => CHECKED.has(e.event_type));
  if (check) {
    const label =
      check.event_type === 'acoustic_id_checked'
        ? `AcoustID ${(check.status ?? 'checked').replaceAll('_', ' ')}`
        : (check.status ?? check.title ?? 'Checked');
    steps.push({ label, tone: historyTone(label) });
  }
  return steps;
}

export function historyOutcomeSummary(entries: LibraryV2HistoryEntry[]): string {
  // a run of maintenance writes carries no verdict: say what it wrote
  if (!entries.some((entry) => entry.status)) {
    return entries[0]?.detail || `${entries.length} result not recorded`;
  }
  const counts = new Map<string, number>();
  for (const entry of entries) {
    const label = entry.status?.replaceAll('_', ' ') || 'result not recorded';
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts].map(([label, count]) => `${count} ${label}`).join(' · ');
}

export function historySubject(entry: LibraryV2HistoryEntry): string {
  return (
    entry.track_title ||
    entry.album_title ||
    (entry.track_id
      ? `Track #${entry.track_id}`
      : entry.album_id
        ? `Release #${entry.album_id}`
        : '—')
  );
}

export interface RecentHistoryLine {
  label: string;
  status: string | null;
  category: string;
  count: number;
  /** Date of the newest event in the run (entries arrive newest first). */
  last: string | null;
}

/** The Overview's short history: a run of the same event reads as one line
 *  ("Search failed 10× · last …"), and only the newest lines are kept. */
export function summarizeRecentHistory(
  entries: LibraryV2HistoryEntry[],
  limit = 6,
): RecentHistoryLine[] {
  const lines: RecentHistoryLine[] = [];
  for (const entry of entries) {
    const label = entry.title || entry.event_type;
    const status = entry.status ?? null;
    const previous = lines.at(-1);
    if (previous && previous.label === label && previous.status === status) {
      previous.count += 1;
      continue;
    }
    if (lines.length === limit) break;
    lines.push({ label, status, category: entry.category, count: 1, last: entry.date });
  }
  return lines;
}
