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
  /** MusicBrainz release-group secondary types ('live', 'soundtrack', ...) */
  secondary_types?: readonly string[] | null;
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
  /** a MusicBrainz discography: secondary types classify, not the title */
  musicbrainz?: boolean;
  /** the non-studio hide MusicBrainz starts with (never one the user chose) */
  mbDeclutter?: boolean;
}

/**
 * Non-studio MusicBrainz release-group secondary types, EXCLUDING Compilation
 * (which has its own toggle). Mirrors `_NON_STUDIO_SECONDARY_TYPES` in
 * core/musicbrainz_search, as 3.6.1's artist page did.
 */
export const NON_STUDIO_SECONDARY = new Set([
  'live',
  'soundtrack',
  'remix',
  'demo',
  'mixtape/street',
  'interview',
  'audiobook',
  'audio drama',
]);

/**
 * MusicBrainz lists an artist's WHOLE catalogue (live, soundtracks, remixes),
 * which buries the studio albums, so there (and only there) non-studio
 * releases start hidden, as on 3.6.1's artist page. Every other source is a
 * clean commercial catalogue and keeps the show-all default.
 */
export function applyMusicBrainzDeclutter(
  state: DiscographyFilterState,
  source: string | null | undefined,
): DiscographyFilterState {
  if (String(source ?? '').toLowerCase() !== 'musicbrainz') return state;
  return {
    ...state,
    content: { ...state.content, live: false },
    musicbrainz: true,
    mbDeclutter: true,
  };
}

/** On MusicBrainz the toggle governs the whole non-studio set. */
export function liveToggleLabel(state: DiscographyFilterState): string {
  return state.musicbrainz ? 'Non-Studio' : 'Live';
}

/**
 * On MusicBrainz, `secondary_types` REPLACES the title's live guess: a studio
 * album called "Live Through This" stays, and soundtrack / remix / demo
 * releases (which a title never tells) count as non-studio.
 */
export function releaseFlags(
  release: ClassifiableRelease,
  musicbrainz: boolean,
): ReleaseContentFlags {
  const base = classifyReleaseContent(release);
  if (!musicbrainz) return base;
  const secondary = (release.secondary_types ?? []).map((s) => String(s).trim().toLowerCase());
  return { ...base, isLive: secondary.some((s) => NON_STUDIO_SECONDARY.has(s)) };
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
  const flags = releaseFlags(release, Boolean(state.musicbrainz));
  // the MusicBrainz non-studio hide is a default the user never chose: it
  // never buries a release they own (3.6.1)
  const ownedExempt = Boolean(state.mbDeclutter) && owned === true;
  if (!ownedExempt) {
    if (!state.content.live && flags.isLive) return false;
    if (!state.content.compilations && flags.isCompilation) return false;
    if (!state.content.featured && flags.isFeatured) return false;
  }
  if (state.ownership !== 'all' && owned !== null) {
    if (state.ownership === 'owned' && !owned) return false;
    if (state.ownership === 'missing' && owned) return false;
  }
  return true;
}
