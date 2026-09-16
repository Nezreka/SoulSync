/**
 * Because You Listen To — one shelf per seed artist.
 *
 * ── What changed, and why ───────────────────────────────────────────────────
 *
 * The first port was a faithful transcription of `_renderByltSection` (10365),
 * `_renderByltTrackCard` (10382) and `loadBecauseYouListenTo` (10403): ten
 * album-sized tiles, no identity on the card, and a click that resolved the
 * ALBUM by name string. On real data that rendered ten copies of one album's
 * artwork under two nearly identical headings, and clicking a track opened
 * something other than the track.
 *
 * The endpoint now serves one stored generation: every section carries a seed
 * identity, a truthful reason, a presentation flag, resolved/unavailable
 * counts, and per-track identity plus library status. This module is the view
 * model for that payload — pure, so the rendering decisions are testable
 * without a DOM.
 *
 * ── The container does not exist in the markup ──────────────────────────────
 *
 * index.html ships no placeholder for this section. The vanilla loader CREATED
 * `#discover-bylt-sections` and inserted it after the release-radar section,
 * bailing entirely if that anchor was missing. The layout pass still treats the
 * container as one slot, so the shelves move together — hence `BYLT_ANCHOR_ID`
 * and its test.
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
 * rendering a placeholder. This is one of the few sections that opts OUT of the
 * shared empty state.
 */
export const BYLT_RENDERS_EMPTY_STATE = false;
export const BYLT_LOADING_MESSAGE = '';

export type ByltPresentation = 'full' | 'compact' | 'insufficient';

export interface ByltTrack {
  /** Provider id. Carried so an action never has to guess from a title. */
  id?: string;
  /** NOTE: `name` and `artist` — NOT `track_name`/`artist_name`. */
  name?: string;
  artist?: string;
  album?: string;
  image_url?: string;
  duration_ms?: number;
  source?: string;
  /** 'direct' | 'genre' — how this track got here. */
  relation?: string;
  /** The related artist, or the shared genre. */
  relation_detail?: string;
  /** Already in the library. Shown, never hidden: the shelf may legitimately
   *  contain something you own, but calling it new would be a lie. */
  owned?: boolean;
  library_track_id?: number | string | null;
  spotify_track_id?: string | null;
  itunes_track_id?: string | null;
  deezer_track_id?: string | null;
  track_data_json?: unknown;
}

export interface ByltReason {
  kind?: string;
  label?: string;
  evidence?: string[];
}

export interface ByltSection {
  /** Stable seed identity. NEVER an ordinal. */
  seed_key?: string;
  artist_name?: string;
  artist_image?: string;
  reason?: ByltReason;
  presentation?: ByltPresentation;
  requested?: number;
  resolved?: number;
  unavailable?: number;
  unavailable_reasons?: Record<string, number>;
  legacy?: boolean;
  tracks?: ByltTrack[];
}

export interface ByltPayload {
  success?: boolean;
  sections?: ByltSection[];
  generation_id?: string | null;
  generated_at?: string | null;
  source?: string | null;
  /** 'ok' | 'empty' | 'stale' | 'failed' | 'legacy' */
  status?: string;
  error?: string | null;
  history_scope?: string;
  history_note?: string | null;
  legacy?: boolean;
}

/** `data.sections || []` (10425). */
export function byltSections(data: ByltPayload | null | undefined): ByltSection[] {
  return data?.sections ?? [];
}

/**
 * The generation this payload came from.
 *
 * It goes into the client query key, so a regenerated set is a different
 * cache entry rather than a stale one served for the rest of the session.
 */
export function byltGenerationId(data: ByltPayload | null | undefined): string {
  return data?.generation_id ?? 'none';
}

/** The header image is omitted entirely when absent — no placeholder (10370). */
export function byltHasArtistImage(section: ByltSection): boolean {
  return Boolean(section.artist_image);
}

/**
 * A shelf's React key.
 *
 * The seed identity when there is one; the index only as a last resort. Keying
 * by index is what let a rank change swap two shelves' contents under their
 * headings.
 */
export function byltShelfKey(section: ByltSection, index: number): string {
  return section.seed_key || `${section.artist_name ?? ''}:${index}`;
}

/** Each shelf's grid id (10377) — the index, not the artist name. */
export function byltCarouselId(idx: number): string {
  return `bylt-carousel-${idx}`;
}

/**
 * Whether a shelf renders its rows.
 *
 * `section.tracks.map(...)` at 10438 is UNGUARDED — a section without a
 * `tracks` array throws there and aborts the whole `onRendered` loop, taking
 * every later shelf's cards with it. The port guards; the shelves are
 * independent and one bad payload should cost one shelf.
 */
export function byltTracks(section: ByltSection): ByltTrack[] {
  return Array.isArray(section.tracks) ? section.tracks : [];
}

/** '3:07'. EMPTY for an unknown length — '0:00' claims a fact we do not have. */
export function byltDuration(ms: number | undefined): string {
  const value = Number(ms) || 0;
  if (value <= 0) return '';
  const minutes = Math.floor(value / 60000);
  const seconds = Math.floor((value % 60000) / 1000);
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export interface ByltRow {
  key: string;
  title: string;
  artist: string;
  album: string;
  duration: string;
  cover: string | null;
  /** The card shows a placeholder only without art. */
  showPlaceholder: boolean;
  owned: boolean;
  /** Short, truthful, and only when we actually know it. */
  why: string;
  /** A track with no provider id can only be acted on by name. */
  hasIdentity: boolean;
}

/**
 * One row of a shelf.
 *
 * Reads `name` and `artist`. Every other card renderer on this page uses
 * `name`/`artist_name`, so a shared card type would quietly blank the second
 * line here.
 */
export function byltRow(track: ByltTrack, index: number): ByltRow {
  const detail = (track.relation_detail ?? '').trim();
  let why = '';
  if (track.relation === 'direct' && detail) why = `Similar artist: ${detail}`;
  else if (track.relation === 'genre' && detail) why = `Shares ${detail}`;
  return {
    key: track.id || `${track.name ?? ''}:${index}`,
    title: track.name ?? '',
    artist: track.artist ?? '',
    album: track.album ?? '',
    duration: byltDuration(track.duration_ms),
    cover: track.image_url ?? null,
    showPlaceholder: !track.image_url,
    owned: Boolean(track.owned),
    why,
    hasIdentity: Boolean(track.id),
  };
}

/**
 * The shelf's one-line reason.
 *
 * Falls back to the plain eyebrow rather than inventing an explanation. No
 * provider is ever quoted — we do not have one.
 */
export function byltReasonLabel(section: ByltSection): string {
  const label = section.reason?.label?.trim();
  if (label) return label;
  return section.artist_name ? `From your ${section.artist_name} listening` : '';
}

/**
 * "2 of 10 no longer available" — said out loud rather than silently dropped.
 *
 * The old endpoint resolved saved ids against the newest 5,000 pool rows and
 * dropped the rest with no trace, so a ten-track shelf could render three.
 */
export function byltUnavailableNote(section: ByltSection): string {
  const missing = Number(section.unavailable) || 0;
  if (missing <= 0) return '';
  const requested = Number(section.requested) || 0;
  const reasons = section.unavailable_reasons ?? {};
  if (reasons['source-unsupported']) {
    return `${missing} of ${requested} can't be shown for the current metadata source`;
  }
  return `${missing} of ${requested} are no longer available`;
}

/** A shelf below the quality bar gets a truthful module, never a full row. */
export function byltIsCompact(section: ByltSection): boolean {
  return (section.presentation ?? 'compact') !== 'full';
}

export function byltIsInsufficient(section: ByltSection): boolean {
  return section.presentation === 'insufficient' || byltTracks(section).length === 0;
}

/**
 * The banner above the shelves, when there is something honest to say.
 *
 * `stale` means the last run failed and this is the previous good set; `failed`
 * means there is nothing to show AND the run broke. Neither is "you have no
 * recommendations", which is what the old handler said for both.
 */
export function byltStatusNote(data: ByltPayload | null | undefined): string {
  if (!data) return '';
  if (data.status === 'stale') return 'Showing your last good set — the newest run failed.';
  if (data.status === 'failed') return "Couldn't build your recommendations on the last run.";
  if (data.status === 'legacy') return '';
  return '';
}

/** Whether the shelves came from pre-generation ordinal rows. */
export function byltIsLegacy(data: ByltPayload | null | undefined): boolean {
  return Boolean(data?.legacy);
}

/**
 * How long a fetched set stays fresh on the client.
 *
 * The first port used `Number.POSITIVE_INFINITY` for both stale and gc time, so
 * a tab left open served the same shelves forever no matter how many times the
 * scanner regenerated them. Fifteen minutes is a policy; infinity is the
 * absence of one. The server key also carries the generation id, so a
 * regenerated set is a different cache entry rather than a stale hit.
 */
export const BYLT_STALE_MS = 15 * 60 * 1000;

/**
 * A shelf row in the shape the download, sync and playback converters read.
 *
 * These rows use `name`/`artist`/`album`; `discoverTrackToSpotifyShape` reads
 * `track_name`/`artist_name`/`album_name`. Handing it a shelf row directly
 * produced "Unknown Artist" and an empty album in the download dialog — the
 * same class of loss the Daily Mix fix chased. `track_data_json` is passed
 * through untouched, because when it exists it is already the richest shape.
 */
export function byltTrackToRow(track: ByltTrack): Record<string, unknown> {
  return {
    id: track.id,
    spotify_track_id: track.spotify_track_id ?? undefined,
    itunes_track_id: track.itunes_track_id ?? undefined,
    deezer_track_id: track.deezer_track_id ?? undefined,
    track_name: track.name,
    artist_name: track.artist,
    album_name: track.album,
    album_cover_url: track.image_url,
    duration_ms: track.duration_ms ?? 0,
    track_data_json: track.track_data_json,
  };
}

/** Every row of a shelf, converter-ready. */
export function byltShelfRows(section: ByltSection): Record<string, unknown>[] {
  return byltTracks(section).map(byltTrackToRow);
}

/** The download/sync identity for one shelf. Seed-scoped, never ordinal. */
export function byltShelfVirtualId(section: ByltSection): string {
  const seed = (section.seed_key || section.artist_name || 'shelf').replace(/[^a-zA-Z0-9]+/g, '_');
  return `discover_bylt_${seed}`;
}

export function byltShelfTitle(section: ByltSection): string {
  return `Because you listen to ${section.artist_name ?? ''}`.trim();
}
