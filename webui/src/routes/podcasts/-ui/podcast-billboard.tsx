import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';

import type { PodcastEpisodeItem, PodcastShowDetail } from '../-podcasts.types';
import { usePodcastContext } from './podcast-context';

import styles from './podcasts-page.module.css';

interface PodcastBillboardProps {
  show: PodcastShowDetail;
  onPlayEpisode: (ep: PodcastEpisodeItem) => void;
}

function cleanDescription(text?: string | null): string {
  if (!text) return '';
  return text
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<\/p>/gi, ' ')
    .replace(/<\/?[^>]+(>|$)/g, '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function PodcastBillboard({ show, onPlayEpisode }: PodcastBillboardProps) {
  const navigate = useNavigate();
  const [isExpanded, setIsExpanded] = useState(false);
  const [copiedFeed, setCopiedFeed] = useState(false);
  const { isWatchingShow, toggleWatchlist, isWatchlistBusy } = usePodcastContext();

  const isWatching = isWatchingShow(show);
  const isBusy = isWatchlistBusy(show);

  const handleToggleWatchlist = () => {
    void toggleWatchlist(show);
  };

  const episodes = show.episodes || [];
  const latestEp = episodes[0];
  const cleanDesc = cleanDescription(show.description);
  const isLongDesc = cleanDesc.length > 280;

  const handleCopyFeed = async () => {
    if (!show.feed_url) return;
    try {
      await navigator.clipboard.writeText(show.feed_url);
      setCopiedFeed(true);
      setTimeout(() => setCopiedFeed(false), 2000);
    } catch {
      // Fallback
      setCopiedFeed(true);
      setTimeout(() => setCopiedFeed(false), 2000);
    }
  };

  return (
    <div className={styles.detailContainer}>
      <button type="button" className={styles.backBtn} onClick={() => void navigate({ to: '/podcasts' })}>
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <line x1="19" y1="12" x2="5" y2="12" />
          <polyline points="12 19 5 12 12 5" />
        </svg>
        <span>Back to Podcasts</span>
      </button>

      <div className={styles.billboardCard}>
        {show.artwork_url && (
          <div
            className={styles.billboardAmbientBackdrop}
            style={{ backgroundImage: `url(${show.artwork_url})` }}
            aria-hidden="true"
          />
        )}
        <div className={styles.billboardBackdropGlow} />

        <div className={styles.billboardCoverWrapper}>
          {show.artwork_url ? (
            <img src={show.artwork_url} alt={show.title} className={styles.billboardCover} />
          ) : (
            <div className={styles.showArtPlaceholder}>🎙️</div>
          )}
        </div>

        <div className={styles.billboardContent}>
          <div className={styles.billboardKicker}>
            {show.categories?.slice(0, 3).map((cat) => (
              <span key={cat} className={styles.kickerTag}>
                {cat}
              </span>
            ))}
            {show.explicit && (
              <span className={styles.kickerExplicitBadge} title="Explicit content">
                E
              </span>
            )}
            {show.language && (
              <span className={styles.essentialChip} style={{ padding: '2px 8px', fontSize: 11 }}>
                {show.language.toUpperCase()}
              </span>
            )}
          </div>

          <h1 className={styles.billboardTitle}>{show.title}</h1>
          <p className={styles.billboardAuthor}>By {show.author || 'Unknown Publisher'}</p>

          <div className={styles.billboardEssentials}>
            <span className={styles.essentialChip}>
              <span className={styles.essentialKey}>Episodes:</span>
              <span className={styles.essentialVal}>
                {episodes.length || show.episode_count || 0}
              </span>
            </span>

            {latestEp?.pub_date && (
              <span className={styles.essentialChip}>
                <span className={styles.essentialKey}>Latest:</span>
                <span className={styles.essentialVal}>
                  {new Date(latestEp.pub_date).toLocaleDateString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                  })}
                </span>
              </span>
            )}

            {show.website && (
              <span className={styles.essentialChip}>
                <span className={styles.essentialKey}>Website:</span>
                <span className={styles.essentialVal}>
                  {new URL(show.website).hostname.replace('www.', '')}
                </span>
              </span>
            )}
          </div>

          <div className={styles.billboardActions}>
            {latestEp && (
              <button
                type="button"
                className={`${styles.actionBtn} ${styles.playActionBtn}`}
                onClick={() => onPlayEpisode(latestEp)}
              >
                <span className={styles.actionBtnIcon}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                    <polygon points="5 3 19 12 5 21 5 3" />
                  </svg>
                </span>
                <span className={styles.actionBtnText}>Play Latest Episode</span>
              </button>
            )}

            <button
              type="button"
              className={`library-artist-watchlist-btn${isWatching ? ' watching' : ''}`}
              id="podcast-watchlist-btn"
              disabled={isBusy}
              onClick={() => void handleToggleWatchlist()}
              title={isWatching ? 'Remove from Watchlist' : 'Add to Watchlist'}
            >
              <span className="watchlist-icon">👁️</span>
              <span className="watchlist-text">
                {isBusy ? 'Updating…' : isWatching ? 'Watching' : 'Add to Watchlist'}
              </span>
            </button>

            {show.feed_url && (
              <button
                type="button"
                className={`${styles.actionBtn} ${styles.rssActionBtn}${copiedFeed ? ` ${styles.rssCopied}` : ''}`}
                onClick={handleCopyFeed}
                title="Copy RSS Feed URL to clipboard"
              >
                <span className={styles.actionBtnIcon}>
                  {copiedFeed ? (
                    <svg
                      width="13"
                      height="13"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  ) : (
                    <svg
                      width="13"
                      height="13"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M4 11a9 9 0 0 1 9 9" />
                      <path d="M4 4a16 16 0 0 1 16 16" />
                      <circle cx="5" cy="19" r="1" />
                    </svg>
                  )}
                </span>
                <span className={styles.actionBtnText}>{copiedFeed ? 'Feed URL Copied' : 'RSS Feed'}</span>
              </button>
            )}

            {show.website && (
              <a
                href={show.website}
                target="_blank"
                rel="noopener noreferrer"
                className={`${styles.actionBtn} ${styles.siteActionBtn}`}
              >
                <span className={styles.actionBtnIcon}>
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                    <polyline points="15 3 21 3 21 9" />
                    <line x1="10" y1="14" x2="21" y2="3" />
                  </svg>
                </span>
                <span className={styles.actionBtnText}>Visit Site ↗</span>
              </a>
            )}
          </div>

          {cleanDesc && (
            <div className={styles.billboardDesc}>
              {isLongDesc && !isExpanded ? `${cleanDesc.slice(0, 260)}…` : cleanDesc}
              {isLongDesc && (
                <button
                  type="button"
                  className={styles.readMoreBtn}
                  onClick={() => setIsExpanded(!isExpanded)}
                >
                  {isExpanded ? 'Show less' : 'Read more'}
                </button>
              )}
            </div>
          )}
        </div>

        {/* Right Column: Latest Episode Showcase & Quick Stats Panel */}
        {latestEp && (
          <div className={styles.billboardShowcasePanel}>
            <div className={styles.billboardLatestCard}>
              <div className={styles.billboardLatestHeader}>
                <span className={styles.latestLiveDot} />
                <span className={styles.billboardLatestKicker}>LATEST RELEASE</span>
              </div>
              <h3 className={styles.billboardLatestTitle} title={latestEp.title}>
                {latestEp.title}
              </h3>
              <div className={styles.billboardLatestMeta}>
                {latestEp.pub_date && (
                  <span>
                    {new Date(latestEp.pub_date).toLocaleDateString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      year: 'numeric',
                    })}
                  </span>
                )}
                {latestEp.duration_seconds != null && latestEp.duration_seconds > 0 && (
                  <>
                    <span className={styles.metaDot}>•</span>
                    <span>{Math.floor(latestEp.duration_seconds / 60)} min</span>
                  </>
                )}
              </div>
              <button
                type="button"
                className={styles.billboardLatestPlayBtn}
                onClick={() => onPlayEpisode(latestEp)}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                <span>Play Episode</span>
              </button>
            </div>

            <div className={styles.billboardStatsGrid}>
              <div className={styles.billboardStatItem}>
                <span className={styles.billboardStatLabel}>Total Archive</span>
                <span className={styles.billboardStatValue}>
                  {(episodes.length || show.episode_count || 0).toLocaleString()} eps
                </span>
              </div>
              <div className={styles.billboardStatItem}>
                <span className={styles.billboardStatLabel}>Language</span>
                <span className={styles.billboardStatValue}>
                  {(show.language || 'en').toUpperCase()}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
