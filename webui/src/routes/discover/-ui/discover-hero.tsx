import type { HeroWatchlistButton, WatchAllPhase } from '../-discover.hero';
import type { DiscoverHeroArtist } from '../-discover.types';

import { explanationLine, explanationParts, explanationTitle } from '../-discover.explanation';
import {
  heroGenres,
  heroIndicators,
  heroPopularityClass,
  heroPopularityWords,
  heroShowsPopularity,
  heroWatchlistLabel,
  HERO_EMPTY_SUBTITLE,
  HERO_WATCHLIST_SUBTITLE,
  HERO_EMPTY_TITLE,
  HERO_LOADING_SUBTITLE,
  HERO_LOADING_TITLE,
  watchAllState,
  WATCH_ALL_IDLE,
  heroIds,
} from '../-discover.hero';
import { FeedbackMenu } from './feedback-menu';

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
  /**
   * the whole rotation, so the dots can be the artists themselves: a strip of
   * faces you can pick from instead of eight identical dots. optional, dots
   * without it.
   */
  artists?: DiscoverHeroArtist[];
  /** the pointer or focus is on the hero: hold the rotation while they read. */
  onPauseChange?: (paused: boolean) => void;
}

/** the reason line, with the artists it names set apart from the words around them. */
function HeroReason({ artist }: { artist: DiscoverHeroArtist }) {
  const parts = explanationParts(artist.explanation);
  if (!parts) {
    return (
      <>
        {explanationLine(artist.explanation) ||
          (artist.is_watchlist ? HERO_WATCHLIST_SUBTITLE : '')}
      </>
    );
  }
  return (
    <>
      {parts.lead}{' '}
      {parts.names.map((name, i) => (
        <span key={name}>
          {i > 0 ? (parts.more > 0 ? ', ' : ' & ') : ''}
          <strong className="discover-hero-seed">{name}</strong>
        </span>
      ))}
      {parts.more > 0 ? ` +${parts.more} more` : ''}
    </>
  );
}

/** five bars, lit by popularity. reads at a glance where "84/100" needs reading. */
function PopularityMeter({ value }: { value: number }) {
  const lit = Math.max(1, Math.min(5, Math.round(value / 20)));
  return (
    <span className="hero-pop-meter" aria-hidden="true">
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className={i < lit ? 'on' : ''} />
      ))}
    </span>
  );
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
  artists,
  onPauseChange,
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
      onMouseEnter={() => onPauseChange?.(true)}
      onMouseLeave={() => onPauseChange?.(false)}
      onFocus={() => onPauseChange?.(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onPauseChange?.(false);
      }}
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
        aria-label="Learn about the Discover page"
      >
        ?
      </button>
      <button
        type="button"
        className="discover-blacklist-btn"
        title="Blocked artists"
        aria-label="Blocked artists"
        onClick={onOpenBlacklist}
      >
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>

      <div className="discover-hero-content">
        <div className="discover-hero-info">
          <div className="discover-hero-label">
            <span className="discover-hero-label-dot" aria-hidden="true" />
            {artist ? 'Picked for you' : loading ? 'Finding picks' : 'Getting started'}
            {artist && rotates ? (
              <span className="discover-hero-count">
                {index + 1} of {count}
              </span>
            ) : null}
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
            title={artist ? explanationTitle(artist.explanation) : undefined}
          >
            {/* NOT static copy: the server's explanation for this artist. The
                watchlist fallback has none (nothing recommended it), and says
                what it is instead of claiming a similarity it doesn't have.
                Empty state still explains what to do. */}
            {artist ? (
              <HeroReason artist={artist} />
            ) : loading ? (
              HERO_LOADING_SUBTITLE
            ) : (
              HERO_EMPTY_SUBTITLE
            )}
          </p>
          {/* The vanilla's meta markup verbatim (474-499): a content wrapper,
              a banded popularity tile with icon/value/label, and the genres in
              their own .hero-genres item as .genre-tag pills. The first draft
              invented flat spans and "84% match" copy — it type-checked,
              passed its tests, and matched nothing style.css styles. */}
          <div className="discover-hero-meta" id="discover-hero-meta">
            <div className="discover-hero-meta-content">
              {artist && heroGenres(artist).length > 0 && (
                <div className="hero-meta-item hero-genres">
                  {heroGenres(artist).map((g) => (
                    <span className="genre-tag" key={g}>
                      {g}
                    </span>
                  ))}
                </div>
              )}
              {artist && heroShowsPopularity(artist) && (
                <div
                  className={`hero-meta-item hero-popularity ${heroPopularityClass(artist.popularity ?? 0)}`}
                  title={`Popularity ${artist.popularity}/100`}
                >
                  <PopularityMeter value={artist.popularity ?? 0} />
                  <span className="meta-value">{artist.popularity}/100</span>
                  <span className="meta-label">{heroPopularityWords(artist.popularity ?? 0)}</span>
                </div>
              )}
              {artist && typeof artist.owned_album_count === 'number' && (
                <div className="hero-meta-item">
                  <span
                    className={`hero-owned${artist.owned_album_count > 0 ? '' : ' hero-owned--none'}`}
                    title="Albums by this artist in your library"
                  >
                    {artist.owned_album_count > 0
                      ? `${artist.owned_album_count} album${artist.owned_album_count === 1 ? '' : 's'} in your library`
                      : 'New to your library'}
                  </span>
                </div>
              )}
            </div>
          </div>
          {!empty && (
            <div className="discover-hero-actions">
              <button
                type="button"
                className={`discover-hero-button primary watchlist-toggle-btn${watchlist?.watching ? ' watching' : ''}`}
                id="discover-hero-add"
                onClick={onToggleWatchlist}
              >
                <span className="watchlist-icon" aria-hidden="true">
                  {watchlist?.watching ? (
                    <svg viewBox="0 0 24 24" width="16" height="16">
                      <path
                        d="M5 12.5l4.5 4.5L19 7.5"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" width="16" height="16">
                      <path
                        d="M12 5v14M5 12h14"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                      />
                    </svg>
                  )}
                </span>
                <span className="watchlist-text">{watchLabel}</span>
              </button>
              <a
                className="discover-hero-button secondary"
                id="discover-hero-discography"
                href={discographyHref}
                style={{ textDecoration: 'none', color: 'inherit' }}
              >
                <span className="button-text">View discography</span>
              </a>
              {artist?.artist_name ? (
                <FeedbackMenu
                  entity={{ type: 'artist', name: artist.artist_name, ids: heroIds(artist) }}
                  explanation={artist.explanation}
                  // the refetch drops it; move on now rather than keep showing it
                  onHidden={() => onNavigate(1)}
                  className="discover-hero-feedback-btn"
                />
              ) : null}
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
                className={`hero-indicator${ind.active ? ' active' : ''}${artists?.[ind.index] ? ' hero-indicator--face' : ''}`}
                aria-label={
                  artists?.[ind.index]?.artist_name
                    ? `Show ${artists[ind.index].artist_name}`
                    : ind.ariaLabel
                }
                title={artists?.[ind.index]?.artist_name}
                aria-current={ind.active ? 'true' : undefined}
                onClick={() => onJump(ind.index)}
              >
                {artists?.[ind.index]?.image_url ? (
                  <img
                    className="hero-indicator-face"
                    src={artists[ind.index].image_url}
                    alt=""
                    loading="lazy"
                  />
                ) : artists?.[ind.index] ? (
                  <span
                    className="hero-indicator-face hero-indicator-face--blank"
                    aria-hidden="true"
                  >
                    {artists[ind.index].artist_name.slice(0, 1)}
                  </span>
                ) : (
                  <span className="hero-indicator-dot" aria-hidden="true" />
                )}
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
            <span className="watch-all-text">
              {watchAll.label === WATCH_ALL_IDLE && count > 1
                ? `Watch all ${count}`
                : watchAll.label}
            </span>
          </button>
          <button
            type="button"
            className="discover-hero-view-all"
            id="discover-hero-view-all"
            onClick={onViewRecommended}
          >
            See all picks
          </button>
        </div>
      </div>
    </div>
  );
}
