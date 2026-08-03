/**
 * Playlist Explorer — the pure core (pages-extra.js:1-1141).
 *
 * Everything here is derivation the vanilla did inline inside a template
 * literal or a DOM walk: badge precedence, the readiness gate, the tree's row
 * shape, the bezier geometry, the zoom clamp. Extracted so the port can be
 * proven against the original without a DOM.
 *
 * Line references are to the pre-port `webui/static/pages-extra.js`.
 */

import type {
  ExplorerAlbum,
  ExplorerArtist,
  ExplorerArtistSection,
  MirroredPlaylist,
} from './-explorer.types';

// ── Picker cards ──────────────────────────────────────────────────────────

/** :70 — the tab labels; anything unlisted is Title-cased. */
const SOURCE_NAMES: Record<string, string> = {
  spotify: 'Spotify',
  tidal: 'Tidal',
  deezer: 'Deezer',
  youtube: 'YouTube',
  beatport: 'Beatport',
  file: 'File',
  other: 'Other',
};

export function explorerSourceKey(playlist: MirroredPlaylist): string {
  return (playlist.source || 'other').toLowerCase();
}

export function explorerSourceLabel(source: string): string {
  return SOURCE_NAMES[source] || source.charAt(0).toUpperCase() + source.slice(1);
}

export interface ExplorerSourceGroup {
  source: string;
  label: string;
  count: number;
  playlists: MirroredPlaylist[];
}

/**
 * :62-88 — group by lowercased source, first-seen order. The tab strip hides
 * itself at one source, and the active source falls back to the first group.
 */
export function groupPlaylistsBySource(
  playlists: MirroredPlaylist[],
  activeSource?: string | null,
): { groups: ExplorerSourceGroup[]; showTabs: boolean; activeSource: string | null } {
  const groups: ExplorerSourceGroup[] = [];
  const byKey = new Map<string, ExplorerSourceGroup>();
  for (const playlist of playlists) {
    const source = explorerSourceKey(playlist);
    let group = byKey.get(source);
    if (!group) {
      group = { source, label: explorerSourceLabel(source), count: 0, playlists: [] };
      byKey.set(source, group);
      groups.push(group);
    }
    group.playlists.push(playlist);
    group.count += 1;
  }
  const active =
    activeSource && byKey.has(activeSource) ? activeSource : (groups[0]?.source ?? null);
  return { groups, showTabs: groups.length > 1, activeSource: active };
}

export type ExplorerBadgeKind =
  | 'downloaded'
  | 'explored'
  | 'wishlisted'
  | 'ready'
  | 'needs-discovery';

export interface ExplorerBadge {
  kind: ExplorerBadgeKind;
  title: string;
  /** The glyph or the percentage the vanilla printed inside the pill. */
  text: string;
}

export interface ExplorerCardView {
  total: number;
  discovered: number;
  pct: number;
  /** Below 50% the card is inert — no click handler at all (:158). */
  isReady: boolean;
  isFullyDiscovered: boolean;
  wasExplored: boolean;
  wishlisted: number;
  inLibrary: number;
  badge: ExplorerBadge | null;
  /** The meta line's tail and the class the vanilla wrapped it in (:138-145). */
  metaText: string;
  metaClass: 'explorer-picker-discovered' | 'explorer-picker-not-ready' | null;
  /** The second meta row, empty when neither counter applies (:146-149). */
  statusParts: { className: string; text: string }[];
  showDiscoverButton: boolean;
}

/**
 * :103-152 — the whole card derivation. The badge ladder is a precedence
 * chain, not a set of independent flags: an explored playlist never shows its
 * wishlist heart, and a mostly-owned one never shows either.
 */
export function explorerCardView(playlist: MirroredPlaylist): ExplorerCardView {
  const total = playlist.total_count || playlist.track_count || 0;
  const discovered = playlist.discovered_count || 0;
  const pct = total > 0 ? Math.round((discovered / total) * 100) : 0;
  const isReady = pct >= 50;
  const isFullyDiscovered = pct === 100;
  const wasExplored = !!(playlist.explored_at || playlist.explored);
  const wishlisted = playlist.wishlisted_count || 0;
  const inLibrary = playlist.in_library_count || 0;

  let badge: ExplorerBadge | null = null;
  if (inLibrary > 0 && inLibrary >= total * 0.8) {
    badge = { kind: 'downloaded', title: 'Most tracks in library', text: '✓' };
  } else if (wasExplored) {
    badge = { kind: 'explored', title: 'Already explored', text: '✓' };
  } else if (wishlisted > 0) {
    badge = { kind: 'wishlisted', title: 'Tracks wishlisted', text: '♥' };
  } else if (isFullyDiscovered) {
    badge = { kind: 'ready', title: 'Ready to explore', text: '★' };
  } else if (!isReady) {
    badge = { kind: 'needs-discovery', title: `Needs discovery (${pct}%)`, text: `${pct}%` };
  }

  let metaText: string;
  let metaClass: ExplorerCardView['metaClass'];
  if (isFullyDiscovered) {
    metaText = 'Fully discovered';
    metaClass = 'explorer-picker-discovered';
  } else if (isReady) {
    metaText = `${pct}% discovered`;
    metaClass = null;
  } else {
    metaText = `${pct}% discovered`;
    metaClass = 'explorer-picker-not-ready';
  }

  const statusParts: { className: string; text: string }[] = [];
  if (inLibrary > 0) {
    statusParts.push({ className: 'explorer-picker-in-library', text: `${inLibrary} in library` });
  }
  if (wishlisted > 0) {
    statusParts.push({ className: 'explorer-picker-wishlisted', text: `${wishlisted} wishlisted` });
  }

  return {
    total,
    discovered,
    pct,
    isReady,
    isFullyDiscovered,
    wasExplored,
    wishlisted,
    inLibrary,
    badge,
    metaText,
    metaClass,
    statusParts,
    showDiscoverButton: !isReady,
  };
}

// ── Tree shape ────────────────────────────────────────────────────────────

/**
 * :435 — the branch/node id stem. Collision-prone by construction (every
 * non-alphanumeric collapses to `_`, so "AC/DC" and "AC-DC" share a key), but
 * the selection set and the album fallback ids are both built from it, so it
 * is kept verbatim.
 */
export function explorerArtistKey(name: string | null | undefined): string {
  return (name || '').replace(/[^a-zA-Z0-9]/g, '_');
}

/** :404 — row N holds N+2 artists, which is what gives the tree its taper. */
export function explorerRowCapacity(rowIndex: number): number {
  return rowIndex + 2;
}

/**
 * :400-419 — the streamed rows, resolved up front. Artists arrive one NDJSON
 * line at a time and land in the last row until it fills, so the sizes run
 * 2, 3, 4, 5… with the final row possibly short.
 */
export function planArtistRows(artistCount: number): number[] {
  const rows: number[] = [];
  let remaining = artistCount;
  let rowIndex = 0;
  while (remaining > 0) {
    const take = Math.min(remaining, explorerRowCapacity(rowIndex));
    rows.push(take);
    remaining -= take;
    rowIndex += 1;
  }
  return rows;
}

/** planArtistRows applied to the artists themselves — the rendered rows. */
export function chunkArtistRows<T>(items: T[]): T[][] {
  const rows: T[][] = [];
  let offset = 0;
  for (const size of planArtistRows(items.length)) {
    rows.push(items.slice(offset, offset + size));
    offset += size;
  }
  return rows;
}

/** :470 — real Spotify id when there is one, else a positional stand-in. */
export function explorerAlbumNodeId(
  album: ExplorerAlbum,
  artistKey: string,
  index: number,
): string {
  return album.spotify_id || `${artistKey}_${index}`;
}

/**
 * :558 — the tracklist fetch only fires for real ids. The vanilla's test is
 * "does it contain an underscore", which works because the fallback key is the
 * only id that can and Spotify's base62 ids never do.
 */
export function isRealAlbumId(id: string): boolean {
  return !id.includes('_');
}

/** :475 / :661 — the type pill. */
export function explorerAlbumTypeLabel(albumType: string | null | undefined): string {
  if (albumType === 'single') return 'Single';
  if (albumType === 'ep') return 'EP';
  return 'Album';
}

/** :541 — blank rather than "0:00" when the API omits the duration. */
export function explorerFormatDuration(ms: number | null | undefined): string {
  if (!ms) return '';
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

/** :316 — the build progress bar and its caption. */
export function explorerBuildProgress(
  artistCount: number,
  totalArtists: number,
): { pct: number; text: string } {
  const pct = totalArtists > 0 ? Math.round((artistCount / totalArtists) * 100) : 0;
  return { pct, text: `Discovering artists... ${artistCount} of ${totalArtists}` };
}

// ── Selection ─────────────────────────────────────────────────────────────

/** :601 — "0 albums selected". */
export function explorerSelectionLabel(count: number): string {
  return `${count} album${count !== 1 ? 's' : ''} selected`;
}

/**
 * :587-593 — Select All takes only real ids and skips what is already owned,
 * so the count never includes an album the button can't act on.
 */
export function explorerSelectableAlbumIds(artists: ExplorerArtist[]): string[] {
  const ids: string[] = [];
  for (const artist of artists) {
    for (const album of artist.albums || []) {
      if (album.spotify_id && !album.owned) ids.push(album.spotify_id);
    }
  }
  return ids;
}

/** :615 — the `.has-selection` ring on a collapsed artist node. */
export function artistHasSelection(artist: ExplorerArtist, selected: ReadonlySet<string>): boolean {
  return (artist.albums || []).some((a) => !!a.spotify_id && selected.has(a.spotify_id));
}

/**
 * :625-634 — the wishlist modal's grouping. Artists with nothing selected drop
 * out entirely, and the id falls back to spotify_id because the build-tree
 * stream fills whichever the source gave it.
 */
export function groupSelectionByArtist(
  artists: ExplorerArtist[],
  selected: ReadonlySet<string>,
): ExplorerArtistSection[] {
  const sections: ExplorerArtistSection[] = [];
  for (const artist of artists) {
    if (!artist.albums) continue;
    const albums = artist.albums.filter((a) => !!a.spotify_id && selected.has(a.spotify_id));
    if (albums.length === 0) continue;
    sections.push({
      artistId: artist.artist_id || artist.spotify_id || null,
      name: artist.name || '',
      image: artist.image_url || null,
      albums,
    });
  }
  return sections;
}

/** :641-642 — the modal's hero subtitle counters. */
export function explorerSelectionTotals(sections: ExplorerArtistSection[]): {
  artists: number;
  albums: number;
  tracks: number;
} {
  return {
    artists: sections.length,
    albums: sections.reduce((sum, s) => sum + s.albums.length, 0),
    tracks: sections.reduce(
      (sum, s) => sum + s.albums.reduce((t, a) => t + (a.track_count || 0), 0),
      0,
    ),
  };
}

// ── SVG geometry ──────────────────────────────────────────────────────────

/** :976 — the cubic that links a parent's bottom to a child's top. */
export function explorerCurvePath(x1: number, y1: number, x2: number, y2: number): string {
  const midY = y1 + (y2 - y1) * 0.45;
  return `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
}

export type ExplorerCurveType = 'root' | 'album' | 'track';

/** :979-1003 — each tier gets its own gradient and weight. */
export function explorerCurveStroke(type: ExplorerCurveType): {
  stroke: string;
  strokeWidth: string;
} {
  if (type === 'root') return { stroke: 'url(#explorer-grad-root)', strokeWidth: '1.5' };
  if (type === 'album') return { stroke: 'url(#explorer-grad-album)', strokeWidth: '1' };
  return { stroke: 'rgba(255,255,255,0.05)', strokeWidth: '0.8' };
}

/** :946-953 — the SVG is sized in unscaled tree space, plus 40px of slack. */
export function explorerSvgSize(
  scrollWidth: number,
  offsetWidth: number,
  scrollHeight: number,
  offsetHeight: number,
): { width: number; height: number } {
  return {
    width: Math.max(scrollWidth, offsetWidth) + 40,
    height: Math.max(scrollHeight, offsetHeight) + 40,
  };
}

export interface ExplorerRectLike {
  left: number;
  top: number;
  bottom: number;
  width: number;
}

/**
 * :956-970 — getBoundingClientRect reports SCALED pixels, but the SVG lives
 * inside the transformed tree and draws in unscaled coordinates. Dividing by
 * the zoom is what keeps the lines glued to the nodes.
 */
export function explorerNodePosition(
  node: ExplorerRectLike,
  tree: { left: number; top: number },
  zoom: number,
): { cx: number; top: number; bottom: number } {
  const scale = zoom || 1;
  return {
    cx: (node.left + node.width / 2 - tree.left) / scale,
    top: (node.top - tree.top) / scale,
    bottom: (node.bottom - tree.top) / scale,
  };
}

// ── Zoom ──────────────────────────────────────────────────────────────────

export const EXPLORER_MIN_ZOOM = 0.2;
export const EXPLORER_MAX_ZOOM = 3;

/** :1037 */
export function clampExplorerZoom(zoom: number): number {
  return Math.max(EXPLORER_MIN_ZOOM, Math.min(EXPLORER_MAX_ZOOM, zoom));
}

/** :1078 — the wheel step, inverted so scrolling up zooms in. */
export function explorerWheelStep(deltaY: number): number {
  return deltaY > 0 ? -0.08 : 0.08;
}

/**
 * :1049-1060 — fit measures the tree at scale 1, leaves 20px of margin on each
 * side, and refuses to zoom past 1.5 so a two-node tree doesn't balloon.
 */
export function explorerFitZoom(
  treeWidth: number,
  treeHeight: number,
  viewportWidth: number,
  viewportHeight: number,
): number {
  const vpW = viewportWidth - 40;
  const vpH = viewportHeight - 40;
  if (treeWidth <= 0 || treeHeight <= 0) return 1;
  return clampExplorerZoom(Math.min(vpW / treeWidth, vpH / treeHeight, 1.5));
}

/** :1064 — centre the scaled tree horizontally after a fit. */
export function explorerFitScrollLeft(
  treeScrollWidth: number,
  zoom: number,
  viewportWidth: number,
): number {
  const vpW = viewportWidth - 40;
  return Math.max(0, (treeScrollWidth * zoom - vpW) / 2);
}
