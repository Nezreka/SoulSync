import type { DiscographyBucket, DiscographyRelease } from './-artist-detail.types';

/**
 * Discography filter state, ported 1:1 from `discographyFilterState`.
 *
 * The vanilla version filtered by mutating `card.style.display` on DOM nodes it
 * had already rendered, reading `data-is-live` / `data-is-compilation` /
 * `data-is-featured` attributes back off them. Here the same decisions are made
 * on the release objects before render, which is why the flags are recomputed
 * rather than read from markup — see releaseFlags().
 */
export interface DiscographyFilterState {
  categories: Record<DiscographyBucket, boolean>;
  content: { live: boolean; compilations: boolean; featured: boolean };
  ownership: 'all' | 'owned' | 'missing';
  /**
   * True only when the automatic MusicBrainz declutter is in effect. It is NOT
   * a user setting, and it changes the rules: see ownedExempt below.
   */
  mbDeclutter: boolean;
}

/**
 * Non-studio MusicBrainz release-group secondary types, EXCLUDING Compilation
 * (which has its own toggle). Mirrors `_NON_STUDIO_SECONDARY_TYPES` in
 * core/musicbrainz_search so the backend and UI agree — keep them in step.
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

/** Neutral show-all. The MB declutter is applied AFTER, once the source is known. */
export function defaultFilterState(): DiscographyFilterState {
  return {
    categories: { albums: true, eps: true, singles: true },
    content: { live: true, compilations: true, featured: true },
    ownership: 'all',
    mbDeclutter: false,
  };
}

export function isMusicBrainzDiscography(source: string | null | undefined): boolean {
  return String(source ?? '').toLowerCase() === 'musicbrainz';
}

/**
 * MusicBrainz lists an artist's WHOLE catalogue (live, soundtracks, remixes),
 * which buries the studio albums — so non-studio content is hidden by default
 * there and ONLY there. Every other source is already a clean commercial
 * catalogue, so its default is untouched.
 *
 * Compilations deliberately stay shown; they have their own toggle.
 */
export function applyMusicBrainzDeclutter(
  state: DiscographyFilterState,
  source: string | null | undefined,
): DiscographyFilterState {
  if (!isMusicBrainzDiscography(source)) return state;
  return { ...state, content: { ...state.content, live: false }, mbDeclutter: true };
}

/** The toggle governs the broader non-studio set on MB, so it is relabelled. */
export function liveToggleLabel(state: DiscographyFilterState): string {
  return state.mbDeclutter ? 'Non-Studio' : 'Live';
}

export interface ReleaseFlags {
  isLive: boolean;
  isCompilation: boolean;
  isFeatured: boolean;
}

/**
 * Title-based classification, ported verbatim from `_classifyReleaseContent`.
 *
 * That function is deliberately SHARED between artist detail and the Download
 * Discography modal (#877) so the two can never drift apart on what counts as
 * live/compilation/featured. Keep these patterns identical to it — a release
 * classified one way in the modal and another way on the page is the exact bug
 * that shared classifier exists to prevent.
 *
 * Note there are no `is_live` / `is_compilation` fields on a release: the
 * backend does not send them. Classification is entirely client-side, off the
 * title, plus `album_type === 'compilation'`.
 */
const LIVE_PATTERN = /\b(live)\b|\(live[^)]*\)|\[live[^\]]*\]/i;
const COMPILATION_PATTERN = /\b(greatest hits|best of|collection|anthology|essential)\b/i;
const FEATURED_PATTERN = /\(?\bfeat\.?\s|\bft\.?\s|\bfeaturing\b/i;

export function classifyReleaseContent(release: DiscographyRelease): ReleaseFlags {
  // `title` on the artist page, `name` in the download modal — both are checked
  // because the same classifier serves both shapes.
  const title = String(release.title ?? release.name ?? '');
  return {
    isLive: LIVE_PATTERN.test(title),
    isCompilation: release.album_type === 'compilation' || COMPILATION_PATTERN.test(title),
    isFeatured: FEATURED_PATTERN.test(title),
  };
}

/**
 * The three content flags for one release.
 *
 * On MusicBrainz, `secondary_types` is authoritative and REPLACES the
 * title-based live guess — that is what stops a studio album called "Live
 * Through This" being hidden, and it also catches soundtrack/remix/demo which a
 * title guess never would. Off MusicBrainz the title heuristic governs.
 */
export function releaseFlags(release: DiscographyRelease, isMusicBrainz: boolean): ReleaseFlags {
  const base = classifyReleaseContent(release);
  if (!isMusicBrainz) return base;

  const secondary = Array.isArray(release.secondary_types)
    ? (release.secondary_types as unknown[]).map((s) => String(s).trim().toLowerCase())
    : [];
  return { ...base, isLive: secondary.some((s) => NON_STUDIO_SECONDARY.has(s)) };
}

/**
 * Whether one release is hidden. Mirrors the vanilla order exactly: content
 * filters first, then ownership, and ownership is only consulted when the
 * content filters did not already hide it.
 */
export function isReleaseHidden(
  release: DiscographyRelease,
  flags: ReleaseFlags,
  state: DiscographyFilterState,
): boolean {
  // On MB the non-studio hide is an automatic default the user never chose, so
  // it must never bury something they OWN. Off MB every toggle is user-driven
  // and is respected as-is — no exemption, which keeps non-MB behaviour
  // byte-identical to before this rule existed.
  const ownedExempt = state.mbDeclutter && release.owned === true;

  let hidden = false;
  if (!ownedExempt) {
    if (!state.content.live && flags.isLive) hidden = true;
    if (!state.content.compilations && flags.isCompilation) hidden = true;
    if (!state.content.featured && flags.isFeatured) hidden = true;
  }

  // `owned === null` means the completion check is still running. Those cards
  // are never hidden by the ownership filter — the filter re-runs when the
  // stream resolves them.
  //
  // The `!hidden` short-circuit mirrors the vanilla shape but is not
  // observable: this block only ever SETS hidden to true, so entering it while
  // already hidden cannot change the answer. Removing it survives mutation
  // testing for that reason — an equivalent mutant, not a missing test.
  if (!hidden && state.ownership !== 'all' && release.owned !== null) {
    if (state.ownership === 'owned' && !release.owned) hidden = true;
    if (state.ownership === 'missing' && release.owned) hidden = true;
  }

  return hidden;
}

export interface SectionCounts {
  visible: number;
  owned: number;
  missing: number;
}

/**
 * The per-section counts, computed over VISIBLE cards only — the vanilla stats
 * line reflected the filtered view, not the whole bucket.
 *
 * A release still being checked (`owned === null`) counts toward `visible` but
 * toward neither owned nor missing, so the two do not necessarily sum.
 */
export function sectionCounts(
  releases: DiscographyRelease[],
  isMusicBrainz: boolean,
  state: DiscographyFilterState,
): SectionCounts {
  const counts: SectionCounts = { visible: 0, owned: 0, missing: 0 };
  for (const release of releases) {
    if (isReleaseHidden(release, releaseFlags(release, isMusicBrainz), state)) continue;
    counts.visible += 1;
    if (release.owned === true) counts.owned += 1;
    else if (release.owned === false) counts.missing += 1;
  }
  return counts;
}
