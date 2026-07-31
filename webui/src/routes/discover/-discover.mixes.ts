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

// ── The live shelf feeders (found missing by the coverage audit) ────────────

export interface MixFeederDef {
  key: string;
  title: string;
  subtitle: string;
  syncKey: string;
  fetchUrl: string;
  /** DOM container the old (now collapsed) per-mix table lived in. */
  contentEl: string;
  loadingMessage?: string;
  emptyMessage?: string;
  errorMessage?: string;
}

/**
 * The four feeders whose loaders live outside the personalized block.
 *
 * Release Radar (2060) and Weekly (2089) go through the section controller and
 * render '' — their only job is to fill the module track array and upsert a mix
 * card. Popular Picks (4553) and Hidden Gems (4580) hand-roll the same shape
 * without a controller.
 *
 * All four then `_collapseOldMixSection` their container: the per-mix table is
 * redundant now that the card opens the tracks in a modal, and collapsing also
 * strips the duplicate sync-status ids that would otherwise shadow the modal's.
 */
export const MIX_FEEDERS: MixFeederDef[] = [
  {
    key: 'release_radar',
    title: 'Fresh Tape',
    subtitle: 'New releases from artists you follow',
    syncKey: 'release_radar',
    fetchUrl: '/api/discover/release-radar',
    contentEl: '#release-radar-playlist',
    loadingMessage: 'Loading release radar...',
    emptyMessage: 'No new releases available',
    errorMessage: 'Failed to load release radar',
  },
  {
    key: 'discovery_weekly',
    title: 'The Archives',
    subtitle: 'A weekly dig through artists across your library',
    syncKey: 'discovery_weekly',
    fetchUrl: '/api/discover/weekly',
    contentEl: '#discovery-weekly-playlist',
    loadingMessage: 'Curating your discovery playlist...',
    emptyMessage: 'No tracks available yet',
    errorMessage: 'Failed to load discovery weekly',
  },
  {
    key: 'popular_picks',
    title: 'Popular Picks',
    subtitle: 'Popular tracks from artists you love',
    syncKey: 'popular_picks',
    fetchUrl: '/api/discover/personalized/popular-picks',
    contentEl: '#personalized-popular-picks',
  },
  {
    key: 'hidden_gems',
    title: 'Hidden Gems',
    subtitle: 'Deeper cuts you might have missed',
    syncKey: 'hidden_gems',
    fetchUrl: '/api/discover/personalized/hidden-gems',
    contentEl: '#personalized-hidden-gems',
  },
];

/**
 * A feeder upserts its card ONLY with tracks (2078, 4562).
 *
 * An empty response is not a card with "0 tracks" — the shelf simply never
 * learns about that mix. The personalized pair additionally hides its whole
 * legacy section on empty.
 */
export function feederShouldUpsert(tracks: unknown[] | null | undefined): boolean {
  return Array.isArray(tracks) && tracks.length > 0;
}

/** `!data.success || !data.tracks || !data.tracks.length` (4562). */
export function feederTracks(
  data: { success?: boolean; tracks?: unknown[] } | null | undefined,
): unknown[] {
  if (!data?.success || !Array.isArray(data.tracks)) return [];
  return data.tracks;
}

// ── The mix modal's track table (4692) ─────────────────────────────────────

export interface CompactRow {
  index: number;
  /** 1-based, shown to the user. */
  position: number;
  name: string;
  artist: string;
  album: string;
  cover: string;
  /** EMPTY for a zero/unknown length rather than "0:00". */
  duration: string;
  selectable: boolean;
}

/**
 * `renderCompactPlaylist` (4692).
 *
 * `selectable` is opt-in per call: the mix modal passes it (that is #1079), the
 * plain playlist renderers do not. When set, each row gains a checkbox and a
 * preview button, and the row itself gets `has-select` so the grid reflows.
 */
export function compactRows(tracks: unknown[], selectable = false): CompactRow[] {
  return tracks.map((track, index) => {
    const t = normalizeTrack(track as never);
    const ms = t.durationMs;
    const min = Math.floor(ms / 60000);
    const sec = Math.floor((ms % 60000) / 1000);
    return {
      index,
      position: index + 1,
      name: t.name,
      artist: t.artist,
      album: t.album,
      cover: t.cover || MIX_COVER_PLACEHOLDER,
      duration: ms > 0 ? `${min}:${sec.toString().padStart(2, '0')}` : '',
      selectable,
    };
  });
}

// ── #1079: the selection bar (4772-4806) ───────────────────────────────────

export const MIX_SEL_IDLE_LABEL = 'Download selected';
export const MIX_TRACK_GONE = 'Track is no longer available';
export const MIX_NO_PLAYBACK = 'Playback is not available here';

export interface MixSelectionBar {
  count: number;
  countLabel: string;
  downloadLabel: string;
  downloadDisabled: boolean;
  /** The header checkbox is checked only when EVERY row is (4802). */
  selectAllChecked: boolean;
}

/**
 * `_updateMixSelBar` (4792).
 *
 * The select-all box requires `total > 0` as well as `count === total` —
 * without it an empty list would show select-all ticked, since 0 === 0.
 */
export function mixSelectionBar(count: number, total: number): MixSelectionBar {
  return {
    count,
    countLabel: `${count} selected`,
    downloadLabel: count > 0 ? `Download selected (${count})` : MIX_SEL_IDLE_LABEL,
    downloadDisabled: count === 0,
    selectAllChecked: total > 0 && count === total,
  };
}

/** Select-all / clear set every box (4780, 4785); clear also unticks the header. */
export function mixSetAllSelected(total: number, checked: boolean): number[] {
  return checked ? Array.from({ length: total }, (_, i) => i) : [];
}
