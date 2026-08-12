import { useState } from 'react';

import type { RecommendedArtist, RecommendedCard } from '../-discover.recommended';

import {
  recommendedCard,
  recommendedVisible,
  recWatchlistClickable,
  REC_WATCH_ADD_LABEL,
  REC_WATCH_ON_LABEL,
  RECOMMENDED_SECTIONS,
} from '../-discover.recommended';
import { DiscoverSection } from './discover-section';

/**
 * The two recommendation shelves.
 *
 * Transcribed from discover.js 965-1003 for the card and 1039-1114 for the two
 * section configs.
 *
 * They are one component because they ARE one card with two reason functions —
 * the vanilla passes `reasonFn`/`titleFn` in and shares everything else. The
 * only visible difference is the copy, which comes from the configs already
 * ported into `-discover.recommended`.
 */

export type RecommendedKind = 'recommended' | 'listening';

export interface RecommendedShelfProps {
  kind: RecommendedKind;
  artists: RecommendedArtist[];
  /** The response's source, which each card may override with its own. */
  source: string;
  loaded: boolean;
  /** Ids known to be on the watchlist already. */
  watchingIds: Set<string>;
  /** Resolved images that arrived after the first paint. */
  images: Record<string, string>;
  buildDetailPath: (id: string, source: string | null) => string;
  onAddToWatchlist: (artistId: string, artistName: string, source?: string) => void;
  /** Only the 'recommended' shelf has a View All; 'listening' does not. */
  onViewAll?: () => void;
}

const TITLES: Record<RecommendedKind, { title: string; subtitle: string }> = {
  recommended: {
    title: "Artists You'll Like",
    subtitle: 'Similar to whole shelves of your library — not yet on your watchlist',
  },
  listening: {
    title: 'Based On Your Listening',
    subtitle: "Artists you'd love — ranked from who you actually play the most",
  },
};

export function RecommendedShelf({
  kind,
  artists,
  source,
  loaded,
  watchingIds,
  images,
  buildDetailPath,
  onAddToWatchlist,
  onViewAll,
}: RecommendedShelfProps) {
  const visible = recommendedVisible(artists);
  const def = RECOMMENDED_SECTIONS[kind];
  const copy = TITLES[kind];

  return (
    <DiscoverSection
      id={kind === 'recommended' ? 'recommended-artists-section' : 'listening-recs-section'}
      title={copy.title}
      subtitle={copy.subtitle}
      count={visible.length}
      loaded={loaded}
      emptyMessage={def.emptyMessage}
      actions={
        onViewAll && (
          <button
            type="button"
            className="btn btn--sm btn--secondary ya-header-btn ya-viewall-btn"
            onClick={onViewAll}
          >
            <span>View All</span>
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
            >
              <path d="M5 12h14" />
              <path d="M12 5l7 7-7 7" />
            </svg>
          </button>
        )
      }
    >
      {/* `.discover-grid`, not a bespoke class — the shelves share the page's
          grid, and the id is what the enrichment pass targets. */}
      <div className="discover-grid" id={`${def.id}-carousel`}>
        {visible.map((artist) => (
          <RecommendedMiniCard
            key={`${artist.artist_id ?? ''}:${artist.artist_name ?? ''}`}
            card={recommendedCard(artist, source, kind)}
            imageOverride={images[artist.artist_id ?? '']}
            watching={watchingIds.has(artist.artist_id ?? '')}
            buildDetailPath={buildDetailPath}
            onAddToWatchlist={onAddToWatchlist}
          />
        ))}
      </div>
    </DiscoverSection>
  );
}

/** Not exported: the shelf is the only caller, and an export nothing outside
 *  uses is an untested contract by definition. */
interface RecommendedMiniCardProps {
  card: RecommendedCard;
  /** An image resolved by the enrichment pass, which wins over the payload's. */
  imageOverride?: string;
  watching: boolean;
  buildDetailPath: (id: string, source: string | null) => string;
  onAddToWatchlist: (artistId: string, artistName: string, source?: string) => void;
}

function RecommendedMiniCard({
  card,
  imageOverride,
  watching,
  buildDetailPath,
  onAddToWatchlist,
}: RecommendedMiniCardProps) {
  // A broken image must fall back, not leave a hole. The vanilla does this with
  // an inline onerror that rewrites the parent; state is the React equivalent.
  const [broken, setBroken] = useState(false);
  const image = imageOverride || card.image;
  // Clickable = there is a SENDABLE id; a name alone 400s at the endpoint.
  const clickable = recWatchlistClickable(card.watchId, card.artistName) && card.watchId !== '';

  return (
    <div
      className="ya-card recommended-artist-card"
      data-artist-name={card.filterName}
      data-artist-id={card.artistId}
      data-artist-source={card.source}
    >
      <a
        className="recommended-card-link"
        href={buildDetailPath(card.artistId, card.source || null)}
        style={{ display: 'block', textDecoration: 'none', color: 'inherit' }}
      >
        <div className="ya-card-img recommended-card-image">
          {image && !broken ? (
            <img src={image} alt={card.artistName} loading="lazy" onError={() => setBroken(true)} />
          ) : (
            <div className="recommended-card-image-fallback">🎤</div>
          )}
        </div>
        <div className="ya-card-gradient" />
        <div className="ya-card-info">
          <div className="ya-card-name">{card.artistName}</div>
          {/* The chips REPLACE the reason line when present — they are the same
              reason, said more clearly. */}
          {card.showChips ? (
            <div className="ya-card-why">
              {card.chips.map((chip) => (
                <span className={`ya-why-chip ya-why-${chip.type}`} key={chip.label}>
                  {chip.icon} {chip.label}
                </span>
              ))}
            </div>
          ) : (
            <div className="ya-card-sub" title={card.reasonTitle}>
              {card.reason}
            </div>
          )}
        </div>
      </a>
      <button
        type="button"
        className={
          watching
            ? 'recommended-card-watchlist-btn ya-card-reco-btn watching'
            : 'recommended-card-watchlist-btn ya-card-reco-btn'
        }
        data-artist-id={card.artistId}
        data-artist-name={card.artistName}
        // A card with neither an id nor a name cannot be watched — the request
        // would have nothing to identify.
        disabled={!clickable || watching}
        onClick={() => onAddToWatchlist(card.watchId, card.artistName, card.watchSource)}
      >
        {watching ? REC_WATCH_ON_LABEL : REC_WATCH_ADD_LABEL}
      </button>
    </div>
  );
}
