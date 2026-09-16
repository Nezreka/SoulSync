/**
 * the youtube iframe player api, loaded once and only when somebody presses
 * play.
 *
 * a plain <iframe> can show a video, but it can't tell us when the video
 * ended (no autoplay-next) or that youtube refused to embed it (the viewer
 * gets youtube's grey "video unavailable" box and no way forward). the api
 * script gives both, so it is worth one script tag from youtube.com the first
 * time a video plays. the caller still has to handle `null`: an ad blocker or
 * a dead network leaves us with the plain iframe, which still plays.
 */

export interface YouTubePlayerEvent {
  data: number;
  target: YouTubePlayer;
}

export interface YouTubePlayer {
  loadVideoById(videoId: string): void;
  playVideo(): void;
  pauseVideo(): void;
  destroy(): void;
  getIframe?(): HTMLIFrameElement;
}

export interface YouTubePlayerOptions {
  videoId: string;
  host?: string;
  width?: string | number;
  height?: string | number;
  playerVars?: Record<string, string | number>;
  events?: {
    onReady?: (event: YouTubePlayerEvent) => void;
    onStateChange?: (event: YouTubePlayerEvent) => void;
    onError?: (event: YouTubePlayerEvent) => void;
  };
}

export interface YouTubeApi {
  Player: new (element: HTMLElement, options: YouTubePlayerOptions) => YouTubePlayer;
  PlayerState: {
    ENDED: number;
    PLAYING: number;
    PAUSED: number;
  };
}

declare global {
  interface Window {
    YT?: Partial<YouTubeApi>;
    onYouTubeIframeAPIReady?: () => void;
  }
}

/** youtube's own numbers, so the caller can name them instead of remembering. */
export const YT_STATE = { ENDED: 0, PLAYING: 1, PAUSED: 2 } as const;

/** 101 and 150 are both "the owner disallowed embedding"; 100 is gone; 2/5 are broken. */
export function isEmbedBlockedError(code: number): boolean {
  return code === 101 || code === 150;
}

export const YOUTUBE_API_SRC = 'https://www.youtube.com/iframe_api';

/** how long to wait for youtube.com before playing without the api. */
const LOAD_TIMEOUT_MS = 6000;

let pending: Promise<YouTubeApi | null> | null = null;

function ready(): YouTubeApi | null {
  const api = window.YT;
  return api && typeof api.Player === 'function' && api.PlayerState ? (api as YouTubeApi) : null;
}

export function loadYouTubeIframeApi(): Promise<YouTubeApi | null> {
  const already = ready();
  if (already) return Promise.resolve(already);
  if (pending) return pending;

  pending = new Promise<YouTubeApi | null>((resolve) => {
    let settled = false;
    const finish = (api: YouTubeApi | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(api);
    };
    const timer = setTimeout(() => {
      // let a later play try again instead of remembering one bad network
      pending = null;
      finish(null);
    }, LOAD_TIMEOUT_MS);

    // youtube calls this global once the script has set up window.YT. chain
    // any callback something else installed so we don't steal it.
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      previous?.();
      finish(ready());
    };

    const existing = document.querySelector<HTMLScriptElement>(`script[src="${YOUTUBE_API_SRC}"]`);
    if (!existing) {
      const script = document.createElement('script');
      script.src = YOUTUBE_API_SRC;
      script.async = true;
      script.onerror = () => {
        pending = null;
        finish(null);
      };
      document.head.appendChild(script);
    }
  });
  return pending;
}
