import type { HeroWatchlistButton, WatchAllPhase } from '../-discover.hero';
import type { DiscoverHeroArtist } from '../-discover.types';

import {
  heroGenres,
  heroIndicators,
  heroPopularityClass,
  heroShowsPopularity,
  heroWatchlistLabel,
  HERO_EMPTY_SUBTITLE,
  HERO_EMPTY_TITLE,
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
}: DiscoverHeroProps) {
  const empty = !artist;
  const watchLabel = watchlist?.label ?? heroWatchlistLabel(false);
  const watchAll = watchAllState(watchAllPhase);
  const indicators = heroIndicators(count, index);
  // One artist is not a slideshow: arrows and dots that go nowhere read as
  // broken controls.
  const rotates = count > 1;

  return (
    <div className="discover-hero">
      <div
        className="discover-hero-background"
        id="discover-hero-bg"
        style={artist?.image_url ? { backgroundImage: `url(${artist.image_url})` } : undefined}
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
          <div className="discover-hero-label">FEATURED ARTIST</div>
          <h1 className="discover-hero-title" id="discover-hero-title">
            {artist ? artist.artist_name : HERO_EMPTY_TITLE}
          </h1>
          <p className="discover-hero-subtitle" id="discover-hero-subtitle">
            {/* The empty subtitle tells the user what to DO; a blank billboard
                just looks broken. */}
            {artist ? 'Discover new music tailored to your taste' : HERO_EMPTY_SUBTITLE}
          </p>
          <div className="discover-hero-meta" id="discover-hero-meta">
            {artist && heroShowsPopularity(artist) && (
              <span
                className={`discover-hero-popularity ${heroPopularityClass(artist.popularity ?? 0)}`}
              >
                {artist.popularity}% match
              </span>
            )}
            {artist &&
              heroGenres(artist).map((g) => (
                <span className="discover-hero-genre" key={g}>
                  {g}
                </span>
              ))}
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

      <div className="discover-hero-indicators" id="discover-hero-indicators">
        {rotates &&
          indicators.map((ind) => (
            <button
              type="button"
              key={ind.index}
              className={ind.active ? 'discover-hero-indicator active' : 'discover-hero-indicator'}
              aria-label={ind.ariaLabel}
              aria-current={ind.active ? 'true' : undefined}
              onClick={() => onJump(ind.index)}
            />
          ))}
      </div>

      <div className="discover-hero-bottom-actions">
        <button
          type="button"
          id="discover-hero-watch-all"
          className={
            watchAll.allWatched ? 'discover-hero-watch-all all-watched' : 'discover-hero-watch-all'
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
  );
}
