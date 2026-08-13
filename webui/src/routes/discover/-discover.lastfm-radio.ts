/**
 * Last.fm Track Radio — search a track, generate a radio playlist from it.
 *
 * Transcribed from `debouncedLastfmTrackSearch` (3240), `_runLastfmTrackSearch`
 * (3250), `selectLastfmRadioTrack` (3287), `clearLastfmRadioSelection` (3296),
 * `generateLastfmRadio` (3302), `_generateLastfmRadioFor` (3312),
 * `initializeLastfmRadioSection` (3349), `_loadLastfmRadioPlaylists` (3367) and
 * the click-outside handler (3387) — read end to end.
 *
 * ── Three pieces of dead code in this region, all verified ──────────────────
 *
 * 1. `generateLastfmRadio` (3302) is UNREACHABLE. Its only non-definition
 *    occurrence in the whole repo is the comment above it — the author's own
 *    "called by nothing now but harmless" (3301). It is not in index.html and
 *    no reachable function calls it. Not ported; see
 *    `LASTFM_PARSE_SEPARATOR` for the one piece of it worth keeping as a note.
 *
 * 2. `_lastfmRadioSelected` (3238) is declared and NEVER read or assigned —
 *    dead module state. Not ported.
 *
 * 3. The `listeners` span (3268) is built as HTML and then used ONLY as a
 *    truthiness test (3276). The `<span class="lastfm-radio-result-listeners">`
 *    it builds is never inserted, its "Nk listeners" formatting never renders,
 *    and that class does not exist in style.css or mobile.css. What actually
 *    shows is `t.listeners.toLocaleString() + ' listeners'`. Ported as the
 *    boolean it really is — see `resultShowsListeners`.
 */

/** `setTimeout(..., 400)` (3247). */
export const LASTFM_SEARCH_DEBOUNCE_MS = 400;

/**
 * `if (q.length < 2) return;` (3251).
 *
 * Checked INSIDE the debounced callback, not before it — so a one-character
 * query still schedules a timer, which then does nothing and leaves whatever
 * the dropdown was showing in place.
 */
export const LASTFM_MIN_QUERY_LENGTH = 2;

export const LASTFM_CONFIGURED_URL = '/api/lastfm/configured';
export const LASTFM_RADIO_GENERATE_URL = '/api/lastfm/radio/generate';
export const LASTFM_RADIO_PLAYLISTS_URL = '/api/discover/listenbrainz/lastfm-radio';

export function lastfmSearchUrl(query: string): string {
  return `/api/lastfm/search/tracks?q=${encodeURIComponent(query)}`;
}

export const LASTFM_GENERATE_FAILED = 'Failed to generate radio';
export const LASTFM_GENERATE_ERROR = 'Error generating Last.fm radio';

/** The card kind the shared ListenBrainz builder is called with (3379). */
export const LASTFM_RADIO_CARD_KIND = 'lastfm_radio';

/**
 * An empty query hides the dropdown IMMEDIATELY, before the debounce (3243).
 *
 * Backspacing to empty therefore clears the list at once instead of 400ms
 * later, and fires no request.
 */
export function lastfmQueryIsEmpty(raw: string | null | undefined): boolean {
  return (raw || '').trim() === '';
}

export function lastfmQueryTooShort(q: string): boolean {
  return q.length < LASTFM_MIN_QUERY_LENGTH;
}

export interface LastfmTrackResult {
  name?: string;
  artist?: string;
  image_url?: string;
  listeners?: number;
}

/**
 * `t.listeners > 0` (3268) — strictly positive.
 *
 * This is the whole of what that dead `listeners` span contributes: a boolean.
 * A track with zero (or missing) listeners shows artist alone, with no ' · '.
 */
export function resultShowsListeners(t: LastfmTrackResult): boolean {
  return (t.listeners ?? 0) > 0;
}

/**
 * The result's second line (3276).
 *
 * Uses `toLocaleString()`, NOT the "Nk" abbreviation the dead span computes.
 */
export function resultSubtitle(t: LastfmTrackResult): string {
  const artist = t.artist ?? '';
  if (!resultShowsListeners(t)) return artist;
  return `${artist} · ${(t.listeners as number).toLocaleString()} listeners`;
}

/** Empty results hide the dropdown rather than showing a "no results" row (3260). */
export function lastfmHasResults(
  data: { results?: LastfmTrackResult[] } | null | undefined,
): boolean {
  return Array.isArray(data?.results) && data.results.length > 0;
}

/**
 * The inline onclick round-trips both strings through encodeURIComponent /
 * decodeURIComponent (3272).
 *
 * That is how a track or artist containing a quote survives being embedded in
 * an HTML attribute containing a JS string literal. React has no such problem —
 * the handler takes the values directly — so the round-trip is NOT ported. It is
 * described here because its absence is deliberate, not an oversight.
 */
export const LASTFM_ONCLICK_ROUNDTRIPS_URI = true;

/**
 * The input shows `${name} — ${artist}` after a selection (3290).
 *
 * EM DASH with surrounding spaces, and `generateLastfmRadio` (dead) parsed it
 * back by splitting on that exact separator — which would break for any track
 * or artist that itself contains " — ". The React port keeps the selected track
 * as state instead of re-parsing the input, so the fragility does not carry
 * over; the separator is kept only for display.
 */
export const LASTFM_DISPLAY_SEPARATOR = ' — ';
export const LASTFM_PARSE_SEPARATOR = ' — ';

export function lastfmSelectionLabel(name: string, artist: string): string {
  return `${name}${LASTFM_DISPLAY_SEPARATOR}${artist}`;
}

/** POST body for generate (3330). */
export function lastfmGenerateBody(
  name: string,
  artist: string,
): { track_name: string; artist_name: string } {
  return { track_name: name, artist_name: artist };
}

/**
 * The generate result (3333-3339).
 *
 * A failed generate CLEARS the container before toasting, so the "Building
 * radio for…" spinner does not sit there forever next to an error toast.
 */
export type LastfmGenerateOutcome =
  | { ok: true }
  | { ok: false; clearContainer: true; message: string };

export function lastfmGenerateOutcome(
  data: { success?: boolean; error?: string } | null | undefined,
): LastfmGenerateOutcome {
  if (data?.success) return { ok: true };
  return { ok: false, clearContainer: true, message: data?.error || LASTFM_GENERATE_FAILED };
}

/**
 * The whole section hides when Last.fm is not configured (3356).
 *
 * Note the order: it bails on a failed `/configured` request WITHOUT hiding
 * (3352), so a transient error leaves whatever was already rendered rather than
 * blanking a working section.
 */
export type LastfmSectionVisibility = 'show' | 'hide' | 'leave-alone';

export function lastfmSectionVisibility(
  responseOk: boolean,
  configured: boolean | undefined,
): LastfmSectionVisibility {
  if (!responseOk) return 'leave-alone';
  return configured ? 'show' : 'hide';
}

/** An empty playlist list empties the container (3374-3377). */
export function lastfmPlaylists(
  data: { success?: boolean; playlists?: unknown[] } | null | undefined,
): unknown[] {
  if (!data?.success || !Array.isArray(data.playlists)) return [];
  return data.playlists;
}
