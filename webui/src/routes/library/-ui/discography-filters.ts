/** The artist page's discography, sorted into sections and filtered, as pure
 *  functions so both Library render modes (table + cards) and any future
 *  consumer share one classification.
 *
 *  `classifyReleaseContent` is a verbatim port of `_classifyReleaseContent`
 *  in `webui/static/library.js` (issue #877 made that the single source of
 *  truth so the artist page and the Download Discography modal could not
 *  drift apart). It still feeds the card's data attributes and Featured.
 *
 *  #2055: live records, compilations, soundtracks, remixes, demos and spoken
 *  word used to sit among the studio albums, with two Include toggles to hide
 *  some of them. Each is now a section of its own under Albums, EPs and
 *  Singles, and the Show pills are those sections.
 */

export interface ClassifiableRelease {
  title?: string | null;
  name?: string | null;
  album_type?: string | null;
  /** MusicBrainz release-group secondary types ('live', 'soundtrack', ...),
   *  as a provider listing carries them */
  secondary_types?: readonly string[] | null;
  /**
   * A library release's kinds, when somebody has said: the types it was filed
   * with, or its MusicBrainz release group's. An array, even an empty one, is
   * an answer ("a plain studio release"); null is "nobody has said", and only
   * then is the title read for a guess.
   */
  release_types?: readonly string[] | null;
}

export interface ReleaseContentFlags {
  isLive: boolean;
  isCompilation: boolean;
  isFeatured: boolean;
}

const LIVE_PATTERN = /\b(live)\b|\(live[^)]*\)|\[live[^\]]*\]/i;
const COMPILATION_PATTERN = /\b(greatest hits|best of|collection|anthology|essential)\b/i;
const FEATURED_PATTERN = /\(?\bfeat\.?\s|\bft\.?\s|\bfeaturing\b/i;
const SOUNDTRACK_PATTERN =
  /\bsoundtrack\b|\boriginal (?:[a-z]+ ){0,3}score\b|\bmotion picture\b|\bO\.?S\.?T\.?(?=$|[\s)\]:-])|\bmusic from (?:the|and inspired)\b/i;
const REMIX_PATTERN = /\bremix(?:es|ed)?\b|\bdj[- ]mix\b|\bmixed by\b/i;
const SPOKEN_PATTERN = /\b(?:audiobook|spoken word|interviews?)\b/i;

export function classifyReleaseContent(release: ClassifiableRelease): ReleaseContentFlags {
  const t = release.title || release.name || '';
  return {
    isLive: LIVE_PATTERN.test(t),
    isCompilation: release.album_type === 'compilation' || COMPILATION_PATTERN.test(t),
    isFeatured: FEATURED_PATTERN.test(t),
  };
}

/** The three sections a release's own type gives it. */
export const BASE_SECTIONS = ['albums', 'eps', 'singles'] as const;
export type BaseSection = (typeof BASE_SECTIONS)[number];

/** Every section, in the order the page lists them: studio work first. */
export const DISCOGRAPHY_SECTIONS = [
  ...BASE_SECTIONS,
  'live',
  'compilations',
  'soundtracks',
  'remixes',
  'demos',
  'spoken',
] as const;
export type DiscographySection = (typeof DISCOGRAPHY_SECTIONS)[number];

export const SECTION_LABELS: Record<DiscographySection, string> = {
  albums: 'Albums',
  eps: 'EPs',
  singles: 'Singles',
  live: 'Live',
  compilations: 'Compilations',
  soundtracks: 'Soundtracks',
  remixes: 'Remixes',
  demos: 'Demos',
  spoken: 'Spoken word',
};

/**
 * MusicBrainz secondary type -> section, most telling first. A live
 * compilation is a live record and a soundtrack compilation a soundtrack:
 * "compilation" only says how it was put together, so it is asked last.
 * Mixtape/Street is left where its primary type puts it: a mixtape is an
 * album in everything but name.
 */
const TYPE_SECTIONS: ReadonlyArray<[DiscographySection, readonly string[]]> = [
  ['spoken', ['spokenword', 'interview', 'audiobook', 'audio drama']],
  ['soundtracks', ['soundtrack']],
  ['live', ['live']],
  ['remixes', ['remix', 'dj-mix']],
  ['demos', ['demo']],
  ['compilations', ['compilation']],
];

/**
 * Which section a release belongs in.
 *
 * `base` is where its own type puts it. A single stays a single whatever
 * else it is: a live B-side or a remix single is what Singles is for. An
 * album or EP moves when it is known to be something else, by its types when
 * anybody has said (a MusicBrainz listing always has, a library release once
 * its release group was looked up), else by its album type, else by the
 * title. The title is only a guess, which is why it is asked last: a studio
 * album called "Live Through This" stays in Albums once its types are known.
 */
export function releaseSection(
  release: ClassifiableRelease,
  base: BaseSection,
  musicbrainz = false,
): DiscographySection {
  if (base === 'singles') return base;
  const told = Array.isArray(release.release_types)
    ? release.release_types
    : musicbrainz
      ? (release.secondary_types ?? [])
      : release.secondary_types?.length
        ? release.secondary_types
        : null;
  if (told) {
    const types = told.map((s) => String(s).trim().toLowerCase());
    for (const [section, names] of TYPE_SECTIONS) {
      if (types.some((type) => names.includes(type))) return section;
    }
  }
  const type = String(release.album_type ?? '').toLowerCase();
  if (type === 'compilation') return 'compilations';
  if (type === 'live') return 'live';
  if (told) return base;
  const title = release.title || release.name || '';
  if (SPOKEN_PATTERN.test(title)) return 'spoken';
  if (SOUNDTRACK_PATTERN.test(title)) return 'soundtracks';
  if (LIVE_PATTERN.test(title)) return 'live';
  if (REMIX_PATTERN.test(title)) return 'remixes';
  if (COMPILATION_PATTERN.test(title)) return 'compilations';
  return base;
}

/** Releases by section, each list in the order it came in (albums before
 *  EPs before singles). Every section is present, empty or not. */
export function groupReleases<T>(
  lists: Readonly<Record<BaseSection, readonly T[]>>,
  describe: (item: T) => ClassifiableRelease,
  musicbrainz = false,
): Record<DiscographySection, T[]> {
  const grouped = Object.fromEntries(
    DISCOGRAPHY_SECTIONS.map((section) => [section, [] as T[]]),
  ) as Record<DiscographySection, T[]>;
  for (const base of BASE_SECTIONS) {
    for (const item of lists[base]) {
      grouped[releaseSection(describe(item), base, musicbrainz)].push(item);
    }
  }
  return grouped;
}

export type DiscographyOwnership = 'all' | 'owned' | 'missing';

export interface DiscographyFilterState {
  /** `Show`: whole sections. */
  categories: Record<DiscographySection, boolean>;
  /** `Include`: content inside the visible sections. */
  content: { featured: boolean };
  /** `Status`: single-select. */
  ownership: DiscographyOwnership;
  /** a MusicBrainz discography: its types classify, never the title */
  musicbrainz?: boolean;
  /** the non-studio hide MusicBrainz starts with (never one the user chose) */
  mbDeclutter?: boolean;
}

/**
 * The sections MusicBrainz starts with hidden: everything that is not studio
 * work, EXCEPT compilations (which 3.6.1's artist page kept, under their own
 * toggle). Mirrors `_NON_STUDIO_SECONDARY_TYPES` in core/musicbrainz_search.
 */
export const NON_STUDIO_SECTIONS: readonly DiscographySection[] = [
  'live',
  'soundtracks',
  'remixes',
  'demos',
  'spoken',
];

/**
 * MusicBrainz lists an artist's WHOLE catalogue (live, soundtracks, remixes),
 * which buries the studio albums, so there (and only there) the non-studio
 * sections start hidden, as on 3.6.1's artist page. Every other source is a
 * clean commercial catalogue and keeps the show-all default.
 */
export function applyMusicBrainzDeclutter(
  state: DiscographyFilterState,
  source: string | null | undefined,
): DiscographyFilterState {
  if (String(source ?? '').toLowerCase() !== 'musicbrainz') return state;
  const categories = { ...state.categories };
  for (const section of NON_STUDIO_SECTIONS) categories[section] = false;
  return { ...state, categories, musicbrainz: true, mbDeclutter: true };
}

export const DEFAULT_DISCOGRAPHY_FILTERS: DiscographyFilterState = {
  categories: Object.fromEntries(DISCOGRAPHY_SECTIONS.map((s) => [s, true])) as Record<
    DiscographySection,
    boolean
  >,
  content: { featured: true },
  ownership: 'all',
};

/** Whether one release, already placed in `section`, is on the page. `owned`
 *  is `null` while its ownership is still being determined: legacy never
 *  hides a card in that state, so neither do we. */
export function passesDiscographyFilters(
  release: ClassifiableRelease,
  state: DiscographyFilterState,
  owned: boolean | null,
  section: DiscographySection,
): boolean {
  if (!state.categories[section]) {
    // the MusicBrainz non-studio hide is a default the user never chose: it
    // never buries a release they own (3.6.1)
    const kept =
      Boolean(state.mbDeclutter) && owned === true && NON_STUDIO_SECTIONS.includes(section);
    if (!kept) return false;
  }
  if (!state.content.featured && classifyReleaseContent(release).isFeatured) return false;
  if (state.ownership !== 'all' && owned !== null) {
    if (state.ownership === 'owned' && !owned) return false;
    if (state.ownership === 'missing' && owned) return false;
  }
  return true;
}
