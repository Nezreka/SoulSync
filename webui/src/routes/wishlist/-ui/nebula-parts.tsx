import { type ReactNode, useEffect, useState } from 'react';

import { orbAnimationDelay } from '../-wishlist.helpers';

/*
 * The wishlist's shared grammar. Music, movies, TV and YouTube all render
 * through these, so the four tabs can't drift into four designs: one orb, one
 * poster, one zone header, one side panel.
 */

/* ── zones ─────────────────────────────────────────────────────────────── */

export interface ZoneAction {
  label: string;
  title?: string;
  disabled?: boolean;
  onClick: () => void;
}

/** a zone's header row: what's wrong, how many, and its one bulk action. */
export function NebulaZoneHead({
  kind,
  title,
  sub,
  action,
  className = '',
}: {
  kind: string;
  title: string;
  sub: string;
  action?: ZoneAction | null;
  className?: string;
}) {
  return (
    <div className={`wlp-zone-head wlp-zone--${kind}${className ? ` ${className}` : ''}`}>
      <h2 className="wlp-zone-title">{title}</h2>
      <span className="wlp-zone-sub">{sub}</span>
      <span className="wlp-zone-rule" aria-hidden="true" />
      {action ? (
        <button
          type="button"
          className="wlp-btn wlp-btn--sm wlp-zone-retry"
          disabled={action.disabled}
          title={action.title}
          onClick={action.onClick}
        >
          {action.label}
        </button>
      ) : null}
    </div>
  );
}

/** a zone: header plus its grid of orbs or posters. */
export function NebulaZone({
  kind,
  title,
  sub,
  action,
  grid = 'orbs',
  children,
}: {
  kind: string;
  title: string;
  sub: string;
  action?: ZoneAction | null;
  grid?: 'orbs' | 'posters';
  children: ReactNode;
}) {
  return (
    <section className={`wlp-zone wlp-zone--${kind}`} aria-label={title}>
      <NebulaZoneHead kind={kind} title={title} sub={sub} action={action} />
      <div
        className={grid === 'posters' ? 'wlp-zone-grid wlp-zone-grid--posters' : 'wlp-zone-grid'}
      >
        {children}
      </div>
    </section>
  );
}

/* ── the orb ───────────────────────────────────────────────────────────── */

export interface NebulaOrbProps {
  name: string;
  image: string;
  /** shown when there's no image. */
  initials: string;
  hue: number;
  sizeClass: 'orb-sm' | 'orb-md' | 'orb-lg';
  meta: string;
  /** a stuck orb gets the amber edge and amber meta. */
  stuck: boolean;
  /** drives the amber edge in css; the music page has always set it. */
  failingCount: number;
  expanded: boolean;
  processing?: boolean;
  pulse?: boolean;
  ringCovers?: string[];
  index: number;
  /** extra data-* attributes (the music page keys orbs by data-artist). */
  data?: Record<string, string>;
  tooltipSub: string;
  onToggle: () => void;
  /** the name under the orb: a link to the artist / show / channel. */
  onOpen?: () => void;
  openTitle?: string;
  /** an image that fails to load falls back to the initials. */
  onImageError?: () => void;
}

/** one orb: a bubble that opens the panel, and a name that opens the page. */
export function NebulaOrb({
  name,
  image,
  initials,
  hue,
  sizeClass,
  meta,
  stuck,
  failingCount,
  expanded,
  processing = false,
  pulse = false,
  ringCovers = [],
  index,
  data = {},
  tooltipSub,
  onToggle,
  onOpen,
  openTitle = 'Open',
}: NebulaOrbProps) {
  // the entrance plays once. a poll refetch can reorder orbs, and moving a node
  // restarts its css animations, so without this every moved orb blinked out
  // and faded back in while a wishlist run was going.
  const [entered, setEntered] = useState(false);
  // a dead image url falls back to the initials instead of an empty circle.
  const [broken, setBroken] = useState(false);
  const showImage = image && !broken;
  const dataAttrs = Object.fromEntries(Object.entries(data).map(([k, v]) => [`data-${k}`, v]));

  return (
    <div
      className={`wl-orb-group${expanded ? ' expanded' : ''}${processing ? ' orb-processing' : ''}${entered ? ' wl-orb-entered' : ''}`}
      {...dataAttrs}
      data-failing={failingCount}
      style={entered ? undefined : { animationDelay: `${orbAnimationDelay(index)}ms` }}
      onAnimationEnd={(event) => {
        if (event.target === event.currentTarget && event.animationName === 'orbEntrance') {
          setEntered(true);
        }
      }}
    >
      <div className="wl-orb-tooltip">
        {name}
        <br />
        <span>{tooltipSub}</span>
      </div>

      <button
        type="button"
        className={`wl-orb ${sizeClass}${pulse ? ' orb-pulse' : ''}`}
        style={{ ['--orb-hue' as string]: hue }}
        aria-label={`${name}, ${meta}`}
        aria-expanded={expanded}
        onClick={onToggle}
      >
        <span className="wl-orb-glow" />
        {showImage ? (
          <img className="wl-orb-img" src={image} alt="" onError={() => setBroken(true)} />
        ) : (
          <span className="wl-orb-initials">{initials}</span>
        )}
        <span className="wl-orb-ring" />

        {ringCovers.length > 0 ? (
          <span className="wl-orb-art-ring">
            {ringCovers.map((url, i) => (
              <img
                key={`${url}-${i}`}
                className="wl-art-ring-item"
                src={url}
                alt=""
                style={{ ['--ring-angle' as string]: `${(360 / ringCovers.length) * i}deg` }}
              />
            ))}
          </span>
        ) : null}
      </button>

      {/* a sibling of .wl-orb, not a child, so its click can't reach the
          toggle. stopPropagation stays as a guard against future nesting. */}
      <button
        type="button"
        className="wl-orb-label"
        title={openTitle}
        onClick={(event) => {
          event.stopPropagation();
          onOpen?.();
        }}
      >
        {name}
      </button>
      <div className={`wl-orb-meta${stuck ? ' wl-orb-meta--stuck' : ''}`}>{meta}</div>
    </div>
  );
}

/* ── the poster (movies: a film is a poster, not an orb) ───────────────── */

export function NebulaPoster({
  title,
  image,
  meta,
  stuck,
  expanded,
  data = {},
  onToggle,
}: {
  title: string;
  image: string;
  meta: string;
  stuck: boolean;
  expanded: boolean;
  data?: Record<string, string>;
  onToggle: () => void;
}) {
  const [broken, setBroken] = useState(false);
  const dataAttrs = Object.fromEntries(Object.entries(data).map(([k, v]) => [`data-${k}`, v]));
  return (
    <button
      type="button"
      className={`wlp-poster${expanded ? ' expanded' : ''}${stuck ? ' is-stuck' : ''}`}
      {...dataAttrs}
      aria-label={`${title}, ${meta}`}
      aria-expanded={expanded}
      onClick={onToggle}
    >
      <span className="wlp-poster-art">
        {image && !broken ? (
          <img src={image} alt="" loading="lazy" onError={() => setBroken(true)} />
        ) : (
          <span className="wlp-poster-ph" aria-hidden="true">
            {title.substring(0, 2).toUpperCase()}
          </span>
        )}
      </span>
      <span className="wlp-poster-title" title={title}>
        {title}
      </span>
      <span className={`wlp-poster-meta${stuck ? ' is-stuck' : ''}`}>{meta}</span>
    </button>
  );
}

/* ── the side panel ────────────────────────────────────────────────────── */

/** any open dialog (a confirm, the source picker, the release picker) owns escape. */
const DIALOG_OPEN = '[role="dialog"], [aria-modal="true"], .modal-overlay.active, .vms-overlay';

/**
 * whether a dialog is actually up. the shell keeps hidden ones in the page at
 * all times (the profile picker is a role="dialog"), so being present is not
 * being open: it has to be shown all the way up.
 */
function dialogShowing(): boolean {
  return Array.from(document.querySelectorAll<HTMLElement>(DIALOG_OPEN)).some((el) => {
    for (let n: HTMLElement | null = el; n && n !== document.body; n = n.parentElement) {
      const cs = getComputedStyle(n);
      // some overlays park themselves faded out instead (the track detail one is
      // opacity 0 + pointer-events none until it opens)
      if (n.hidden || cs.display === 'none' || cs.visibility === 'hidden') return false;
      if (cs.opacity === '0' || cs.pointerEvents === 'none') return false;
    }
    return true;
  });
}

export interface NebulaSheetProps {
  /** names the panel for assistive tech: "<name> on the wishlist". */
  label: string;
  image: string;
  /** a film poster sits as a rectangle, everything else as a circle. */
  avatarShape?: 'circle' | 'poster';
  avatarFallback: string;
  name: ReactNode;
  meta: ReactNode;
  callout?: { headline: string; detail: string } | null;
  actions: ReactNode;
  children?: ReactNode;
  onClose: () => void;
  closeLabel?: string;
}

/**
 * the panel beside the field. side by side on wide screens, a drawer with a
 * tap-to-close backdrop below that, a bottom sheet on phones (css). escape
 * closes it unless a dialog opened from it is the thing being dismissed.
 */
export function NebulaSheet({
  label,
  image,
  avatarShape = 'circle',
  avatarFallback,
  name,
  meta,
  callout,
  actions,
  children,
  onClose,
  closeLabel = 'Close artist panel',
}: NebulaSheetProps) {
  const [broken, setBroken] = useState(false);
  const showImage = image && !broken;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (dialogShowing()) return;
      onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const avatarClass = `wlp-sheet-avatar${avatarShape === 'poster' ? ' wlp-sheet-avatar--poster' : ''}`;

  return (
    <>
      {/* only shown where the panel floats over the field (tablet, phone):
          a tap outside closes it. */}
      <div className="wlp-sheet-scrim" aria-hidden="true" onClick={onClose} />
      <aside className="wlp-sheet" aria-label={label}>
        <div className="wlp-sheet-backdrop" aria-hidden="true">
          {showImage ? <img src={image} alt="" /> : null}
        </div>
        <button
          type="button"
          className="wlp-sheet-close"
          aria-label={closeLabel}
          title="Close"
          onClick={onClose}
        >
          ✕
        </button>

        <div className="wlp-sheet-head">
          {showImage ? (
            <img className={avatarClass} src={image} alt="" onError={() => setBroken(true)} />
          ) : (
            <div className={`${avatarClass} wlp-sheet-avatar--ph`} aria-hidden="true">
              {avatarFallback}
            </div>
          )}
          <div className="wlp-sheet-id">
            <h2 className="wlp-sheet-name">{name}</h2>
            <div className="wlp-sheet-meta">{meta}</div>
          </div>
        </div>

        {callout ? (
          <div className="wlp-sheet-callout" role="note">
            <span className="wlp-sheet-callout-icon" aria-hidden="true">
              !
            </span>
            <div>
              <div className="wlp-sheet-callout-title">{callout.headline}</div>
              <p className="wlp-sheet-callout-text">{callout.detail}</p>
            </div>
          </div>
        ) : null}

        <div className="wlp-sheet-actions">{actions}</div>

        <div className="wlp-sheet-body">{children}</div>
      </aside>
    </>
  );
}

/** the field + panel stage. lifts above the sticky command bar while a panel floats. */
export function NebulaStage({
  sheetOpen,
  field,
  sheet,
}: {
  sheetOpen: boolean;
  field: ReactNode;
  sheet: ReactNode;
}) {
  return (
    <div className={`wl-nebula${sheetOpen ? ' has-sheet' : ''}`}>
      <div className={`wlp-stage${sheetOpen ? ' has-sheet' : ''}`}>
        {field}
        {sheet}
      </div>
    </div>
  );
}
