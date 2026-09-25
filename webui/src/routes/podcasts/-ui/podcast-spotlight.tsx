import { useEffect, useRef, useState } from 'react';

import type { PodcastShowSummary } from '../-podcasts.types';
import { usePodcastContext } from './podcast-context';

import styles from './podcasts-page.module.css';

interface PodcastSpotlightProps {
  shows: PodcastShowSummary[];
  onSelectShow: (show: PodcastShowSummary) => void;
}

export function PodcastSpotlight({ shows, onSelectShow }: PodcastSpotlightProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isHovered, setIsHovered] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { isWatchingShow, toggleWatchlist, isWatchlistBusy } = usePodcastContext();

  const totalShows = Math.min(shows.length, 5);
  const activeShow = shows[currentIndex] || shows[0];

  // Auto-advance carousel every 7.5s, pause on hover
  useEffect(() => {
    if (totalShows <= 1 || isHovered) {
      if (timerRef.current) clearInterval(timerRef.current);
      return;
    }

    timerRef.current = setInterval(() => {
      setCurrentIndex((prev) => (prev + 1) % totalShows);
    }, 5500);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [totalShows, isHovered]);

  // Keep index within bounds if shows array changes
  useEffect(() => {
    if (currentIndex >= totalShows) {
      setCurrentIndex(0);
    }
  }, [currentIndex, totalShows]);

  if (!activeShow) return null;

  const cat = activeShow.categories?.[0] || 'Top Pick';
  const displayDesc =
    activeShow.description?.trim() ||
    `Explore episodes, investigative stories, and audio insights from ${activeShow.author || activeShow.title}.`;

  const handlePrev = (e: React.MouseEvent) => {
    e.stopPropagation();
    setCurrentIndex((prev) => (prev - 1 + totalShows) % totalShows);
  };

  const handleNext = (e: React.MouseEvent) => {
    e.stopPropagation();
    setCurrentIndex((prev) => (prev + 1) % totalShows);
  };

  const handleDotClick = (e: React.MouseEvent, idx: number) => {
    e.stopPropagation();
    setCurrentIndex(idx);
  };

  return (
    <div
      className={styles.spotlightCard}
      onClick={() => onSelectShow(activeShow)}
      role="button"
      tabIndex={0}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelectShow(activeShow);
        } else if (e.key === 'ArrowRight') {
          e.preventDefault();
          setCurrentIndex((prev) => (prev + 1) % totalShows);
        } else if (e.key === 'ArrowLeft') {
          e.preventDefault();
          setCurrentIndex((prev) => (prev - 1 + totalShows) % totalShows);
        }
      }}
    >
      {/* Full-bleed ambient backdrop with zero seams */}
      {activeShow.artwork_url && (
        <div
          className={styles.spotlightAmbientBackdrop}
          style={{ backgroundImage: `url(${activeShow.artwork_url})` }}
          aria-hidden="true"
        />
      )}
      <div className={styles.spotlightScrim} aria-hidden="true" />

      {/* Foreground Left: Cover Art */}
      <div className={styles.spotlightArtWrapper}>
        {activeShow.artwork_url ? (
          <img
            src={activeShow.artwork_url}
            alt={activeShow.title}
            className={styles.spotlightArt}
          />
        ) : (
          <div className={styles.showArtPlaceholder}>🎙️</div>
        )}
      </div>

      {/* Foreground Center-Left: Show Info */}
      <div className={styles.spotlightContent}>
        <div className={styles.spotlightBadgeRow}>
          <span className={styles.spotlightBadge}>★ FEATURED PODCAST</span>
          <span className={styles.spotlightCategoryChip}>{cat}</span>
          {activeShow.explicit && <span className={styles.kickerExplicitBadge}>E</span>}
          {totalShows > 1 && (
            <span className={styles.spotlightIndexBadge}>
              {currentIndex + 1} of {totalShows}
            </span>
          )}
        </div>

        <h2 className={styles.spotlightTitle}>{activeShow.title}</h2>
        <p className={styles.spotlightAuthor}>By {activeShow.author || 'Featured Publisher'}</p>

        <p className={styles.spotlightDesc}>
          {displayDesc.length > 240 ? `${displayDesc.slice(0, 240)}…` : displayDesc}
        </p>

        <div className={styles.spotlightActions}>
          <button
            type="button"
            className={styles.spotlightListenBtn}
            onClick={() => onSelectShow(activeShow)}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
            <span>Explore Episodes</span>
          </button>

          <button
            type="button"
            className={`${styles.spotlightWatchlistBtn} ${
              isWatchingShow(activeShow) ? styles.spotlightWatchlistBtnActive : ''
            } ${isWatchlistBusy(activeShow) ? styles.spotlightWatchlistBtnBusy : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              void toggleWatchlist(activeShow);
            }}
            disabled={isWatchlistBusy(activeShow)}
            aria-label={
              isWatchingShow(activeShow)
                ? `Remove "${activeShow.title}" from Watchlist`
                : `Add "${activeShow.title}" to Watchlist`
            }
          >
            {isWatchingShow(activeShow) ? (
              <>
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                  <circle cx="12" cy="12" r="3" fill="currentColor" />
                </svg>
                <span>Watching</span>
                <span className={styles.spotlightWatchlistCheck}>✓</span>
              </>
            ) : (
              <>
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
                <span>Add to Watchlist</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Foreground Right: Balanced Meta Highlights */}
      <div className={styles.spotlightMetaPanel}>
        <div className={styles.spotlightMetaItem}>
          <span className={styles.spotlightMetaIcon}>🎙️</span>
          <div className={styles.spotlightMetaTextGroup}>
            <span className={styles.spotlightMetaLabel}>Available Content</span>
            <span className={styles.spotlightMetaValue}>
              {activeShow.episode_count ? `${activeShow.episode_count} Episodes` : 'Full Archive'}
            </span>
          </div>
        </div>

        <div className={styles.spotlightMetaItem}>
          <span className={styles.spotlightMetaIcon}>🔥</span>
          <div className={styles.spotlightMetaTextGroup}>
            <span className={styles.spotlightMetaLabel}>Curator Pick</span>
            <span className={styles.spotlightMetaValue}>Top in {cat}</span>
          </div>
        </div>

        <div className={styles.spotlightMetaItem}>
          <span className={styles.spotlightMetaIcon}>⚡</span>
          <div className={styles.spotlightMetaTextGroup}>
            <span className={styles.spotlightMetaLabel}>Audio Format</span>
            <span className={styles.spotlightMetaValue}>Direct RSS Audio</span>
          </div>
        </div>
      </div>

      {/* Carousel Navigation Controls (Arrows + Dots) */}
      {totalShows > 1 && (
        <div className={styles.spotlightCarouselControls} onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            className={styles.carouselArrowBtn}
            onClick={handlePrev}
            aria-label="Previous featured podcast"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>

          <div className={styles.carouselDots}>
            {shows.slice(0, totalShows).map((show, idx) => (
              <button
                key={show.feed_url || show.title + idx}
                type="button"
                className={`${styles.carouselDot} ${idx === currentIndex ? styles.carouselDotActive : ''}`}
                onClick={(e) => handleDotClick(e, idx)}
                aria-label={`Go to slide ${idx + 1}: ${show.title}`}
              >
                {idx === currentIndex && (
                  <span
                    key={`dot-prog-${currentIndex}`}
                    className={`${styles.carouselDotProgressBar} ${isHovered ? styles.carouselDotProgressPaused : ''}`}
                  />
                )}
              </button>
            ))}
          </div>

          <button
            type="button"
            className={styles.carouselArrowBtn}
            onClick={handleNext}
            aria-label="Next featured podcast"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}
