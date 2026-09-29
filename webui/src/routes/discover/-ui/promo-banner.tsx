import type { ReactNode } from 'react';

import { BackdropVideo } from './backdrop-video';

/**
 * a full-width banner that advertises one thing (a release, an artist, a song)
 * the way spotify's home does: big type over a moving background.
 *
 * the background is always alive. the artwork drifts (a slow ken burns pan and
 * zoom) under a sheen in the artwork's own colour, and when the banner holds
 * the video stage its music video fades in over that, once youtube says it's
 * actually playing. the artwork also sits sharp on the left as the ad's cover.
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
}: PromoBannerProps) {
  return (
    <section
      ref={rootRef}
      className={`dsc-promo dsc-promo--${kind} dsc-promo--${size}${playing && videoId ? ' is-live' : ''}`}
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
        <BackdropVideo videoId={videoId} playing={playing} onUnplayable={onUnplayable} />
        <div className="dsc-promo-scrim" />
      </div>
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
        {size === 'portrait' ? null : (
          <div
            className={`dsc-promo-cover${round ? ' round' : ''}`}
            aria-hidden="true"
            style={art ? { backgroundImage: `url('${art}')` } : undefined}
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
