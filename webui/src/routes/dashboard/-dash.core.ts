/**
 * Dashboard pure core — the provider pill reducers, transcribed 1:1 from
 * enrichment.js's `update<Provider>StatusFromData` functions.
 *
 * ONE FUNCTION PER PROVIDER, deliberately. The P0 proved the 16 renderers are
 * NOT one template: three tier-selection state machines (Forms A/B/C), five
 * gate schemes, per-provider class-chain ORDER, and per-provider copy. A single
 * config-driven pill would silently flatten 14 distinct behaviours, so each
 * reducer mirrors its original body closely enough to diff side by side, and
 * shares only what is PROVEN identical (Tidal/Qobuz are byte-identical after
 * slug substitution — verified mechanically — so they share one body).
 *
 * Known quirks preserved on purpose, each tested:
 * - MusicBrainz's tier lines interpolate BARE `total` (no `|| 0`) — a missing
 *   total renders the string "undefined". Every other provider guards it.
 * - Several providers never clear tooltip-current (`null` = keep previous).
 * - JioSaavn checks `!enabled` FIRST — disabled outranks idle/running.
 * - Spotify's Free-bridge gates (#798/#887): `using_free` neutralises
 *   not-authenticated, rate-limited AND daily-budget as "stuck" states.
 */

import type {
  CurrentItem,
  EnrichProgress,
  EnrichTierProgress,
  PillView,
  ProviderStatusPayload,
} from './-dash.types';

// ── current_item accessors (object | string union) ───────────────────────────
//
// Every wave-1 provider reads `data.current_item.name` / `.type` — PROPERTY
// ACCESS. On a string payload that yields `undefined` and the vanilla falls to
// its fallback branch; it never formats the string itself. These accessors
// reproduce exactly that. The three string-item providers (Discogs,
// SimilarArtists, SoulID) do NOT use them — their originals consume
// `data.current_item` raw, and their reducers will too.

function itemName(item: CurrentItem | null | undefined): string | undefined {
  if (typeof item === 'string') return undefined;
  return item?.name;
}

function itemType(item: CurrentItem | null | undefined): string | undefined {
  if (typeof item === 'string') return undefined;
  return item?.type;
}

// ── tier helpers ─────────────────────────────────────────────────────────────

interface Tiers {
  artists: EnrichTierProgress;
  albums: EnrichTierProgress;
  tracks: EnrichTierProgress;
  artistsComplete: boolean;
  albumsComplete: boolean;
}

/** The shared preamble of every tier block, including the exact comparison:
 *  `matched >= total` on possibly-undefined values (undefined >= undefined is
 *  false, exactly as in the vanilla). */
function tiers(progress: EnrichProgress): Tiers {
  const artists = progress.artists || {};
  const albums = progress.albums || {};
  const tracks = progress.tracks || {};
  return {
    artists,
    albums,
    tracks,
    artistsComplete: (artists.matched as number) >= (artists.total as number),
    albumsComplete: (albums.matched as number) >= (albums.total as number),
  };
}

/** A guarded tier line — `X: m / t (p%)` with `|| 0` on every slot. Used by
 *  every provider EXCEPT MusicBrainz, whose bare-total lines are written out
 *  verbatim in its own reducer. */
function tierLine(label: string, tier: EnrichTierProgress): string {
  return `${label}: ${tier.matched || 0} / ${tier.total || 0} (${tier.percent || 0}%)`;
}

/**
 * Form A tier selection (the MAJORITY form — MusicBrainz, AudioDB, Deezer,
 * JioSaavn, LastFM, and Genius's two-tier variant of it):
 *   artist || (!artistsComplete && !currentType)      → Artists
 *   album  || (artistsComplete && !albumsComplete)    → Albums
 *   track  || (artistsComplete && albumsComplete)     → Tracks
 *   else                                              → Artists
 */
function formATier(t: Tiers, currentType: string | undefined): 'artists' | 'albums' | 'tracks' {
  if (currentType === 'artist' || (!t.artistsComplete && !currentType)) return 'artists';
  if (currentType === 'album' || (t.artistsComplete && !t.albumsComplete)) return 'albums';
  if (currentType === 'track' || (t.artistsComplete && t.albumsComplete)) return 'tracks';
  return 'artists';
}

/**
 * Form C tier selection (Tidal/Qobuz only):
 *   artist || (!artistsComplete && !currentType)      → Artists
 *   album  || (!albumsComplete && !currentType)       → Albums
 *   else                                              → Tracks
 */
function formCTier(t: Tiers, currentType: string | undefined): 'artists' | 'albums' | 'tracks' {
  if (currentType === 'artist' || (!t.artistsComplete && !currentType)) return 'artists';
  if (currentType === 'album' || (!t.albumsComplete && !currentType)) return 'albums';
  return 'tracks';
}

/**
 * Form B tier selection (Spotify, iTunes): explicit `.includes()` matching on a
 * `|| ''`-guarded currentType, THEN completion fallbacks, else Tracks.
 */
function formBTier(t: Tiers, currentTypeRaw: string | undefined): 'artists' | 'albums' | 'tracks' {
  const currentType = currentTypeRaw || '';
  if (currentType === 'artist') return 'artists';
  if (currentType.includes('album')) return 'albums';
  if (currentType.includes('track')) return 'tracks';
  if (!t.artistsComplete) return 'artists';
  if (!t.albumsComplete) return 'albums';
  return 'tracks';
}

const TIER_LABELS = { artists: 'Artists', albums: 'Albums', tracks: 'Tracks' } as const;

// ── MusicBrainz — Form A, no auth concept, capitalised-type current ─────────

export function musicbrainzPill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';
  else if (data.paused) stateClass = 'paused';

  let status: string;
  if (data.idle) status = 'Complete';
  else if (data.running && !data.paused) status = 'Running';
  else if (data.paused) {
    status = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
  } else status = 'Idle';

  let current: string | null;
  const name = itemName(data.current_item);
  if (data.idle) current = 'All items processed';
  else if (data.current_item && name) {
    const type = itemType(data.current_item) || 'item';
    current = `${type.charAt(0).toUpperCase() + type.slice(1)}: "${name}"`;
  } else current = 'No active matches';

  let progress: string | null = null;
  if (data.progress) {
    const t = tiers(data.progress);
    const tier = formATier(t, itemType(data.current_item));
    // Verbatim from the vanilla: MusicBrainz interpolates the BARE total — no
    // `|| 0` — so a missing total renders "undefined". Preserved; a fix here
    // would be invisible drift from the original.
    const chosen = t[tier];
    progress = `${TIER_LABELS[tier]}: ${chosen.matched || 0} / ${chosen.total} (${chosen.percent || 0}%)`;
  }

  return { stateClass, status, current, progress };
}

// ── AudioDB — MusicBrainz's shape with guarded totals ────────────────────────

export function audiodbPill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';
  else if (data.paused) stateClass = 'paused';

  let status: string;
  if (data.idle) status = 'Complete';
  else if (data.running && !data.paused) status = 'Running';
  else if (data.paused) {
    status = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
  } else status = 'Idle';

  let current: string | null;
  const name = itemName(data.current_item);
  if (data.idle) current = 'All items processed';
  else if (data.current_item && name) {
    const type = itemType(data.current_item) || 'item';
    current = `${type.charAt(0).toUpperCase() + type.slice(1)}: "${name}"`;
  } else current = 'No active matches';

  let progress: string | null = null;
  if (data.progress) {
    const t = tiers(data.progress);
    const tier = formATier(t, itemType(data.current_item));
    progress = tierLine(TIER_LABELS[tier], t[tier]);
  }

  return { stateClass, status, current, progress };
}

// ── Deezer — AudioDB minus the current-else (stale-text preserved as null) ──

export function deezerPill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';
  else if (data.paused) stateClass = 'paused';

  let status: string;
  if (data.idle) status = 'Complete';
  else if (data.running && !data.paused) status = 'Running';
  else if (data.paused) {
    status = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
  } else status = 'Idle';

  // NO final else in the vanilla — the previous text stays (stale-text quirk).
  let current: string | null = null;
  const name = itemName(data.current_item);
  if (data.idle) current = 'All items processed';
  else if (data.current_item && name) current = `Now: ${name}`;

  let progress: string | null = null;
  if (data.progress) {
    const t = tiers(data.progress);
    const tier = formATier(t, itemType(data.current_item));
    progress = tierLine(TIER_LABELS[tier], t[tier]);
  }

  return { stateClass, status, current, progress };
}

// ── JioSaavn — Deezer plus a `!enabled` gate that OUTRANKS everything ───────

export function jiosaavnPill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (!data.enabled) stateClass = 'paused';
  else if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';
  else if (data.paused) stateClass = 'paused';

  let status: string;
  if (!data.enabled) status = 'Disabled';
  else if (data.idle) status = 'Complete';
  else if (data.running && !data.paused) status = 'Running';
  else if (data.paused) {
    status = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
  } else status = 'Idle';

  let current: string | null = null;
  const name = itemName(data.current_item);
  if (!data.enabled) current = 'Enable in Settings → Advanced → Experimental';
  else if (data.idle) current = 'All items processed';
  else if (data.current_item && name) current = `Now: ${name}`;

  let progress: string | null = null;
  if (data.progress) {
    const t = tiers(data.progress);
    const tier = formATier(t, itemType(data.current_item));
    progress = tierLine(TIER_LABELS[tier], t[tier]);
  }

  return { stateClass, status, current, progress };
}

// ── The authenticated-provider family (LastFM / Tidal / Qobuz) ──────────────
//
// Shared shape: paused → no-auth → complete → active, NO yield_reason, a
// "connect/add key" copy when unauthenticated, a Pending line in place of tiers
// while unauthenticated (only when `data.progress` exists — matching the
// vanilla's outer guard), and NO current-else.

function authedPill(
  data: ProviderStatusPayload,
  connectCopy: string,
  pickTier: (t: Tiers, currentType: string | undefined) => 'artists' | 'albums' | 'tracks',
): PillView {
  const notAuthenticated = data.authenticated === false;

  let stateClass: PillView['stateClass'] = null;
  if (data.paused) stateClass = 'paused';
  else if (notAuthenticated) stateClass = 'no-auth';
  else if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';

  let status: string;
  if (data.paused) status = 'Paused';
  else if (notAuthenticated) status = 'Not Authenticated';
  else if (data.idle) status = 'Complete';
  else if (data.running) status = 'Running';
  else status = 'Idle';

  let current: string | null = null;
  const name = itemName(data.current_item);
  if (data.paused) current = notAuthenticated ? connectCopy : 'Click to resume';
  else if (notAuthenticated) current = connectCopy;
  else if (data.idle) current = 'All items processed';
  else if (data.current_item && name) current = `Now: ${name}`;

  let progress: string | null = null;
  if (data.progress) {
    if (notAuthenticated) {
      progress = `Pending: ${data.stats?.pending || 0} items`;
    } else {
      const t = tiers(data.progress);
      const tier = pickTier(t, itemType(data.current_item));
      progress = tierLine(TIER_LABELS[tier], t[tier]);
    }
  }

  return { stateClass, status, current, progress };
}

/** LastFM — the authed family with Form A tiers and API-key copy. */
export function lastfmPill(data: ProviderStatusPayload): PillView {
  return authedPill(data, 'Add Last.fm API key in Settings to enrich', formATier);
}

/** Tidal — Form C tiers. Byte-identical to Qobuz in the vanilla (verified by
 *  slug-substitution diff), so both share this body. */
export function tidalPill(data: ProviderStatusPayload): PillView {
  return authedPill(data, 'Connect Tidal in Settings to enrich', formCTier);
}

/** Qobuz — see tidalPill. */
export function qobuzPill(data: ProviderStatusPayload): PillView {
  return authedPill(data, 'Connect Qobuz in Settings to enrich', formCTier);
}

// ── Spotify — the outlier: four gates + Free-bridge semantics (#798/#887) ───

export function spotifyPill(data: ProviderStatusPayload): PillView {
  const isRateLimited = data.rate_limited === true;
  // The real API is unauthed/banned but the worker is still matching via the
  // no-creds Spotify Free source — treat it as running, not stuck.
  const bridgingFree = data.using_free === true;
  const notAuthenticated = data.authenticated === false && !bridgingFree;
  const rateLimitedStuck = isRateLimited && !bridgingFree;
  // The daily budget is a real-API cap; bridged to Free it no longer applies.
  const budgetStuck = Boolean(data.daily_budget && data.daily_budget.exhausted) && !bridgingFree;

  let stateClass: PillView['stateClass'] = null;
  if (data.paused) stateClass = 'paused';
  else if (notAuthenticated) stateClass = 'no-auth';
  else if (rateLimitedStuck || budgetStuck) stateClass = 'paused';
  else if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';

  let status: string;
  if (data.paused) status = 'Paused';
  else if (notAuthenticated) status = 'Not Authenticated';
  else if (rateLimitedStuck) status = 'Rate Limited';
  else if (bridgingFree) status = 'Running (Spotify Free)';
  else if (budgetStuck) status = 'Daily Limit Reached';
  else if (data.idle) status = 'Complete';
  else if (data.running) status = 'Running';
  else status = 'Idle';

  let current: string | null;
  const name = itemName(data.current_item);
  if (data.paused) {
    current = notAuthenticated ? 'Connect Spotify in Settings to enrich' : 'Click to resume';
  } else if (notAuthenticated) {
    current = 'Connect Spotify in Settings to enrich';
  } else if (rateLimitedStuck) {
    const remaining = data.rate_limit?.remaining_seconds || 0;
    current =
      remaining > 0
        ? `Waiting ${Math.ceil(remaining / 60)}m for rate limit to clear`
        : 'Waiting for rate limit to clear';
  } else if (bridgingFree && data.current_item && name) {
    current = `Now: ${name} (via Spotify Free)`;
  } else if (budgetStuck) {
    const resets = data.daily_budget?.resets_in_seconds || 0;
    const hours = Math.floor(resets / 3600);
    const mins = Math.floor((resets % 3600) / 60);
    current = `Resets in ${hours}h ${mins}m`;
  } else if (data.idle) {
    current = 'All items processed';
  } else if (data.current_item && name) {
    current = `Now: ${name}`;
  } else {
    current = 'Waiting for next item...';
  }

  let progress: string | null = null;
  if (data.progress) {
    if (notAuthenticated) {
      progress = `Pending: ${data.stats?.pending || 0} items`;
    } else {
      const t = tiers(data.progress);
      const tier = formBTier(t, itemType(data.current_item));
      progress = tierLine(TIER_LABELS[tier], t[tier]);
    }
  }

  return { stateClass, status, current, progress };
}

// ── iTunes — MusicBrainz's gates with Form B tiers and Deezer's current ─────

export function itunesPill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';
  else if (data.paused) stateClass = 'paused';

  let status: string;
  if (data.idle) status = 'Complete';
  else if (data.running && !data.paused) status = 'Running';
  else if (data.paused) {
    status = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
  } else status = 'Idle';

  // NO final else — stale-text quirk, like Deezer.
  let current: string | null = null;
  const name = itemName(data.current_item);
  if (data.idle) current = 'All items processed';
  else if (data.current_item && name) current = `Now: ${name}`;

  let progress: string | null = null;
  if (data.progress) {
    const t = tiers(data.progress);
    const tier = formBTier(t, itemType(data.current_item));
    progress = tierLine(TIER_LABELS[tier], t[tier]);
  }

  return { stateClass, status, current, progress };
}

// ── Genius — the authed family with a TWO-tier Form A (no albums pass) ──────

export function geniusPill(data: ProviderStatusPayload): PillView {
  const notAuthenticated = data.authenticated === false;

  let stateClass: PillView['stateClass'] = null;
  if (data.paused) stateClass = 'paused';
  else if (notAuthenticated) stateClass = 'no-auth';
  else if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';

  let status: string;
  if (data.paused) status = 'Paused';
  else if (notAuthenticated) status = 'Not Authenticated';
  else if (data.idle) status = 'Complete';
  else if (data.running) status = 'Running';
  else status = 'Idle';

  let current: string | null = null;
  const name = itemName(data.current_item);
  if (data.paused) {
    current = notAuthenticated
      ? 'Add Genius access token in Settings to enrich'
      : 'Click to resume';
  } else if (notAuthenticated) current = 'Add Genius access token in Settings to enrich';
  else if (data.idle) current = 'All items processed';
  else if (data.current_item && name) current = `Now: ${name}`;

  let progress: string | null = null;
  if (data.progress) {
    if (notAuthenticated) {
      progress = `Pending: ${data.stats?.pending || 0} items`;
    } else {
      // Genius has NO albums pass: artists, else Tracks.
      const artists = data.progress.artists || {};
      const tracks = data.progress.tracks || {};
      const currentType = itemType(data.current_item);
      const artistsComplete = (artists.matched as number) >= (artists.total as number);
      if (currentType === 'artist' || (!artistsComplete && !currentType)) {
        progress = tierLine('Artists', artists);
      } else {
        progress = tierLine('Tracks', tracks);
      }
    }
  }

  return { stateClass, status, current, progress };
}

// ── Bandcamp — keyless: `enabled === false` (STRICT) maps to no-auth ────────

export function bandcampPill(data: ProviderStatusPayload): PillView {
  // Strict comparison, verbatim: an ABSENT `enabled` is NOT disabled — unlike
  // JioSaavn, whose `!data.enabled` treats missing as disabled.
  const disabled = data.enabled === false;

  let stateClass: PillView['stateClass'] = null;
  if (disabled) stateClass = 'no-auth';
  else if (data.paused) stateClass = 'paused';
  else if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';

  let status: string;
  if (disabled) status = 'Disabled';
  else if (data.paused) status = 'Paused';
  else if (data.idle) status = 'Complete';
  else if (data.running) status = 'Running';
  else status = 'Idle';

  let current: string | null = null;
  const name = itemName(data.current_item);
  if (disabled) current = 'Enable in Settings → Advanced → Experimental';
  // No auth ternary here — Bandcamp's paused copy is unconditional.
  else if (data.paused) current = 'Click to resume';
  else if (data.idle) current = 'All items processed';
  else if (data.current_item && name) current = `Now: ${name}`;

  let progress: string | null = null;
  if (data.progress) {
    if (disabled) {
      progress = `Pending: ${data.stats?.pending || 0} items`;
    } else {
      // Two tiers only — core/bandcamp_worker.py has no artist pass.
      const albums = data.progress.albums || {};
      const tracks = data.progress.tracks || {};
      const currentType = itemType(data.current_item);
      const albumsComplete = (albums.matched as number) >= (albums.total as number);
      if (currentType === 'album' || (!albumsComplete && !currentType)) {
        progress = tierLine('Albums', albums);
      } else {
        progress = tierLine('Tracks', tracks);
      }
    }
  }

  return { stateClass, status, current, progress };
}

// ── Amazon — no auth, paused checked FIRST, Form C, keeps its else ──────────

export function amazonPill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (data.paused) stateClass = 'paused';
  else if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';

  let status: string;
  if (data.paused) {
    status = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
  } else if (data.idle) status = 'Complete';
  else if (data.running) status = 'Running';
  else status = 'Idle';

  let current: string | null;
  const name = itemName(data.current_item);
  if (data.idle) current = 'All items processed';
  else if (data.current_item && name) current = `Now: ${name}`;
  else current = 'No active matches';

  let progress: string | null = null;
  if (data.progress) {
    const t = tiers(data.progress);
    const tier = formCTier(t, itemType(data.current_item));
    progress = tierLine(TIER_LABELS[tier], t[tier]);
  }

  return { stateClass, status, current, progress };
}

// ── Discogs — STRING current_item, stats not progress, pipe separators ──────

export function discogsPill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';
  else if (data.paused) stateClass = 'paused';

  let status: string;
  if (data.idle) status = 'Complete';
  else if (data.running && !data.paused) status = 'Running';
  else if (data.paused) {
    status = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
  } else status = 'Idle';

  // The vanilla consumes current_item RAW — object payloads would stringify,
  // but the channel carries strings here.
  let current: string | null;
  if (data.idle) current = 'All items processed';
  else if (data.current_item) current = `Processing: "${data.current_item}"`;
  else current = 'No active matches';

  // Discogs reads data.stats, not data.progress — and pipes, not tiers.
  let progress: string | null = null;
  if (data.stats) {
    const s = data.stats;
    progress = `Matched: ${s.matched || 0} | Not found: ${s.not_found || 0} | Pending: ${s.pending || 0}`;
  }

  return { stateClass, status, current, progress };
}

// ── SimilarArtists — STRING item, UNCONDITIONAL single-tier progress ────────

export function similarArtistsPill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (data.paused) stateClass = 'paused';
  else if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';

  let status: string;
  if (data.paused) status = 'Paused';
  else if (data.idle) status = 'Complete';
  else if (data.running) status = 'Running';
  else status = 'Idle';

  let current: string | null;
  if (data.idle) current = 'All library artists processed';
  else if (data.current_item) current = `Now: ${data.current_item}`;
  else current = 'No active matches';

  // Written UNCONDITIONALLY in the vanilla — no `if (data.progress)` guard, so
  // this is never null: an empty payload renders the zero line.
  const a = (data.progress && data.progress.artists) || {};
  const progress = `Artists: ${a.matched || 0} / ${a.total || 0} (${a.percent || 0}%)`;

  return { stateClass, status, current, progress };
}

// ── Hydrabase — active/paused only, inline status colors, no tooltip body ───

export function hydrabasePill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (data.running && !data.paused) stateClass = 'active';
  else if (data.paused) stateClass = 'paused';

  let status: string;
  let statusColor: string;
  if (data.paused) {
    status = 'Paused';
    statusColor = '#ffc107';
  } else if (data.running) {
    status = 'Active';
    statusColor = '#ffffff';
  } else {
    status = 'Stopped';
    statusColor = '#ff5252';
  }

  // Hydrabase's tooltip has no current/progress lines at all.
  return { stateClass, status, current: null, progress: null, statusColor };
}

// ── SoulID — STRING item shown RAW and checked FIRST; never paused ──────────

export function soulidPill(data: ProviderStatusPayload): PillView {
  // The vanilla removes only active/complete — 'paused' is never added (nor
  // removed), even though the status chain can SAY Paused.
  let stateClass: PillView['stateClass'] = null;
  if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';

  let status: string;
  if (data.idle) status = 'Complete';
  else if (data.running && !data.paused) status = 'Running';
  else if (data.paused) status = 'Paused';
  else status = 'Idle';

  // current_item FIRST — it outranks idle here, unlike everywhere else — and
  // is rendered raw with no prefix.
  let current: string;
  if (data.current_item) current = `${data.current_item}`;
  else if (data.idle) current = 'All entities have soul IDs';
  else current = 'No items processing';

  let progress: string | null = null;
  if (data.stats) {
    const s = data.stats;
    const parts: string[] = [];
    if (s.artists_processed) parts.push(`Artists: ${s.artists_processed}`);
    if (s.albums_processed) parts.push(`Albums: ${s.albums_processed}`);
    if (s.tracks_processed) parts.push(`Tracks: ${s.tracks_processed}`);
    if ((s.pending as number) > 0) parts.push(`Pending: ${s.pending}`);
    progress = parts.length ? parts.join(' · ') : 'No items processed yet';
  }

  return { stateClass, status, current, progress };
}

// ── Repair — job-based, with the `!enabled` override and the orb badge ──────

export function repairPill(data: ProviderStatusPayload): PillView {
  let stateClass: PillView['stateClass'] = null;
  if (data.idle) stateClass = 'complete';
  else if (data.running && !data.paused) stateClass = 'active';
  else if (data.paused) stateClass = 'paused';

  let status: string;
  if (data.idle) status = 'Complete';
  else if (data.running && !data.paused) status = 'Running';
  else if (data.paused) {
    status = data.yield_reason === 'downloads' ? 'Yielding for downloads' : 'Paused';
  } else status = 'Idle';

  let current: string;
  const name = itemName(data.current_item);
  if (data.idle) {
    current = 'All jobs complete — waiting for next schedule';
  } else if (data.current_job && data.current_job.display_name) {
    const jobName = data.current_job.display_name;
    const jobProgress = data.progress && data.progress.current_job;
    if (jobProgress && (jobProgress.total as number) > 0) {
      // scanned and percent are interpolated BARE in the vanilla — only total
      // is guarded (by the > 0 test above).
      current = `${jobName}: ${jobProgress.scanned} / ${jobProgress.total} (${jobProgress.percent}%)`;
    } else {
      current = `Running: ${jobName}`;
    }
  } else if (data.current_item && name) {
    current = `Running: ${name}`;
  } else {
    current = 'No active repairs';
  }

  let progress: string | null = null;
  if (data.progress) {
    const tracks = data.progress.tracks || {};
    const parts: string[] = [];
    if ((tracks.total as number) > 0)
      parts.push(`Checked: ${tracks.checked || 0} / ${tracks.total || 0}`);
    if ((tracks.repaired as number) > 0) parts.push(`Repaired: ${tracks.repaired}`);
    const pending = data.findings_pending || 0;
    if (pending > 0) parts.push(`Findings: ${pending}`);
    progress = parts.length ? parts.join(' · ') : 'No items processed yet';
  }

  const view: PillView = { stateClass, status, current, progress };
  // The trailing override, verbatim: a payload without `enabled` forces paused
  // — this runs AFTER the whole chain and beats even complete/active.
  if (!data.enabled) view.stateClass = 'paused';
  return view;
}

/** The repair orb's findings badge: count + shown-only-when-nonzero. */
export function repairFindingsBadge(data: ProviderStatusPayload): {
  count: number;
  visible: boolean;
} {
  const count = data.findings_pending || 0;
  return { count, visible: count > 0 };
}

// ── formatCountdownTime — the watchlist hero button's title timer ────────────

/**
 * Transcribed 1:1 from media-player.js:formatCountdownTime. The watchlist
 * button's `Next auto-scan in …` title runs it over `next_run_in_seconds`.
 * Ported rather than window-called because it is pure and media-player.js has
 * no reason to export it.
 */
export function formatCountdownTime(seconds: number | null | undefined): string {
  // Format seconds as countdown timer (e.g., "24m 13s", "2h 15m", "23h 59m")
  if (seconds === null || seconds === undefined || seconds < 0) return '';
  if (seconds === 0) return '0s'; // Show "0s" instead of hiding timer

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  } else if (minutes > 0) {
    return `${minutes}m ${secs}s`;
  } else {
    return `${secs}s`;
  }
}
