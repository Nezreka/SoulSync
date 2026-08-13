/**
 * Because You Listen To — one shelf per seed artist.
 *
 * Transcribed from `_renderByltSection` (10365), `_renderByltTrackCard` (10382)
 * and `loadBecauseYouListenTo` (10403) — read end to end.
 *
 * ── The container does not exist in the markup ──────────────────────────────
 *
 * index.html ships no placeholder for this section. The loader CREATES
 * `#discover-bylt-sections` and inserts it after the release-radar section,
 * bailing entirely if that anchor is missing. The layout pass then treats the
 * container as one slot (`{ id: 'discover-bylt-sections' }`), so the shelves
 * move together.
 *
 * That anchor dependency is easy to lose in a React port where every section is
 * declarative — hence `BYLT_ANCHOR_ID` and its test.
 */

/** The container this section creates for itself. */
export const BYLT_CONTAINER_ID = 'discover-bylt-sections';

/** Inserted after this element's `.discover-section`; absent → render nothing. */
export const BYLT_ANCHOR_ID = 'discover-release-radar';

export const BYLT_SUBTITLE = 'Because you listen to';

/**
 * `renderEmptyState: false`, `loadingMessage: ''` (10429-10430).
 *
 * Deliberate: with nothing to show the container stays blank rather than
 * rendering a placeholder, matching the original no-op. This is one of the few
 * sections that opts OUT of the shared empty state.
 */
export const BYLT_RENDERS_EMPTY_STATE = false;
export const BYLT_LOADING_MESSAGE = '';

export interface ByltTrack {
  /** NOTE: `name` and `artist` — NOT `track_name`/`artist_name`. */
  name?: string;
  artist?: string;
  /** The album NAME (web_server 32468) — the click-to-download resolve
   *  needs it; the vanilla card just never read it. */
  album?: string;
  image_url?: string;
}

export interface ByltSection {
  artist_name?: string;
  artist_image?: string;
  tracks?: ByltTrack[];
}

/** `data.sections || []` (10425). */
export function byltSections(data: { sections?: ByltSection[] } | null | undefined): ByltSection[] {
  return data?.sections ?? [];
}

/** The header image is omitted entirely when absent — no placeholder (10370). */
export function byltHasArtistImage(section: ByltSection): boolean {
  return Boolean(section.artist_image);
}

export interface ByltCard {
  title: string;
  subtitle: string;
  image: string | null;
  /** The placeholder is rendered either way, and shown only without art (10390). */
  showPlaceholder: boolean;
}

/**
 * A track card (10382).
 *
 * Reads `t.name` and `t.artist`. Every other card renderer on this page uses
 * `name`/`artist_name`, so a shared card type would quietly blank the second
 * line here.
 */
export function byltTrackCard(t: ByltTrack): ByltCard {
  return {
    title: t.name ?? '',
    subtitle: t.artist ?? '',
    image: t.image_url ?? null,
    showPlaceholder: !t.image_url,
  };
}

/** Each shelf's grid id (10377) — the index, not the artist name. */
export function byltCarouselId(idx: number): string {
  return `bylt-carousel-${idx}`;
}

/**
 * Whether a shelf renders its cards.
 *
 * `section.tracks.map(...)` at 10438 is UNGUARDED — a section without a
 * `tracks` array throws there and aborts the whole `onRendered` loop, taking
 * every later shelf's cards with it. The port guards; the shelves are
 * independent and one bad payload should cost one shelf.
 */
export function byltTracks(section: ByltSection): ByltTrack[] {
  return Array.isArray(section.tracks) ? section.tracks : [];
}
