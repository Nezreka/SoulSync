import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { WatchlistPodcast } from '../-watchlist.types';

import {
  removeWatchlistPodcast,
  watchlistPodcastsQueryOptions,
} from '../-watchlist.api';
import { formatRelativeScanTime } from '../-watchlist.helpers';
import { PodcastSettingsModal } from './podcast-settings-modal';
import styles from './watchlist-page.module.css';

interface WatchlistPodcastsTabProps {
  profileId: number;
  searchFilter?: string;
}

export function WatchlistPodcastsTab({ profileId, searchFilter = '' }: WatchlistPodcastsTabProps) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const podcastsQuery = useQuery(watchlistPodcastsQueryOptions(profileId));
  const podcasts = podcastsQuery.data ?? [];

  const [activeMenuPodcastId, setActiveMenuPodcastId] = useState<number | null>(null);
  const [selectedPodcastForSettings, setSelectedPodcastForSettings] = useState<WatchlistPodcast | null>(null);

  const menuRef = useRef<HTMLDivElement | null>(null);

  // Close dropdown menu on outside click
  useEffect(() => {
    if (activeMenuPodcastId === null) return;
    const handleDocumentClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setActiveMenuPodcastId(null);
      }
    };
    document.addEventListener('mousedown', handleDocumentClick);
    return () => {
      document.removeEventListener('mousedown', handleDocumentClick);
    };
  }, [activeMenuPodcastId]);

  const filteredPodcasts = useMemo(() => {
    const q = searchFilter.trim().toLowerCase();
    if (!q) return podcasts;
    return podcasts.filter(
      (pod) =>
        pod.title?.toLowerCase().includes(q) ||
        pod.author?.toLowerCase().includes(q) ||
        pod.description?.toLowerCase().includes(q),
    );
  }, [podcasts, searchFilter]);

  const removeMutation = useMutation({
    mutationFn: ({ feedUrl, itunesId }: { feedUrl: string; itunesId?: number | null }) =>
      removeWatchlistPodcast(feedUrl, itunesId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: watchlistPodcastsQueryOptions(profileId).queryKey,
      });
      try {
        window.updateWatchlistButtonCount?.();
      } catch {
        /* non-fatal */
      }
      setActiveMenuPodcastId(null);
    },
    onError: (err: Error) => {
      window.showToast?.(err.message || 'Could not remove podcast', 'error');
    },
  });

  const handleOpenShow = (podcast: WatchlistPodcast) => {
    const podcastId = podcast.itunes_id
      ? String(podcast.itunes_id)
      : encodeURIComponent(podcast.feed_url);
    void navigate({ to: '/podcasts/$podcastId', params: { podcastId } });
  };

  const handleRemove = async (podcast: WatchlistPodcast) => {
    setActiveMenuPodcastId(null);
    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove Podcast from Watchlist',
      message: `Stop monitoring "${podcast.title || 'this podcast'}" for new episodes?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    removeMutation.mutate({ feedUrl: podcast.feed_url, itunesId: podcast.itunes_id });
  };

  if (podcasts.length === 0) {
    return (
      <div className="watchlist-page-empty">
        <div className="watchlist-page-empty-icon">
          <svg
            width="64"
            height="64"
            viewBox="0 0 24 24"
            fill="none"
            stroke="rgba(255,255,255,0.15)"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="22" />
          </svg>
        </div>
        <h3>No podcasts in watchlist</h3>
        <p>Browse or search podcasts, then click "Add to Watchlist" to follow new episodes and manage downloads.</p>
        <button
          className="btn btn--primary"
          type="button"
          onClick={() => void navigate({ to: '/podcasts' })}
        >
          Explore Podcasts
        </button>
      </div>
    );
  }

  return (
    <div className={styles.podcastsTabContainer}>
      {filteredPodcasts.length === 0 ? (
        <div className="watchlist-page-empty" style={{ padding: '40px 0' }}>
          <p style={{ color: 'var(--text-secondary, #9aa0aa)' }}>
            No podcasts match &quot;{searchFilter}&quot;
          </p>
        </div>
      ) : (
        <div className={styles.podcastsGrid}>
          {filteredPodcasts.map((podcast) => {
            const isMenuOpen = activeMenuPodcastId === podcast.id;

            return (
              <div
                key={podcast.id}
                className={styles.podcastCard}
                onClick={() => handleOpenShow(podcast)}
                role="button"
                aria-label={podcast.title}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    handleOpenShow(podcast);
                  }
                }}
              >
                {/* Cover Art Wrapper */}
                <div className={styles.podcastArtWrapper}>
                  {podcast.artwork_url ? (
                    <img
                      src={podcast.artwork_url}
                      alt={podcast.title}
                      className={styles.podcastArt}
                      loading="lazy"
                    />
                  ) : (
                    <div className={styles.podcastArtPlaceholder}>🎙️</div>
                  )}

                  {/* Menu Button */}
                  <div
                    className={styles.podcastActions}
                    onClick={(e) => e.stopPropagation()}
                    ref={isMenuOpen ? menuRef : undefined}
                  >
                    <button
                      type="button"
                      className={styles.podcastMenuBtn}
                      title="Podcast options"
                      aria-label="Podcast options"
                      onClick={() =>
                        setActiveMenuPodcastId(isMenuOpen ? null : podcast.id)
                      }
                    >
                      •••
                    </button>

                    {isMenuOpen && (
                      <div className={styles.podcastDropdownMenu}>
                        <button
                          type="button"
                          className={styles.podcastDropdownItem}
                          onClick={() => {
                            setActiveMenuPodcastId(null);
                            setSelectedPodcastForSettings(podcast);
                          }}
                        >
                          <span>⚙️</span>
                          <span>Download Settings</span>
                        </button>
                        <button
                          type="button"
                          className={`${styles.podcastDropdownItem} ${styles.podcastDropdownItemDanger}`}
                          onClick={() => void handleRemove(podcast)}
                        >
                          <span>🗑️</span>
                          <span>Remove from Watchlist</span>
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                {/* Show Details */}
                <div className={styles.podcastContent}>
                  <div className={styles.podcastTitle} title={podcast.title}>
                    {podcast.title}
                  </div>
                  <div className={styles.podcastAuthor} title={podcast.author || 'Unknown Host'}>
                    {podcast.author || 'Unknown Host'}
                  </div>

                  {/* Badges */}
                  <div className={styles.podcastBadgesRow}>
                    {podcast.auto_download ? (
                      <span className={styles.podcastBadgeSuccess} title="Automatically downloading new episodes">
                        ⚡ Auto-download
                      </span>
                    ) : (
                      <span className={styles.podcastBadgeMuted} title="Monitoring for updates without auto-download">
                        👁️ Monitored
                      </span>
                    )}

                    <span
                      className={styles.podcastBadgeRetention}
                      title={`Episodes kept for ${podcast.retention_days === 0 ? 'indefinitely' : `${podcast.retention_days ?? 14} days`}`}
                    >
                      {podcast.retention_days === 0 ? 'Keep forever' : `⏳ ${podcast.retention_days ?? 14}d`}
                    </span>

                    {podcast.episode_count != null && podcast.episode_count > 0 && (
                      <span className={styles.podcastBadgeCount}>
                        {podcast.episode_count} eps
                      </span>
                    )}
                  </div>

                  {/* Scan status footer */}
                  <div className={styles.podcastFooter}>
                    <span className={styles.podcastScanText}>
                      {podcast.last_scan_timestamp
                        ? formatRelativeScanTime(podcast.last_scan_timestamp)
                        : 'Not scanned yet'}
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Settings Modal */}
      {selectedPodcastForSettings && (
        <PodcastSettingsModal
          podcast={selectedPodcastForSettings}
          profileId={profileId}
          isOpen={Boolean(selectedPodcastForSettings)}
          onClose={() => setSelectedPodcastForSettings(null)}
        />
      )}
    </div>
  );
}
