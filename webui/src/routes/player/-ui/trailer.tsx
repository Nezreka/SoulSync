/**
 * trailers, the way the video side does them: a muted trailer that takes
 * over the backdrop a moment after a movie or show page opens, and a Trailer
 * button that plays the whole thing with sound. the trailer is TMDB's youtube
 * key from the title's extras; it plays as a youtube embed, which is not the
 * player core (stage b) and never writes a resume position.
 *
 * whether the background trailer plays is the viewer's choice on this
 * device; it starts from the video side's own billboard setting and stays
 * off for anyone who asked their system for less motion.
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

import { extrasQuery, videoPrefsQuery } from '../-player.api';
import { PlayIcon } from './icons';
import { styles as s } from './kit';

const KEY = 'ss-player-trailers';
const CHANGED = 'ss-player-trailers-changed';
/** how long the page shows its backdrop before the trailer starts */
const START_AFTER_MS = 2600;
/** reveal anyway if youtube never reports playing (a blocked handshake) */
const REVEAL_FALLBACK_MS = 4500;

type Choice = 'on' | 'off' | null;

function readChoice(): Choice {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'on' || v === 'off' ? v : null;
  } catch {
    return null;
  }
}

export function setTrailerChoice(on: boolean) {
  try {
    localStorage.setItem(KEY, on ? 'on' : 'off');
  } catch {
    /* private mode: lasts until the page closes */
  }
  window.dispatchEvent(new Event(CHANGED));
}

function subscribe(cb: () => void) {
  window.addEventListener(CHANGED, cb);
  window.addEventListener('storage', cb);
  return () => {
    window.removeEventListener(CHANGED, cb);
    window.removeEventListener('storage', cb);
  };
}

function reducedMotion(): boolean {
  return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

/** the effective setting: this device's choice, else the video side's */
export function trailersOn(
  choice: Choice,
  serverDefault: boolean | undefined,
  lessMotion: boolean,
) {
  if (choice) return choice === 'on';
  if (lessMotion) return false;
  return serverDefault ?? true;
}

export function useTrailersSetting(): [boolean, (on: boolean) => void] {
  const choice = useSyncExternalStore(subscribe, readChoice, () => null);
  const prefs = useQuery(videoPrefsQuery());
  return [trailersOn(choice, prefs.data?.billboard_autoplay, reducedMotion()), setTrailerChoice];
}

export function embedUrl(key: string, background: boolean): string {
  const params = new URLSearchParams({
    autoplay: '1',
    playsinline: '1',
    rel: '0',
    modestbranding: '1',
    enablejsapi: '1',
  });
  if (background) {
    params.set('mute', '1');
    params.set('controls', '0');
    // most trailers open on a rating card and studio logos
    params.set('start', '5');
  }
  return `https://www.youtube.com/embed/${encodeURIComponent(key)}?${params.toString()}`;
}

/** youtube's player state from a postMessage, or null */
export function playerState(data: unknown): number | null {
  let msg: { event?: string; info?: unknown } | null = null;
  try {
    msg = typeof data === 'string' ? JSON.parse(data) : null;
  } catch {
    return null;
  }
  if (!msg) return null;
  if (msg.event === 'onStateChange' && typeof msg.info === 'number') return msg.info;
  const info = msg.info as { playerState?: unknown } | undefined;
  if (msg.event === 'infoDelivery' && typeof info?.playerState === 'number')
    return info.playerState;
  return null;
}

/**
 * the muted trailer behind a hero. renders nothing until it is time, then an
 * embed that fades in over the backdrop once youtube says it is playing, and
 * fades back out when it ends.
 */
export function BackgroundTrailer({ kind, id }: { kind: 'movie' | 'show'; id: number }) {
  const [enabled] = useTrailersSetting();
  const trailer = useQuery({ ...extrasQuery(kind, id), enabled, select: (e) => e.trailer ?? null });
  const [stage, setStage] = useState<'waiting' | 'loading' | 'playing' | 'ended' | 'stopped'>(
    'waiting',
  );
  const [muted, setMuted] = useState(true);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const key = trailer.data?.key;

  useEffect(() => {
    setStage('waiting');
    if (!enabled || !key) return;
    const t = window.setTimeout(() => setStage('loading'), START_AFTER_MS);
    return () => window.clearTimeout(t);
  }, [enabled, key]);

  // the page went to the background: a trailer with sound must not keep going
  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        setStage((st) => (st === 'playing' || st === 'loading' ? 'stopped' : st));
    };
    document.addEventListener('visibilitychange', onHide);
    return () => document.removeEventListener('visibilitychange', onHide);
  }, []);

  useEffect(() => {
    if (stage !== 'loading') return;
    const frame = frameRef.current;
    const handshake = () => {
      try {
        frame?.contentWindow?.postMessage(
          '{"event":"listening","id":"pl-bg","channel":"widget"}',
          '*',
        );
      } catch {
        /* not ready */
      }
    };
    const onMessage = (event: MessageEvent) => {
      if (!frame?.contentWindow || event.source !== frame.contentWindow) return;
      const st = playerState(event.data);
      if (st === 1) setStage('playing');
      else if (st === 0) setStage('ended');
    };
    window.addEventListener('message', onMessage);
    frame?.addEventListener('load', handshake);
    const again = window.setTimeout(handshake, 800);
    const fallback = window.setTimeout(
      () => setStage((st) => (st === 'loading' ? 'playing' : st)),
      REVEAL_FALLBACK_MS,
    );
    return () => {
      window.removeEventListener('message', onMessage);
      frame?.removeEventListener('load', handshake);
      window.clearTimeout(again);
      window.clearTimeout(fallback);
    };
  }, [stage]);

  // once playing, keep listening for the end
  useEffect(() => {
    if (stage !== 'playing') return;
    const frame = frameRef.current;
    const onMessage = (event: MessageEvent) => {
      if (!frame?.contentWindow || event.source !== frame.contentWindow) return;
      if (playerState(event.data) === 0) setStage('ended');
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [stage]);

  if (!key || stage === 'waiting' || stage === 'stopped' || stage === 'ended') return null;

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    frameRef.current?.contentWindow?.postMessage(
      JSON.stringify({ event: 'command', func: next ? 'mute' : 'unMute', args: [] }),
      '*',
    );
  };

  return (
    <>
      <div
        className={s.heroTrailer}
        data-playing={stage === 'playing' ? 'true' : undefined}
        aria-hidden="true"
      >
        <iframe
          ref={frameRef}
          title="Trailer"
          src={embedUrl(key, true)}
          allow="autoplay; encrypted-media; picture-in-picture"
          tabIndex={-1}
        />
      </div>
      {stage === 'playing' ? (
        <div className={s.trailerControls}>
          <button
            type="button"
            className={s.trailerBtn}
            onClick={toggleMute}
            aria-label={muted ? 'Unmute trailer' : 'Mute trailer'}
            data-nav
          >
            {muted ? 'Sound on' : 'Sound off'}
          </button>
          <button
            type="button"
            className={s.trailerBtn}
            onClick={() => setStage('stopped')}
            aria-label="Stop trailer"
            data-nav
          >
            Stop
          </button>
        </div>
      ) : null}
    </>
  );
}

/** a youtube video (a trailer, a clip) full screen, with sound */
export function VideoDialog({
  videoKey,
  title,
  onClose,
}: {
  videoKey: string;
  title: string;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [onClose]);
  return inPlayer(
    <div
      className={s.trailerModal}
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className={s.trailerBox}>
        <iframe
          title={title}
          src={embedUrl(videoKey, false)}
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          allowFullScreen
        />
        <button
          ref={closeRef}
          type="button"
          className={s.trailerClose}
          onClick={onClose}
          aria-label="Close"
          data-nav
        >
          Close
        </button>
      </div>
    </div>,
  );
}

/** the Trailer button: the whole trailer, with sound, in a dialog */
export function TrailerButton({ kind, id }: { kind: 'movie' | 'show'; id: number }) {
  const trailer = useQuery({ ...extrasQuery(kind, id), select: (e) => e.trailer ?? null });
  const [open, setOpen] = useState(false);
  if (!trailer.data?.key) return null;
  return (
    <>
      <button type="button" className={s.btn} onClick={() => setOpen(true)} data-nav>
        <PlayIcon />
        Trailer
      </button>
      {open ? (
        <VideoDialog
          videoKey={trailer.data.key}
          title={trailer.data.name ?? 'Trailer'}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

/**
 * a full-screen layer, mounted on the player's root: inside a page (which
 * animates with a transform) or the header (a backdrop filter) position:fixed
 * would be boxed into that element
 */
export function inPlayer(node: ReactNode) {
  const root =
    typeof document !== 'undefined' ? document.querySelector('[data-player-root]') : null;
  return root ? createPortal(node, root) : node;
}
