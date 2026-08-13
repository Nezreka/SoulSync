import type { Discography, DiscographyBucket, DiscographyRelease } from './-artist-detail.types';

/**
 * Discography gap-fill (#1067) — "show me what my OTHER sources know about".
 *
 * A view option, persisted per browser, that APPENDS releases the base source
 * never listed. The base discography renders untouched; gap cards slot into the
 * real Album/EP/Single grids at their year-sorted position (Boulder's live
 * feedback: a separate section felt bolted-on) and carry a source badge, and
 * each one keeps its owning source so clicks resolve from THERE — a gap
 * release's id only means anything on the source that listed it.
 */

const STORAGE_KEY = 'discog_gapfill';

export function gapFillEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    // Private-mode / disabled storage: treat as off rather than throwing.
    return false;
  }
}

export function setGapFillEnabled(on: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, on ? '1' : '0');
  } catch {
    // The chip still toggles for this page view; it just will not persist.
  }
}

/**
 * The client half of the backend's conservative same-release rule.
 *
 * Edition parens are KEPT — "Album" and "Album (Deluxe Edition)" are different
 * releases and must not collapse into one.
 */
export function gapNorm(title: unknown): string {
  return String(title || '')
    .toLowerCase()
    .replace(/[^\w\s()]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Year from `year` or the first four characters of `release_date`. */
export function gapYear(release: { year?: unknown; release_date?: unknown }): number | null {
  let raw: unknown = release?.year;
  if (raw == null && release?.release_date) raw = String(release.release_date).slice(0, 4);
  const year = parseInt(String(raw), 10);
  return year >= 1000 && year <= 3000 ? year : null;
}

/**
 * Same release? Titles must match exactly once normalised, and the years must
 * be within one of each other — an UNKNOWN year on either side counts as a
 * match, because the alternative is showing the same album twice.
 */
export function gapSameRelease(
  a: { title?: unknown; name?: unknown; year?: unknown; release_date?: unknown },
  b: { title?: unknown; name?: unknown; year?: unknown; release_date?: unknown },
): boolean {
  const ta = gapNorm(a.title ?? a.name);
  const tb = gapNorm(b.title ?? b.name);
  if (!ta || ta !== tb) return false;
  const ya = gapYear(a);
  const yb = gapYear(b);
  if (ya == null || yb == null) return true;
  return Math.abs(ya - yb) <= 1;
}

/**
 * Badge text per source, mirroring SOURCE_LABELS in shared-helpers.js.
 *
 * That map is a top-level `const` in a classic script, so it is a global
 * LEXICAL binding and never lands on `window` — a module cannot read it. The
 * copy is kept honest by a differential parity test that extracts the labels
 * from shared-helpers.js and requires them to match exactly.
 */
export const SOURCE_LABEL_TEXT: Record<string, string> = {
  spotify: 'Spotify',
  spotify_free: 'Spotify (no auth)',
  itunes: 'Apple Music',
  deezer: 'Deezer',
  discogs: 'Discogs',
  hydrabase: 'Hydrabase',
  amazon: 'Amazon Music',
  musicbrainz: 'MusicBrainz',
  jiosaavn: 'JioSaavn',
  bandcamp: 'Bandcamp',
  youtube_videos: 'Music Videos',
  soulseek: 'Basic Search',
};

/** An unknown source falls back to its raw key, exactly as the vanilla did. */
export function gapSourceLabel(source: string | undefined): string {
  return SOURCE_LABEL_TEXT[source ?? ''] ?? (source || '');
}

export interface GapRelease extends DiscographyRelease {
  /** Which source listed this release — clicks and downloads resolve there. */
  _gap_source?: string;
  /** Which grid it belongs in. */
  _bucket: DiscographyBucket;
  /**
   * Track count for the ownership stream ONLY.
   *
   * Deliberately not `track_count`: the vanilla's gap card carried no track
   * count, so releaseToAlbumData fell through to its "never 0" default of 1.
   * Setting the real field here would silently change what the download modal
   * opens with.
   */
  _gap_track_count?: number;
}

const BUCKET_FOR: Record<string, DiscographyBucket> = {
  albums: 'albums',
  eps: 'eps',
  singles: 'singles',
};

/**
 * Flatten the gap-fill response into release objects.
 *
 * `owned` starts FALSE, not null: these are known-missing by definition, and a
 * null would render them as permanently "checking" if the ownership stream
 * never reached them.
 */
export function gapReleasesFromResponse(payload: unknown): GapRelease[] {
  const gaps = (payload as { gaps?: Record<string, unknown[]> })?.gaps ?? {};
  const releases: GapRelease[] = [];

  for (const key of ['albums', 'eps', 'singles'] as const) {
    for (const raw of gaps[key] ?? []) {
      const gap = raw as Record<string, unknown>;
      releases.push({
        id: gap.id as string | number,
        title: (gap.title as string) || (gap.name as string) || 'Unknown Release',
        image_url: (gap.image_url as string) || '',
        year: gap.year as number,
        release_date: gap.release_date as string,
        album_type: (gap.album_type as string) || key,
        owned: false,
        _gap_track_count: (gap.track_count as number) || (gap.total_tracks as number) || 0,
        _gap_source: (gap.gap_source as string) || undefined,
        _bucket: BUCKET_FOR[key],
      });
    }
  }
  return releases;
}

/**
 * Drop gaps the page already shows.
 *
 * This runs against what was RENDERED, not against the base source's own list:
 * the library-merged view can contain owned releases the base source never
 * listed, and those must not come back as "missing" gap cards.
 */
export function dedupeGaps(gaps: GapRelease[], rendered: Discography): GapRelease[] {
  const shown = [...(rendered.albums ?? []), ...(rendered.eps ?? []), ...(rendered.singles ?? [])];
  return gaps.filter((gap) => !shown.some((release) => gapSameRelease(gap, release)));
}

/**
 * Slot gap releases into the base buckets at their year-sorted position.
 *
 * Grids render newest-first and unknown years sink to the end, so each gap goes
 * before the first release OLDER than it. The base order is otherwise left
 * alone — this is an insertion, not a re-sort, so a source that lists releases
 * in its own deliberate order keeps it.
 */
export function mergeGapReleases(base: Discography, gaps: GapRelease[]): Discography {
  if (gaps.length === 0) return base;

  const merged: Discography = { ...base };
  for (const bucket of ['albums', 'eps', 'singles'] as const) {
    const forBucket = gaps.filter((gap) => gap._bucket === bucket);
    if (forBucket.length === 0) continue;

    const releases = [...(base[bucket] ?? [])];
    for (const gap of forBucket) {
      const year = gapYear(gap) || 0;
      const index = releases.findIndex((release) => (gapYear(release) || 0) < year);
      if (index === -1) releases.push(gap);
      else releases.splice(index, 0, gap);
    }
    merged[bucket] = releases;
  }
  return merged;
}

/**
 * The completion-stream body for gap releases.
 *
 * Each entry carries its OWN `source` and the top-level source is null: a gap
 * release's id is only meaningful on the source that listed it, so they cannot
 * ride the base artist's stream.
 */
export function gapStreamPayload(artistName: string, gaps: GapRelease[]) {
  const payload: {
    artist_name: string;
    albums: unknown[];
    eps: unknown[];
    singles: unknown[];
    source: null;
  } = { artist_name: artistName, albums: [], eps: [], singles: [], source: null };

  for (const gap of gaps) {
    payload[gap._bucket].push({
      id: gap.id,
      title: gap.title || '',
      track_count: gap._gap_track_count || 0,
      album_type: gap.album_type || gap._bucket,
      year: gap.year,
      release_date: gap.release_date,
      source: gap._gap_source || null,
    });
  }
  return payload;
}

/** The gap-fill request URL, with the base source so it can be excluded. */
export function gapFillUrl(
  artistId: unknown,
  artistName: string | undefined,
  baseSource: string | undefined,
): string {
  const params = new URLSearchParams();
  if (artistName) params.set('artist_name', artistName);
  if (baseSource) params.set('base_source', baseSource);
  return `/api/artist/${encodeURIComponent(String(artistId))}/discography/gap-fill?${params}`;
}
