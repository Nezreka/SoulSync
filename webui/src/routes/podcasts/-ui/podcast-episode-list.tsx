import { useEffect, useState } from 'react';

import type { PodcastDownloadItem, PodcastEpisodeItem } from '../-podcasts.types';

import styles from './podcasts-page.module.css';

interface PodcastEpisodeListProps {
  episodes: PodcastEpisodeItem[];
  showArtwork?: string | null;
  activeEpisodeGuid?: string;
  isPlaying?: boolean;
  onPlayEpisode: (ep: PodcastEpisodeItem) => void;
  onDownloadEpisode: (ep: PodcastEpisodeItem) => void;
  onOpenShowNotes?: (ep: PodcastEpisodeItem) => void;
  downloads: Record<string, PodcastDownloadItem>;
}

function formatDuration(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return '';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (hrs > 0) return `${hrs}h ${mins}m`;
  return `${mins}m`;
}

function formatFileSize(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return '';
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

const CHUNK_SIZE = 40;

function stripHtml(html?: string | null): string {
  if (!html) return '';
  return html
    .replace(/<[^>]*>?/gm, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function PodcastEpisodeList({
  episodes,
  showArtwork,
  activeEpisodeGuid,
  isPlaying,
  onPlayEpisode,
  onDownloadEpisode,
  onOpenShowNotes,
  downloads,
}: PodcastEpisodeListProps) {
  const [visibleCount, setVisibleCount] = useState(CHUNK_SIZE);

  // Reset pagination when episodes filter/sort changes
  useEffect(() => {
    setVisibleCount(CHUNK_SIZE);
  }, [episodes.length]);

  if (!episodes.length) {
    return (
      <div className={styles.emptyContainer}>
        <p>No episodes found matching the current filter.</p>
      </div>
    );
  }

  const visibleEpisodes = episodes.slice(0, visibleCount);
  const hasMore = visibleCount < episodes.length;

  return (
    <div className={styles.episodeListContainer}>
      <div className={styles.episodeList}>
        {visibleEpisodes.map((ep, idx) => {
          const isCurrent = activeEpisodeGuid === ep.guid;
          const isCurrentPlaying = isCurrent && isPlaying;
          const duration = formatDuration(ep.duration_seconds);
          const fileSize = formatFileSize(ep.enclosure_length);
          const epArt = ep.artwork_url || showArtwork;
          const snippet = stripHtml(ep.description || ep.show_notes);

          // Find download status
          const dlRecord = Object.values(downloads).find(
            (d) => d.enclosure_url === ep.enclosure_url,
          );
          const isDownloaded = dlRecord?.status === 'completed';
          const isDownloading = dlRecord?.status === 'downloading' || dlRecord?.status === 'queued';

          const pubDateStr = ep.pub_date
            ? new Date(ep.pub_date).toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
              })
            : '';

          return (
            <div
              key={ep.guid || `${ep.title}-${idx}`}
              className={`${styles.episodeRow} ${isCurrent ? styles.episodeRowActive : ''}`}
            >
              {/* Left Column: Artwork Thumbnail & Floating Play */}
              <div className={styles.episodeMediaCol}>
                <div className={styles.episodeThumbWrapper}>
                  {epArt ? (
                    <img src={epArt} alt="" className={styles.episodeThumb} loading="lazy" />
                  ) : (
                    <div className={styles.episodeThumbPlaceholder}>🎙️</div>
                  )}
                  <button
                    type="button"
                    className={`${styles.playToggleBtn} ${isCurrentPlaying ? styles.playToggleBtnPlaying : ''}`}
                    onClick={() => onPlayEpisode(ep)}
                    title={isCurrentPlaying ? 'Pause episode' : 'Play episode'}
                    aria-label={isCurrentPlaying ? 'Pause episode' : 'Play episode'}
                  >
                    {isCurrentPlaying ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                        <rect x="6" y="4" width="4" height="16" />
                        <rect x="14" y="4" width="4" height="16" />
                      </svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                        <polygon points="6 4 20 12 6 20 6 4" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>

              {/* Center Content: Kicker, Title, 2-line preview, Action bar */}
              <div className={styles.episodeRowInfo}>
                <div className={styles.episodeMetaKicker}>
                  {pubDateStr && (
                    <span className={styles.kickerDate}>{pubDateStr.toUpperCase()}</span>
                  )}
                  {duration && (
                    <span className={styles.kickerDuration}>• {duration.toUpperCase()}</span>
                  )}
                  {ep.episode_type && ep.episode_type !== 'full' && (
                    <span className={`${styles.episodeTypePill} ${styles.episodeTypeSpecial}`}>
                      {ep.episode_type.toUpperCase()}
                    </span>
                  )}
                  {ep.season != null && ep.season > 0 && (
                    <span className={styles.episodeTypePill}>
                      S{ep.season}
                      {ep.episode_number != null ? ` · E${ep.episode_number}` : ''}
                    </span>
                  )}
                  {(!ep.season || ep.season <= 0) && ep.episode_number != null && (
                    <span className={styles.episodeTypePill}>EP {ep.episode_number}</span>
                  )}
                </div>

                <h4
                  className={styles.episodeTitle}
                  title={ep.title}
                  onClick={() => onPlayEpisode(ep)}
                >
                  {ep.title}
                </h4>

                {snippet && (
                  <p className={styles.episodeSnippet} title={snippet}>
                    {snippet}
                  </p>
                )}

                {/* Cohesive Action Toolbar right below content */}
                <div className={styles.episodeActionsBar}>
                  <button
                    type="button"
                    className={`${styles.episodePlayPill} ${isCurrentPlaying ? styles.episodePlayPillActive : ''}`}
                    onClick={() => onPlayEpisode(ep)}
                  >
                    {isCurrentPlaying ? (
                      <>
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                          <rect x="6" y="4" width="4" height="16" />
                          <rect x="14" y="4" width="4" height="16" />
                        </svg>
                        <span>Pause</span>
                      </>
                    ) : (
                      <>
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                          <polygon points="6 4 20 12 6 20 6 4" />
                        </svg>
                        <span>{duration || 'Play'}</span>
                      </>
                    )}
                  </button>

                  {onOpenShowNotes && (
                    <button
                      type="button"
                      className={styles.notesDrawerBtn}
                      onClick={() => onOpenShowNotes(ep)}
                      title="View full show notes & transcript"
                      aria-label="View full show notes"
                    >
                      <svg
                        width="13"
                        height="13"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <polyline points="14 2 14 8 20 8" />
                        <line x1="16" y1="13" x2="8" y2="13" />
                        <line x1="16" y1="17" x2="8" y2="17" />
                      </svg>
                      <span>Notes</span>
                    </button>
                  )}

                  <button
                    type="button"
                    className={`${styles.downloadBtn} ${isDownloaded ? styles.downloadBtnCompleted : ''} ${isDownloading ? styles.downloadBtnLoading : ''}`}
                    onClick={() => {
                      if (!isDownloaded && !isDownloading && ep.enclosure_url) {
                        onDownloadEpisode(ep);
                      }
                    }}
                    disabled={!ep.enclosure_url || isDownloaded || isDownloading}
                    title={
                      !ep.enclosure_url
                        ? 'No direct audio stream available to download'
                        : isDownloaded
                          ? 'Already downloaded'
                          : isDownloading
                            ? `Downloading (${dlRecord?.percent || 0}%)`
                            : 'Download episode'
                    }
                  >
                    {isDownloaded ? (
                      <>
                        <span>✓</span>
                        <span>Saved</span>
                      </>
                    ) : isDownloading ? (
                      <>
                        <div
                          className={styles.spinner}
                          style={{ width: 12, height: 12, borderWidth: 2 }}
                        />
                        <span>{dlRecord?.percent ? `${dlRecord.percent}%` : 'Saving…'}</span>
                      </>
                    ) : (
                      <>
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
                          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                          <polyline points="7 10 12 15 17 10" />
                          <line x1="12" y1="15" x2="12" y2="3" />
                        </svg>
                        <span>Download</span>
                      </>
                    )}
                  </button>

                  {fileSize && <span className={styles.fileSizeBadge}>{fileSize}</span>}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Pagination Bar for large episode catalogues */}
      {hasMore && (
        <div className={styles.paginationBar}>
          <span className={styles.paginationText}>
            Showing {visibleEpisodes.length} of {episodes.length} episodes
          </span>
          <div className={styles.paginationActions}>
            <button
              type="button"
              className={styles.loadMoreBtn}
              onClick={() => setVisibleCount((prev) => prev + CHUNK_SIZE)}
            >
              Load Next {Math.min(CHUNK_SIZE, episodes.length - visibleEpisodes.length)} Episodes ↓
            </button>
            <button
              type="button"
              className={styles.loadAllBtn}
              onClick={() => setVisibleCount(episodes.length)}
            >
              Show All ({episodes.length})
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
