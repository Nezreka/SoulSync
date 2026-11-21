/**
 * the player's building blocks: artwork, cards, rails, grids, the hero, play
 * buttons, badges, empty states. every page is made of these, and they are
 * what the phone and tv apps copy.
 */

import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ComponentProps, type ReactNode } from 'react';

import type { RailCard } from '../-lib/rails';
import type { PlayVerdict } from '../-player.types';

import { BackIcon, CheckIcon, ChevronIcon, GridIcon, ListIcon, MoreIcon, PlayIcon } from './icons';
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
  useEffect(() => setState(src ? 'loading' : 'failed'), [src]);
  return (
    <>
      {src && state !== 'failed' ? (
        <img
          className={className ?? s.artImg}
          src={src}
          srcSet={srcSet}
          sizes={srcSet ? (sizes ?? 'auto, 220px') : undefined}
          alt=""
          loading={eager ? 'eager' : 'lazy'}
          fetchPriority={eager ? 'high' : 'auto'}
          decoding="async"
          data-loaded={state === 'ok'}
          onLoad={() => setState('ok')}
          onError={() => setState('failed')}
        />
      ) : null}
      {state === 'failed' ? (
        <span className={s.artFallback} aria-hidden="true">
          {alt}
        </span>
      ) : null}
    </>
  );
}

// ── cards ──────────────────────────────────────────────────────────────

export interface CardAction {
  label: string;
  onSelect: () => void;
}

export function Card({
  card,
  round,
  actions,
}: {
  card: RailCard;
  round?: boolean;
  actions?: CardAction[];
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

  const body = (
    <>
      <div className={s.art}>
        <Art src={card.art} srcSet={card.artSet} sizes={card.artSizes} alt={card.title} />
        {card.have ? (
          <span className={s.cardHave} title={card.have} aria-label={card.have}>
            <CheckIcon />
          </span>
        ) : null}
        {card.badge ? <span className={s.cardBadge}>{card.badge}</span> : null}
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
        {card.subtitle ? <div className={s.cardSub}>{card.subtitle}</div> : null}
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
    >
      {card.href ? (
        <PlayerLink href={card.href} className={s.card} data-nav {...handlers}>
          {body}
        </PlayerLink>
      ) : (
        <div className={s.card} tabIndex={0} data-nav {...handlers}>
          {body}
        </div>
      )}
      {panel}
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
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(update);
    observer.observe(el);
    for (const child of Array.from(el.children).slice(0, 1)) observer.observe(child);
    return () => observer.disconnect();
  });
  const page = (dir: 1 | -1) => {
    const el = ref.current;
    if (el) el.scrollBy({ left: dir * el.clientWidth * 0.85, behavior: 'smooth' });
  };
  return (
    <div className={s.hscroll}>
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
}: {
  title: string;
  cards: RailCard[];
  more?: { to: string; label?: string };
  round?: boolean;
  actionsFor?: (card: RailCard) => CardAction[];
}) {
  if (!cards.length) return null;
  return (
    <Rail title={title} more={more}>
      {cards.map((card) => (
        <Card key={card.key} card={card} round={round} actions={actionsFor?.(card)} />
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
}: {
  cards: RailCard[];
  shape?: RailCard['shape'];
  round?: boolean;
  /** a card's own menu (remove from the watchlist) */
  actionsFor?: (card: RailCard) => CardAction[];
  /** a list reads better for long filmographies and big libraries */
  view?: ViewMode;
}) {
  // a library is thousands of titles: render a screenful ahead and grow as
  // the end comes near, instead of putting every card in the page at once
  const shown = useProgressive(cards.length, `${view}:${cards.length}:${cards[0]?.key ?? ''}`);
  return (
    <>
      {view === 'list' ? (
        <div className={s.cardList} role="list">
          {cards.slice(0, shown.count).map((card) => (
            <CardRow key={card.key} card={card} round={round} />
          ))}
        </div>
      ) : (
        <div className={s.grid} data-shape={shape}>
          {cards.slice(0, shown.count).map((card) => (
            <Card key={card.key} card={card} round={round} actions={actionsFor?.(card)} />
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
export function CardRow({ card, round }: { card: RailCard; round?: boolean }) {
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
      {card.href ? (
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

export const PLAYBACK_NOT_YET = 'Playback is not built yet. It is the next stage of the player.';

/**
 * the play button every page shows. until the player core exists it is
 * visibly inert: aria-disabled (still focusable, so a remote can land on it
 * and read why), dimmed, and it says so on hover and to screen readers.
 */
export function PlayButton({
  label = 'Play',
  primary = true,
}: {
  label?: string;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      className={primary ? s.btnPrimary : s.btn}
      aria-disabled="true"
      title={PLAYBACK_NOT_YET}
      aria-description={PLAYBACK_NOT_YET}
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

const VERDICT_WORDS: Record<PlayVerdict, { label: string; hint: string }> = {
  yes: { label: 'Direct play', hint: 'Plays in the browser as is' },
  maybe: { label: 'May play', hint: 'Depends on the browser' },
  no: { label: 'Needs converting', hint: 'This browser cannot play the file as is' },
};

export function VerdictBadge({ verdict, reasons }: { verdict: PlayVerdict; reasons?: string[] }) {
  const words = VERDICT_WORDS[verdict];
  return (
    <span
      className={s.badge}
      data-tone={verdict}
      title={[words.hint, ...(reasons ?? [])].join('\n')}
    >
      <span className={s.badgeDot} />
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
