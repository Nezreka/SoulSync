import type { CacheItem, CacheSectionDef, CacheSectionKey } from '../-discover.cache-sections';

import {
  cacheDiscoverCard,
  GENRE_EXPLORER_SECTION,
  genrePill,
  gridClamp,
} from '../-discover.cache-sections';
import { DiscoverAlbumCard } from './album-shelves';

/**
 * The cache-backed discovery shelves.
 *
 * Transcribed from `_insertCacheSection` + `_cacheDiscoverCard` + `_clampGrid`
 * + the five loaders (discover.js 10452-10788). All five render through
 * `_insertCacheSection`'s one template — eyebrow subtitle ABOVE an h3 title,
 * like the BYLT shelves — and a loader that gets nothing back creates NO
 * section element at all, so an empty shelf renders null here, not an empty
 * box (`cacheSectionShouldRender`; the caller can also just pass no items).
 *
 * The card is the shared DiscoverAlbumCard: the vanilla comment on
 * `_cacheDiscoverCard` says outright it is the "Unified Discover album card
 * (#discover redesign) — same .ya-card", and the owned tick rides the same
 * badge slot Your Albums uses.
 */

export interface CacheShelfProps {
  def: CacheSectionDef;
  items: CacheItem[];
  /** The Show all / Show less state — one flag per shelf, owned by the hook. */
  expanded: boolean;
  onToggleExpand: () => void;
  onOpenItem: (key: CacheSectionKey, index: number) => void;
}

export function CacheShelf({ def, items, expanded, onToggleExpand, onOpenItem }: CacheShelfProps) {
  if (items.length === 0) return null;
  const clamp = gridClamp(items.length, expanded);

  return (
    <div className="discover-section" id={def.id}>
      <div className="discover-section-header">
        <div>
          <div className="discover-section-subtitle">{def.subtitle}</div>
          <h3 className="discover-section-title">{def.title}</h3>
        </div>
      </div>
      <div className="discover-grid">
        {items.map((item, i) => {
          const card = cacheDiscoverCard(item);
          // The vanilla clamps by toggling display on the card (10635) rather
          // than dropping it; kept, so expanding is instant and image loads
          // are not re-triggered.
          return (
            <div key={i} style={i < clamp.visibleCount ? undefined : { display: 'none' }}>
              <DiscoverAlbumCard
                cover={card.cover}
                albumName={card.title}
                artistName={card.subtitle}
                badge={card.ownedBadge ? { className: 'owned', icon: '✓' } : undefined}
                onOpen={() => onOpenItem(def.key, i)}
              />
            </div>
          );
        })}
      </div>
      {/* The toggle is the grid's SIBLING (10641), outside the card flow. */}
      {clamp.toggleVisible && (
        <button type="button" className="discover-show-all" onClick={onToggleExpand}>
          {clamp.label}
        </button>
      )}
    </div>
  );
}

export interface GenreExplorerSectionProps {
  genres: { genre?: string; explored?: boolean; artist_count?: number }[];
  onOpenGenre: (genre: string) => void;
}

/**
 * The odd one out (10745): pills in a `.genre-explorer-grid` with NO
 * `.discover-grid` wrapper and no clamp. Its top-of-page position is the
 * page's ordering concern, not this component's.
 */
export function GenreExplorerSection({ genres, onOpenGenre }: GenreExplorerSectionProps) {
  if (genres.length === 0) return null;

  return (
    <div className="discover-section" id={GENRE_EXPLORER_SECTION.id}>
      <div className="discover-section-header">
        <div>
          <div className="discover-section-subtitle">{GENRE_EXPLORER_SECTION.subtitle}</div>
          <h3 className="discover-section-title">{GENRE_EXPLORER_SECTION.title}</h3>
        </div>
      </div>
      <div className="genre-explorer-grid">
        {genres.map((g) => {
          const pill = genrePill(g);
          return (
            <div
              key={pill.genre}
              className={`genre-explorer-pill ${pill.explored ? 'explored' : 'unexplored'}`}
              style={{ cursor: 'pointer' }}
              onClick={() => onOpenGenre(pill.genre)}
            >
              <span className="genre-pill-name">{pill.genre}</span>
              <span className="genre-pill-count">{pill.countLabel}</span>
              {pill.isNew && <span className="genre-pill-badge">New</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
