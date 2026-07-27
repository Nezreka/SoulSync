import type { DiscographyRelease } from './-artist-detail.types';

/**
 * Release-card derivations, ported from `createReleaseCard` (library.js:1683).
 *
 * Kept as pure functions so each branch is testable without a DOM — the vanilla
 * built this by hand with createElement and innerHTML.
 */

export type CompletionClass = 'checking' | 'completed' | 'nearly_complete' | 'partial' | 'missing';

export interface CompletionOverlay {
  className: CompletionClass;
  label: string;
}

/** `release-card album-card` plus a state suffix. Both classes matter:
 *  `.release-card` keeps the existing filter/state CSS and JS queries working,
 *  `.album-card` carries the big-photo visual treatment. */
export function releaseCardClassName(release: DiscographyRelease): string {
  if (release.owned === null) return 'release-card album-card checking';
  if (release.owned === false) return 'release-card album-card missing';
  return 'release-card album-card';
}

/**
 * The top-right completion badge.
 *
 * Returns null for a SOURCE artist — the vanilla omitted the overlay entirely
 * there (`document.body.dataset.artistSource === 'source'`), because there is
 * no library to be complete against; the card is just artwork + title.
 *
 * `track_completion` arrives in two shapes, and both are handled:
 *   - an OBJECT {owned_tracks,total_tracks,missing_tracks} -> "3/12" style
 *   - a NUMBER percentage -> "75%" style
 * The 75% threshold splits 'nearly_complete' from 'partial' in both.
 */
export function completionOverlay(
  release: DiscographyRelease,
  isSourceArtist: boolean,
): CompletionOverlay | null {
  if (isSourceArtist) return null;

  const completion = release.track_completion;

  if (release.owned === null || completion === 'checking') {
    return { className: 'checking', label: 'Checking...' };
  }

  if (!release.owned) {
    return { className: 'missing', label: 'Missing' };
  }

  if (completion && typeof completion === 'object') {
    const tc = completion as {
      owned_tracks?: number;
      total_tracks?: number;
      missing_tracks?: number;
    };
    const ownedTracks = tc.owned_tracks || 0;
    const totalTracks = tc.total_tracks || 0;
    const missingTracks = tc.missing_tracks || 0;
    if (missingTracks === 0) return { className: 'completed', label: '✓ Owned' };
    const pct = totalTracks > 0 ? Math.round((ownedTracks / totalTracks) * 100) : 0;
    return {
      className: pct >= 75 ? 'nearly_complete' : 'partial',
      label: `${ownedTracks}/${totalTracks}`,
    };
  }

  // Percentage form. `|| 100` is deliberate: a missing/zero completion on an
  // owned release is treated as fully owned, matching the vanilla.
  const pct = (completion as number) || 100;
  if (pct === 100) return { className: 'completed', label: '✓ Owned' };
  return { className: pct >= 75 ? 'nearly_complete' : 'partial', label: `${pct}%` };
}

/**
 * Display year.
 *
 * Prefers a leading 4-digit year in `release_date`, else parses the date, else
 * falls back to `release.year`. Both parsed paths are sanity-bounded to
 * 1900 < year <= currentYear + 1 — a nonsense date renders no year rather than
 * a wrong one. The +1 allows announced-but-unreleased records.
 */
export function releaseYearText(release: DiscographyRelease, now: Date = new Date()): string {
  const limit = now.getFullYear() + 1;
  const inRange = (y: number) => Boolean(y) && !Number.isNaN(y) && y > 1900 && y <= limit;

  const raw = release.release_date;
  if (typeof raw === 'string' && raw) {
    const match = /^(\d{4})/.exec(raw);
    if (match) {
      const year = parseInt(match[1], 10);
      if (inRange(year)) return String(year);
    } else {
      const year = new Date(raw).getFullYear();
      if (inRange(year)) return String(year);
    }
  }

  return release.year ? String(release.year) : '';
}

/** Lazy background: the vanilla set data-bg-src and let an IntersectionObserver
 *  swap it in. Empty/whitespace urls are skipped so no observer work is queued. */
export function releaseBackgroundSrc(release: DiscographyRelease): string | null {
  const url = release.image_url;
  return typeof url === 'string' && url.trim() !== '' ? url : null;
}

/** Only `true` shows the badge — not any truthy value. */
export function isExplicit(release: DiscographyRelease): boolean {
  return release.explicit === true;
}

export function musicbrainzReleaseUrl(release: DiscographyRelease): string | null {
  const id = release.musicbrainz_release_id;
  return id ? `https://musicbrainz.org/release/${id}` : null;
}
