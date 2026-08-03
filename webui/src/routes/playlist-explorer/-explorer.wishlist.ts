/**
 * The explorer's Add-to-Wishlist modal, pure half (explorerAddToWishlist :636,
 * _explorerWishlistToggleFilter :728, _explorerWishlistUpdateCount :738).
 *
 * The modal reuses the discography modal's markup and CSS, so it reuses that
 * module's footer derivation too rather than restating the same strings.
 */

import { discogFooter } from '@/routes/artist-detail/-artist-detail.discography-modal';

import type { ExplorerAlbum, ExplorerArtistSection } from './-explorer.types';

import { explorerAlbumTypeLabel } from './-explorer.core';

export interface ExplorerWishlistCard {
  /** Always a real Spotify id — groupSelectionByArtist drops anything else. */
  albumId: string;
  artistId: string | null;
  artistName: string;
  album: ExplorerAlbum;
  /** The `data-type` the filter buttons match on. */
  type: string;
  typeLabel: string;
  tracks: number;
  owned: boolean;
  /** Position WITHIN its artist section — the CSS stagger restarts per artist. */
  indexInSection: number;
  /** Which section it belongs to. Grouping by NAME would merge two artists
   *  that happen to share one, which the explorer's tree can easily contain. */
  sectionIndex: number;
}

/** The three filter buttons the modal renders. */
export const EXPLORER_WISHLIST_FILTER_TYPES = ['album', 'ep', 'single'] as const;

export type ExplorerWishlistFilters = Record<string, boolean>;

export const EXPLORER_WISHLIST_DEFAULT_FILTERS: ExplorerWishlistFilters = {
  album: true,
  ep: true,
  single: true,
};

/** One flat, ordered card list — artist sections in order, albums within. */
export function explorerWishlistCards(sections: ExplorerArtistSection[]): ExplorerWishlistCard[] {
  const cards: ExplorerWishlistCard[] = [];
  sections.forEach((section, sectionIndex) => {
    section.albums.forEach((album, indexInSection) => {
      if (!album.spotify_id) return;
      cards.push({
        albumId: album.spotify_id,
        artistId: section.artistId,
        artistName: section.name,
        album,
        type: album.album_type || 'album',
        typeLabel: explorerAlbumTypeLabel(album.album_type),
        tracks: album.track_count || 0,
        owned: !!album.owned,
        indexInSection,
        sectionIndex,
      });
    });
  });
  return cards;
}

/**
 * A type no filter button targets — 'compilation', say — is ALWAYS visible.
 * The vanilla hid cards by querying `[data-type="<filter>"]`, so a type with no
 * button was never selected and never hidden.
 */
export function explorerWishlistCardVisible(type: string, filters: ExplorerWishlistFilters) {
  return filters[type] ?? true;
}

/** Owned releases start unticked; everything else is ticked (:665). */
export function explorerWishlistDefaultChecked(cards: ExplorerWishlistCard[]): Set<string> {
  return new Set(cards.filter((card) => !card.owned).map((card) => card.albumId));
}

/**
 * What the footer counts and the submit sends: ticked AND currently visible.
 * A card hidden by a filter is excluded even while its box stays ticked —
 * `_explorerWishlistUpdateCount` skipped `display: none` cards, and the submit
 * collector applied the same test.
 */
export function explorerWishlistActive(
  cards: ExplorerWishlistCard[],
  checked: ReadonlySet<string>,
  filters: ExplorerWishlistFilters,
): ExplorerWishlistCard[] {
  return cards.filter(
    (card) => checked.has(card.albumId) && explorerWishlistCardVisible(card.type, filters),
  );
}

/** The footer line + submit label — the discography modal's, verbatim. */
export function explorerWishlistFooter(active: ExplorerWishlistCard[]) {
  return discogFooter(active.map((card) => ({ tracks: card.tracks })));
}

export interface ExplorerWishlistArtistGroup {
  artistId: unknown;
  name: string;
  albums: ExplorerAlbum[];
}

/**
 * Regroup the active cards by artist for submission, keeping first-seen artist
 * order. The vanilla built this map by walking the checked checkboxes, so an
 * artist whose every release was filtered out drops away entirely.
 */
export function groupWishlistByArtist(
  active: ExplorerWishlistCard[],
): ExplorerWishlistArtistGroup[] {
  const groups: ExplorerWishlistArtistGroup[] = [];
  const byId = new Map<string, ExplorerWishlistArtistGroup>();
  for (const card of active) {
    const key = String(card.artistId);
    let group = byId.get(key);
    if (!group) {
      group = { artistId: card.artistId, name: card.artistName, albums: [] };
      byId.set(key, group);
      groups.push(group);
    }
    group.albums.push(card.album);
  }
  return groups;
}

/** The closing line under the progress list (:870). */
export function explorerWishlistDoneText(totalAdded: number): string {
  return `Done — ${totalAdded} tracks added to wishlist`;
}
