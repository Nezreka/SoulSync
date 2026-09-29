import { useEffect, useRef, useState, type ReactNode, type Ref } from 'react';

import type { Spotlight, TasteGap, WeekSummary } from '../-discover.pulse';

import { releaseKind, shortDate, tasteGapLine } from '../-discover.pulse';
import { recentAlbumCover } from '../-discover.recent-releases';

/**
 * the banners that are about you, not the catalogue.
 *
 * each one is data the app already had and never showed on discover: your
 * week from the stats worker, the genre you play far past what you own, and
 * a new release from the artist on repeat. each renders nothing when its
 * data isn't there, so a fresh install never sees a banner of zeroes.
 */

const fmt = new Intl.NumberFormat();

function reducedMotion(): boolean {
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

/** true once the element has been on screen (and straight away without an observer). */
export function useSeenOnce<T extends Element>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    const el = ref.current;
    if (seen || !el || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { threshold: 0.35 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [seen]);
  return [ref, seen];
}

/** eases a number up from 0 to target once `run` turns true. */
export function useCountUp(target: number, run: boolean, ms = 1100): number {
  const [value, setValue] = useState(() => (run && reducedMotion() ? target : 0));
  useEffect(() => {
    if (!run) return;
    if (reducedMotion() || typeof requestAnimationFrame === 'undefined') {
      setValue(target);
      return;
    }
    let raf = 0;
    const start = performance.now();
    // read the clock here, not the frame's timestamp: that one isn't on
    // performance.now's clock everywhere, and a mismatch stalls the count
    const tick = () => {
      const t = Math.min(1, (performance.now() - start) / ms);
      setValue(Math.round(target * (1 - Math.pow(1 - t, 3))));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, run, ms]);
  return value;
}

export interface WeekBannerProps {
  week: WeekSummary;
  onPlayTop: () => void;
  playing?: boolean;
}

/** your week in music: the number, the shape of the week, who you played most. */
export function WeekBanner({ week, onPlayTop, playing = false }: WeekBannerProps) {
  const up = week.change !== null && week.change >= 0;
  const artist = week.topArtist;
  const [ref, seen] = useSeenOnce<HTMLElement>();
  const shown = useCountUp(week.plays, seen);
  return (
    <section
      ref={ref}
      className={`dsc-pulse dsc-week${seen ? ' in-view' : ''}`}
      aria-label="Your week in music"
    >
      {artist?.image_url ? (
        <div
          className="dsc-week-wash"
          aria-hidden="true"
          style={{ backgroundImage: `url('${artist.image_url}')` }}
        />
      ) : null}
      <div className="dsc-week-main">
        <span className="dsc-pulse-eyebrow">Your week in music</span>
        <div className="dsc-week-figure">
          <strong aria-label={fmt.format(week.plays)}>{fmt.format(shown)}</strong>
          <span>plays</span>
          {week.change !== null ? (
            <span
              className={`dsc-week-change ${up ? 'up' : 'down'}`}
              title="Compared with the 7 days before"
            >
              {up ? '↑' : '↓'} {Math.abs(week.change)}%
            </span>
          ) : null}
        </div>
        <p className="dsc-week-sub">
          {fmt.format(week.artists)} artists
          {week.streak > 1 ? ` · ${week.streak}-day streak` : ''}
          {week.topGenre ? ` · mostly ${week.topGenre.toLowerCase()}` : ''}
        </p>
        <div className="dsc-pulse-actions">
          {week.topTracks.length > 0 ? (
            <button
              type="button"
              className="dsc-pulse-btn primary"
              onClick={onPlayTop}
              disabled={playing}
            >
              <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                <path d="M8 5.5v13l11-6.5z" fill="currentColor" />
              </svg>
              {playing ? 'Starting…' : 'Play your top tracks'}
            </button>
          ) : null}
          <a className="dsc-pulse-btn" href="/stats">
            Your stats
          </a>
        </div>
      </div>

      {week.days.length > 1 ? (
        <div className="dsc-week-chart" role="img" aria-label={daysLabel(week)}>
          {week.days.map((d, i) => (
            <div
              className="dsc-week-day"
              key={d.date}
              title={`${d.date}: ${fmt.format(d.plays)} plays`}
            >
              <div className="dsc-week-bar-slot">
                <div
                  className="dsc-week-bar"
                  style={
                    {
                      height: `${Math.max(4, Math.round(d.share * 100))}%`,
                      '--bar-delay': `${i * 70}ms`,
                    } as React.CSSProperties
                  }
                />
              </div>
              <span className="dsc-week-day-label">{d.label}</span>
            </div>
          ))}
        </div>
      ) : null}

      {artist ? (
        <div className="dsc-week-top">
          {artist.image_url ? (
            <img className="dsc-week-top-img" src={artist.image_url} alt="" loading="lazy" />
          ) : (
            <span className="dsc-week-top-img dsc-week-top-img--blank" aria-hidden="true">
              {artist.name.slice(0, 1)}
            </span>
          )}
          <span className="dsc-week-top-label">On repeat</span>
          <span className="dsc-week-top-name">{artist.name}</span>
          {artist.play_count ? (
            <span className="dsc-week-top-plays">{fmt.format(artist.play_count)} plays</span>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** the chart as a sentence, for screen readers. */
export function daysLabel(week: WeekSummary): string {
  return `Plays per day: ${week.days.map((d) => `${d.date} ${d.plays}`).join(', ')}`;
}

export interface TasteGapBannerProps {
  gap: TasteGap;
  onExplore: (genre: string) => void;
}

/** the genre you play far more than you own, with the way to fix it. */
export function TasteGapBanner({ gap, onExplore }: TasteGapBannerProps) {
  const played = Math.round(gap.playedPct);
  const owned = Math.round(gap.ownedPct * 10) / 10;
  // both bars on one scale, the track a little longer than the bigger one
  const scale = (pct: number) =>
    `${Math.max(1, Math.round((pct / (gap.playedPct * 1.15)) * 100))}%`;
  return (
    <section className="dsc-pulse dsc-gap" aria-label="Taste gap">
      <span className="dsc-pulse-eyebrow">Your collection is missing</span>
      <h3 className="dsc-gap-genre">{gap.genre}</h3>
      <p className="dsc-gap-line">{tasteGapLine(gap)}</p>
      <div className="dsc-gap-bars">
        <div className="dsc-gap-row">
          <span>Your plays</span>
          <div className="dsc-gap-track">
            <div className="dsc-gap-fill played" style={{ width: scale(gap.playedPct) }} />
          </div>
          <b>{played}%</b>
        </div>
        <div className="dsc-gap-row">
          <span>Your library</span>
          <div className="dsc-gap-track">
            <div className="dsc-gap-fill" style={{ width: scale(gap.ownedPct) }} />
          </div>
          <b>{owned}%</b>
        </div>
      </div>
      <div className="dsc-pulse-actions">
        <button type="button" className="dsc-pulse-btn" onClick={() => onExplore(gap.genre)}>
          Explore {gap.genre.toLowerCase()}
        </button>
      </div>
    </section>
  );
}

export interface SpotlightBannerProps {
  spotlight: Spotlight;
  onOpen: () => void;
  /** the banner's element, for the video stage */
  rootRef?: Ref<HTMLElement>;
  /** the release's music video, when the spotlight holds the stage */
  backdrop?: ReactNode;
  /** 'r, g, b' from the cover */
  glowRgb?: string | null;
}

/**
 * the "ad": one release, full width, from the artist you've had on repeat.
 * one action, and it's the obvious one.
 */
export function SpotlightBanner({
  spotlight,
  onOpen,
  rootRef,
  backdrop,
  glowRgb,
}: SpotlightBannerProps) {
  const { album } = spotlight;
  const cover = recentAlbumCover(album);
  const date = shortDate(album.release_date);
  return (
    <section
      ref={rootRef}
      className="dsc-spotlight"
      aria-label="Spotlight"
      style={glowRgb ? ({ '--spot-rgb': glowRgb } as React.CSSProperties) : undefined}
    >
      <div
        className="dsc-spotlight-wash"
        aria-hidden="true"
        style={cover ? { backgroundImage: `url('${cover}')` } : undefined}
      />
      {backdrop}
      <button
        type="button"
        className="dsc-spotlight-cover"
        onClick={onOpen}
        aria-label={`Open ${album.album_name}`}
      >
        {cover ? <img src={cover} alt="" loading="lazy" /> : null}
      </button>
      <div className="dsc-spotlight-copy">
        <span className="dsc-pulse-eyebrow">
          {releaseKind(album.album_type)}
          {date ? ` · ${date}` : ''}
        </span>
        <h3 className="dsc-spotlight-title">{album.album_name}</h3>
        <p className="dsc-spotlight-artist">{album.artist_name}</p>
        <p className="dsc-spotlight-reason">{spotlight.reason}</p>
        <div className="dsc-pulse-actions">
          <button type="button" className="dsc-pulse-btn primary" onClick={onOpen}>
            Open {releaseKind(album.album_type).replace('New ', '')}
          </button>
        </div>
      </div>
    </section>
  );
}
