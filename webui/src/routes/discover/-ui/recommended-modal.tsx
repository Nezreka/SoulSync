import { useState } from 'react';

import type { RecommendedArtist } from '../-discover.recommended';

import { recommendationReason, recommendationReasonTitle } from '../-discover.helpers';
import {
  recModalCountLabel,
  recModalGenres,
  recModalSource,
  recommendedMatches,
  recWatchlistClickable,
  REC_MODAL_ADD_ALL,
  REC_MODAL_SEARCH_PLACEHOLDER,
  REC_MODAL_TITLE,
  REC_WATCH_ADD_LABEL,
  REC_WATCH_ON_LABEL,
} from '../-discover.recommended';

/**
 * The Recommended Artists modal.
 *
 * Transcribed from discover.js 811-880.
 *
 * It is NOT the carousel card in a bigger box: this one carries genre tags, a
 * separate similarity line, and a source fallback that is three deep rather than
 * two — the modal can be opened from a primed cache with no fresh response to
 * read a source off, so the module-level cached source is the last resort.
 */

export interface RecommendedModalProps {
  artists: RecommendedArtist[];
  /** The source from the response that filled this modal, if there was one. */
  source: string | null;
  /** The module-level cached source — the last resort for a primed open. */
  cachedSource: string | null;
  watchingIds: Set<string>;
  images: Record<string, string>;
  addingAll?: boolean;
  buildDetailPath: (id: string, source: string | null) => string;
  onClose: () => void;
  onAddToWatchlist: (artistId: string, artistName: string) => void;
  onAddAll: () => void;
}

export function RecommendedModal({
  artists,
  source,
  cachedSource,
  watchingIds,
  images,
  addingAll,
  buildDetailPath,
  onClose,
  onAddToWatchlist,
  onAddAll,
}: RecommendedModalProps) {
  const [query, setQuery] = useState('');

  // The count is of EVERYTHING, not of what survives the filter — it is the
  // size of the recommendation set, which typing does not change.
  const countLabel = recModalCountLabel(artists.length);

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-container playlist-modal recommended-modal">
        <div className="playlist-modal-header">
          <div className="playlist-header-content" style={{ width: '100%' }}>
            <h2>{REC_MODAL_TITLE}</h2>
            <div className="playlist-quick-info">
              <span className="playlist-track-count">{countLabel}</span>
            </div>
          </div>
          <button
            type="button"
            className="playlist-modal-close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <div className="playlist-modal-body">
          <div className="recommended-actions-bar">
            <div className="recommended-search-container">
              <input
                type="text"
                className="recommended-search-input"
                id="recommended-search-input"
                placeholder={REC_MODAL_SEARCH_PLACEHOLDER}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <button
              type="button"
              className="recommended-add-all-btn"
              id="recommended-add-all-btn"
              disabled={addingAll}
              onClick={onAddAll}
            >
              {REC_MODAL_ADD_ALL}
            </button>
          </div>
          <div className="recommended-artists-grid" id="recommended-artists-grid">
            {artists
              .filter((a) => recommendedMatches((a.artist_name ?? '').toLowerCase(), query))
              .map((artist) => (
                <ModalCard
                  key={`${artist.artist_id ?? ''}:${artist.artist_name ?? ''}`}
                  artist={artist}
                  artistSource={recModalSource(artist, source, cachedSource)}
                  imageOverride={images[artist.artist_id ?? '']}
                  watching={watchingIds.has(artist.artist_id ?? '')}
                  buildDetailPath={buildDetailPath}
                  onClose={onClose}
                  onAddToWatchlist={onAddToWatchlist}
                />
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}

interface ModalCardProps {
  artist: RecommendedArtist;
  artistSource: string;
  imageOverride?: string;
  watching: boolean;
  buildDetailPath: (id: string, source: string | null) => string;
  onClose: () => void;
  onAddToWatchlist: (artistId: string, artistName: string) => void;
}

function ModalCard({
  artist,
  artistSource,
  imageOverride,
  watching,
  buildDetailPath,
  onClose,
  onAddToWatchlist,
}: ModalCardProps) {
  const [broken, setBroken] = useState(false);
  const id = artist.artist_id ?? '';
  const name = artist.artist_name ?? '';
  const image = imageOverride || artist.image_url;
  const clickable = recWatchlistClickable(id, name);

  return (
    <div
      className="recommended-artist-card"
      data-artist-name={name.toLowerCase()}
      data-artist-id={id}
      data-artist-source={artistSource}
    >
      <button
        type="button"
        className={
          watching ? 'recommended-card-watchlist-btn watching' : 'recommended-card-watchlist-btn'
        }
        data-artist-id={id}
        data-artist-name={name}
        disabled={!clickable || watching}
        onClick={() => onAddToWatchlist(id, name)}
      >
        {watching ? REC_WATCH_ON_LABEL : REC_WATCH_ADD_LABEL}
      </button>
      <a
        className="recommended-card-link"
        href={buildDetailPath(id, artistSource || null)}
        style={{ display: 'block', textDecoration: 'none', color: 'inherit' }}
        // Following the link leaves the page; the modal must not be left open
        // behind the navigation.
        onClick={onClose}
      >
        <div className="recommended-card-image">
          {image && !broken ? (
            <img src={image} alt={name} loading="lazy" onError={() => setBroken(true)} />
          ) : (
            <div className="recommended-card-image-fallback">🎤</div>
          )}
        </div>
        <div className="recommended-card-info">
          <span className="recommended-card-name">{name}</span>
          <span
            className="recommended-card-similarity"
            title={recommendationReasonTitle(artist as never)}
          >
            {recommendationReason(artist as never)}
          </span>
          <div className="recommended-card-genres">
            {recModalGenres(artist).map((g) => (
              <span className="recommended-card-genre" key={g}>
                {g}
              </span>
            ))}
          </div>
        </div>
      </a>
    </div>
  );
}
