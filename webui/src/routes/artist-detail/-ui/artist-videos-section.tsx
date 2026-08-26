import { useCallback, useEffect, useMemo, useState } from 'react';

import type { SearchVideo } from '../../search/-search.types';

import {
  artistVideoSearchQuery,
  curateArtistVideos,
  videoWatchUrl,
} from '../-artist-detail.videos';
import { streamVideoSearch } from '../../search/-search.api';
import { formatVideoDuration, formatViewCount } from '../../search/-search.helpers';
import { useVideoDownloads } from '../../search/-search.use-video-downloads';

const RING_LENGTH = 97.4;

type Status = 'idle' | 'loading' | 'ready' | 'empty' | 'error';
type DownloadProgress = ReturnType<typeof useVideoDownloads>['progress'];

function VideoProgressOverlay({ state, percent }: { state: string; percent: number }) {
  const safePercent = Math.min(100, Math.max(0, Number.isFinite(percent) ? percent : 0));
  return (
    <>
      <div className={`enh-video-progress-ring${state === 'downloading' ? '' : ' hidden'}`}>
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
            strokeDashoffset={RING_LENGTH * (1 - safePercent / 100)}
            strokeLinecap="round"
            transform="rotate(-90 18 18)"
          />
        </svg>
      </div>
      <div className={`enh-video-done${state === 'completed' ? '' : ' hidden'}`}>✓</div>
      <div className={`enh-video-error${state === 'errored' ? '' : ' hidden'}`}>!</div>
    </>
  );
}

function videoProgressState(video: SearchVideo, progress: DownloadProgress) {
  const id = String(video.video_id ?? video.url ?? video.title ?? '');
  return { state: progress[id]?.state ?? 'idle', percent: progress[id]?.percent ?? 0 };
}

function downloadLabel(state: string, percent: number): string {
  if (state === 'completed') return 'Saved';
  if (state === 'downloading') return `${Math.round(percent)}%`;
  if (state === 'errored') return 'Retry';
  return 'Save';
}

function VideoMeta({ video, featured = false }: { video: SearchVideo; featured?: boolean }) {
  const duration = formatVideoDuration(video.duration);
  const views = formatViewCount(video.view_count);
  const date = String(video.upload_date ?? '').trim();
  return (
    <div className="artist-video-meta">
      <span>{video.channel || 'YouTube'}</span>
      {views ? <span>{views} views</span> : null}
      {featured && date ? <span>{date}</span> : null}
      {duration ? <span>{duration}</span> : null}
    </div>
  );
}

function ArtistVideoSpotlight({
  video,
  progress,
  onDownload,
  onWatch,
}: {
  video: SearchVideo;
  progress: DownloadProgress;
  onDownload: (video: SearchVideo) => void;
  onWatch: (video: SearchVideo) => void;
}) {
  const { state, percent } = videoProgressState(video, progress);
  const duration = formatVideoDuration(video.duration);
  return (
    <article className={`artist-video-card artist-video-spotlight featured ${state}`}>
      <button
        type="button"
        className="artist-video-spotlight-media"
        onClick={() => onWatch(video)}
        aria-label={`Watch ${video.title ?? 'video'} on YouTube`}
      >
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
        <span className="artist-video-spotlight-sheen" aria-hidden="true" />
        <span className="artist-video-play artist-video-play-large" aria-hidden="true">
          ▶
        </span>
        <VideoProgressOverlay state={state} percent={percent} />
        {duration ? <span className="artist-video-duration">{duration}</span> : null}
      </button>
      <div className="artist-video-spotlight-copy">
        <div className="artist-video-eyebrow">
          <span>YouTube</span>
          <span>Featured video</span>
        </div>
        <h4 className="artist-video-title" title={video.title}>
          {video.title || 'Untitled video'}
        </h4>
        <VideoMeta video={video} featured />
        <div className="artist-video-actions">
          <button
            type="button"
            className="artist-video-primary"
            onClick={() => onWatch(video)}
            aria-label={`Watch ${video.title ?? 'video'} on YouTube`}
          >
            <span aria-hidden="true">▶</span>
            Watch
          </button>
          <button
            type="button"
            className="artist-video-secondary"
            onClick={() => onDownload(video)}
            disabled={state === 'downloading' || state === 'completed'}
          >
            <span aria-hidden="true">↓</span>
            {downloadLabel(state, percent)}
          </button>
        </div>
      </div>
    </article>
  );
}

function ArtistVideoRailItem({
  video,
  index,
  progress,
  onDownload,
  onWatch,
}: {
  video: SearchVideo;
  index: number;
  progress: DownloadProgress;
  onDownload: (video: SearchVideo) => void;
  onWatch: (video: SearchVideo) => void;
}) {
  const { state, percent } = videoProgressState(video, progress);
  const duration = formatVideoDuration(video.duration);
  return (
    <article className={`artist-video-card artist-video-rail-item ${state}`}>
      <span className="artist-video-index">{String(index + 1).padStart(2, '0')}</span>
      <button
        type="button"
        className="artist-video-rail-thumb"
        onClick={() => onWatch(video)}
        aria-label={`Watch ${video.title ?? 'video'} on YouTube`}
      >
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
        <span className="artist-video-mini-play" aria-hidden="true">
          ▶
        </span>
        <VideoProgressOverlay state={state} percent={percent} />
        {duration ? <span className="artist-video-duration mini">{duration}</span> : null}
      </button>
      <div className="artist-video-rail-copy">
        <h4 className="artist-video-title" title={video.title}>
          {video.title || 'Untitled video'}
        </h4>
        <VideoMeta video={video} />
      </div>
      <div className="artist-video-rail-actions">
        <button
          type="button"
          className="artist-video-icon-btn"
          onClick={() => onWatch(video)}
          aria-label={`Watch ${video.title ?? 'video'} on YouTube`}
          title="Watch"
        >
          ▶
        </button>
        <button
          type="button"
          className="artist-video-icon-btn"
          onClick={() => onDownload(video)}
          disabled={state === 'downloading' || state === 'completed'}
          aria-label={`${downloadLabel(state, percent)} ${video.title ?? 'video'}`}
          title={downloadLabel(state, percent)}
        >
          {state === 'completed' ? '✓' : state === 'downloading' ? Math.round(percent) : '↓'}
        </button>
      </div>
    </article>
  );
}

export function ArtistVideosSection({ artistName }: { artistName?: string | null }) {
  const [status, setStatus] = useState<Status>('idle');
  const [videos, setVideos] = useState<SearchVideo[]>([]);
  const [reloadToken, setReloadToken] = useState(0);
  const downloads = useVideoDownloads();
  const query = artistVideoSearchQuery(artistName);

  useEffect(() => {
    if (!query) {
      setStatus('idle');
      setVideos([]);
      return;
    }

    const controller = new AbortController();
    setStatus('loading');
    setVideos([]);

    void streamVideoSearch(
      query,
      (chunk) => {
        if (controller.signal.aborted) return;
        setVideos(curateArtistVideos(chunk, query));
      },
      controller.signal,
    )
      .then((result) => {
        if (controller.signal.aborted) return;
        const curated = curateArtistVideos(result, query);
        setVideos(curated);
        setStatus(curated.length ? 'ready' : 'empty');
      })
      .catch((error) => {
        if (controller.signal.aborted || (error as Error).name === 'AbortError') return;
        setVideos([]);
        setStatus('error');
      });

    return () => controller.abort();
  }, [query, reloadToken]);

  const shown = useMemo(() => curateArtistVideos(videos, query), [videos, query]);
  const featured = shown[0];
  const rest = shown.slice(1);

  const watch = useCallback((video: SearchVideo) => {
    const url = videoWatchUrl(video);
    if (!url) return;
    window.open(url, '_blank', 'noopener,noreferrer');
  }, []);

  if (status === 'idle' || status === 'empty') return null;

  return (
    <section className="artist-videos-section" id="artist-videos-section" aria-live="polite">
      <div className="artist-videos-topline">
        <div>
          <span className="artist-videos-kicker">Video shelf</span>
          <h3>Music Videos</h3>
        </div>
        <div className="artist-videos-actions">
          <span id="artist-videos-count" className="artist-videos-count">
            {status === 'loading'
              ? 'Searching YouTube'
              : `${shown.length} video${shown.length === 1 ? '' : 's'}`}
          </span>
          <button
            type="button"
            className="artist-videos-refresh"
            onClick={() => setReloadToken((value) => value + 1)}
            disabled={status === 'loading'}
            title="Refresh music videos"
            aria-label="Refresh music videos"
          >
            ↻
          </button>
        </div>
      </div>

      {status === 'error' ? (
        <div className="artist-videos-empty">Music videos are unavailable right now.</div>
      ) : status === 'loading' && !featured ? (
        <div className="artist-videos-loading">
          <div />
          <div />
          <div />
        </div>
      ) : featured ? (
        <div className="artist-videos-stage">
          <ArtistVideoSpotlight
            video={featured}
            progress={downloads.progress}
            onDownload={downloads.download}
            onWatch={watch}
          />
          {rest.length ? (
            <div className="artist-video-rail" aria-label="More music videos">
              <div className="artist-video-rail-header">
                <span>Up next</span>
                <span>{rest.length}</span>
              </div>
              <div className="artist-video-rail-list">
                {rest.map((video, index) => (
                  <ArtistVideoRailItem
                    key={String(video.video_id ?? video.url ?? video.title)}
                    video={video}
                    index={index}
                    progress={downloads.progress}
                    onDownload={downloads.download}
                    onWatch={watch}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
