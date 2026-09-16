import { useMemo, useState } from 'react';

import type { PodcastShowSummary } from '../-podcasts.types';
import { usePodcastContext } from './podcast-context';

import styles from './podcasts-page.module.css';

interface PodcastGridProps {
  title: string;
  shows: PodcastShowSummary[];
  isLoading?: boolean;
  onSelectShow: (show: PodcastShowSummary) => void;
}

type SortOption = 'default' | 'alpha' | 'episodes';

export function PodcastGrid({ title, shows, isLoading, onSelectShow }: PodcastGridProps) {
  const [sortBy, setSortBy] = useState<SortOption>('default');
  const { isWatchingShow, toggleWatchlist, isWatchlistBusy } = usePodcastContext();

  const sortedShows = useMemo(() => {
    if (!shows.length) return [];
    const list = [...shows];
    if (sortBy === 'alpha') {
      list.sort((a, b) => a.title.localeCompare(b.title));
    } else if (sortBy === 'episodes') {
      list.sort((a, b) => (b.episode_count || 0) - (a.episode_count || 0));
    }
    return list;
  }, [shows, sortBy]);

  if (isLoading) {
    return (
      <div>
        <div className={styles.sectionHeaderRow}>
          <div className={styles.sectionTitleGroup}>
            <h2 className={styles.sectionTitle}>{title}</h2>
            <span className={styles.sectionCount}>Loading…</span>
          </div>
        </div>

        <div className={styles.showsGrid}>
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={`skel-${i}`} className={`${styles.showCard} ${styles.showCardSkeleton}`}>
              <div className={`${styles.showArtWrapper} ${styles.skeletonPulse}`} />
              <div className={styles.showCardInfo}>
                <div className={`${styles.skeletonLine} ${styles.skeletonTitleLine}`} />
                <div className={`${styles.skeletonLine} ${styles.skeletonSubLine}`} />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (!shows.length) {
    return (
      <div className={styles.emptyContainer}>
        <span style={{ fontSize: 36 }}>🎙️</span>
        <p>No podcasts found. Try searching for a different topic or host.</p>
      </div>
    );
  }

  const isSearch = title.startsWith('Search Results for ');
  let searchWord = '';
  if (isSearch) {
    searchWord = title.replace('Search Results for ', '').replace(/^["']|["']$/g, '');
  }

  return (
    <div>
      <div className={styles.sectionHeaderRow}>
        <div className={styles.sectionTitleGroup}>
          <h2 className={styles.sectionTitle}>
            {isSearch ? (
              <>
                <svg
                  className={styles.searchTitleIcon}
                  width="20"
                  height="20"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <span>Search Results for </span>
                <span className={styles.sectionTitleHighlight}>"{searchWord}"</span>
              </>
            ) : (
              title
            )}
          </h2>
          <span className={styles.sectionCount}>{shows.length} shows</span>
          {isSearch && (
            <p className={styles.searchSubtitle}>Showing top matches from the global podcast directory</p>
          )}
        </div>

        {shows.length > 5 && (
          <div className={styles.gridControls}>
            <div className={styles.gridSortTabs} role="group" aria-label="Sort podcasts">
              <button
                type="button"
                className={`${styles.gridSortTab} ${sortBy === 'default' ? styles.gridSortTabActive : ''}`}
                onClick={() => setSortBy('default')}
              >
                Featured
              </button>
              <button
                type="button"
                className={`${styles.gridSortTab} ${sortBy === 'alpha' ? styles.gridSortTabActive : ''}`}
                onClick={() => setSortBy('alpha')}
              >
                A–Z
              </button>
              <button
                type="button"
                className={`${styles.gridSortTab} ${sortBy === 'episodes' ? styles.gridSortTabActive : ''}`}
                onClick={() => setSortBy('episodes')}
              >
                Most Episodes
              </button>
            </div>
          </div>
        )}
      </div>

      <div className={styles.showsGrid}>
        {sortedShows.map((show, idx) => {
          const key = show.itunes_id ?? show.feed_url ?? `${show.title}-${idx}`;
          const cat = show.categories?.[0] || '';
          const isWatching = isWatchingShow(show);
          const isBusy = isWatchlistBusy(show);

          return (
            <div
              key={key}
              className={styles.showCard}
              style={{ animationDelay: `${Math.min(idx * 30, 450)}ms` }}
              onClick={() => onSelectShow(show)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelectShow(show);
                }
              }}
            >
              <div className={styles.showArtWrapper}>
                {show.artwork_url ? (
                  <img
                    src={show.artwork_url}
                    alt={show.title}
                    className={styles.showArt}
                    loading="lazy"
                  />
                ) : (
                  <div className={styles.showArtPlaceholder}>🎙️</div>
                )}

                {/* Floating Quick Action Eye Button (Apple/Meta-style Frosted Material) */}
                <button
                  type="button"
                  className={`${styles.cardWatchlistEyeBtn} ${
                    isWatching ? styles.cardWatchlistEyeBtnActive : ''
                  } ${isBusy ? styles.cardWatchlistEyeBtnBusy : ''}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void toggleWatchlist(show);
                  }}
                  title={
                    isWatching
                      ? `Remove "${show.title}" from Watchlist`
                      : `Add "${show.title}" to Watchlist`
                  }
                  aria-label={
                    isWatching
                      ? `Remove "${show.title}" from Watchlist`
                      : `Add "${show.title}" to Watchlist`
                  }
                >
                  {isWatching ? (
                    <svg
                      width="15"
                      height="15"
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
                  ) : (
                    <svg
                      width="15"
                      height="15"
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
                  )}
                </button>

                <div className={styles.showArtHoverOverlay}>
                  <div className={styles.gridPlayBtn} aria-hidden="true">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                      <polygon points="6 4 20 12 6 20 6 4" />
                    </svg>
                  </div>
                </div>
              </div>

              <div className={styles.showCardInfo}>
                <h3 className={styles.showCardTitle} title={show.title}>
                  <span>{show.title}</span>
                  {show.explicit && (
                    <span className={styles.explicitTag} title="Explicit content">
                      E
                    </span>
                  )}
                </h3>
                <p className={styles.showCardAuthor} title={show.author}>
                  {show.author || 'Unknown Host'}
                </p>
                <div className={styles.showCardMetaRow}>
                  {show.episode_count != null && show.episode_count > 0 && (
                    <span className={styles.showCardEpisodes}>
                      {show.episode_count.toLocaleString()} eps
                    </span>
                  )}
                  {show.episode_count != null && show.episode_count > 0 && cat && (
                    <span className={styles.metaDot}>•</span>
                  )}
                  {cat && <span className={styles.showCardCategory}>{cat}</span>}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
