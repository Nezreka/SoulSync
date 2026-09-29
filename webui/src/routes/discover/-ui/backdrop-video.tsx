import { useEffect, useRef, useState } from 'react';

import {
  isEmbedBlockedError,
  loadYouTubeIframeApi,
  YT_STATE,
  type YouTubePlayer,
} from '../../artist-detail/-artist-detail.youtube-api';

/**
 * a music video playing muted behind a banner.
 *
 * mounted ONLY while its banner holds the stage (see -discover.video-stage),
 * so leaving the screen tears the player down. it stays invisible until
 * youtube itself says the video is PLAYING: a black loading frame, a spinner,
 * or "the owner disallowed embedding" never shows. an error hands the banner
 * back to its animated artwork and tells the page this video won't play.
 */

export interface BackdropVideoProps {
  videoId: string | null;
  playing: boolean;
  /** youtube refused this video (embedding off, removed): try something else */
  onUnplayable?: (videoId: string) => void;
}

type MutablePlayer = YouTubePlayer & { mute?: () => void };

export function BackdropVideo({ videoId, playing, onUnplayable }: BackdropVideoProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const [live, setLive] = useState(false);
  const unplayable = useRef(onUnplayable);
  unplayable.current = onUnplayable;

  useEffect(() => {
    setLive(false);
    if (!videoId || !playing || !host.current) return;
    let player: MutablePlayer | null = null;
    let cancelled = false;
    const mountPoint = document.createElement('div');
    host.current.appendChild(mountPoint);
    void loadYouTubeIframeApi().then((api) => {
      if (cancelled || !api) {
        if (!cancelled && !api) unplayable.current?.(videoId);
        return;
      }
      player = new api.Player(mountPoint, {
        videoId,
        host: 'https://www.youtube-nocookie.com',
        width: '100%',
        height: '100%',
        playerVars: {
          autoplay: 1,
          mute: 1,
          controls: 0,
          loop: 1,
          playlist: videoId,
          playsinline: 1,
          modestbranding: 1,
          rel: 0,
          iv_load_policy: 3,
          disablekb: 1,
          fs: 0,
          start: 20,
        },
        events: {
          onReady: (e) => {
            (e.target as MutablePlayer).mute?.();
            e.target.playVideo();
          },
          onStateChange: (e) => {
            if (!cancelled && e.data === YT_STATE.PLAYING) setLive(true);
          },
          onError: (e) => {
            if (cancelled) return;
            setLive(false);
            if (isEmbedBlockedError(e.data) || e.data === 100 || e.data === 2 || e.data === 5) {
              unplayable.current?.(videoId);
            }
          },
        },
      });
    });
    return () => {
      cancelled = true;
      try {
        player?.destroy();
      } catch {
        /* already gone */
      }
      mountPoint.remove();
    };
  }, [videoId, playing]);

  if (!videoId || !playing) return null;
  return (
    <div ref={host} className={`dsc-backdrop-video${live ? ' ready' : ''}`} aria-hidden="true" />
  );
}
