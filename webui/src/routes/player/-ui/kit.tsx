/**
 * the player's building blocks: artwork, cards, rails, grids, the hero, play
 * buttons, badges, empty states. every page is made of these, and they are
 * what the phone and tv apps copy.
 */

import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ComponentProps, type ReactNode } from 'react';

import type { RailCard } from '../-lib/rails';
import type { PlayVerdict } from '../-player.types';

import { MoreIcon, PlayIcon } from './icons';
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
  alt,
  className,
  eager,
}: {
  src?: string | null;
  alt: string;
  className?: string;
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
          alt=""
          loading={eager ? 'eager' : 'lazy'}
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
        <Art src={card.art} alt={card.title} />
        {card.flag ? (
          <span className={s.cardFlag} data-tone={card.flag.tone}>
            {card.flag.label}
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
      <div className={s.railTrack}>{children}</div>
    </section>
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

export function CardGrid({
  cards,
  shape = 'poster',
  round,
}: {
  cards: RailCard[];
  shape?: RailCard['shape'];
  round?: boolean;
}) {
  return (
    <div className={s.grid} data-shape={shape}>
      {cards.map((card) => (
        <Card key={card.key} card={card} round={round} />
      ))}
    </div>
  );
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
}: {
  /** a background trailer layer (trailer.tsx), between the backdrop and its scrim */
  trailer?: ReactNode;
  backdrop?: string | null;
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
        {backdrop ? <Art src={backdrop} alt="" className={undefined} eager /> : null}
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
