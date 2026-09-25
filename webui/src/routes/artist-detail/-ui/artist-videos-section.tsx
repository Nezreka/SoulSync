import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { SearchVideo } from '../../search/-search.types';

import {
  ARTIST_VIDEO_FETCH_INITIAL,
  ARTIST_VIDEO_FETCH_MAX,
  ARTIST_VIDEO_PAGE,
  artistVideoSearchQuery,
  curateArtistVideos,
  formatUploadDate,
  loadMoreStep,
  nextVideoAfter,
  pauseLibraryPlayback,
  previousVideoBefore,
  videoKey,
  videoWatchUrl,
} from '../-artist-detail.videos';
import { isEmbedBlockedError } from '../-artist-detail.youtube-api';
import { streamVideoSearch } from '../../search/-search.api';
import { formatVideoDuration, formatViewCount } from '../../search/-search.helpers';
import { useVideoDownloads } from '../../search/-search.use-video-downloads';
import { YouTubeEmbed } from './youtube-embed';

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

function openOnYouTube(video: SearchVideo) {
  const url = videoWatchUrl(video);
  if (!url) return;
  window.open(url, '_blank', 'noopener,noreferrer');
}

function VideoMeta({ video, featured = false }: { video: SearchVideo; featured?: boolean }) {
  const duration = formatVideoDuration(video.duration);
  const views = formatViewCount(video.view_count);
  const date = formatUploadDate(video.upload_date);
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
  onPlay,
}: {
  video: SearchVideo;
  progress: DownloadProgress;
  onDownload: (video: SearchVideo) => void;
  onPlay: (video: SearchVideo) => void;
}) {
  const { state, percent } = videoProgressState(video, progress);
  const duration = formatVideoDuration(video.duration);
  return (
    <article className={`artist-video-card artist-video-spotlight featured ${state}`}>
      <button
        type="button"
        className="artist-video-spotlight-media"
        onClick={() => onPlay(video)}
        aria-label={`Play ${video.title ?? 'video'}`}
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
            onClick={() => onPlay(video)}
            aria-label={`Play ${video.title ?? 'video'}`}
          >
            <span aria-hidden="true">▶</span>
            Play
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
          <button
            type="button"
            className="artist-video-secondary"
            onClick={() => openOnYouTube(video)}
            aria-label={`Open ${video.title ?? 'video'} on YouTube`}
          >
            <span aria-hidden="true">↗</span>
            YouTube
          </button>
        </div>
      </div>
    </article>
  );
}

function ArtistVideoPlayer({
  video,
  position,
  total,
  progress,
  autoplayNext,
  docked,
  hasPrevious,
  hasNext,
  playbackError,
  onDownload,
  onPrevious,
  onNext,
  onEnded,
  onPlaybackError,
  onToggleAutoplay,
  onClose,
  onReturn,
}: {
  video: SearchVideo;
  position: number;
  total: number;
  progress: DownloadProgress;
  autoplayNext: boolean;
  docked: boolean;
  hasPrevious: boolean;
  hasNext: boolean;
  playbackError: number | null;
  onDownload: (video: SearchVideo) => void;
  onPrevious: () => void;
  onNext: () => void;
  onEnded: () => void;
  onPlaybackError: (code: number) => void;
  onToggleAutoplay: () => void;
  onClose: () => void;
  onReturn: () => void;
}) {
  const { state, percent } = videoProgressState(video, progress);
  const title = video.title || 'Untitled video';
  return (
    <article
      className={`artist-video-card artist-video-player${docked ? ' docked' : ''}`}
      aria-label={`Now playing: ${title}`}
    >
      <div className="artist-video-player-frame">
        {playbackError === null ? (
          <YouTubeEmbed
            video={video}
            onEnded={onEnded}
            onError={onPlaybackError}
            onPlaying={pauseLibraryPlayback}
          />
        ) : (
          <div className="artist-video-player-blocked" role="alert">
            {video.thumbnail ? <img src={video.thumbnail} alt="" /> : null}
            <div className="artist-video-player-blocked-copy">
              <strong>
                {isEmbedBlockedError(playbackError)
                  ? "This video can't be played here"
                  : "This video isn't available"}
              </strong>
              <span>
                {isEmbedBlockedError(playbackError)
                  ? 'Its owner only allows playback on YouTube.'
                  : 'YouTube could not load it.'}
              </span>
              <div className="artist-video-actions">
                <button
                  type="button"
                  className="artist-video-primary"
                  onClick={() => openOnYouTube(video)}
                >
                  <span aria-hidden="true">↗</span>
                  Open on YouTube
                </button>
                {hasNext ? (
                  <button type="button" className="artist-video-secondary" onClick={onNext}>
                    Skip to next
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="artist-video-player-bar">
        <div className="artist-video-player-copy">
          <div className="artist-video-eyebrow">
            <span className="artist-video-live-dot">Now playing</span>
            <span>
              {position} of {total}
            </span>
          </div>
          <h4 className="artist-video-title" title={title}>
            {title}
          </h4>
          <VideoMeta video={video} featured />
        </div>
        <div className="artist-video-player-controls">
          <button
            type="button"
            className="artist-video-icon-btn"
            onClick={onPrevious}
            disabled={!hasPrevious}
            aria-label="Previous video"
            title="Previous"
          >
            ⏮
          </button>
          <button
            type="button"
            className="artist-video-icon-btn"
            onClick={onNext}
            disabled={!hasNext}
            aria-label="Next video"
            title="Next"
          >
            ⏭
          </button>
          <button
            type="button"
            className={`artist-video-toggle${autoplayNext ? ' on' : ''}`}
            onClick={onToggleAutoplay}
            aria-pressed={autoplayNext}
            title="Play the next video when this one ends"
          >
            Autoplay
          </button>
          <button
            type="button"
            className="artist-video-icon-btn"
            onClick={() => onDownload(video)}
            disabled={state === 'downloading' || state === 'completed'}
            aria-label={`${downloadLabel(state, percent)} ${title}`}
            title={downloadLabel(state, percent)}
          >
            {state === 'completed' ? '✓' : state === 'downloading' ? Math.round(percent) : '↓'}
          </button>
          <button
            type="button"
            className="artist-video-icon-btn"
            onClick={() => openOnYouTube(video)}
            aria-label={`Open ${title} on YouTube`}
            title="Open on YouTube"
          >
            ↗
          </button>
          {docked ? (
            <button
              type="button"
              className="artist-video-icon-btn"
              onClick={onReturn}
              aria-label="Back to the video shelf"
              title="Back to the video shelf"
            >
              ⤴
            </button>
          ) : null}
          <button
            type="button"
            className="artist-video-icon-btn artist-video-close"
            onClick={onClose}
            aria-label="Close player"
            title="Close (Esc)"
          >
            ×
          </button>
        </div>
      </div>
    </article>
  );
}

function ArtistVideoRailItem({
  video,
  index,
  playing,
  progress,
  onDownload,
  onPlay,
}: {
  video: SearchVideo;
  index: number;
  playing: boolean;
  progress: DownloadProgress;
  onDownload: (video: SearchVideo) => void;
  onPlay: (video: SearchVideo) => void;
}) {
  const { state, percent } = videoProgressState(video, progress);
  const duration = formatVideoDuration(video.duration);
  const itemRef = useRef<HTMLElement | null>(null);

  // keep the playing item in view inside the rail's own scroll box. not
  // scrollIntoView: that would also scroll the page back to the stage, which
  // is exactly what a docked player is there to avoid.
  useEffect(() => {
    const item = itemRef.current;
    const list = item?.closest<HTMLElement>('.artist-video-rail-list');
    if (!playing || !item || !list) return;
    const itemBox = item.getBoundingClientRect();
    const listBox = list.getBoundingClientRect();
    if (itemBox.top < listBox.top) list.scrollTop -= listBox.top - itemBox.top;
    else if (itemBox.bottom > listBox.bottom) list.scrollTop += itemBox.bottom - listBox.bottom;
  }, [playing]);

  return (
    <article
      ref={itemRef}
      className={`artist-video-card artist-video-rail-item ${state}${playing ? ' playing' : ''}`}
      aria-current={playing ? 'true' : undefined}
    >
      <span className="artist-video-index" aria-hidden="true">
        {playing ? '▶' : String(index + 1).padStart(2, '0')}
      </span>
      <button
        type="button"
        className="artist-video-rail-thumb"
        onClick={() => onPlay(video)}
        aria-label={`Play ${video.title ?? 'video'}`}
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
          onClick={() => onPlay(video)}
          aria-label={`Play ${video.title ?? 'video'}`}
          title="Play"
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

export function ArtistVideosSection({
  artistName,
  standalone = false,
}: {
  artistName?: string | null;
  standalone?: boolean;
}) {
  const [status, setStatus] = useState<Status>('idle');
  // everything yt-dlp has handed back for this artist, uncurated
  const [pool, setPool] = useState<SearchVideo[]>([]);
  const [fetchedLimit, setFetchedLimit] = useState(0);
  const [fetchingMore, setFetchingMore] = useState(false);
  const [visible, setVisible] = useState(ARTIST_VIDEO_PAGE);
  const [reloadToken, setReloadToken] = useState(0);
  const [nowPlaying, setNowPlaying] = useState<string | null>(null);
  const [playbackError, setPlaybackError] = useState<number | null>(null);
  const [autoplayNext, setAutoplayNext] = useState(true);
  const [docked, setDocked] = useState(false);
  const downloads = useVideoDownloads();
  const query = artistVideoSearchQuery(artistName);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const slotRef = useRef<HTMLDivElement | null>(null);
  const moreControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!query) {
      setStatus('idle');
      setPool([]);
      return;
    }

    const controller = new AbortController();
    setStatus('loading');
    setPool([]);
    setFetchedLimit(0);
    setVisible(ARTIST_VIDEO_PAGE);
    setNowPlaying(null);
    setPlaybackError(null);
    setFetchingMore(false);
    moreControllerRef.current?.abort();

    void streamVideoSearch(
      query,
      (chunk) => {
        if (controller.signal.aborted) return;
        setPool(chunk);
      },
      controller.signal,
      { limit: ARTIST_VIDEO_FETCH_INITIAL },
    )
      .then((result) => {
        if (controller.signal.aborted) return;
        setPool(result);
        setFetchedLimit(ARTIST_VIDEO_FETCH_INITIAL);
        setStatus(curateArtistVideos(result, query).length ? 'ready' : 'empty');
      })
      .catch((error) => {
        if (controller.signal.aborted || (error as Error).name === 'AbortError') return;
        setPool([]);
        setStatus('error');
      });

    return () => {
      controller.abort();
      moreControllerRef.current?.abort();
    };
  }, [query, reloadToken]);

  const curated = useMemo(() => curateArtistVideos(pool, query), [pool, query]);
  const shown = useMemo(() => curated.slice(0, visible), [curated, visible]);
  const playing = useMemo(
    () => (nowPlaying ? (shown.find((video) => videoKey(video) === nowPlaying) ?? null) : null),
    [shown, nowPlaying],
  );
  const featured = shown[0];
  const rail = playing ? shown : shown.slice(1);
  const isPlaying = Boolean(nowPlaying);
  const moreStep = loadMoreStep({
    curatedCount: curated.length,
    visible,
    fetchedLimit: fetchedLimit || ARTIST_VIDEO_FETCH_INITIAL,
  });

  const play = useCallback(
    (video: SearchVideo) => {
      const key = videoKey(video);
      if (!key) return;
      pauseLibraryPlayback();
      setPlaybackError(null);
      setNowPlaying(key);
      // the first play brings the stage into view; a queue step while docked
      // leaves the viewer where they are
      if (!nowPlaying) {
        stageRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
      }
    },
    [nowPlaying],
  );

  const stepTo = useCallback((video: SearchVideo | null) => {
    if (!video) return;
    pauseLibraryPlayback();
    setPlaybackError(null);
    setNowPlaying(videoKey(video));
  }, []);

  const next = useCallback(() => {
    const upcoming = nextVideoAfter(shown, nowPlaying);
    if (upcoming) {
      stepTo(upcoming);
      return;
    }
    // the queue ran past the visible page; reveal the next one and keep going
    const hidden = curated[shown.length];
    if (hidden) {
      setVisible((count) => count + ARTIST_VIDEO_PAGE);
      stepTo(hidden);
    }
  }, [shown, curated, nowPlaying, stepTo]);

  const previous = useCallback(() => {
    stepTo(previousVideoBefore(shown, nowPlaying));
  }, [shown, nowPlaying, stepTo]);

  const ended = useCallback(() => {
    if (autoplayNext) next();
  }, [autoplayNext, next]);

  const close = useCallback(() => {
    setNowPlaying(null);
    setPlaybackError(null);
    setDocked(false);
  }, []);

  const hasNext = Boolean(nextVideoAfter(shown, nowPlaying) || curated[shown.length]);
  const hasPrevious = Boolean(previousVideoBefore(shown, nowPlaying));

  const loadMore = useCallback(() => {
    if (moreStep === 'reveal') {
      setVisible((count) => count + ARTIST_VIDEO_PAGE);
      return;
    }
    if (moreStep !== 'fetch' || fetchingMore) return;

    const controller = new AbortController();
    moreControllerRef.current = controller;
    setFetchingMore(true);
    void streamVideoSearch(query, () => undefined, controller.signal, {
      limit: ARTIST_VIDEO_FETCH_MAX,
    })
      .then((result) => {
        if (controller.signal.aborted) return;
        // merged, not replaced: the first pool's order is what the viewer has
        // been looking at, and curate drops the repeats
        setPool((current) => [...current, ...result]);
        setFetchedLimit(ARTIST_VIDEO_FETCH_MAX);
        setVisible((count) => count + ARTIST_VIDEO_PAGE);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setFetchedLimit(ARTIST_VIDEO_FETCH_MAX);
      })
      .finally(() => {
        if (!controller.signal.aborted) setFetchingMore(false);
      });
  }, [moreStep, fetchingMore, query]);

  // escape closes the player, the same way every overlay in the app does
  useEffect(() => {
    if (!isPlaying) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      const target = event.target as HTMLElement | null;
      if (target && /^(input|textarea|select)$/i.test(target.tagName)) return;
      close();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isPlaying, close]);

  // when the stage scrolls away mid-video the player docks to the corner. the
  // slot keeps its height so the page doesn't jump underneath the viewer.
  // keyed on whether anything plays, not which video: re-observing on every
  // queue step would undock for a frame and jump the iframe around.
  useEffect(() => {
    const slot = slotRef.current;
    if (!isPlaying || !slot || typeof IntersectionObserver !== 'function') {
      setDocked(false);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry) return;
        if (entry.isIntersecting) {
          slot.style.minHeight = '';
          setDocked(false);
        } else {
          slot.style.minHeight = `${slot.offsetHeight}px`;
          setDocked(true);
        }
      },
      { threshold: 0 },
    );
    observer.observe(slot);
    return () => {
      observer.disconnect();
      slot.style.minHeight = '';
      setDocked(false);
    };
  }, [isPlaying]);

  const returnToShelf = useCallback(() => {
    slotRef.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }, []);

  if (!standalone && (status === 'idle' || status === 'empty')) return null;

  const countLabel =
    status === 'loading'
      ? 'Searching YouTube'
      : `${curated.length} video${curated.length === 1 ? '' : 's'}`;

  return (
    <section className="artist-videos-section" id="artist-videos-section" aria-live="polite">
      <div className="artist-videos-topline">
        <div>
          <span className="artist-videos-kicker">{standalone ? 'YouTube' : 'Video shelf'}</span>
          <h3>Music Videos</h3>
        </div>
        <div className="artist-videos-actions">
          <span id="artist-videos-count" className="artist-videos-count">
            {countLabel}
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

      {status === 'empty' ? (
        <div className="artist-videos-empty">No music videos found for this artist.</div>
      ) : status === 'error' ? (
        <div className="artist-videos-empty">Music videos are unavailable right now.</div>
      ) : status === 'loading' && !featured ? (
        <div className="artist-videos-loading">
          <div />
          <div />
          <div />
        </div>
      ) : featured ? (
        <>
          <div className={`artist-videos-stage${playing ? ' playing' : ''}`} ref={stageRef}>
            {playing ? (
              <div className="artist-video-player-slot" ref={slotRef}>
                <ArtistVideoPlayer
                  video={playing}
                  position={shown.findIndex((video) => videoKey(video) === nowPlaying) + 1}
                  total={curated.length}
                  progress={downloads.progress}
                  autoplayNext={autoplayNext}
                  docked={docked}
                  hasPrevious={hasPrevious}
                  hasNext={hasNext}
                  playbackError={playbackError}
                  onDownload={downloads.download}
                  onPrevious={previous}
                  onNext={next}
                  onEnded={ended}
                  onPlaybackError={setPlaybackError}
                  onToggleAutoplay={() => setAutoplayNext((value) => !value)}
                  onClose={close}
                  onReturn={returnToShelf}
                />
              </div>
            ) : (
              <ArtistVideoSpotlight
                video={featured}
                progress={downloads.progress}
                onDownload={downloads.download}
                onPlay={play}
              />
            )}
            {rail.length ? (
              <div
                className="artist-video-rail"
                aria-label={playing ? 'Video queue' : 'More music videos'}
              >
                <div className="artist-video-rail-header">
                  <span>{playing ? 'Queue' : 'Up next'}</span>
                  <span>{rail.length}</span>
                </div>
                <div className="artist-video-rail-list">
                  {rail.map((video, index) => (
                    <ArtistVideoRailItem
                      key={videoKey(video) || String(index)}
                      video={video}
                      index={index}
                      playing={Boolean(nowPlaying) && videoKey(video) === nowPlaying}
                      progress={downloads.progress}
                      onDownload={downloads.download}
                      onPlay={play}
                    />
                  ))}
                </div>
              </div>
            ) : null}
          </div>
          {moreStep !== 'none' && status !== 'loading' ? (
            <div className="artist-videos-more">
              <button
                type="button"
                className="artist-videos-more-btn"
                onClick={loadMore}
                disabled={fetchingMore}
                aria-busy={fetchingMore}
              >
                {fetchingMore ? 'Searching YouTube for more' : 'Show more videos'}
              </button>
              <span className="artist-videos-more-count">
                {shown.length} of {curated.length}
                {moreStep === 'fetch' ? '+' : ''}
              </span>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
