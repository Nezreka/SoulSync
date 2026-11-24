/**
 * the player's building blocks: artwork, cards, rails, grids, the hero, play
 * buttons, badges, empty states. every page is made of these, and they are
 * what the phone and tv apps copy.
 */

import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ComponentProps, type ReactNode } from 'react';

import type { RailCard } from '../-lib/rails';
import type { PlayVerdict } from '../-player.types';

import { canRetryArt, retryArt } from '../-lib/images';
import { playerSettingsQuery } from '../-player.api';
import { inferContextTargetFromCard, openContextMenu } from './context-menu';
import {
  BackIcon,
  CheckIcon,
  ChevronIcon,
  GridIcon,
  ListIcon,
  MoreIcon,
  PlayIcon,
  PlusIcon,
} from './icons';
import { usePlayCard } from './playback/play-card';
import s from './player.module.css';
import { usePreview } from './preview';

// ── links ──────────────────────────────────────────────────────────────

/**
 * a router link to a player path built at runtime (cards carry their href as
 * data). every href the player makes starts with /player.
 */
export function PlayerLink({
  href,
  ...props
}: Omit<ComponentProps<typeof Link>, 'to'> & { href: string }) {
  return <Link {...props} to={href as '/player'} />;
}

// ── artwork ────────────────────────────────────────────────────────────

/** an image that fades in when loaded, and says the title when there is none */
/** a steady colour for a title with no picture: the same title, the same colour */
function hueOf(text: string): number {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) | 0;
  return Math.abs(h) % 360;
}

export function Art({
  src,
  srcSet,
  sizes,
  alt,
  className,
  eager,
}: {
  src?: string | null;
  /** widths the server can make (images.ts); the browser picks by rendered size */
  srcSet?: string;
  sizes?: string;
  alt: string;
  className?: string;
  /** above the fold: fetch now, and first */
  eager?: boolean;
}) {
  const [state, setState] = useState<'loading' | 'ok' | 'failed'>(src ? 'loading' : 'failed');
  // library art may answer "not yet" while it's fetched: ask again a couple of times
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    setState(src ? 'loading' : 'failed');
    setAttempt(0);
  }, [src]);
  return (
    <>
      {src && state !== 'failed' ? (
        <img
          className={className ?? s.artImg}
          src={attempt ? retryArt(src, attempt) : src}
          srcSet={srcSet}
          sizes={srcSet ? (sizes ?? 'auto, 220px') : undefined}
          alt=""
          loading={eager ? 'eager' : 'lazy'}
          fetchPriority={eager ? 'high' : 'auto'}
          decoding="async"
          data-loaded={state === 'ok'}
          onLoad={() => setState('ok')}
          onError={() => {
            if (canRetryArt(src) && attempt < 2) {
              const next = attempt + 1;
              window.setTimeout(() => setAttempt(next), 2000 * next);
            } else setState('failed');
          }}
        />
      ) : null}
      {state === 'failed' ? (
        <span
          className={s.artFallback}
          style={{ '--fb-hue': String(hueOf(alt)) } as React.CSSProperties}
          aria-hidden="true"
        >
          <span className={s.artFallbackText}>{alt}</span>
        </span>
      ) : null}
    </>
  );
}

// ── cards ──────────────────────────────────────────────────────────────

/** the play button on a card's art: plays it here; the card still opens its page */
export function CardPlay({
  target,
  title,
}: {
  target: NonNullable<RailCard['play']>;
  title: string;
}) {
  const playCard = usePlayCard();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      className={s.cardPlay}
      data-busy={busy ? 'true' : undefined}
      aria-label={`Play ${title}`}
      title={failed ? 'Nothing here you can play' : `Play ${title}`}
      onClick={(e) => {
        e.stopPropagation();
        if (busy) return;
        setBusy(true);
        setFailed(false);
        playCard(target)
          .then((ok) => setFailed(!ok))
          .catch(() => setFailed(true))
          .finally(() => setBusy(false));
      }}
      data-nav
    >
      <PlayIcon />
    </button>
  );
}

export interface CardAction {
  label: string;
  onSelect: () => void;
}

/** a card that does something here instead of opening a page (play a video) */
export type CardOpen = (card: RailCard) => void;

function openKeys(open: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      open();
    }
  };
}

export function Card({
  card,
  round,
  actions,
  onOpen,
}: {
  card: RailCard;
  round?: boolean;
  actions?: CardAction[];
  onOpen?: CardOpen;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menuOpen]);

  const { handlers, panel } = usePreview(card.preview, card.title, card.href);
  const settingsQ = useQuery({ ...playerSettingsQuery(), enabled: !!card.preview });
  const prefs = settingsQ.data?.settings;
  const showMovieRatings = prefs?.show_movie_ratings ?? false;
  const showTvEpisodeCount = prefs?.show_tv_episode_count ?? true;
  const showYearOnCards = prefs?.show_year_on_cards ?? true;

  const visibleBadge = (() => {
    if (!card.badge) return undefined;
    if (card.preview?.kind === 'movie' && card.badge.startsWith('★') && !showMovieRatings) {
      return undefined;
    }
    if (card.preview?.kind === 'show' && /\bep\b/i.test(card.badge) && !showTvEpisodeCount) {
      return undefined;
    }
    return card.badge;
  })();

  const visibleSubtitle = (() => {
    if (!card.subtitle) return undefined;
    if (!showYearOnCards && card.preview) {
      const cleaned = card.subtitle.replace(/^\d{4}(\s*·\s*)?/, '').trim();
      return cleaned || undefined;
    }
    return card.subtitle;
  })();

  const body = (
    <>
      <div className={s.art}>
        <Art src={card.art} srcSet={card.artSet} sizes={card.artSizes} alt={card.title} />
        {card.have ? (
          <span className={s.cardHave} title={card.have} aria-label={card.have}>
            <CheckIcon />
          </span>
        ) : card.muted ? (
          <span className={s.cardMissing} title={card.muted} aria-label={card.muted}>
            <PlusIcon />
          </span>
        ) : null}
        {visibleBadge ? <span className={s.cardBadge}>{visibleBadge}</span> : null}
        {card.progress !== undefined ? (
          <div
            className={s.progress}
            role="progressbar"
            aria-label="Progress"
            aria-valuenow={Math.round(card.progress * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className={s.progressFill} style={{ width: `${card.progress * 100}%` }} />
          </div>
        ) : null}
      </div>
      <div className={s.cardText}>
        <div className={s.cardTitle} title={card.title}>
          {card.title}
        </div>
        {visibleSubtitle ? <div className={s.cardSub}>{visibleSubtitle}</div> : null}
      </div>
    </>
  );

  return (
    <div
      className={s.cardWrap}
      data-muted={card.muted ? 'true' : undefined}
      title={card.muted}
      data-shape={card.shape}
      data-round={round ? 'true' : undefined}
      ref={menuRef}
      onContextMenu={(e) => {
        e.preventDefault();
        const isOwned = !card.muted && !/\/tmdb\//.test(card.href ?? '');
        openContextMenu({
          ...inferContextTargetFromCard(e, card, {
            onOpen: isOwned && onOpen ? () => onOpen(card) : undefined,
            extraActions: actions?.map((a) => ({ label: a.label, onSelect: a.onSelect })),
          }),
          owned: isOwned,
        });
      }}
    >
      {onOpen ? (
        <div
          className={s.card}
          role="button"
          tabIndex={0}
          aria-label={card.title}
          onClick={() => onOpen(card)}
          onKeyDown={openKeys(() => onOpen(card))}
          data-nav
          {...handlers}
        >
          {body}
        </div>
      ) : card.href ? (
        <PlayerLink href={card.href} className={s.card} data-nav {...handlers}>
          {body}
        </PlayerLink>
      ) : (
        <div className={s.card} tabIndex={0} data-nav {...handlers}>
          {body}
        </div>
      )}
      {panel}
      {card.play ? (
        // over the art but outside the link: a click inside a link is the page's
        // before react can stop it, and opened the album as well as playing it
        <div className={s.cardPlaySlot}>
          <CardPlay target={card.play} title={card.title} />
        </div>
      ) : null}
      {actions?.length ? (
        <>
          <button
            type="button"
            className={s.cardMenuBtn}
            aria-label={`More for ${card.title}`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
            data-nav
          >
            <MoreIcon />
          </button>
          {menuOpen ? (
            <div className={s.cardMenu} role="menu">
              {actions.map((action) => (
                <button
                  key={action.label}
                  type="button"
                  role="menuitem"
                  className={s.menuItem}
                  onClick={() => {
                    setMenuOpen(false);
                    action.onSelect();
                  }}
                  data-nav
                >
                  {action.label}
                </button>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

// ── rails + grids ──────────────────────────────────────────────────────

export function Rail({
  title,
  more,
  children,
}: {
  title: string;
  more?: { to: string; label?: string };
  children: ReactNode;
}) {
  return (
    <section className={s.rail} aria-label={title}>
      <div className={s.railHead}>
        <h2 className={s.sectionTitle}>{title}</h2>
        {more ? (
          <PlayerLink href={more.to} className={s.railMore} data-nav>
            {more.label ?? 'See all'}
          </PlayerLink>
        ) : null}
      </div>
      <HScroll className={s.railTrack}>{children}</HScroll>
    </section>
  );
}

/**
 * a row that scrolls sideways. scrollbars stay hidden, so a mouse gets
 * arrows at the ends (only where there is more that way) that page about a
 * screen at a time; touch swipes, a remote moves focus and the row follows.
 */
export function HScroll({ className, children }: { className: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: true, end: true });
  const update = () => {
    const el = ref.current;
    if (!el) return;
    const next = {
      start: el.scrollLeft <= 4,
      end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 4,
    };
    setEdges((was) => (was.start === next.start && was.end === next.end ? was : next));
  };
  useEffect(() => {
    update();
    const el = ref.current;
    if (!el) return;
    const raf = window.requestAnimationFrame(update);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null;
    ro?.observe(el);
    for (const child of Array.from(el.children).slice(0, 1)) ro?.observe(child);
    const mo = typeof MutationObserver !== 'undefined' ? new MutationObserver(update) : null;
    mo?.observe(el, { childList: true });
    window.addEventListener('resize', update);
    return () => {
      window.cancelAnimationFrame(raf);
      ro?.disconnect();
      mo?.disconnect();
      window.removeEventListener('resize', update);
    };
  }, [children]);
  const page = (dir: 1 | -1) => {
    const el = ref.current;
    if (el) el.scrollBy({ left: dir * el.clientWidth * 0.85, behavior: 'smooth' });
  };
  return (
    <div className={s.hscroll} onMouseEnter={update}>
      <div ref={ref} className={className} onScroll={update}>
        {children}
      </div>
      {edges.start ? null : (
        <button
          type="button"
          className={s.hArrow}
          data-side="prev"
          aria-label="Scroll back"
          tabIndex={-1}
          data-nav-skip
          onClick={() => page(-1)}
        >
          <BackIcon />
        </button>
      )}
      {edges.end ? null : (
        <button
          type="button"
          className={s.hArrow}
          data-side="next"
          aria-label="Scroll on"
          tabIndex={-1}
          data-nav-skip
          onClick={() => page(1)}
        >
          <ChevronIcon />
        </button>
      )}
    </div>
  );
}

export function CardRail({
  title,
  cards,
  more,
  round,
  actionsFor,
  onOpen,
}: {
  title: string;
  cards: RailCard[];
  more?: { to: string; label?: string };
  round?: boolean;
  actionsFor?: (card: RailCard) => CardAction[];
  onOpen?: CardOpen;
}) {
  if (!cards.length) return null;
  return (
    <Rail title={title} more={more}>
      {cards.map((card) => (
        <Card
          key={card.key}
          card={card}
          round={round}
          actions={actionsFor?.(card)}
          onOpen={onOpen}
        />
      ))}
    </Rail>
  );
}

export type ViewMode = 'grid' | 'list';

export function CardGrid({
  cards,
  shape = 'poster',
  round,
  view = 'grid',
  actionsFor,
  onOpen,
}: {
  cards: RailCard[];
  shape?: RailCard['shape'];
  round?: boolean;
  /** a card's own menu (remove from the watchlist) */
  actionsFor?: (card: RailCard) => CardAction[];
  /** a list reads better for long filmographies and big libraries */
  view?: ViewMode;
  onOpen?: CardOpen;
}) {
  // a library is thousands of titles: render a screenful ahead and grow as
  // the end comes near, instead of putting every card in the page at once
  const shown = useProgressive(cards.length, `${view}:${cards.length}:${cards[0]?.key ?? ''}`);
  return (
    <>
      {view === 'list' ? (
        <div className={s.cardList} role="list">
          {cards.slice(0, shown.count).map((card) => (
            <CardRow key={card.key} card={card} round={round} onOpen={onOpen} />
          ))}
        </div>
      ) : (
        <div className={s.grid} data-shape={shape}>
          {cards.slice(0, shown.count).map((card) => (
            <Card
              key={card.key}
              card={card}
              round={round}
              actions={actionsFor?.(card)}
              onOpen={onOpen}
            />
          ))}
        </div>
      )}
      {shown.count < cards.length ? (
        <div ref={shown.sentinel} className={s.gridMore} aria-hidden="true" />
      ) : null}
    </>
  );
}

/** one card as a list row: small art, the words, the badges */
export function CardRow({
  card,
  round,
  onOpen,
}: {
  card: RailCard;
  round?: boolean;
  onOpen?: CardOpen;
}) {
  const body = (
    <>
      <span className={s.rowThumb} data-shape={card.shape} data-round={round ? 'true' : undefined}>
        <Art src={card.art} alt={card.title} />
      </span>
      <span className={s.rowMain}>
        <span className={s.rowTitle}>{card.title}</span>
        {card.subtitle ? <span className={s.rowSub}>{card.subtitle}</span> : null}
      </span>
      <span className={s.rowEnd}>
        {card.badge ? <span>{card.badge}</span> : null}
        {card.have ? (
          <span className={s.rowHave} title={card.have} aria-label={card.have}>
            <CheckIcon />
          </span>
        ) : null}
      </span>
    </>
  );
  return (
    <div
      role="listitem"
      data-muted={card.muted ? 'true' : undefined}
      title={card.muted}
      className={s.cardRowWrap}
    >
      {onOpen ? (
        <div
          className={s.cardRow}
          role="button"
          tabIndex={0}
          onClick={() => onOpen(card)}
          onKeyDown={openKeys(() => onOpen(card))}
          data-nav
        >
          {body}
        </div>
      ) : card.href ? (
        <PlayerLink href={card.href} className={s.cardRow} data-nav>
          {body}
        </PlayerLink>
      ) : (
        <div className={s.cardRow} tabIndex={0} data-nav>
          {body}
        </div>
      )}
    </div>
  );
}

/** grid or list, remembered per page on this device */
export function useViewMode(
  key: string,
  initial: ViewMode = 'grid',
): [ViewMode, (v: ViewMode) => void] {
  const storageKey = `ss-player-view:${key}`;
  const [view, setView] = useState<ViewMode>(() => {
    try {
      const v = localStorage.getItem(storageKey);
      return v === 'list' || v === 'grid' ? v : initial;
    } catch {
      return initial;
    }
  });
  const set = (v: ViewMode) => {
    setView(v);
    try {
      localStorage.setItem(storageKey, v);
    } catch {
      /* private mode */
    }
  };
  return [view, set];
}

export function ViewToggle({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (v: ViewMode) => void;
}) {
  return (
    <div className={s.viewToggle} role="radiogroup" aria-label="View">
      <button
        type="button"
        role="radio"
        aria-checked={value === 'grid'}
        aria-label="Grid"
        title="Grid"
        onClick={() => onChange('grid')}
        data-nav
      >
        <GridIcon />
      </button>
      <button
        type="button"
        role="radio"
        aria-checked={value === 'list'}
        aria-label="List"
        title="List"
        onClick={() => onChange('list')}
        data-nav
      >
        <ListIcon />
      </button>
    </div>
  );
}

export const GRID_STEP = 60;

/** the nearest ancestor that scrolls vertically, or null for the viewport */
export function scrollParent(el: HTMLElement): HTMLElement | null {
  for (let node = el.parentElement; node; node = node.parentElement) {
    const overflow = getComputedStyle(node).overflowY;
    if (overflow === 'auto' || overflow === 'scroll') return node;
  }
  return null;
}

/**
 * how many of ``total`` to render: a step, and another step each time the
 * sentinel comes within a couple of screens. ``resetKey`` starts over (a new
 * sort or filter).
 */
export function useProgressive(total: number, resetKey: string, step = GRID_STEP) {
  const [count, setCount] = useState(step);
  const observer = useRef<IntersectionObserver | null>(null);
  useEffect(() => setCount(step), [resetKey, step]);
  useEffect(() => () => observer.current?.disconnect(), []);
  const sentinel = (el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    if (!el || typeof IntersectionObserver === 'undefined') return;
    // the margin only reaches ahead inside the observer's root, so the root
    // is the box that actually scrolls (the page, or the guide's own grid)
    observer.current = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setCount((c) => Math.min(total, c + step));
      },
      { root: scrollParent(el), rootMargin: '1600px 0px' },
    );
    observer.current.observe(el);
  };
  return { count: Math.min(count, total), sentinel };
}

export function SkeletonRail({
  shape = 'poster',
  count = 7,
}: {
  shape?: RailCard['shape'];
  count?: number;
}) {
  return (
    <section className={s.rail} aria-busy="true">
      <div className={s.railHead}>
        <div className={s.skeleton} style={{ width: '12em', height: '1.3em' }} />
      </div>
      <div className={s.railTrack}>
        {Array.from({ length: count }, (_, i) => (
          <div key={i} className={s.cardWrap} data-shape={shape}>
            <div className={`${s.art} ${s.skeleton}`} />
          </div>
        ))}
      </div>
    </section>
  );
}

// ── hero ───────────────────────────────────────────────────────────────

export function Hero({
  backdrop,
  poster,
  posterShape,
  posterRound,
  kicker,
  title,
  logo,
  meta,
  badges,
  overview,
  actions,
  trailer,
  backdropSrcSet,
}: {
  /** a background trailer layer (trailer.tsx), between the backdrop and its scrim */
  trailer?: ReactNode;
  backdrop?: string | null;
  backdropSrcSet?: string;
  poster?: string | null;
  posterShape?: 'poster' | 'square';
  posterRound?: boolean;
  kicker?: ReactNode;
  title: string;
  logo?: string | null;
  meta?: ReactNode;
  badges?: ReactNode;
  overview?: string | null;
  actions?: ReactNode;
}) {
  const [logoOk, setLogoOk] = useState(true);
  return (
    <header className={s.hero}>
      <div className={s.heroBackdrop}>
        {backdrop ? (
          <Art
            src={backdrop}
            srcSet={backdropSrcSet}
            sizes="100vw"
            alt=""
            className={undefined}
            eager
          />
        ) : null}
        {trailer}
      </div>
      <div className={s.heroBody}>
        {poster !== undefined ? (
          <div
            className={s.heroPoster}
            data-shape={posterShape}
            data-round={posterRound ? 'true' : undefined}
          >
            <Art src={poster} alt={title} eager />
          </div>
        ) : null}
        <div className={s.heroText}>
          {kicker ? <div className={s.heroKicker}>{kicker}</div> : null}
          {logo && logoOk ? (
            <img className={s.heroLogo} src={logo} alt={title} onError={() => setLogoOk(false)} />
          ) : (
            <h1 className={s.heroTitle}>{title}</h1>
          )}
          {meta ? <div className={s.heroMeta}>{meta}</div> : null}
          {badges}
          {overview ? <p className={s.heroOverview}>{overview}</p> : null}
          {actions ? <div className={s.heroActions}>{actions}</div> : null}
        </div>
      </div>
    </header>
  );
}

// ── play affordances (stage a: laid out, not live) ─────────────────────

/** what an unwired play button says: honest, never a button that pretends */
export const PLAYBACK_NOT_YET = "This one can't be played from here yet.";

/**
 * the play button every page shows. with ``onPlay`` it plays; without one
 * (a kind of thing nothing plays yet) it is visibly inert: aria-disabled
 * (still focusable, so a remote can land on it and read why), dimmed, and
 * it says so on hover and to screen readers.
 */
export function PlayButton({
  label = 'Play',
  primary = true,
  onPlay,
  why = PLAYBACK_NOT_YET,
}: {
  label?: string;
  primary?: boolean;
  onPlay?: () => void;
  /** why it can't play, when there's no onPlay */
  why?: string;
}) {
  if (onPlay)
    return (
      <button
        type="button"
        className={primary ? s.btnPrimary : s.btn}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onPlay();
        }}
        data-nav
      >
        <PlayIcon />
        {label}
      </button>
    );
  return (
    <button
      type="button"
      className={primary ? s.btnPrimary : s.btn}
      aria-disabled="true"
      title={why}
      aria-description={why}
      onClick={(event) => event.preventDefault()}
      data-nav
    >
      <PlayIcon />
      {label}
    </button>
  );
}

export function NotYetNote({ children }: { children?: ReactNode }) {
  return <span className={s.notYet}>{children ?? 'Playback comes in the next stage'}</span>;
}

// ── badges ─────────────────────────────────────────────────────────────

export function Badge({ children, tone }: { children: ReactNode; tone?: PlayVerdict }) {
  return (
    <span className={s.badge} data-tone={tone}>
      {children}
    </span>
  );
}

const VERDICT_WORDS: Record<PlayVerdict, { label: string; hint: string; aria: string }> = {
  yes: { label: 'Direct play', hint: 'Plays in the browser as it is', aria: 'Direct play' },
  maybe: {
    label: 'Possible direct play',
    hint: 'May play as it is, depending on the browser. If not, it plays through the converter.',
    aria: 'Possible direct play',
  },
  no: {
    label: 'Direct play',
    hint: "Can't play in a browser as it is. It plays through the converter.",
    aria: 'No direct play',
  },
};

/** the glyph in the verdict's circle: a check, a cross, a question */
function VerdictGlyph({ verdict }: { verdict: PlayVerdict }) {
  return (
    <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      {verdict === 'yes' ? (
        <path
          d="M3 6.3 5.1 8.4 9 4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : verdict === 'no' ? (
        <path
          d="M3.8 3.8 8.2 8.2M8.2 3.8 3.8 8.2"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
        />
      ) : (
        <>
          <path
            d="M4.4 4.6a1.7 1.7 0 1 1 2.4 1.5c-.5.3-.8.6-.8 1.2"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
          <circle cx="6" cy="9" r=".8" fill="currentColor" />
        </>
      )}
    </svg>
  );
}

/**
 * can a browser play this file as it is: direct play with a green check,
 * direct play with a red cross (it plays through the converter), or
 * possible direct play. the reasons are on hover.
 */
export function VerdictBadge({ verdict, reasons }: { verdict: PlayVerdict; reasons?: string[] }) {
  const words = VERDICT_WORDS[verdict];
  return (
    <span
      className={s.verdict}
      data-tone={verdict}
      title={[words.hint, ...(reasons ?? [])].join('\n')}
      aria-label={[words.aria, ...(reasons ?? [])].join('. ')}
    >
      <span className={s.verdictIcon}>
        <VerdictGlyph verdict={verdict} />
      </span>
      {words.label}
    </span>
  );
}

// ── empty + error states ───────────────────────────────────────────────

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={s.empty} role="status">
      <div className={s.emptyTitle}>{title}</div>
      {children ? <div className={s.emptyText}>{children}</div> : null}
    </div>
  );
}

export function PageTitle({ children }: { children: ReactNode }) {
  return <h1 className={s.pageTitle}>{children}</h1>;
}

export function Tabs<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className={s.tabs} role="tablist" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          className={s.tab}
          aria-selected={option.value === value}
          onClick={() => onChange(option.value)}
          data-nav
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export { s as styles };
