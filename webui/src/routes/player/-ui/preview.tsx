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
import { movieDetailQuery, showDetailQuery } from '../-player.api';
import { Art, Badge, PlayButton, PlayerLink, styles as s } from './kit';

export interface PreviewTarget {
  kind: 'movie' | 'show';
  id: number;
}

const OPEN_MS_POINTER = 450;
const OPEN_MS_FOCUS = 900;
const CLOSE_MS = 140;

function canHover(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(hover: hover)').matches;
}

/** where the panel goes: centred on the card, inside the viewport */
export function placePanel(
  card: { left: number; top: number; width: number; height: number },
  viewport: { width: number; height: number },
  minWidth: number,
): { left: number; top: number; width: number } {
  const width = Math.min(viewport.width - 24, Math.max(minWidth, card.width * 1.9));
  const left = Math.min(
    viewport.width - width - 12,
    Math.max(12, card.left + card.width / 2 - width / 2),
  );
  const top = Math.max(12, card.top - card.height * 0.12);
  return { left, top, width };
}

function PreviewPanel({
  target,
  title,
  href,
  anchor,
  onEnter,
  onLeave,
}: {
  target: PreviewTarget;
  title: string;
  href?: string;
  anchor: DOMRect;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const movie = useQuery({ ...movieDetailQuery(target.id), enabled: target.kind === 'movie' });
  const show = useQuery({ ...showDetailQuery(target.id), enabled: target.kind === 'show' });
  const detail = target.kind === 'movie' ? movie.data : show.data;
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const root = document.querySelector('[data-player-root]');
  if (!root) return null;
  const rootBox = root.getBoundingClientRect();
  const fontSize = Number.parseFloat(getComputedStyle(root).fontSize) || 16;
  const place = placePanel(
    {
      left: anchor.left - rootBox.left,
      top: anchor.top - rootBox.top,
      width: anchor.width,
      height: anchor.height,
    },
    { width: rootBox.width, height: rootBox.height },
    fontSize * 22,
  );
  const seasons = show.data?.seasons?.filter((x) => x.season_number > 0).length;
  const meta = [
    detail?.year ? String(detail.year) : '',
    target.kind === 'movie' && movie.data?.runtime_minutes
      ? runtime(movie.data.runtime_minutes * 60)
      : '',
    seasons ? `${seasons} season${seasons === 1 ? '' : 's'}` : '',
  ].filter(Boolean);

  return createPortal(
    <div
      className={s.preview}
      data-open={shown ? 'true' : undefined}
      style={{ left: place.left, top: place.top, width: place.width } as CSSProperties}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocusCapture={onEnter}
      onBlurCapture={onLeave}
      role="dialog"
      aria-label={title}
    >
      <div className={s.previewArt}>
        <Art src={backdropUrl(target.kind, target.id, 780)} alt={title} eager />
        {detail?.logo ? (
          <img className={s.previewLogo} src={proxiedImage(detail.logo) ?? ''} alt="" />
        ) : null}
      </div>
      <div className={s.previewBody}>
        <div className={s.previewTitle}>{detail?.title ?? title}</div>
        <div className={s.badges}>
          {meta.map((m) => (
            <span key={m} className={s.previewMeta}>
              {m}
            </span>
          ))}
          {detail?.content_rating ? <Badge>{detail.content_rating}</Badge> : null}
          {detail?.rating ? <Badge>★ {detail.rating.toFixed(1)}</Badge> : null}
        </div>
        {detail?.genres?.length ? (
          <div className={s.previewGenres}>{detail.genres.slice(0, 4).join(' · ')}</div>
        ) : null}
        {detail?.overview ? <p className={s.previewText}>{detail.overview}</p> : null}
        {!detail && (movie.isLoading || show.isLoading) ? (
          <div className={s.skeleton} style={{ height: '3.2em' }} />
        ) : null}
        <div className={s.heroActions}>
          <PlayButton />
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
      />
    ) : null;

  return { handlers, panel };
}
