/**
 * the card preview: rest on a movie or show (mouse, or a remote's focus) and
 * the card opens up in place with its backdrop, the facts that decide a
 * watch (year, length, rating, genres) and the overview, read from the
 * title's own detail. it is the same panel on every screen size the player
 * has a pointer or a remote; touch screens open the page instead.
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';

import { runtime } from '../-lib/format';
import { backdropUrl, proxiedImage } from '../-lib/rails';
import { extrasQuery, movieDetailQuery, showDetailQuery } from '../-player.api';
import { Art, Badge, PlayButton, PlayerLink, styles as s } from './kit';
import { embedUrl, useTrailersSetting } from './trailer';
import { WatchlistButton } from './watchlist';

export interface PreviewTarget {
  kind: 'movie' | 'show';
  id: number;
}

// a second of resting on a card: passing over a row never opens one
const OPEN_MS_POINTER = 1000;
const OPEN_MS_FOCUS = 1200;
const CLOSE_MS = 140;

function canHover(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(hover: hover)').matches;
}

export interface Bounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

const EDGE = 12;

/**
 * where the panel goes. it grows out of the card: centred on it, but the
 * first card of a row grows rightward and the last leftward (lined up with
 * the row's edge, never over the sidebar), its art sits over the card's art,
 * and it stays clear of the header above and the transport bar below.
 */
export function placePanel(
  card: { left: number; top: number; width: number; height: number },
  bounds: Bounds,
  minWidth: number,
  panelHeight: number,
): { left: number; top: number; width: number; origin: string } {
  const room = bounds.right - bounds.left - EDGE * 2;
  const width = Math.min(room, Math.max(minWidth, card.width * 1.85));
  const centred = card.left + card.width / 2 - width / 2;
  const left = Math.min(bounds.right - EDGE - width, Math.max(bounds.left + EDGE, centred));
  const art = (width * 9) / 16;
  // the panel's art centred on the card's art, then kept between header and bar
  const ideal = card.top + Math.min(card.height, card.width * 1.5) / 2 - art / 2;
  const top = Math.max(bounds.top + EDGE, Math.min(ideal, bounds.bottom - EDGE - panelHeight));
  const originX = Math.min(100, Math.max(0, ((card.left + card.width / 2 - left) / width) * 100));
  const originY = Math.min(
    100,
    Math.max(0, ((card.top + card.height / 2 - top) / Math.max(panelHeight, 1)) * 100),
  );
  return { left, top, width, origin: `${originX.toFixed(1)}% ${originY.toFixed(1)}%` };
}

/** the room a preview has: the page column, under the header, above the bar */
function previewBounds(root: Element): Bounds {
  const rootBox = root.getBoundingClientRect();
  const main = root.querySelector('main')?.getBoundingClientRect() ?? rootBox;
  const header = root.querySelector('main > div')?.getBoundingClientRect();
  return {
    left: main.left - rootBox.left,
    right: main.right - rootBox.left,
    top: (header ? header.bottom : main.top) - rootBox.top,
    bottom: main.bottom - rootBox.top,
  };
}

function PreviewPanel({
  target,
  title,
  href,
  anchor,
  onEnter,
  onLeave,
  onClose,
}: {
  target: PreviewTarget;
  title: string;
  href?: string;
  anchor: DOMRect;
  onEnter: () => void;
  onLeave: () => void;
  onClose: () => void;
}) {
  const movie = useQuery({ ...movieDetailQuery(target.id), enabled: target.kind === 'movie' });
  const show = useQuery({ ...showDetailQuery(target.id), enabled: target.kind === 'show' });
  const [trailersOnHere] = useTrailersSetting();
  const extras = useQuery({ ...extrasQuery(target.kind, target.id), enabled: trailersOnHere });
  const detail = target.kind === 'movie' ? movie.data : show.data;
  const [shown, setShown] = useState(false);
  const [height, setHeight] = useState(0);
  const [playing, setPlaying] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, []);
  // the real height once the facts land, so the panel never runs under the bar
  useLayoutEffect(() => {
    const h = panelRef.current?.offsetHeight ?? 0;
    if (h && Math.abs(h - height) > 4) setHeight(h);
  });
  // the trailer takes over the art after a moment, muted, like the pages do
  const trailerKey = trailersOnHere ? extras.data?.trailer?.key : undefined;
  useEffect(() => {
    if (!trailerKey) return;
    const t = window.setTimeout(() => setPlaying(true), 1400);
    return () => window.clearTimeout(t);
  }, [trailerKey]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const root = document.querySelector('[data-player-root]');
  if (!root) return null;
  const rootBox = root.getBoundingClientRect();
  const fontSize = Number.parseFloat(getComputedStyle(root).fontSize) || 16;
  const card = {
    left: anchor.left - rootBox.left,
    top: anchor.top - rootBox.top,
    width: anchor.width,
    height: anchor.height,
  };
  const place = placePanel(card, previewBounds(root), fontSize * 23, height || fontSize * 24);
  // grow out of the card: start scaled down onto it, then open
  const startScale = Math.min(1, card.width / place.width);
  const seasons = show.data?.seasons?.filter((x) => x.season_number > 0).length;
  const next = show.data?.next_up;
  const meta = [
    detail?.year ? String(detail.year) : '',
    target.kind === 'movie' && movie.data?.runtime_minutes
      ? runtime(movie.data.runtime_minutes * 60)
      : '',
    seasons ? `${seasons} season${seasons === 1 ? '' : 's'}` : '',
  ].filter(Boolean);

  return createPortal(
    <div
      ref={panelRef}
      className={s.preview}
      data-open={shown ? 'true' : undefined}
      style={
        {
          left: place.left,
          top: place.top,
          width: place.width,
          transformOrigin: place.origin,
          '--preview-start': startScale,
        } as CSSProperties
      }
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocusCapture={onEnter}
      onBlurCapture={onLeave}
      role="dialog"
      aria-label={title}
    >
      <div className={s.previewArt}>
        <Art src={backdropUrl(target.kind, target.id, 780)} alt={title} eager />
        {playing && trailerKey ? (
          <div className={s.previewTrailer} aria-hidden="true">
            <iframe
              title="Trailer"
              src={embedUrl(trailerKey, true)}
              allow="autoplay; encrypted-media"
              tabIndex={-1}
            />
          </div>
        ) : null}
        {detail?.logo ? (
          <img className={s.previewLogo} src={proxiedImage(detail.logo) ?? ''} alt="" />
        ) : null}
      </div>
      <div className={s.previewBody}>
        {detail?.logo ? null : <div className={s.previewTitle}>{detail?.title ?? title}</div>}
        <div className={s.badges}>
          {detail?.rating ? (
            <span className={s.previewScore}>★ {detail.rating.toFixed(1)}</span>
          ) : null}
          {meta.map((m) => (
            <span key={m} className={s.previewMeta}>
              {m}
            </span>
          ))}
          {detail?.content_rating ? <Badge>{detail.content_rating}</Badge> : null}
        </div>
        {next ? (
          <div className={s.previewNext}>
            {next.resume ? 'Resume' : 'Up next'} · S{next.season_number} E{next.episode_number}
            {next.title ? ` · ${next.title}` : ''}
          </div>
        ) : null}
        {detail?.genres?.length ? (
          <div className={s.previewGenres}>{detail.genres.slice(0, 4).join(' · ')}</div>
        ) : null}
        {detail?.overview ? <p className={s.previewText}>{detail.overview}</p> : null}
        {!detail && (movie.isLoading || show.isLoading) ? (
          <div className={s.skeleton} style={{ height: '3.2em' }} />
        ) : null}
        <div className={s.heroActions}>
          <PlayButton />
          <WatchlistButton kind={target.kind} id={target.id} compact />
          {href ? (
            <PlayerLink href={href} className={s.btn} data-nav>
              More info
            </PlayerLink>
          ) : null}
        </div>
      </div>
    </div>,
    root,
  );
}

/**
 * hook a card up to a preview. returns the handlers for the card and the
 * panel to render (null while closed).
 */
export function usePreview(target: PreviewTarget | undefined, title: string, href?: string) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  const cardRef = useRef<HTMLElement | null>(null);

  const clear = () => {
    window.clearTimeout(openTimer.current);
    window.clearTimeout(closeTimer.current);
  };
  useEffect(() => clear, []);

  // a scroll moves the card out from under the panel: close it
  useLayoutEffect(() => {
    if (!anchor) return;
    const close = () => setAnchor(null);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [anchor]);

  const open = (delay: number) => {
    if (!target) return;
    clear();
    openTimer.current = window.setTimeout(() => {
      if (cardRef.current) setAnchor(cardRef.current.getBoundingClientRect());
    }, delay);
  };
  const close = () => {
    window.clearTimeout(openTimer.current);
    closeTimer.current = window.setTimeout(() => setAnchor(null), CLOSE_MS);
  };

  const handlers = target
    ? {
        ref: (el: HTMLElement | null) => {
          cardRef.current = el;
        },
        onMouseEnter: () => canHover() && open(OPEN_MS_POINTER),
        onMouseLeave: close,
        onFocus: () => open(OPEN_MS_FOCUS),
        onBlur: close,
      }
    : {};

  const panel =
    target && anchor ? (
      <PreviewPanel
        target={target}
        title={title}
        href={href}
        anchor={anchor}
        onEnter={() => window.clearTimeout(closeTimer.current)}
        onLeave={close}
        onClose={() => setAnchor(null)}
      />
    ) : null;

  return { handlers, panel };
}
