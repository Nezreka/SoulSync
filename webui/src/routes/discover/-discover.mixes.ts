/**
 * Your Mixes — the mix registry, the card cover, and the mix modal's actions.
 *
 * Transcribed from `_discoverMixRegistry` / `_yourMixKeys` (4832-4836),
 * `_buildMixCard` (4838), `_hydrateMixCovers` (4870), `_renderMixGrid` (4897),
 * `_upsertMixCard` (4906), `openMixModalByKey` (4926) and `openMixModal` (4931)
 * — read end to end, along with all eight call sites that feed the shelf.
 *
 * ── The registry is NOT the shelf ───────────────────────────────────────────
 *
 * There are two collections and conflating them is the trap the vanilla calls
 * out at 4833: `_discoverMixRegistry` holds EVERY mix on the page, including
 * the decades, Last.fm and ListenBrainz sections' mixes, because
 * `openMixModalByKey` has to resolve any of them. `_yourMixKeys` is the ordered
 * subset that belongs to the "Your Mixes" shelf. Rendering
 * `Object.values(registry)` would leak three other sections onto the shelf.
 */

import { normalizeTrack } from './-discover.helpers';

/** `while (covers.length < 4)` — the mosaic is always a full 2x2 (4850). */
export const MIX_COVER_TILES = 4;
export const MIX_COVER_PLACEHOLDER = '/static/placeholder-album.png';

export interface DiscoverMix {
  key: string;
  title: string;
  subtitle?: string;
  tracks?: unknown[];
  trackCount?: number;
  syncKey?: string;
  statusBase?: string;
  /** Sections with no per-track art supply their own cover (4841). */
  coverHtml?: string;
  actions?: MixAction[];
  fetchTracks?: () => unknown[] | Promise<unknown[]>;
}

export interface MixAction {
  label: string;
  closeFirst?: boolean;
  primary?: boolean;
  isSync?: boolean;
  onclick: string;
}

/**
 * Every key that feeds the shared shelf, traced to its `_upsertMixCard` call.
 *
 * There are eight call sites but only SEVEN live feeders. `daily_mix_*` is
 * produced solely by `loadPersonalizedDailyMixes` (4639), which the
 * reachability closure over discover.js reports as unreachable — no other
 * function's body names it, and it is absent from index.html and every other
 * static script. `-discover.api.ts` already recorded its endpoint as dead. It
 * is listed here so the port does not silently "restore" a section that has not
 * rendered for users, and so nobody re-adds it without reconnecting a caller.
 *
 * This list is documentation, not a render source — the shelf renders whatever
 * actually registered.
 */
export const YOUR_MIX_FEEDERS = [
  { key: 'release_radar', title: 'Fresh Tape', syncKey: 'release_radar', live: true },
  { key: 'discovery_weekly', title: 'The Archives', syncKey: 'discovery_weekly', live: true },
  // title is built from the season
  { key: 'seasonal_playlist', title: null, syncKey: 'seasonal_playlist', live: true },
  { key: 'popular_picks', title: 'Popular Picks', syncKey: 'popular_picks', live: true },
  { key: 'hidden_gems', title: 'Hidden Gems', syncKey: 'hidden_gems', live: true },
  { key: 'listening_mix', title: 'Your Listening Mix', syncKey: 'listening_mix', live: true },
  // variadic, no syncKey — and its only producer is unreachable
  { key: 'daily_mix_*', title: null, syncKey: null, live: false },
  {
    key: 'discovery_shuffle',
    title: 'Discovery Shuffle',
    syncKey: 'discovery_shuffle',
    live: true,
  },
] as const;

/** The seven feeders that can actually put a card on the shelf. */
export const LIVE_MIX_FEEDERS = YOUR_MIX_FEEDERS.filter((f) => f.live);

/**
 * Track count for the card's meta line (4854).
 *
 * `mix.tracks ? mix.tracks.length : (mix.trackCount || 0)` — an EMPTY array is
 * truthy, so a mix that loaded and found nothing reads "0 tracks" rather than
 * falling through to a stale `trackCount`.
 */
export function mixTrackCount(mix: DiscoverMix): number {
  return mix.tracks ? mix.tracks.length : mix.trackCount || 0;
}

/**
 * The 2x2 mosaic (4844-4851).
 *
 * Covers are DEDUPED before the cap, so a mix whose first four tracks share one
 * album still produces four distinct tiles if a fifth track differs. Short of
 * four it pads with the placeholder rather than rendering a ragged grid.
 */
export function mixCoverTiles(tracks: unknown[] | undefined): string[] {
  const covers: string[] = [];
  for (const t of tracks || []) {
    const c = normalizeTrack(t as never).cover;
    if (c && !covers.includes(c)) covers.push(c);
    if (covers.length >= MIX_COVER_TILES) break;
  }
  while (covers.length < MIX_COVER_TILES) covers.push(MIX_COVER_PLACEHOLDER);
  return covers;
}

/** Does this mix supply its own cover instead of a mosaic? (4840) */
export function mixUsesSolidCover(mix: DiscoverMix): boolean {
  return Boolean(mix.coverHtml);
}

/**
 * Whether a lazily-loaded mix should have its cover upgraded (4872).
 *
 * Already has tracks, or has no loader → nothing to do.
 */
export function mixNeedsCoverHydration(mix: DiscoverMix): boolean {
  return !mix.tracks && Boolean(mix.fetchTracks);
}

/**
 * After a lazy load, whether to replace the placeholder cover (4885).
 *
 * With NO usable art the placeholder stays. Replacing it with four placeholder
 * tiles would look identical but throw away the solid-cover styling that some
 * sections rely on.
 */
export function mixCoverUpgradeApplies(tracks: unknown[]): boolean {
  return mixCoverTiles(tracks).some((c) => c !== MIX_COVER_PLACEHOLDER);
}

// ── The registry / shelf split ──────────────────────────────────────────────

export interface MixRegistry {
  /** Every mix on the page, keyed — what openMixModalByKey resolves against. */
  all: Record<string, DiscoverMix>;
  /** ORDERED subset belonging to the Your Mixes shelf. */
  shelfKeys: string[];
}

export function emptyMixRegistry(): MixRegistry {
  return { all: {}, shelfKeys: [] };
}

/**
 * `_renderMixGrid` (4897) — registers a section's mixes WITHOUT adding them to
 * the shelf. This is the half that must not touch `shelfKeys`.
 */
export function registerSectionMixes(registry: MixRegistry, mixes: DiscoverMix[]): MixRegistry {
  const all = { ...registry.all };
  for (const m of mixes) all[m.key] = m;
  return { all, shelfKeys: registry.shelfKeys };
}

/**
 * `_upsertMixCard` (4906) — registers a mix AND puts it on the shelf.
 *
 * Re-upserting an existing key refreshes the mix but does NOT move it: the key
 * order is append-only, so a section that reloads does not reshuffle the shelf
 * under the user.
 */
export function upsertShelfMix(registry: MixRegistry, mix: DiscoverMix): MixRegistry {
  const shelfKeys = registry.shelfKeys.includes(mix.key)
    ? registry.shelfKeys
    : [...registry.shelfKeys, mix.key];
  return { all: { ...registry.all, [mix.key]: mix }, shelfKeys };
}

/** The shelf's cards, in key order (4911). */
export function shelfMixes(registry: MixRegistry): DiscoverMix[] {
  return registry.shelfKeys.map((k) => registry.all[k]).filter(Boolean);
}

/** The section reveals itself as soon as anything registers (4913). */
export function shelfVisible(registry: MixRegistry): boolean {
  return registry.shelfKeys.length > 0;
}

/** `openMixModalByKey` silently does nothing for an unknown key (4926). */
export function resolveMix(registry: MixRegistry, key: string): DiscoverMix | null {
  return registry.all[key] ?? null;
}

// ── The mix modal ───────────────────────────────────────────────────────────

/**
 * The id prefix the live sync-progress elements use (4943).
 *
 * Mirrors `startDiscoverPlaylistSync`'s convention — underscores become hyphens
 * — so a running sync's updates land on THIS modal's status elements. A mix
 * with neither an explicit base nor a syncKey gets '' and simply has no live
 * status.
 */
export function mixStatusBase(mix: DiscoverMix): string {
  return mix.statusBase || (mix.syncKey ? mix.syncKey.replace(/_/g, '-') : '');
}

export const MIX_ACTION_DOWNLOAD = 'Download';
export const MIX_ACTION_SYNC = 'Sync';

/**
 * The modal's buttons (4945-4951).
 *
 * A mix either brings its OWN actions (decades, ListenBrainz, Last.fm radio) or
 * gets the built-in Download/Sync pair from its syncKey. With neither — which
 * is exactly the daily mixes — it gets no actions at all, and that is the
 * vanilla's behaviour, not an oversight to paper over.
 *
 * Download carries `closeFirst` because it opens a second modal beneath this
 * one, which would otherwise be uninteractable.
 */
export function mixActions(mix: DiscoverMix): MixAction[] {
  if (mix.actions) return mix.actions;
  if (!mix.syncKey) return [];
  return [
    { label: MIX_ACTION_DOWNLOAD, closeFirst: true, onclick: 'download' },
    { label: MIX_ACTION_SYNC, primary: true, isSync: true, onclick: 'sync' },
  ];
}
