import { useEffect } from 'react';

import type { PodcastDownloadItem, PodcastEpisodeItem } from '../-podcasts.types';

import styles from './podcasts-page.module.css';

interface PodcastShowNotesModalProps {
  episode: PodcastEpisodeItem;
  showTitle?: string;
  showArtwork?: string | null;
  downloads: Record<string, PodcastDownloadItem>;
  onClose: () => void;
  onPlayEpisode: (ep: PodcastEpisodeItem) => void;
  onDownloadEpisode: (ep: PodcastEpisodeItem) => void;
}

export function PodcastShowNotesModal({
  episode,
  showTitle,
  showArtwork,
  downloads,
  onClose,
  onPlayEpisode,
  onDownloadEpisode,
}: PodcastShowNotesModalProps) {
  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const pubDateFormatted = episode.pub_date
    ? new Date(episode.pub_date).toLocaleDateString(undefined, {
        weekday: 'short',
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      })
    : null;

  const durationFormatted = episode.duration_seconds
    ? episode.duration_seconds >= 3600
      ? `${Math.floor(episode.duration_seconds / 3600)}h ${Math.floor((episode.duration_seconds % 3600) / 60)}m`
      : `${Math.floor(episode.duration_seconds / 60)}m`
    : null;

  const fileSizeFormatted = episode.enclosure_length
    ? `${(episode.enclosure_length / (1024 * 1024)).toFixed(1)} MB`
    : null;

  const dlRecord = episode.enclosure_url ? downloads[episode.enclosure_url] : null;
  const isDownloaded = dlRecord?.status === 'completed';
  const isDownloading = dlRecord?.status === 'downloading' || dlRecord?.status === 'queued';

  const notesHtml =
    episode.show_notes || episode.description || '<p>No show notes provided for this episode.</p>';
  const artUrl = episode.artwork_url || showArtwork;

  return (
    <div className={styles.showNotesOverlay} onClick={onClose} role="dialog" aria-modal="true">
      <div className={styles.showNotesDrawer} onClick={(e) => e.stopPropagation()}>
        {/* Drawer Header */}
        <div className={styles.showNotesHeader}>
          <div className={styles.showNotesHeaderTop}>
            <div className={styles.showNotesMetaBadges}>
              {episode.episode_type && episode.episode_type !== 'full' && (
                <span className={styles.episodeTypeTag}>{episode.episode_type.toUpperCase()}</span>
              )}
              {episode.season != null && episode.season > 0 && (
                <span className={styles.seasonEpBadge}>
                  Season {episode.season}
                  {episode.episode_number != null ? ` · Episode ${episode.episode_number}` : ''}
                </span>
              )}
              {episode.season == null && episode.episode_number != null && (
                <span className={styles.seasonEpBadge}>Episode {episode.episode_number}</span>
              )}
            </div>

            <button
              type="button"
              className={styles.showNotesCloseBtn}
              onClick={onClose}
              aria-label="Close show notes"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          <div className={styles.showNotesTitleRow}>
            {artUrl && (
              <img src={artUrl} alt={episode.title} className={styles.showNotesArtThumb} />
            )}
            <div>
              <h2 className={styles.showNotesTitle}>{episode.title}</h2>
              {showTitle && <p className={styles.showNotesShowName}>{showTitle}</p>}
              <div className={styles.showNotesStatsRow}>
                {pubDateFormatted && <span>📅 {pubDateFormatted}</span>}
                {durationFormatted && <span>⏱️ {durationFormatted}</span>}
                {fileSizeFormatted && <span>💾 {fileSizeFormatted}</span>}
              </div>
            </div>
          </div>

          <div className={styles.showNotesActions}>
            <button
              type="button"
              className={styles.showNotesPlayBtn}
              onClick={() => {
                onPlayEpisode(episode);
              }}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
              <span>Play Episode</span>
            </button>

            {episode.enclosure_url && (
              <button
                type="button"
                className={styles.showNotesDownloadBtn}
                data-download-action=""
                onClick={() => onDownloadEpisode(episode)}
                disabled={isDownloading || isDownloaded}
              >
                {isDownloaded ? (
                  <>
                    <span>✓</span> <span>Downloaded</span>
                  </>
                ) : isDownloading ? (
                  <>
                    <span className={styles.smallSpinner} /> <span>Downloading…</span>
                  </>
                ) : (
                  <>
                    <svg
                      width="15"
                      height="15"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.5"
                    >
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                      <polyline points="7 10 12 15 17 10" />
                      <line x1="12" y1="15" x2="12" y2="3" />
                    </svg>
                    <span>Download</span>
                  </>
                )}
              </button>
            )}
          </div>
        </div>

        {/* Drawer Body (HTML show notes, clickable timestamps & links) */}
        <div className={styles.showNotesBody}>
          <h3 className={styles.showNotesSectionHeading}>Episode Notes & Transcript</h3>
          <div
            className={styles.showNotesHtmlContent}
            dangerouslySetInnerHTML={{ __html: notesHtml }}
          />
        </div>
      </div>
    </div>
  );
}
