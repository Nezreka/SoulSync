/**
 * The playlist library's filters — the sync page organised by STATE instead of
 * by source.
 *
 * The page used to ask "which service did this come from?" fifteen times, in a
 * strip. But provenance is the thing a user cares about least once a playlist
 * is in; what they came to find out is which ones still need something doing to
 * them. So the top-level split is state, and source drops to a chip you can
 * filter by — which is what source always actually was.
 *
 * Everything here is pure: rows in, rows out. The counts the tabs show come
 * from the same predicates that do the filtering, so a tab can never advertise
 * a number its own filter would not produce.
 */

import type { MirroredPlaylistRow } from './-sync.mirrored';

export type LibraryFilter =
  | 'all'
  | 'attention'
  | 'running'
  | 'synced'
  /**
   * Schedule is a SECOND dimension sharing one strip, which is a deliberate
   * simplification rather than an oversight: the page only ever narrows to one
   * group at a time, and two rows of tabs to express that would cost more than
   * the combinations are worth. The page shows a cadence on every card now, so
   * "which of these is actually scheduled" is the obvious next question and it
   * had no answer.
   */
  | 'scheduled'
  | 'unscheduled';

/** Chip order, and the labels. `all` is first and always present. */
export const LIBRARY_FILTERS: readonly { id: LibraryFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'attention', label: 'Needs attention' },
  { id: 'running', label: 'Working' },
  /**
   * "Discovered", not "Complete". The count behind it is discovered_count —
   * tracks the discovery step MATCHED to a source track — which is not the same
   * as owning the file. A playlist can sit here with every track matched and
   * nothing downloaded, and "Complete" claimed otherwise. The database keeps a
   * separate `in_library` figure for the stronger question; nothing on this
   * page reads it yet.
   */
  { id: 'synced', label: 'Discovered' },
  { id: 'scheduled', label: 'Scheduled' },
  { id: 'unscheduled', label: 'Unscheduled' },
];

/** How many of a row's tracks have been found. */
export function libraryDiscovered(row: MirroredPlaylistRow): number {
  return row.discovered_count || 0;
}

/** How many there are in total — `total_count` first, the row's own count after. */
export function libraryTotal(row: MirroredPlaylistRow): number {
  return row.total_count || row.track_count || 0;
}

/**
 * A pipeline is mid-flight. Read off the live pipeline state rather than the
 * row, because that is what the card's phase line is already showing.
 */
export function libraryIsRunning(row: MirroredPlaylistRow): boolean {
  const status = row.pipeline_state?.status;
  return status === 'running' || status === 'pipeline_running';
}

/**
 * Something here would benefit from a human.
 *
 * Two ways in: the pipeline failed outright, or discovery finished short —
 * tracks the playlist claims to have that we could not find. A playlist nothing
 * has ever been attempted on is NOT "attention": it is untouched, and calling
 * every new mirror a problem would make the filter useless on day one.
 */
export function libraryNeedsAttention(row: MirroredPlaylistRow): boolean {
  if (row.pipeline_state?.status === 'error' || row.pipeline_state?.error) return true;
  // A run still in flight is ALWAYS short of its total — it has not finished
  // yet. Counting that as "needs attention" would flag every playlist the
  // moment you started working on it, which is the opposite of useful. An
  // error above still wins, because a failed run needs a human whether or not
  // the poller has caught up.
  if (libraryIsRunning(row)) return false;
  const discovered = libraryDiscovered(row);
  if (discovered <= 0) return false; // never attempted, not failed
  return discovered < libraryTotal(row);
}

/**
 * Every track the playlist claims to have, MATCHED — not necessarily owned.
 * See the label note on LIBRARY_FILTERS.
 */
export function libraryIsComplete(row: MirroredPlaylistRow): boolean {
  const total = libraryTotal(row);
  if (total <= 0) return false;
  if (libraryNeedsAttention(row) || libraryIsRunning(row)) return false;
  return libraryDiscovered(row) >= total;
}

/**
 * `scheduled` is the set of playlist ids that have a cadence.
 *
 * Passed in rather than read off the row: a schedule is an automation, stored
 * nowhere on the playlist itself, and the card strip already holds this map.
 * Omitting it makes both schedule filters match NOTHING rather than everything
 * — an empty tab is a visible mistake, whereas silently showing all 38 under
 * "Scheduled" would look like an answer.
 */
export function libraryMatchesFilter(
  row: MirroredPlaylistRow,
  filter: LibraryFilter,
  scheduled?: ReadonlySet<number>,
): boolean {
  switch (filter) {
    case 'attention':
      return libraryNeedsAttention(row);
    case 'running':
      return libraryIsRunning(row);
    case 'synced':
      return libraryIsComplete(row);
    case 'scheduled':
      return row.id !== undefined && Boolean(scheduled?.has(row.id));
    case 'unscheduled':
      return row.id !== undefined && !scheduled?.has(row.id);
    default:
      return true;
  }
}

/**
 * Free-text match over the name a user actually SEES.
 *
 * A renamed playlist is matched on both its custom name and its original: the
 * card shows the custom one, but someone who remembers importing "Discover
 * Weekly" should still find it after calling it "Monday".
 */
export function libraryMatchesSearch(row: MirroredPlaylistRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [row.custom_name, row.name].some((n) => (n ?? '').toLowerCase().includes(needle));
}

/**
 * Narrow by text BEFORE the tabs count, so the counts describe what a search
 * actually left behind rather than the whole library.
 */
export function librarySearch(
  rows: readonly MirroredPlaylistRow[],
  query: string,
): MirroredPlaylistRow[] {
  return rows.filter((row) => libraryMatchesSearch(row, query));
}

/**
 * Apply the state filter and the source filter together.
 *
 * `sources` empty means "no source filter" rather than "no sources" — an empty
 * chip selection showing an empty library would read as data loss.
 */
export function libraryVisibleRows(
  rows: readonly MirroredPlaylistRow[],
  filter: LibraryFilter,
  sources: ReadonlySet<string>,
  scheduled?: ReadonlySet<number>,
): MirroredPlaylistRow[] {
  return rows.filter(
    (row) =>
      libraryMatchesFilter(row, filter, scheduled) &&
      (sources.size === 0 || sources.has(row.source ?? '')),
  );
}

/**
 * Counts per state tab, computed against the SOURCE filter only.
 *
 * Deliberately not against the active state filter: the tabs have to say how
 * many you would get by switching to them, and a count that collapsed to the
 * current tab's own total would make every other tab read zero.
 */
export function libraryFilterCounts(
  rows: readonly MirroredPlaylistRow[],
  sources: ReadonlySet<string>,
  scheduled?: ReadonlySet<number>,
): Record<LibraryFilter, number> {
  const scoped = rows.filter((row) => sources.size === 0 || sources.has(row.source ?? ''));
  return {
    all: scoped.length,
    attention: scoped.filter(libraryNeedsAttention).length,
    running: scoped.filter(libraryIsRunning).length,
    synced: scoped.filter(libraryIsComplete).length,
    scheduled: scoped.filter((r) => libraryMatchesFilter(r, 'scheduled', scheduled)).length,
    unscheduled: scoped.filter((r) => libraryMatchesFilter(r, 'unscheduled', scheduled)).length,
  };
}

/**
 * Which state tabs to render.
 *
 * A tab with no rows has no tab — the dashboard's rule, and the reason its
 * bands stay calm. `all` always survives so the strip never empties, and the
 * ACTIVE tab survives even at zero, because removing the tab you are standing
 * on would move the page out from under you.
 */
export function libraryVisibleFilters(
  counts: Record<LibraryFilter, number>,
  active: LibraryFilter,
): readonly { id: LibraryFilter; label: string }[] {
  return LIBRARY_FILTERS.filter((f) => f.id === 'all' || f.id === active || counts[f.id] > 0);
}

/** The sources present in the library, in a stable order, for the chip row. */
export function librarySources(rows: readonly MirroredPlaylistRow[]): string[] {
  const seen: string[] = [];
  for (const row of rows) {
    const source = row.source ?? '';
    if (source && !seen.includes(source)) seen.push(source);
  }
  return seen.sort();
}

/**
 * The header line — the dashboard's hello-strip rule applied here.
 *
 * Anything unknown is OMITTED rather than zeroed: a fresh install shows
 * "No playlists yet" instead of a row of zeroes pretending to be a report.
 */
export function librarySummary(rows: readonly MirroredPlaylistRow[]): string {
  if (rows.length === 0) return 'No playlists yet';
  const parts = [`${rows.length} playlist${rows.length === 1 ? '' : 's'}`];

  const running = rows.filter(libraryIsRunning).length;
  if (running > 0) parts.push(`${running} working`);

  const missing = rows.reduce((sum, row) => {
    if (!libraryNeedsAttention(row)) return sum;
    return sum + Math.max(0, libraryTotal(row) - libraryDiscovered(row));
  }, 0);
  if (missing > 0) parts.push(`${missing} track${missing === 1 ? '' : 's'} missing`);

  return parts.join(' · ');
}

/* ── The card's own state ─────────────────────────────────────────────────── */

/**
 * What a card is, in one word — the thing its artwork ring expresses.
 *
 * `ok` is the interesting one: it renders NOTHING extra. A ring on every card
 * would be forty things announcing that nothing is wrong, which is the same
 * mistake as a full green progress bar on a finished row.
 */
export type LibraryCardState = 'error' | 'short' | 'working' | 'ok';

export function libraryCardState(row: MirroredPlaylistRow): LibraryCardState {
  if (row.pipeline_state?.status === 'error' || row.pipeline_state?.error) return 'error';
  if (libraryIsRunning(row)) return 'working';
  if (libraryNeedsAttention(row)) return 'short';
  return 'ok';
}

/** How full the ring is: discovered over total, clamped, 0 when unknowable. */
export function libraryCoveragePct(row: MirroredPlaylistRow): number {
  const total = libraryTotal(row);
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((libraryDiscovered(row) / total) * 100)));
}

/** How many tracks the playlist claims to have that we have not found. */
export function libraryMissingCount(row: MirroredPlaylistRow): number {
  return Math.max(0, libraryTotal(row) - libraryDiscovered(row));
}

/**
 * Problems first.
 *
 * This ordering is what lets the cards stay calm: with the broken one already
 * at the front, the design does not need warning colours, severity stripes or
 * alarm chrome to make it findable. Ties keep the incoming order — the backend
 * sends newest-updated first, which is a sensible second key and one the user
 * has already been living with.
 */
const STATE_RANK: Readonly<Record<LibraryCardState, number>> = {
  error: 0,
  short: 1,
  working: 2,
  ok: 3,
};

export function librarySortedRows(
  rows: readonly MirroredPlaylistRow[],
): MirroredPlaylistRow[] {
  // A COPY: sorting the caller's array in place would reorder the row list the
  // rest of the tab still holds.
  return [...rows].sort((a, b) => STATE_RANK[libraryCardState(a)] - STATE_RANK[libraryCardState(b)]);
}
