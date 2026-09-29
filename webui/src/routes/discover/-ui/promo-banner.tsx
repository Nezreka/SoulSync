import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react';

import { BackdropVideo } from './backdrop-video';

/**
 * a full-width banner that advertises one thing (a release, an artist, a song)
 * the way spotify's home does: big type over a moving background.
 *
 * the background is always alive. the artwork drifts (a slow ken burns pan and
 * zoom) under a sheen in the artwork's own colour, and when the banner holds
 * the video stage its music video fades in over that, once youtube says it's
 * actually playing. the artwork also sits sharp on the left as the ad's cover.
 *
 * a live video can be unmuted (tap for sound), which pauses soulsync's own
 * player and holds the stage on this banner until it's muted again. under a
 * mouse the banner tilts toward the pointer with a glare that follows it.
 */

export interface PromoBannerProps {
  /** a class hook per kind: release / artist / throwback */
  kind: string;
  eyebrow: string;
  title: string;
  subtitle?: ReactNode;
  art: string | null;
  /** round art for an artist, square for a release or song */
  round?: boolean;
  actions: ReactNode;
  /** 'r, g, b' from the artwork */
  glowRgb?: string | null;
  /** the banner's element, for the video stage */
  rootRef?: (el: HTMLElement | null) => void;
  videoId: string | null;
  playing: boolean;
  onUnplayable?: (videoId: string) => void;
  videoToggle?: { on: boolean; onToggle: () => void };
  /**
   * wide: a full-width row. feature: the big bento tile. tile: a smaller bento
   * tile. portrait: a tall 9:16 card, art filling it, words at the bottom.
   */
  size?: 'wide' | 'feature' | 'tile' | 'portrait';
  /** the pointer entered or left: the stage plays a hovered card's video first */
  onHoverChange?: (hover: boolean) => void;
  /** its sound went on or off: the stage keeps a banner with sound on */
  onSoundChange?: (on: boolean) => void;
  /** a rail is cycling through its cards and this is the one up: show the countdown */
  cycleMs?: number | null;
}

/** pointer position over the banner as -1..1 from its centre, for the tilt */
export function tiltFrom(
  x: number,
  y: number,
  rect: { left: number; top: number; width: number; height: number },
): { px: number; py: number } {
  const clamp = (n: number) => Math.max(-1, Math.min(1, n));
  if (rect.width <= 0 || rect.height <= 0) return { px: 0, py: 0 };
  return {
    px: clamp(((x - rect.left) / rect.width) * 2 - 1),
    py: clamp(((y - rect.top) / rect.height) * 2 - 1),
  };
}

function pauseSoulSyncPlayer() {
  const audio = document.getElementById('audio-player') as HTMLAudioElement | null;
  if (audio && !audio.paused) audio.pause();
}

export function PromoBanner({
  kind,
  eyebrow,
  title,
  subtitle,
  art,
  round = false,
  actions,
  glowRgb,
  rootRef,
  videoId,
  playing,
  onUnplayable,
  videoToggle,
  size = 'wide',
  onHoverChange,
  onSoundChange,
  cycleMs,
}: PromoBannerProps) {
  const live = playing && Boolean(videoId);
  const [soundOn, setSoundOn] = useState(false);
  const soundCb = useRef(onSoundChange);
  soundCb.current = onSoundChange;
  // every time the video stops (scrolled away, lost the stage), it's muted again
  useEffect(() => {
    if (!live) setSoundOn(false);
  }, [live]);
  useEffect(() => {
    soundCb.current?.(soundOn);
  }, [soundOn]);
  const toggleSound = () => {
    if (!soundOn) pauseSoulSyncPlayer();
    setSoundOn(!soundOn);
  };

  const frame = useRef(0);
  const tilts = size !== 'wide';
  const onPointerMove = (e: PointerEvent<HTMLElement>) => {
    if (e.pointerType !== 'mouse') return;
    const el = e.currentTarget;
    const { px, py } = tiltFrom(e.clientX, e.clientY, el.getBoundingClientRect());
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      el.style.setProperty('--px', px.toFixed(3));
      el.style.setProperty('--py', py.toFixed(3));
    });
  };
  const onPointerLeave = (e: PointerEvent<HTMLElement>) => {
    cancelAnimationFrame(frame.current);
    e.currentTarget.style.setProperty('--px', '0');
    e.currentTarget.style.setProperty('--py', '0');
  };
  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  return (
    <section
      ref={rootRef}
      className={`dsc-promo dsc-promo--${kind} dsc-promo--${size}${live ? ' is-live' : ''}${soundOn ? ' has-sound' : ''}`}
      onPointerMove={tilts ? onPointerMove : undefined}
      onPointerLeave={tilts ? onPointerLeave : undefined}
      onMouseEnter={onHoverChange ? () => onHoverChange(true) : undefined}
      onMouseLeave={onHoverChange ? () => onHoverChange(false) : undefined}
      onFocus={onHoverChange ? () => onHoverChange(true) : undefined}
      onBlur={
        onHoverChange
          ? (e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onHoverChange(false);
            }
          : undefined
      }
      aria-label={`${eyebrow}: ${title}`}
      style={glowRgb ? ({ '--promo-rgb': glowRgb } as React.CSSProperties) : undefined}
    >
      <div className="dsc-promo-bg" aria-hidden="true">
        {art ? (
          <div className="dsc-promo-drift" style={{ backgroundImage: `url('${art}')` }} />
        ) : null}
        <div className="dsc-promo-sheen" />
        <BackdropVideo
          videoId={videoId}
          playing={playing}
          onUnplayable={onUnplayable}
          muted={!soundOn}
        />
        <div className="dsc-promo-scrim" />
        {tilts ? <div className="dsc-promo-glare" /> : null}
      </div>
      {live && cycleMs ? (
        <div
          key={videoId}
          className="dsc-promo-countdown"
          aria-hidden="true"
          style={{ animationDuration: `${cycleMs}ms` }}
        />
      ) : null}
      {live ? (
        <button
          type="button"
          className={`dsc-promo-sound${soundOn ? ' on' : ''}`}
          aria-label={soundOn ? 'Mute this video' : 'Play this video with sound'}
          aria-pressed={soundOn}
          title={soundOn ? 'Mute' : 'Tap for sound'}
          onClick={toggleSound}
        >
          <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
            <path d="M4 9h4l5-4v14l-5-4H4z" fill="currentColor" />
            {soundOn ? (
              <path
                d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            ) : (
              <path
                d="M16 9.5l5 5M21 9.5l-5 5"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            )}
          </svg>
          {size === 'portrait' ? null : <span>{soundOn ? 'Sound on' : 'Tap for sound'}</span>}
        </button>
      ) : null}
      {videoToggle ? (
        <button
          type="button"
          className={`dsc-promo-video-toggle${videoToggle.on ? ' on' : ''}`}
          title={videoToggle.on ? 'Turn video backgrounds off' : 'Turn video backgrounds on'}
          aria-label={videoToggle.on ? 'Turn video backgrounds off' : 'Turn video backgrounds on'}
          aria-pressed={videoToggle.on}
          onClick={videoToggle.onToggle}
        >
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <rect
              x="3"
              y="6"
              width="13"
              height="12"
              rx="2"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            />
            <path
              d="M16 10l5-3v10l-5-3z"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            {videoToggle.on ? null : (
              <path d="M3 3l18 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            )}
          </svg>
        </button>
      ) : null}
      <div className="dsc-promo-body">
        {size === 'portrait' || !art ? null : (
          <div
            className={`dsc-promo-cover${round ? ' round' : ''}`}
            aria-hidden="true"
            style={{ backgroundImage: `url('${art}')` }}
          />
        )}
        <div className="dsc-promo-copy">
          <span className="dsc-promo-eyebrow">{eyebrow}</span>
          <h3 className="dsc-promo-title">{title}</h3>
          {subtitle ? <p className="dsc-promo-sub">{subtitle}</p> : null}
          <div className="dsc-promo-actions">{actions}</div>
        </div>
      </div>
    </section>
  );
}
