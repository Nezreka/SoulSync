import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import type { WatchlistPodcast } from '../-watchlist.types';

import {
  removeWatchlistPodcast,
  updateWatchlistPodcastSettings,
  watchlistPodcastsQueryOptions,
} from '../-watchlist.api';
import styles from './watchlist-page.module.css';

interface PodcastSettingsModalProps {
  podcast: WatchlistPodcast;
  profileId: number;
  isOpen: boolean;
  onClose: () => void;
}

const RETENTION_PRESETS = [
  { days: 7, label: '7 days' },
  { days: 14, label: '14 days (Default)' },
  { days: 30, label: '30 days' },
  { days: 60, label: '60 days' },
  { days: 0, label: 'Keep forever (0)' },
];

export function PodcastSettingsModal({
  podcast,
  profileId,
  isOpen,
  onClose,
}: PodcastSettingsModalProps) {
  const queryClient = useQueryClient();

  const [autoDownload, setAutoDownload] = useState(Boolean(podcast.auto_download));
  const [retentionDays, setRetentionDays] = useState(podcast.retention_days ?? 14);

  useEffect(() => {
    setAutoDownload(Boolean(podcast.auto_download));
    setRetentionDays(podcast.retention_days ?? 14);
  }, [podcast]);

  const saveMutation = useMutation({
    mutationFn: () =>
      updateWatchlistPodcastSettings(podcast.feed_url, podcast.itunes_id, {
        auto_download: autoDownload,
        retention_days: retentionDays,
      }),
    onSuccess: () => {
      window.showToast?.('Podcast settings updated', 'success');
      void queryClient.invalidateQueries({
        queryKey: watchlistPodcastsQueryOptions(profileId).queryKey,
      });
      onClose();
    },
    onError: (err: Error) => {
      window.showToast?.(err.message || 'Failed to update settings', 'error');
    },
  });

  const removeMutation = useMutation({
    mutationFn: () => removeWatchlistPodcast(podcast.feed_url, podcast.itunes_id),
    onSuccess: () => {
      window.showToast?.(`Removed "${podcast.title}" from watchlist`, 'info');
      void queryClient.invalidateQueries({
        queryKey: watchlistPodcastsQueryOptions(profileId).queryKey,
      });
      try {
        window.updateWatchlistButtonCount?.();
      } catch {
        /* non-fatal */
      }
      onClose();
    },
    onError: (err: Error) => {
      window.showToast?.(err.message || 'Failed to remove from watchlist', 'error');
    },
  });

  const handleRemove = async () => {
    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove Podcast from Watchlist',
      message: `Stop monitoring "${podcast.title || 'this podcast'}" for new episodes?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    removeMutation.mutate();
  };

  if (!isOpen) return null;

  return (
    <div
      className="modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Podcast Settings"
    >
      <div
        className={`watchlist-artist-config-modal ${styles.podcastModal}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="wl-global-modal-head">
          <div className={styles.modalShowHeader}>
            {podcast.artwork_url ? (
              <img
                src={podcast.artwork_url}
                alt={podcast.title}
                className={styles.modalShowArt}
              />
            ) : (
              <div className={styles.modalShowArtPlaceholder}>🎙️</div>
            )}
            <div>
              <h2 id="podcast-settings-title" className="wl-global-modal-title">
                {podcast.title || 'Podcast Settings'}
              </h2>
              <p className="wl-global-modal-sub">
                {podcast.author || 'Download & Retention Preferences'}
              </p>
            </div>
          </div>
          <button className="wl-global-modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="watchlist-artist-config-content">
          <div className="watchlist-artist-config-body">
            {/* Auto Download Section */}
            <div className="config-section">
              <h3 className="config-section-title">Auto-Download</h3>
              <p className="config-section-subtitle">
                Configure automatic downloading for newly published episodes.
              </p>
              <label className="config-option">
                <input
                  type="checkbox"
                  checked={autoDownload}
                  onChange={(e) => setAutoDownload(e.target.checked)}
                />
                <div className="config-option-content">
                  <div className="config-option-icon">⚡</div>
                  <div className="config-option-text">
                    <span className="config-option-title">Auto-download new episodes</span>
                    <span className="config-option-description">
                      Automatically grab fresh episodes when new feed releases are detected.
                    </span>
                  </div>
                </div>
              </label>
            </div>

            {/* Retention Section */}
            <div className="config-section">
              <h3 className="config-section-title">Episode Retention Period</h3>
              <p className="config-section-subtitle">
                Podcasts are episodic and can accumulate quickly. How long should downloaded
                episodes be kept before automated cleanup?
              </p>

              <div className={styles.retentionPresetList}>
                {RETENTION_PRESETS.map((preset) => (
                  <button
                    key={preset.days}
                    type="button"
                    className={`${styles.retentionPresetBtn} ${retentionDays === preset.days ? styles.retentionPresetBtnActive : ''}`}
                    onClick={() => setRetentionDays(preset.days)}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>

              <div className={styles.retentionInputRow}>
                <label htmlFor="custom-retention-input" className={styles.retentionInputLabel}>
                  Custom retention (days):
                </label>
                <div className={styles.retentionInputWrapper}>
                  <input
                    id="custom-retention-input"
                    type="number"
                    min="0"
                    max="3650"
                    value={retentionDays}
                    onChange={(e) => {
                      const val = parseInt(e.target.value, 10);
                      setRetentionDays(isNaN(val) || val < 0 ? 0 : val);
                    }}
                    className={styles.retentionInput}
                  />
                  <span className={styles.retentionInputSuffix}>days</span>
                </div>
                <span className={styles.retentionHint}>
                  {retentionDays === 0
                    ? 'Episodes will be kept indefinitely (never cleaned up).'
                    : `Episodes older than ${retentionDays} days will be eligible for cleanup.`}
                </span>
              </div>
            </div>

            {/* Unfollow / Remove Option */}
            <div className="config-section">
              <h3 className="config-section-title">Danger Zone</h3>
              <div className={styles.dangerZoneRow}>
                <div>
                  <div className={styles.dangerTitle}>Remove from Watchlist</div>
                  <div className={styles.dangerSub}>
                    Stop monitoring this show for new releases. Existing downloads are untouched.
                  </div>
                </div>
                <button
                  type="button"
                  className="btn btn--danger"
                  onClick={() => void handleRemove()}
                  disabled={removeMutation.isPending}
                >
                  {removeMutation.isPending ? 'Removing...' : 'Remove'}
                </button>
              </div>
            </div>
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="btn btn--secondary"
              onClick={onClose}
              disabled={saveMutation.isPending}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
