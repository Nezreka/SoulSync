/**
 * The label page's pure logic, ported from static/label-detail.js.
 *
 * Every function here has a named counterpart in the vanilla file, and the
 * tests pin the ones whose exact behaviour is load-bearing — the ownership key
 * in particular, because it is what joins three separate concerns (the
 * library-check response, the cover cache, and the card lookup on click).
 */

import type { LabelFilter, LabelRelease, LabelSort } from './-label-detail.types';

/**
 * `_key` — the identity of a release across the whole page.
 *
 * Lowercased "artist||album". Deliberately NOT normalised beyond case: the
 * ownership response is matched back POSITIONALLY, so this key only has to be
 * stable within one session, and loosening it would merge two genuinely
 * different releases (a self-titled album and a same-named EP) into one card.
 */
export function releaseKey(release: LabelRelease | undefined | null): string {
  return `${(release?.artist || '').toLowerCase()}||${(release?.album || '').toLowerCase()}`;
}

/** `_normStr` — the looser form, used ONLY to pick a match out of search results. */
export function normalizeForMatch(value: unknown): string {
  return String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/**
 * `_coverUrl` — the lazy cover endpoint for one release.
 *
 * Cover Art Archive proved unreachable from both the browser
 * (ERR_CONNECTION_RESET) and the server (502s), so /api/labels/cover resolves
 * the album on iTunes and 302s to a CDN the browser can actually load.
 *
 * `cb=2` is a deliberate cache-buster: an earlier build cached the 302 for a
 * day, pinning a dead CAA target. Bump it if that ever needs breaking again —
 * and note the empty-string return, which is what suppresses the lazy-load
 * attempt entirely for a release with nothing to look up.
 */
export function coverUrl(release: LabelRelease | undefined | null): string {
  if (!release) return '';
  const params: string[] = [];
  if (release.release_id) params.push(`release_id=${encodeURIComponent(release.release_id)}`);
  if (release.artist) params.push(`artist=${encodeURIComponent(release.artist)}`);
  if (release.album) params.push(`album=${encodeURIComponent(release.album)}`);
  if (params.length) params.push('cb=2');
  return params.length ? `/api/labels/cover?${params.join('&')}` : '';
}

/**
 * `_visible` — filter, then sort.
 *
 * The two sorts that are not "newest" are expressed as transforms OF the
 * newest-first order the server already returned, exactly as the vanilla did:
 * 'oldest' reverses it, and 'artist' sorts by artist with the year as a
 * DESCENDING tiebreak (b vs a), so an artist's newest release still leads.
 */
export function visibleReleases(
  releases: LabelRelease[],
  owned: ReadonlySet<string>,
  filter: LabelFilter,
  sort: LabelSort,
): LabelRelease[] {
  let rows = releases.slice();
  if (filter === 'owned') rows = rows.filter((r) => owned.has(releaseKey(r)));
  else if (filter === 'missing') rows = rows.filter((r) => !owned.has(releaseKey(r)));

  if (sort === 'oldest') return rows.slice().reverse();
  if (sort === 'artist') {
    return rows
      .slice()
      .sort(
        (a, b) =>
          (a.artist || '').localeCompare(b.artist || '') ||
          (b.year || '').localeCompare(a.year || ''),
      );
  }
  return rows;
}

/**
 * `_updateCounts` — the numbers on the three filter pills.
 *
 * Counted over EVERYTHING loaded so far, not the visible rows, so switching
 * filters does not change the counts. Ownership is still resolving in the
 * background, so these move as batches come back.
 */
export function filterCounts(
  releases: LabelRelease[],
  owned: ReadonlySet<string>,
): { all: number; owned: number; missing: number } {
  const total = releases.length;
  const ownedCount = releases.filter((r) => owned.has(releaseKey(r))).length;
  return { all: total, owned: ownedCount, missing: total - ownedCount };
}

/** The hero's "N releases · M artists" line, with the vanilla's pluralisation. */
export function catalogMetaLine(
  total: number | undefined,
  artistCount: number | undefined,
): string {
  const releases = total || 0;
  const artists = artistCount || 0;
  return (
    `${releases} release${releases === 1 ? '' : 's'} · ` +
    `${artists} artist${artists === 1 ? '' : 's'}`
  );
}

/**
 * The empty-state copy.
 *
 * Three distinct cases, and the vanilla distinguished them: nothing loaded at
 * all, versus a filter that excluded everything — which names the filter, so
 * the user knows the catalog is not empty, their filter is.
 */
export function emptyStateText(loadedCount: number, filter: LabelFilter): string {
  if (!loadedCount) return 'No releases to show.';
  return `No ${filter === 'owned' ? 'owned' : 'missing'} releases in this label.`;
}

/**
 * The ownership overlay a card shows.
 *
 * Three states, not two: a release whose ownership has not been checked yet
 * shows NOTHING. Rendering "Missing" before the check returns would flash a
 * wrong answer on every card in a freshly loaded page.
 */
export function ownershipOverlay(
  key: string,
  owned: ReadonlySet<string>,
  checked: ReadonlySet<string>,
): 'owned' | 'missing' | null {
  if (owned.has(key)) return 'owned';
  return checked.has(key) ? 'missing' : null;
}
