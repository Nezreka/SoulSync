import type { SearchVideo } from '../-search.types';

import { formatVideoDuration, formatViewCount } from '../-search.helpers';
import { DownloadIcon } from './search-icons';
import styles from './search.module.css';

/**
 * The YouTube music-video grid.
 *
 * the section is a normal part of the tree (the vanilla built it in JS on first
 * use), and the video object is passed straight to the handler instead of being
 * serialised into an onclick attribute, which broke on a quote in a title.
 *
 * the progress ring, tick and cross keep their global .enh-video-* classes:
 * the artist page's videos share that styling.
 */
export type VideoDownloadState = 'idle' | 'downloading' | 'completed' | 'errored';

export interface VideoProgress {
  state: VideoDownloadState;
  /** 0-100, only meaningful while downloading. */
  percent: number;
}

/** The ring's circumference, as the vanilla's stroke-dasharray. */
const RING_LENGTH = 97.4;

export function VideoGrid({
  videos,
  progress,
  onDownload,
}: {
  videos: SearchVideo[];
  progress: Record<string, VideoProgress>;
  onDownload: (video: SearchVideo) => void;
}) {
  return (
    <section className={styles.section} id="enh-videos-section">
      <div className={styles.sectionHead}>
        <h2 className={styles.sectionTitle}>
          Music videos
          <span className={styles.sectionCount} id="enh-videos-count">
            {videos.length}
          </span>
        </h2>
      </div>
      {!videos.length ? (
        <p className={styles.stateText}>No music videos found.</p>
      ) : (
        <div className={styles.videos} id="enh-videos-list">
          {videos.map((video) => {
            const id = String(video.video_id ?? '');
            const state = progress[id]?.state ?? 'idle';
            const percent = progress[id]?.percent ?? 0;
            const duration = formatVideoDuration(video.duration);
            const views = formatViewCount(video.view_count);
            return (
              <button
                key={id || video.title}
                type="button"
                className={styles.video}
                data-video-id={id}
                data-state={state}
                aria-label={`Download ${video.title}`}
                onClick={() => onDownload(video)}
              >
                <span className={styles.videoThumb}>
                  {video.thumbnail ? (
                    <img
                      src={video.thumbnail}
                      alt=""
                      loading="lazy"
                      onError={(event) => {
                        event.currentTarget.style.display = 'none';
                      }}
                    />
                  ) : null}
                  {state === 'idle' ? (
                    <span className={styles.videoAction} aria-hidden="true">
                      <DownloadIcon />
                    </span>
                  ) : null}
                  <span
                    className={`enh-video-progress-ring${state === 'downloading' ? '' : ' hidden'}`}
                  >
                    <svg viewBox="0 0 36 36">
                      <circle
                        className="enh-video-progress-bg"
                        cx="18"
                        cy="18"
                        r="15.5"
                        fill="none"
                        stroke="rgba(255,255,255,0.15)"
                        strokeWidth="3"
                      />
                      <circle
                        className="enh-video-progress-bar"
                        cx="18"
                        cy="18"
                        r="15.5"
                        fill="none"
                        stroke="rgb(var(--accent-rgb))"
                        strokeWidth="3"
                        strokeDasharray={RING_LENGTH}
                        strokeDashoffset={RING_LENGTH * (1 - Math.min(100, percent) / 100)}
                        strokeLinecap="round"
                        transform="rotate(-90 18 18)"
                      />
                    </svg>
                  </span>
                  <span className={`enh-video-done${state === 'completed' ? '' : ' hidden'}`}>
                    ✓
                  </span>
                  <span className={`enh-video-error${state === 'errored' ? '' : ' hidden'}`}>
                    ✗
                  </span>
                  {duration ? <span className={styles.videoDuration}>{duration}</span> : null}
                </span>
                <span className={styles.videoTitle} title={video.title}>
                  {video.title}
                </span>
                <span className={styles.videoSub}>
                  {video.channel}
                  {views ? ` · ${views} views` : ''}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
