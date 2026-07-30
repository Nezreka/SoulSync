import type { SearchVideo } from '../-search.types';

import { formatVideoDuration, formatViewCount } from '../-search.helpers';

/**
 * The YouTube music-video grid.
 *
 * Two things differ from the vanilla by construction rather than by choice:
 *
 * 1. **The section is a normal part of the tree.** The vanilla CREATED
 *    #enh-videos-section in JS on first use and appended it to the results
 *    container, which is why it always landed after Labels and then persisted,
 *    hidden, for the rest of the session.
 * 2. **The video object is passed, not serialised into an attribute.** The
 *    vanilla built `onclick="_downloadMusicVideo(this, ${JSON.stringify(v)…})"`,
 *    so a title containing a quote or a backslash broke the card. Handing the
 *    object straight to the handler removes that whole class of failure.
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
    <div className="enh-dropdown-section" id="enh-videos-section">
      <div className="enh-section-header">
        <span className="enh-section-icon">🎬</span>
        <h4 className="enh-section-title">Music Videos</h4>
        <span className="enh-section-count" id="enh-videos-count">
          {videos.length}
        </span>
      </div>
      <div className="enh-video-grid" id="enh-videos-list">
        {!videos.length ? (
          <div className="enh-empty-state">No music videos found</div>
        ) : (
          videos.map((video) => {
            const id = String(video.video_id ?? '');
            const state = progress[id]?.state ?? 'idle';
            const percent = progress[id]?.percent ?? 0;
            const duration = formatVideoDuration(video.duration);
            const views = formatViewCount(video.view_count);

            const classes = ['enh-video-card'];
            if (state === 'downloading') classes.push('downloading');
            if (state === 'completed') classes.push('completed');
            if (state === 'errored') classes.push('errored');

            return (
              <div
                key={id || video.title}
                className={classes.join(' ')}
                data-video-id={id}
                role="button"
                tabIndex={0}
                onClick={() => onDownload(video)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onDownload(video);
                  }
                }}
              >
                <div className="enh-video-thumb">
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
                  <div className="enh-video-play">▶</div>
                  <div
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
                        // Counts DOWN as it fills: full offset is empty.
                        strokeDashoffset={RING_LENGTH * (1 - Math.min(100, percent) / 100)}
                        strokeLinecap="round"
                        transform="rotate(-90 18 18)"
                      />
                    </svg>
                  </div>
                  <div className={`enh-video-done${state === 'completed' ? '' : ' hidden'}`}>✓</div>
                  <div className={`enh-video-error${state === 'errored' ? '' : ' hidden'}`}>✗</div>
                  {duration ? <span className="enh-video-duration">{duration}</span> : null}
                </div>
                <div className="enh-video-info">
                  <div className="enh-video-title" title={video.title}>
                    {video.title}
                  </div>
                  <div className="enh-video-channel">
                    {video.channel}
                    {views ? ` · ${views} views` : ''}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
