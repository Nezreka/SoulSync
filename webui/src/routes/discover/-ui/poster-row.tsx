import {
  isValidElement,
  useEffect,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from 'react';

import { useDominantColor } from '../-discover.backdrop';
import { inkFor, paletteFor, poppy, type PosterDay } from '../-discover.posters';
import { useReveal } from './pulse-banners';

/**
 * the poster row: three loud, flat colour cards, the way spotify runs its
 * campaign tiles. a show near you as a gig poster (the date huge, stage lights
 * sweeping), a release as a record sliding out of its sleeve and spinning, and
 * who you played most this week as their name scrolling in giant type behind
 * their face. each takes its colour from its artwork, pushed loud, with black
 * or white type to suit. the animation is css only, and it stops while the
 * row is off screen.
 */

/** true while the element is on screen, for pausing animation off it. */
export function useOnScreen<T extends Element>(): [(el: T | null) => void, boolean] {
  const [el, setEl] = useState<T | null>(null);
  const [on, setOn] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver((entries) =>
      setOn(entries[entries.length - 1]?.isIntersecting ?? false),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [el]);
  return [setEl, on];
}

function Poster({
  kind,
  rgb,
  label,
  children,
}: {
  kind: string;
  rgb: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <article
      className={`dsc-poster dsc-poster--${kind} ink-${inkFor(rgb)}`}
      aria-label={label}
      style={{ '--poster-rgb': rgb } as CSSProperties}
    >
      {children}
    </article>
  );
}

/** a line of words repeated for a marquee, doubled so the loop has no seam */
function Marquee({ className, words }: { className: string; words: string }) {
  const run = Array.from({ length: 6 }, () => words).join(' • ') + ' • ';
  return (
    <div className={className} aria-hidden="true">
      <span>{run}</span>
      <span>{run}</span>
    </div>
  );
}

export interface ConcertPosterProps {
  artist: string;
  day: PosterDay;
  venue?: string;
  city?: string;
  url?: string;
  more: number;
  photo?: string | null;
}

export function ConcertPoster({ artist, day, venue, city, url, more, photo }: ConcertPosterProps) {
  const rgb = paletteFor(artist);
  const where = [venue, city].filter(Boolean).join(', ');
  return (
    <Poster
      kind={photo ? 'concert' : 'concert dsc-poster--typeset'}
      rgb={rgb}
      label={`Live: ${artist}, ${day.month} ${day.day}`}
    >
      <div className="dsc-poster-art" aria-hidden="true">
        <div className="dsc-poster-dots" />
        {photo ? (
          <div className="dsc-poster-photo" style={{ backgroundImage: `url('${photo}')` }} />
        ) : null}
        <div className="dsc-poster-beam dsc-poster-beam--a" />
        <div className="dsc-poster-beam dsc-poster-beam--b" />
      </div>
      <div className="dsc-poster-body">
        <span className="dsc-poster-tag">Live near you</span>
        <div className="dsc-poster-date">
          <span className="dsc-poster-month">{day.month}</span>
          <span className="dsc-poster-day">{day.day}</span>
        </div>
        <h3 className="dsc-poster-title">{artist}</h3>
        <p className="dsc-poster-sub">
          {day.weekday}
          {where ? ` · ${where}` : ''}
        </p>
        <div className="dsc-poster-actions">
          {url ? (
            <a className="dsc-poster-btn" href={url} target="_blank" rel="noreferrer">
              Get tickets
            </a>
          ) : null}
          {more > 0 ? (
            <span className="dsc-poster-more">
              +{more} more {more === 1 ? 'show' : 'shows'}
            </span>
          ) : null}
        </div>
      </div>
      <Marquee className="dsc-poster-marquee" words={`Live • ${artist}`} />
    </Poster>
  );
}

export interface AlbumPosterProps {
  title: string;
  artist: string;
  tag: string;
  art: string;
  onOpen: () => void;
  openLabel: string;
}

export function AlbumPoster({ title, artist, tag, art, onOpen, openLabel }: AlbumPosterProps) {
  const rgb = poppy(useDominantColor(art), paletteFor(title));
  const cover = { backgroundImage: `url('${art}')` };
  return (
    <Poster kind="album" rgb={rgb} label={`${tag}: ${title} by ${artist}`}>
      <div className="dsc-poster-art" aria-hidden="true">
        <div className="dsc-poster-blob dsc-poster-blob--a" />
        <div className="dsc-poster-blob dsc-poster-blob--b" />
      </div>
      <div className="dsc-poster-record" aria-hidden="true">
        <div className="dsc-poster-vinyl">
          <div className="dsc-poster-vinyl-label" style={cover} />
        </div>
        <div className="dsc-poster-sleeve" style={cover} />
      </div>
      <div className="dsc-poster-body">
        <span className="dsc-poster-tag">{tag}</span>
        <h3 className="dsc-poster-title">{title}</h3>
        <p className="dsc-poster-sub">{artist}</p>
        <div className="dsc-poster-actions">
          <button type="button" className="dsc-poster-btn" onClick={onOpen}>
            {openLabel}
          </button>
        </div>
      </div>
    </Poster>
  );
}

export interface ArtistPosterProps {
  name: string;
  plays: number;
  photo?: string | null;
  href?: string | null;
}

const fmt = new Intl.NumberFormat();

export function ArtistPoster({ name, plays, photo, href }: ArtistPosterProps) {
  const rgb = poppy(useDominantColor(photo ?? null), paletteFor(name));
  return (
    <Poster kind="artist" rgb={rgb} label={`Your number one this week: ${name}`}>
      <div className="dsc-poster-art" aria-hidden="true">
        <div className="dsc-poster-type">
          {[0, 1, 2, 3].map((row) => (
            <Marquee key={row} className="dsc-poster-type-row" words={name} />
          ))}
        </div>
      </div>
      <div
        className="dsc-poster-face"
        aria-hidden="true"
        style={photo ? { backgroundImage: `url('${photo}')` } : undefined}
      >
        <span className="dsc-poster-ring" />
        <span className="dsc-poster-ring dsc-poster-ring--late" />
      </div>
      <div className="dsc-poster-body">
        <span className="dsc-poster-tag">Your #1 this week</span>
        <div className="dsc-poster-figure">
          <strong>{fmt.format(plays)}</strong>
          <span>plays</span>
        </div>
        <h3 className="dsc-poster-title">{name}</h3>
        {href ? (
          <div className="dsc-poster-actions">
            <a className="dsc-poster-btn" href={href}>
              View artist
            </a>
          </div>
        ) : null}
      </div>
    </Poster>
  );
}

const WHAT: Record<string, string> = {
  concert: 'a show near you',
  album: 'a new release',
  artist: 'who you played most this week',
};

/** "a show near you, a new release and ..." for the posters that are there */
export function postersLine(keys: string[]): string {
  const parts = keys.map((k) => WHAT[k]).filter(Boolean);
  if (parts.length === 0) return '';
  const line =
    parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}`;
  return `${line[0].toUpperCase()}${line.slice(1)}.`;
}

/** the posters, keyed concert / album / artist; missing ones are null */
export function PosterRow({ children }: { children: ReactNode[] }) {
  const [screenRef, onScreen] = useOnScreen<HTMLElement>();
  const [revealRef, revealed] = useReveal<HTMLDivElement>();
  const shown = children.filter((c): c is ReactElement => isValidElement(c));
  if (shown.length === 0) return null;
  return (
    <section
      ref={screenRef}
      className={`dsc-posters${onScreen ? '' : ' is-paused'}`}
      aria-label="Right now"
    >
      <header className="dsc-section-head">
        <h3>Right now</h3>
        <p>{postersLine(shown.map((c) => String(c.key)))}</p>
      </header>
      <div
        ref={revealRef}
        className={`dsc-posters-grid dsc-posters-grid--${shown.length} dsc-reveal${revealed ? ' is-in' : ''}`}
      >
        {shown}
      </div>
    </section>
  );
}
