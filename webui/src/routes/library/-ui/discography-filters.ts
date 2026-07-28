/** ldp-04: the legacy artist page's discography filter bar, ported as pure
 *  functions so both Library V2 render modes (table + legacy cards) and any
 *  future consumer share one classification.
 *
 *  `classifyReleaseContent` is a verbatim port of `_classifyReleaseContent`
 *  in `webui/static/library.js` — issue #877 made that the single source of
 *  truth precisely so the artist page and the Download Discography modal
 *  could not drift apart, and a re-derived V2 copy would reopen that gap.
 */

export interface ClassifiableRelease {
  title?: string | null;
  name?: string | null;
  album_type?: string | null;
}

export interface ReleaseContentFlags {
  isLive: boolean;
  isCompilation: boolean;
  isFeatured: boolean;
}

const LIVE_PATTERN = /\b(live)\b|\(live[^)]*\)|\[live[^\]]*\]/i;
const COMPILATION_PATTERN = /\b(greatest hits|best of|collection|anthology|essential)\b/i;
const FEATURED_PATTERN = /\(?\bfeat\.?\s|\bft\.?\s|\bfeaturing\b/i;

export function classifyReleaseContent(release: ClassifiableRelease): ReleaseContentFlags {
  const t = release.title || release.name || '';
  return {
    isLive: LIVE_PATTERN.test(t),
    isCompilation: release.album_type === 'compilation' || COMPILATION_PATTERN.test(t),
    isFeatured: FEATURED_PATTERN.test(t),
  };
}

export type DiscographyOwnership = 'all' | 'owned' | 'missing';

export interface DiscographyFilterState {
  /** `Show`: whole release-type sections. */
  categories: { albums: boolean; eps: boolean; singles: boolean };
  /** `Include`: content types inside the visible sections. */
  content: { live: boolean; compilations: boolean; featured: boolean };
  /** `Status`: single-select. */
  ownership: DiscographyOwnership;
}

export const DEFAULT_DISCOGRAPHY_FILTERS: DiscographyFilterState = {
  categories: { albums: true, eps: true, singles: true },
  content: { live: true, compilations: true, featured: true },
  ownership: 'all',
};

/** Whether one release survives the `Include`/`Status` filters. `owned` is
 *  `null` while its ownership is still being determined — legacy never hides
 *  a card in that state, so neither do we. */
export function passesDiscographyFilters(
  release: ClassifiableRelease,
  state: DiscographyFilterState,
  owned: boolean | null,
): boolean {
  const flags = classifyReleaseContent(release);
  if (!state.content.live && flags.isLive) return false;
  if (!state.content.compilations && flags.isCompilation) return false;
  if (!state.content.featured && flags.isFeatured) return false;
  if (state.ownership !== 'all' && owned !== null) {
    if (state.ownership === 'owned' && !owned) return false;
    if (state.ownership === 'missing' && owned) return false;
  }
  return true;
}
