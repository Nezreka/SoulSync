import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';

import { fetchAlbumPlayTracks, yearInListeningQueryOptions } from '../-year.api';
import {
  countUpDuration,
  countUpValue,
  prefersReducedMotion,
  staggerDelay,
} from '../-year.animation';
import {
  DEFAULT_YEAR_CARD_OPTIONS,
  MAX_YEAR_CARD_STATS,
  YEAR_CARD_ASPECTS,
  YEAR_CARD_ASPECT_KEYS,
  YEAR_CARD_LAYOUTS,
  YEAR_CARD_LAYOUT_KEYS,
  YEAR_CARD_PALETTES,
  YEAR_CARD_STAT_DEFS,
  YEAR_CARD_THEMES,
  buildYearCardModel,
  drawYearCard,
  toggleCardStat,
  type YearCardAspect,
  type YearCardLayout,
  type YearCardOptions,
  type YearCardTheme,
} from '../-year.card';
import {
  buildYearSlides,
  describeListeningTime,
  formatHour,
  formatStoryDate,
  monthBarHeight,
  peakMonthPlays,
} from '../-year.helpers';
import type { YearAlbum, YearInListening, YearSlideKind } from '../-year.types';

import styles from './year-story.module.css';

/**
 * Your Year in Listening — a takeover, not a section.
 *
 * Three things make the format work, in order of importance: a FIXED period
 * rather than a filter, a SEQUENCE of single-idea moments rather than a grid,
 * and something to keep at the end. All three are structural, which is why the
 * slide list comes from `buildYearSlides` (tested separately) and this file
 * only renders whichever slides that returned.
 *
 * Open state lives in the URL (`?story=year`) so the story is linkable and
 * survives a reload, the same way the range and tab already do.
 *
 * ARTWORK is the point, not decoration — this surface is carried by it. Every
 * row that names an artist, album or track shows its image, and every artist
 * row links through to artist detail. The images arrive already attached by
 * `core/stats/enrich.py`; the fallback below is for rows the library has no
 * art for, which must render as a shape rather than a hole.
 */

/** Artist detail is reached through the library source, same as the page. */
const ARTIST_DETAIL_SOURCE = 'library' as const;

interface YearStoryProps {
  onClose: () => void;
}

export function YearStory({ onClose }: YearStoryProps) {
  const { data, isPending, isError, error } = useQuery(yearInListeningQueryOptions());
  const [step, setStep] = useState(0);

  const slides = useMemo(() => buildYearSlides(data), [data]);
  // A shorter story after a refetch must not strand the reader past the end.
  const current = Math.min(step, slides.length - 1);

  const next = useCallback(() => {
    setStep((s) => Math.min(s + 1, slides.length - 1));
  }, [slides.length]);

  const previous = useCallback(() => {
    setStep((s) => Math.max(s - 1, 0));
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key === 'ArrowRight' || event.key === ' ' || event.key === 'Enter') {
        event.preventDefault();
        next();
        return;
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        previous();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [next, previous, onClose]);

  // A takeover that leaves the page behind it scrolling is disorienting.
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      aria-label="Your Year in Listening"
    >
      <div className={styles.pips} aria-hidden="true">
        {slides.map((slide, index) => (
          <span key={slide} className={`${styles.pip} ${index <= current ? styles.pipDone : ''}`} />
        ))}
      </div>

      <button type="button" className={styles.close} onClick={onClose} aria-label="Close your year">
        ✕
      </button>

      <div className={styles.stage}>
        {isPending ? (
          <p className={styles.quiet}>Reading your year…</p>
        ) : isError ? (
          <div className={styles.slide}>
            <p className={styles.quiet}>
              {error instanceof Error ? error.message : 'Could not read your year.'}
            </p>
          </div>
        ) : (
          <YearSlide kind={slides[current]} year={data} onNavigate={onClose} />
        )}
      </div>

      <div className={styles.controls}>
        <button
          type="button"
          className={styles.navButton}
          onClick={previous}
          disabled={current === 0}
        >
          Back
        </button>
        <span className={styles.counter}>
          {current + 1} / {slides.length}
        </span>
        <button
          type="button"
          className={styles.navButton}
          onClick={current === slides.length - 1 ? onClose : next}
        >
          {current === slides.length - 1 ? 'Done' : 'Next'}
        </button>
      </div>
    </div>
  );
}

/**
 * A number that counts up to its value once, when its slide arrives.
 *
 * rAF rather than a CSS transition because the thing being animated is the
 * TEXT, not a style. Honours reduce-motion by landing on the value
 * immediately — see `prefersReducedMotion` for why that is not optional.
 */
function useCountUp(target: number): number {
  const [value, setValue] = useState(() => (prefersReducedMotion() ? target : 0));

  useEffect(() => {
    if (prefersReducedMotion()) {
      setValue(target);
      return;
    }
    const duration = countUpDuration(target);
    if (duration <= 0) {
      setValue(target);
      return;
    }
    let frame = 0;
    // The clock is taken from the FIRST rAF timestamp, not from
    // performance.now(). The two are not guaranteed to share a time origin,
    // and where they do not, `elapsed` comes out negative and the number sits
    // at zero forever — an animation that silently never finishes.
    let start: number | null = null;
    const tick = (now: number) => {
      if (start === null) start = now;
      const elapsed = now - start;
      setValue(countUpValue(target, elapsed, duration));
      if (elapsed < duration) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target]);

  return value;
}

function CountUp({ value, className }: { value: number; className?: string }) {
  const shown = useCountUp(value);
  // aria-label carries the FINAL value so a screen reader is not read a
  // number that is still moving.
  return (
    <span className={className} aria-label={value.toLocaleString()}>
      {shown.toLocaleString()}
    </span>
  );
}

/** Wraps a list item so it fades in behind the ones before it. */
function Reveal({
  index,
  className,
  children,
}: {
  index: number;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`${styles.reveal} ${className ?? ''}`}
      style={{ animationDelay: `${staggerDelay(index)}ms` }}
    >
      {children}
    </div>
  );
}

/**
 * Artwork, or a shape that holds the same space.
 *
 * A missing image must never collapse a row — half the point of these slides
 * is the rhythm of the grid, and a library with patchy art would otherwise
 * render as ragged holes. The fallback carries the first letter so a row is
 * still identifiable at a glance.
 */
function Art({
  src,
  name,
  className,
}: {
  src?: string | null;
  name: string;
  className: string;
}) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return (
      <div className={`${className} ${styles.artFallback}`} aria-hidden="true">
        {(name || '?').charAt(0).toUpperCase()}
      </div>
    );
  }
  return (
    <img className={className} src={src} alt="" loading="lazy" onError={() => setFailed(true)} />
  );
}

/** An artist name that clicks through, when we resolved an id for them. */
function ArtistLink({
  id,
  children,
  className,
  onNavigate,
}: {
  id?: string | number | null;
  children: React.ReactNode;
  className?: string;
  onNavigate: () => void;
}) {
  if (id == null || id === '') {
    return <span className={className}>{children}</span>;
  }
  return (
    <Link
      to="/artist-detail/$source/$id"
      params={{ source: ARTIST_DETAIL_SOURCE, id: String(id) }}
      className={className}
      // The story is an overlay over /stats. Navigating away has to drop the
      // ?story=year param too, or coming Back lands the reader in the middle
      // of a story they had already left.
      onClick={onNavigate}
    >
      {children}
    </Link>
  );
}

function YearSlide({
  kind,
  year,
  onNavigate,
}: {
  kind: YearSlideKind;
  year: YearInListening | undefined;
  onNavigate: () => void;
}) {
  if (!year) return null;

  switch (kind) {
    case 'opening':
      return (
        <div className={styles.slide}>
          <p className={styles.kicker}>{year.period.label}</p>
          <h2 className={styles.hero}>Your Year in Listening</h2>
          {year.has_data && year.totals.plays > 0 ? (
            <>
              <p className={styles.lede}>Twelve months, one story. Let&apos;s go.</p>
              <OpeningCollage year={year} />
            </>
          ) : (
            <p className={styles.lede}>
              Nothing played in this window yet — once SoulSync has some listening history, your
              year shows up here.
            </p>
          )}
        </div>
      );

    case 'totals':
      return (
        <div className={styles.slide}>
          <p className={styles.kicker}>You listened</p>
          <h2 className={styles.hero}>
            <CountUp value={year.totals.plays} />
          </h2>
          <p className={styles.lede}>times</p>
          <div className={styles.factRow}>
            <Reveal index={1}>
              <Fact value={describeListeningTime(year.totals.minutes)} label="of music" />
            </Reveal>
            <Reveal index={2}>
              <Fact value={year.totals.artists.toLocaleString()} label="artists" />
            </Reveal>
            <Reveal index={3}>
              <Fact value={year.totals.active_days.toLocaleString()} label="days with music" />
            </Reveal>
          </div>
        </div>
      );

    case 'months': {
      const peak = peakMonthPlays(year);
      const busiest = year.months.reduce((best, m) => (m.plays > best.plays ? m : best));
      return (
        <div className={styles.slide}>
          <p className={styles.kicker}>Month by month</p>
          <div className={styles.monthStrip}>
            {year.months.map((month, index) => (
              <div key={month.month} className={styles.monthColumn}>
                <div className={styles.monthBarTrack}>
                  <div
                    className={styles.monthBar}
                    style={{
                      // The custom property is what the grow-in keyframe
                      // animates TO, so the bar rises to its real height
                      // rather than appearing at it.
                      ['--bar-height' as string]: `${monthBarHeight(month.plays, peak) * 100}%`,
                      animationDelay: `${staggerDelay(index, 45, 480)}ms`,
                    }}
                    title={
                      month.plays > 0
                        ? `${month.label}: ${month.plays.toLocaleString()} plays${
                            month.top_artist ? ` · ${month.top_artist}` : ''
                          }`
                        : `${month.label}: nothing played`
                    }
                  />
                </div>
                {/* The month key, not the full label — twelve "Sep 2025"s do
                    not fit, and the year is already on the opening slide. */}
                <span className={styles.monthLabel}>{month.label.split(' ')[0]}</span>
              </div>
            ))}
          </div>
          {busiest.plays > 0 ? (
            <p className={styles.lede}>
              {busiest.label} was your loudest month
              {busiest.top_artist ? `, led by ${busiest.top_artist}` : ''}.
            </p>
          ) : null}
        </div>
      );
    }

    case 'top-artist': {
      const top = year.top_artists[0];
      return (
        <div className={styles.slide}>
          <p className={styles.kicker}>Your number one</p>
          <Art src={top.image_url} name={top.name} className={styles.heroPortrait} />
          <h2 className={styles.hero}>
            <ArtistLink id={top.id} className={styles.heroLink} onNavigate={onNavigate}>
              {top.name}
            </ArtistLink>
          </h2>
          <p className={styles.lede}>
            <CountUp value={top.plays} /> plays
          </p>
          {top.months_on_top > 0 ? (
            <p className={styles.footnote}>
              Top artist in {top.months_on_top} of your {year.period.months} months.
            </p>
          ) : null}
        </div>
      );
    }

    case 'artist-countdown':
      return (
        <div className={styles.slide}>
          <p className={styles.kicker}>Your top artists</p>
          <ol className={styles.countdown}>
            {year.top_artists.map((artist, index) => (
              <li
                key={artist.name}
                className={`${styles.countdownRow} ${styles.reveal}`}
                style={{ animationDelay: `${staggerDelay(index, 80)}ms` }}
              >
                <span className={styles.countdownRank}>{index + 1}</span>
                <Art src={artist.image_url} name={artist.name} className={styles.countdownArt} />
                <span className={styles.countdownName}>
                  <ArtistLink id={artist.id} className={styles.rowLink} onNavigate={onNavigate}>
                    {artist.name}
                  </ArtistLink>
                </span>
                <span className={styles.countdownPlays}>{artist.plays.toLocaleString()}</span>
              </li>
            ))}
          </ol>
        </div>
      );

    case 'top-albums':
      return (
        <div className={styles.slide}>
          <p className={styles.kicker}>The albums you lived in</p>
          <div className={styles.albumGrid}>
            {year.top_albums.slice(0, 4).map((album, index) => (
              <AlbumCard
                key={`${album.name}-${album.artist ?? ''}`}
                album={album}
                index={index}
              />
            ))}
          </div>
          <p className={styles.footnote}>Click an album to play it.</p>
        </div>
      );

    case 'top-track': {
      const track = year.top_tracks[0];
      const first = formatStoryDate(track.first_played);
      const last = formatStoryDate(track.last_played);
      return (
        <div className={styles.slide}>
          <p className={styles.kicker}>On repeat</p>
          <Art src={track.image_url} name={track.name} className={styles.heroSquare} />
          <h2 className={styles.heroSmall}>{track.name}</h2>
          {track.artist ? (
            <p className={styles.lede}>
              <ArtistLink id={track.artist_id} className={styles.rowLink} onNavigate={onNavigate}>
                {track.artist}
              </ArtistLink>
            </p>
          ) : null}
          <p className={styles.footnote}>
            {track.plays.toLocaleString()} plays
            {first && last && first !== last ? ` — first on ${first}, most recently ${last}` : ''}
            {first && last && first === last ? ` — all on ${first}` : ''}
          </p>
        </div>
      );
    }

    case 'discoveries':
      return (
        <div className={styles.slide}>
          <p className={styles.kicker}>You found them this year</p>
          <div className={styles.discoveryGrid}>
            {year.discoveries.slice(0, 8).map((discovery) => (
              <ArtistLink
                key={discovery.name}
                id={discovery.id}
                className={styles.discoveryCard}
                onNavigate={onNavigate}
              >
                <Art
                  src={discovery.image_url}
                  name={discovery.name}
                  className={styles.discoveryArt}
                />
                <span className={styles.discoveryName}>{discovery.name}</span>
                <span className={styles.discoveryPlays}>
                  {discovery.plays.toLocaleString()} plays
                </span>
              </ArtistLink>
            ))}
          </div>
          <p className={styles.footnote}>
            Artists whose very first play in your history landed inside this year.
          </p>
        </div>
      );

    case 'when': {
      const day = formatStoryDate(year.peak_day.date);
      const hour = formatHour(year.top_hour.hour);
      return (
        <div className={styles.slide}>
          <p className={styles.kicker}>When you listened</p>
          {day ? (
            <>
              <h2 className={styles.heroSmall}>{day}</h2>
              <p className={styles.lede}>
                your biggest day — {year.peak_day.plays.toLocaleString()} plays
              </p>
            </>
          ) : null}
          {hour ? (
            <p className={styles.footnote}>
              More than any other hour, you pressed play around {hour}.
            </p>
          ) : null}
        </div>
      );
    }

    case 'card':
      return <YearCard year={year} />;

    default:
      return null;
  }
}

/**
 * An album you can put back on.
 *
 * The year already knows which albums mattered; making them inert would be
 * the surface stopping one step short of the thing the user actually wants.
 * Tracks load AT CLICK TIME — four albums are shown and at most one is
 * played, so fetching every tracklist up front is three wasted queries.
 *
 * An album with no resolved id (or no owned files behind it) stays a card
 * rather than a dead button, because a play control that does nothing is
 * worse than no control.
 */
function AlbumCard({ album, index }: { album: YearAlbum; index: number }) {
  const [loading, setLoading] = useState(false);
  const playable = album.id != null && album.id !== '';

  const play = async () => {
    if (!playable || loading) return;
    setLoading(true);
    try {
      const tracks = await fetchAlbumPlayTracks(album.id as string | number);
      if (!tracks.length) {
        window.showToast?.(`No owned tracks for ${album.name} yet`, 'info');
        return;
      }
      await window.playTrackList?.(tracks, album.name);
    } catch {
      window.showToast?.('Could not play that album', 'error');
    } finally {
      setLoading(false);
    }
  };

  const body = (
    <>
      <span className={styles.albumArtWrap}>
        <Art src={album.image_url} name={album.name} className={styles.albumArt} />
        {playable ? (
          <span className={styles.albumPlayBadge} aria-hidden="true">
            {loading ? '…' : '▶'}
          </span>
        ) : null}
      </span>
      <span className={styles.albumName}>{album.name}</span>
      <span className={styles.albumArtist}>{album.artist}</span>
      <span className={styles.albumPlays}>{album.plays.toLocaleString()} plays</span>
    </>
  );

  const style = { animationDelay: `${staggerDelay(index, 90)}ms` };

  if (!playable) {
    return (
      <div className={`${styles.albumCard} ${styles.reveal}`} style={style}>
        {body}
      </div>
    );
  }

  return (
    <button
      type="button"
      className={`${styles.albumCard} ${styles.albumCardPlayable} ${styles.reveal}`}
      style={style}
      onClick={() => void play()}
      aria-label={`Play ${album.name}`}
    >
      {body}
    </button>
  );
}

/** A strip of the year's art under the title — the first thing seen. */
function OpeningCollage({ year }: { year: YearInListening }) {
  const art = useMemo(
    () =>
      Array.from(
        new Set(
          [
            ...(year.top_artists ?? []).map((a) => a.image_url),
            ...(year.top_albums ?? []).map((a) => a.image_url),
            ...(year.discoveries ?? []).map((d) => d.image_url),
          ].filter((url): url is string => Boolean(url)),
        ),
      ).slice(0, 5),
    [year],
  );
  if (!art.length) return null;
  return (
    <div className={styles.collage} aria-hidden="true">
      {art.map((src) => (
        <img key={src} className={styles.collageArt} src={src} alt="" loading="lazy" />
      ))}
    </div>
  );
}

function Fact({ value, label }: { value: string; label: string }) {
  return (
    <div className={styles.fact}>
      <strong className={styles.factValue}>{value}</strong>
      <span className={styles.factLabel}>{label}</span>
    </div>
  );
}

/** Load an image for the canvas, or resolve null if it cannot be used.
 *
 *  `crossOrigin='anonymous'` is what keeps the canvas exportable: artwork that
 *  came from the media server is proxied same-origin, but anything from an
 *  external metadata provider is not, and drawing one of those without CORS
 *  taints the canvas so `toBlob` throws. Null here means "draw the tile
 *  empty", which is always better than failing the whole save. */
function loadCardImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

/**
 * The thing you keep — a studio, not a template.
 *
 * Spotify ships one fixed composition because they render it for half a
 * billion people. We render it for one person who owns their music, so the
 * card can offer real choices AND use the actual covers off their disk.
 * Four layouts, three aspects, four themes, and the user picks which numbers
 * appear.
 *
 * Content and geometry live in `-year.card.ts` so both are testable without a
 * canvas; this file wires the controls and owns the export.
 */
function YearCard({ year }: { year: YearInListening }) {
  const [options, setOptions] = useState<YearCardOptions>(DEFAULT_YEAR_CARD_OPTIONS);
  const [status, setStatus] = useState<'idle' | 'working' | 'saved' | 'copied' | 'error'>('idle');
  const previewRef = useRef<HTMLCanvasElement | null>(null);

  const model = useMemo(() => buildYearCardModel(year, options), [year, options]);
  const set = <K extends keyof YearCardOptions>(key: K, value: YearCardOptions[K]) => {
    setOptions((o) => ({ ...o, [key]: value }));
    setStatus('idle');
  };

  // Live preview, drawn by the SAME pass that exports — the two cannot drift.
  useEffect(() => {
    let cancelled = false;
    const canvas = previewRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Draw immediately with no art so the card is never blank while images
    // are in flight, then again once they land.
    drawYearCard(ctx, model, []);
    void Promise.all(model.artUrls.map(loadCardImage)).then((images) => {
      if (cancelled) return;
      ctx.clearRect(0, 0, model.width, model.height);
      drawYearCard(ctx, model, images);
    });

    return () => {
      cancelled = true;
    };
  }, [model]);

  /** Render at full size and hand back a PNG blob, or null. */
  const render = async (): Promise<Blob | null> => {
    const canvas = document.createElement('canvas');
    canvas.width = model.width;
    canvas.height = model.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    const images = await Promise.all(model.artUrls.map(loadCardImage));
    drawYearCard(ctx, model, images);

    const toBlob = () =>
      new Promise<Blob | null>((resolve) => {
        try {
          canvas.toBlob(resolve, 'image/png');
        } catch {
          // A tainted canvas throws here rather than resolving null.
          resolve(null);
        }
      });

    let blob = await toBlob();
    if (!blob && images.some(Boolean)) {
      // Almost certainly a tainted canvas from artwork that ignored CORS.
      // The card without its covers is still the card — better than nothing.
      ctx.clearRect(0, 0, model.width, model.height);
      drawYearCard(ctx, { ...model, artUrls: [] }, []);
      blob = await toBlob();
    }
    return blob;
  };

  const save = async () => {
    setStatus('working');
    const blob = await render();
    if (!blob) {
      setStatus('error');
      return;
    }
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = model.filename;
    link.click();
    // Revoking immediately can cancel the download in some browsers.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
    setStatus('saved');
  };

  /** Straight to the clipboard — the actual share gesture on desktop, where
   *  a downloaded file still has to be found and dragged somewhere. */
  const copy = async () => {
    setStatus('working');
    try {
      const blob = await render();
      if (!blob || typeof ClipboardItem === 'undefined' || !navigator.clipboard?.write) {
        setStatus('error');
        return;
      }
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      setStatus('copied');
    } catch {
      setStatus('error');
    }
  };

  const statCount = options.stats.length;

  return (
    <div className={styles.slide}>
      <p className={styles.kicker}>That was your year</p>

      <div className={styles.cardLayout}>
        <div className={styles.cardStage}>
          <canvas
            ref={previewRef}
            className={styles.cardPreview}
            width={model.width}
            height={model.height}
            style={{ aspectRatio: `${model.width} / ${model.height}` }}
            aria-label="Your year card preview"
          />
        </div>

        <div className={styles.cardControls}>
          <fieldset className={styles.cardGroup}>
            <legend className={styles.cardControlLabel}>Layout</legend>
            <div className={styles.layoutRow}>
              {YEAR_CARD_LAYOUT_KEYS.map((key) => (
                <button
                  key={key}
                  type="button"
                  className={`${styles.layoutChip} ${options.layout === key ? styles.chipActive : ''}`}
                  aria-pressed={options.layout === key}
                  title={YEAR_CARD_LAYOUTS[key].blurb}
                  onClick={() => set('layout', key as YearCardLayout)}
                >
                  <span className={`${styles.layoutGlyph} ${styles[`glyph_${key}`] ?? ''}`} aria-hidden="true" />
                  {YEAR_CARD_LAYOUTS[key].label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className={styles.cardGroup}>
            <legend className={styles.cardControlLabel}>Size</legend>
            <div className={styles.chipRow}>
              {YEAR_CARD_ASPECT_KEYS.map((key) => (
                <button
                  key={key}
                  type="button"
                  className={`${styles.chip} ${options.aspect === key ? styles.chipActive : ''}`}
                  aria-pressed={options.aspect === key}
                  // Explicit: the label and ratio are separate elements, so
                  // the computed name would run together as "Post4:5".
                  aria-label={`${YEAR_CARD_ASPECTS[key].label} ${YEAR_CARD_ASPECTS[key].ratio}`}
                  onClick={() => set('aspect', key as YearCardAspect)}
                >
                  {YEAR_CARD_ASPECTS[key].label}
                  <em className={styles.chipHint}>{YEAR_CARD_ASPECTS[key].ratio}</em>
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className={styles.cardGroup}>
            <legend className={styles.cardControlLabel}>Theme</legend>
            <div className={styles.chipRow}>
              {YEAR_CARD_THEMES.map((theme) => (
                <button
                  key={theme}
                  type="button"
                  className={`${styles.swatch} ${options.theme === theme ? styles.swatchActive : ''}`}
                  style={{
                    background: `linear-gradient(140deg, ${YEAR_CARD_PALETTES[theme].from}, ${YEAR_CARD_PALETTES[theme].to})`,
                    color: YEAR_CARD_PALETTES[theme].text,
                  }}
                  aria-pressed={options.theme === theme}
                  onClick={() => set('theme', theme as YearCardTheme)}
                >
                  {YEAR_CARD_PALETTES[theme].label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className={styles.cardGroup}>
            <legend className={styles.cardControlLabel}>
              Numbers <span className={styles.cardCount}>{statCount}/{MAX_YEAR_CARD_STATS}</span>
            </legend>
            <div className={styles.chipRow}>
              {YEAR_CARD_STAT_DEFS.map((def) => {
                const on = options.stats.includes(def.key);
                const full = !on && statCount >= MAX_YEAR_CARD_STATS;
                return (
                  <button
                    key={def.key}
                    type="button"
                    className={`${styles.chip} ${on ? styles.chipActive : ''}`}
                    aria-pressed={on}
                    disabled={full}
                    title={full ? `Up to ${MAX_YEAR_CARD_STATS} fit on a card` : undefined}
                    onClick={() => set('stats', toggleCardStat(options.stats, def.key))}
                  >
                    {def.label}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <label className={styles.cardToggle}>
            <input
              type="checkbox"
              checked={options.artwork}
              onChange={(e) => set('artwork', e.target.checked)}
            />
            Include artwork
          </label>
          <label className={styles.cardToggle}>
            <input
              type="checkbox"
              checked={options.showRunnersUp}
              onChange={(e) => set('showRunnersUp', e.target.checked)}
            />
            Name the runners-up
          </label>

          <div className={styles.cardActions}>
            <button type="button" className={styles.saveButton} onClick={() => void save()}>
              {status === 'working' ? 'Rendering…' : status === 'saved' ? 'Saved' : 'Save image'}
            </button>
            <button type="button" className={styles.copyButton} onClick={() => void copy()}>
              {status === 'copied' ? 'Copied' : 'Copy'}
            </button>
          </div>
          {status === 'error' ? (
            <span className={styles.cardError}>Could not render the card.</span>
          ) : null}
        </div>
      </div>
    </div>
  );
}
