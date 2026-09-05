import type { HeroWatchlistButton, WatchAllPhase } from '../-discover.hero';
import type { DiscoverHeroArtist } from '../-discover.types';

import { recommendationReason, recommendationReasonTitle } from '../-discover.helpers';
import {
  heroGenres,
  heroIndicators,
  heroPopularityClass,
  heroShowsPopularity,
  heroWatchlistLabel,
  HERO_EMPTY_SUBTITLE,
  HERO_EMPTY_TITLE,
  HERO_LOADING_SUBTITLE,
  HERO_LOADING_TITLE,
  HERO_WATCHLIST_ICON,
  watchAllState,
} from '../-discover.hero';

/**
 * The discover page's hero billboard.
 *
 * Transcribed from index.html 4245-4304 for the markup, against the decisions
 * already ported into `-discover.hero`.
 *
 * Everything conditional here has a reason that is not obvious from the markup:
 * a popularity of zero is REAL and must render, the arrows and indicators are
 * pointless with one artist, and the empty state has to say what to do rather
 * than leave a blank billboard.
 */

export interface DiscoverHeroProps {
  artist: DiscoverHeroArtist | null;
  /** How many artists are in the rotation — decides the arrows and the dots. */
  count: number;
  index: number;
  /**
   * The resolved watchlist button, or null when the check has not answered.
   *
   * Null is not "not watching": a check that failed says NOTHING about
   * membership, and the vanilla leaves the button exactly as it was rather than
   * guessing. The default copy is the same either way, so the distinction only
   * shows in the class the stylesheet keys off.
   */
  watchlist: HeroWatchlistButton | null;
  watchAllPhase: WatchAllPhase;
  discographyHref: string;
  onNavigate: (direction: number) => void;
  onJump: (index: number) => void;
  onToggleWatchlist: () => void;
  onWatchAll: () => void;
  onViewRecommended: () => void;
  onOpenBlacklist: () => void;
  /** The hero query is still in flight — show loading copy, not empty copy. */
  loading?: boolean;
}

export function DiscoverHero({
  artist,
  count,
  index,
  watchlist,
  watchAllPhase,
  discographyHref,
  onNavigate,
  onJump,
  onToggleWatchlist,
  onWatchAll,
  onViewRecommended,
  onOpenBlacklist,
  loading = false,
}: DiscoverHeroProps) {
  const empty = !artist;
  const watchLabel = watchlist?.label ?? heroWatchlistLabel(false);
  const watchAll = watchAllState(watchAllPhase);
  const indicators = heroIndicators(count, index);
  // One artist is not a slideshow: arrows and dots that go nowhere read as
  // broken controls.
  const rotates = count > 1;

  return (
    <div
      className={`discover-hero${empty ? ' discover-hero--empty' : ''}${loading ? ' discover-hero--loading' : ''}`}
    >
      <div
        className="discover-hero-background"
        id="discover-hero-bg"
        style={
          artist?.image_url
            ? {
                backgroundImage: `url('${artist.image_url}')`,
                backgroundSize: 'cover',
                backgroundPosition: 'center',
              }
            : undefined
        }
      />
      <div className="discover-hero-overlay" />

      {rotates && (
        <>
          <button
            type="button"
            className="discover-hero-nav discover-hero-nav-prev"
            aria-label="Previous artist"
            onClick={() => onNavigate(-1)}
          >
            <span>‹</span>
          </button>
          <button
            type="button"
            className="discover-hero-nav discover-hero-nav-next"
            aria-label="Next artist"
            onClick={() => onNavigate(1)}
          >
            <span>›</span>
          </button>
        </>
      )}

      <button
        type="button"
        className="tool-help-button discover-page-help-button"
        data-tool="discover-page"
        title="Learn about the Discover page"
      >
        ?
      </button>
      <button
        type="button"
        className="discover-blacklist-btn"
        title="Blocked Artists"
        onClick={onOpenBlacklist}
      >
        🚫
      </button>

      <div className="discover-hero-content">
        <div className="discover-hero-info">
          <div className="discover-hero-label">
            {artist ? 'FEATURED ARTIST' : loading ? 'LOADING SIGNALS' : 'DISCOVERY SETUP'}
          </div>
          <h1 className="discover-hero-title" id="discover-hero-title">
            {/* Three states, not two: while the hero payload is in flight
                (6-23s on the first visit after a restart — the server caches
                it after that) the copy says LOADING. The old two-state render
                told a 23-second lie: 'run a watchlist scan' while the scan's
                own data was busy arriving. */}
            {artist ? artist.artist_name : loading ? HERO_LOADING_TITLE : HERO_EMPTY_TITLE}
          </h1>
          <p
            className="discover-hero-subtitle"
            id="discover-hero-subtitle"
            // The full provenance list; the visible line truncates (468-469).
            title={artist ? recommendationReasonTitle(artist as never) : undefined}
          >
            {/* NOT static copy. The vanilla sets this to the "because you have
                X, Y" line per artist (468); the static text is only the markup's
                pre-load placeholder. Empty state still explains what to do. */}
            {artist
              ? recommendationReason(artist as never)
              : loading
                ? HERO_LOADING_SUBTITLE
                : HERO_EMPTY_SUBTITLE}
          </p>
          {/* The vanilla's meta markup verbatim (474-499): a content wrapper,
              a banded popularity tile with icon/value/label, and the genres in
              their own .hero-genres item as .genre-tag pills. The first draft
              invented flat spans and "84% match" copy — it type-checked,
              passed its tests, and matched nothing style.css styles. */}
          <div className="discover-hero-meta" id="discover-hero-meta">
            <div className="discover-hero-meta-content">
              {artist && heroShowsPopularity(artist) && (
                <div
                  className={`hero-meta-item hero-popularity ${heroPopularityClass(artist.popularity ?? 0)}`}
                >
                  <span className="meta-icon">⭐</span>
                  <span className="meta-value">{artist.popularity}/100</span>
                  <span className="meta-label">Popularity</span>
                </div>
              )}
              {artist && typeof artist.owned_album_count === 'number' && (
                <div className="hero-meta-item">
                  <span
                    className={`hero-owned${artist.owned_album_count > 0 ? '' : ' hero-owned--none'}`}
                    title="Albums by this artist in your library"
                  >
                    {artist.owned_album_count > 0
                      ? `♛ ${artist.owned_album_count} album${artist.owned_album_count === 1 ? '' : 's'} in your library`
                      : 'Not in your library yet — start here'}
                  </span>
                </div>
              )}
              {artist && heroGenres(artist).length > 0 && (
                <div className="hero-meta-item hero-genres">
                  {heroGenres(artist).map((g) => (
                    <span className="genre-tag" key={g}>
                      {g}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
          {!empty && (
            <div className="discover-hero-actions">
              <a
                className="discover-hero-button secondary"
                id="discover-hero-discography"
                href={discographyHref}
                style={{ textDecoration: 'none', color: 'inherit' }}
              >
                <span className="button-icon">📀</span>
                <span className="button-text">View Discography</span>
              </a>
              <button
                type="button"
                className={`discover-hero-button primary watchlist-toggle-btn${watchlist?.watching ? ' watching' : ''}`}
                id="discover-hero-add"
                onClick={onToggleWatchlist}
              >
                <span className="watchlist-icon">{HERO_WATCHLIST_ICON}</span>
                <span className="watchlist-text">{watchLabel}</span>
              </button>
            </div>
          )}
        </div>
        <div className="discover-hero-image" id="discover-hero-image">
          {artist?.image_url ? (
            <img src={artist.image_url} alt={artist.artist_name} />
          ) : (
            <div className="hero-image-placeholder">🎧</div>
          )}
        </div>
      </div>

      {/* One reserved row for both. They used to be two absolutely positioned
          boxes sharing bottom: 24px, one centred and one right-aligned, so the
          dots painted straight through the Watch All pill on anything narrow.
          Now they are cells of the same flex row and cannot overlap. */}
      <div className="discover-hero-controls">
        <div className="discover-hero-indicators" id="discover-hero-indicators">
          {rotates &&
            indicators.map((ind) => (
              <button
                type="button"
                key={ind.index}
                className={ind.active ? 'hero-indicator active' : 'hero-indicator'}
                aria-label={ind.ariaLabel}
                aria-current={ind.active ? 'true' : undefined}
                onClick={() => onJump(ind.index)}
              >
                <span className="hero-indicator-dot" aria-hidden="true" />
              </button>
            ))}
        </div>

        <div className="discover-hero-bottom-actions">
          <button
            type="button"
            id="discover-hero-watch-all"
            className={
              watchAll.allWatched
                ? 'discover-hero-watch-all all-watched'
                : 'discover-hero-watch-all'
            }
            disabled={watchAll.disabled}
            onClick={onWatchAll}
          >
            <span className="watch-all-icon">{HERO_WATCHLIST_ICON}</span>
            <span className="watch-all-text">{watchAll.label}</span>
          </button>
          <button
            type="button"
            className="discover-hero-view-all"
            id="discover-hero-view-all"
            onClick={onViewRecommended}
          >
            View Recommended
          </button>
        </div>
      </div>
    </div>
  );
}
