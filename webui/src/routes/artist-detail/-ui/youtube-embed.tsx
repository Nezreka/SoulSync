import { useEffect, useRef, useState } from 'react';

import type { SearchVideo } from '../../search/-search.types';

import { videoEmbedUrl } from '../-artist-detail.videos';
import { type YouTubePlayer, YT_STATE, loadYouTubeIframeApi } from '../-artist-detail.youtube-api';

type Mode = 'loading' | 'api' | 'iframe';

/**
 * one youtube video, playing in the page.
 *
 * the player is created once and switched between videos with
 * loadVideoById: tearing the iframe down for every queue step would mean a
 * black flash and a fresh handshake each time. the element youtube swaps for
 * its iframe is created imperatively inside the host div so react never
 * reconciles it away.
 *
 * when the api can't load (blocked, offline) a plain iframe plays the video
 * instead. that path still watches; it just can't report ended or blocked.
 */
export function YouTubeEmbed({
  video,
  onEnded,
  onError,
  onPlaying,
}: {
  video: SearchVideo;
  onEnded: () => void;
  onError: (code: number) => void;
  onPlaying: () => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const playerRef = useRef<YouTubePlayer | null>(null);
  const loadedIdRef = useRef<string | null>(null);
  const videoIdRef = useRef(String(video.video_id ?? ''));
  const [mode, setMode] = useState<Mode>('loading');
  // the latest handlers without re-creating the player when they change
  const handlers = useRef({ onEnded, onError, onPlaying });
  handlers.current = { onEnded, onError, onPlaying };

  const videoId = String(video.video_id ?? '');
  videoIdRef.current = videoId;

  useEffect(() => {
    let cancelled = false;
    void loadYouTubeIframeApi().then((api) => {
      if (cancelled) return;
      const host = hostRef.current;
      if (!api || !host) {
        setMode('iframe');
        return;
      }
      const mount = document.createElement('div');
      host.appendChild(mount);
      loadedIdRef.current = videoIdRef.current;
      playerRef.current = new api.Player(mount, {
        videoId: videoIdRef.current,
        host: 'https://www.youtube-nocookie.com',
        width: '100%',
        height: '100%',
        playerVars: {
          autoplay: 1,
          rel: 0,
          playsinline: 1,
          origin: window.location.origin,
        },
        events: {
          onStateChange: (event) => {
            if (event.data === YT_STATE.ENDED) handlers.current.onEnded();
            else if (event.data === YT_STATE.PLAYING) handlers.current.onPlaying();
          },
          onError: (event) => handlers.current.onError(Number(event.data)),
        },
      });
      setMode('api');
    });
    return () => {
      cancelled = true;
      try {
        playerRef.current?.destroy();
      } catch {
        // a player that never finished setting up has nothing to destroy
      }
      playerRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (mode !== 'api' || !playerRef.current || !videoId) return;
    if (loadedIdRef.current === videoId) return;
    loadedIdRef.current = videoId;
    playerRef.current.loadVideoById(videoId);
  }, [mode, videoId]);

  // the app's own player starting is the viewer choosing it over the video
  useEffect(() => {
    const player = globalThis.audioPlayer;
    if (!player || typeof player.addEventListener !== 'function') return;
    const pauseVideo = () => {
      try {
        playerRef.current?.pauseVideo();
      } catch {
        // not ready yet, nothing to pause
      }
    };
    player.addEventListener('play', pauseVideo);
    return () => player.removeEventListener('play', pauseVideo);
  }, []);

  return (
    <div className={`artist-video-embed ${mode}`} ref={hostRef} data-video-id={videoId}>
      {mode === 'iframe' ? (
        <iframe
          src={videoEmbedUrl(video, window.location.origin)}
          title={video.title || 'YouTube video'}
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          allowFullScreen
        />
      ) : null}
      {mode === 'loading' ? (
        <div className="artist-video-embed-loading" aria-live="polite">
          {video.thumbnail ? <img src={video.thumbnail} alt="" /> : null}
          <span>Loading player</span>
        </div>
      ) : null}
    </div>
  );
}
