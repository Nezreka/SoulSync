/**
 * the home billboard: a handful of what's new in your library, one at a
 * time, full bleed. each shows its title logo (tmdb's wordmark, the text
 * title when there isn't one), what it is, its story, and play / more info.
 * a muted trailer takes over the backdrop after a few seconds when the
 * profile has trailers on; the billboard waits for it to finish, otherwise
 * it moves on every ten seconds. pointing at it, or focusing inside it,
 * holds it still.
 */

import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';

import type { CatalogMovie, CatalogShow } from '../-player.types';

import { plainText, runtime } from '../-lib/format';
import { backdropSet, HERO_SIZES } from '../-lib/images';
import { logosQuery, movieDetailQuery, showDetailQuery } from '../-player.api';
import { InfoIcon } from './icons';
import { Art, PlayButton, PlayerLink, styles as s } from './kit';
import { usePlayTitle } from './playback/play-title';
import { BackgroundTrailer, type TrailerStage } from './trailer';
import { WatchlistButton } from './watchlist';

export type Slide =
  | { kind: 'movie'; item: CatalogMovie; why: string }
  | { kind: 'show'; item: CatalogShow; why: string };

const ADVANCE_MS = 10_000;

const hrefOf = (slide: Slide) =>
  slide.kind === 'movie' ? `/player/movies/${slide.item.id}` : `/player/tv/${slide.item.id}`;

function Story({ slide }: { slide: Slide }) {
  const movie = useQuery({ ...movieDetailQuery(slide.item.id), enabled: slide.kind === 'movie' });
  const show = useQuery({ ...showDetailQuery(slide.item.id), enabled: slide.kind === 'show' });
  const overview = plainText((slide.kind === 'movie' ? movie.data : show.data)?.overview);
  return overview ? (
    <p className={s.billboardOverview}>{overview}</p>
  ) : (
    <p className={s.billboardOverview} />
  );
}

export function Billboard({ slides }: { slides: Slide[] }) {
  const [index, setIndex] = useState(0);
  const [held, setHeld] = useState(false);
  const [trailer, setTrailer] = useState<TrailerStage>('waiting');
  const playTitle = usePlayTitle();
  const logos = useQuery(
    logosQuery(slides.filter((x) => x.item.tmdb_id).map((x) => `${x.kind}:${x.item.tmdb_id}`)),
  );
  const slide = slides[index % Math.max(1, slides.length)];
  const next = useCallback(
    () => setIndex((i) => (i + 1) % Math.max(1, slides.length)),
    [slides.length],
  );

  // a new slide starts its trailer clock over
  useEffect(() => setTrailer('waiting'), [index]);

  // move on: every ten seconds, unless held or a trailer is on; a trailer
  // that finished (or was stopped) moves on straight away
  useEffect(() => {
    if (slides.length < 2 || held) return;
    if (trailer === 'loading' || trailer === 'playing') return;
    if (trailer === 'ended') {
      next();
      return;
    }
    const t = window.setTimeout(next, ADVANCE_MS);
    return () => window.clearTimeout(t);
  }, [index, held, trailer, slides.length, next]);

  const onStage = useCallback((stage: TrailerStage) => setTrailer(stage), []);
  const backdrops = useMemo(
    () =>
      slides.map((x) => ({ key: `${x.kind}:${x.item.id}`, set: backdropSet(x.kind, x.item.id) })),
    [slides],
  );

  if (!slide) return null;
  const item = slide.item;
  const logo = item.tmdb_id ? logos.data?.[`${slide.kind}:${item.tmdb_id}`] : null;
  const progressKey = `${index}-${held}-${trailer}`;

  return (
    <section
      className={s.billboard}
      aria-roledescription="carousel"
      aria-label="Featured"
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setHeld(false);
      }}
    >
      <div className={s.billboardArt}>
        {backdrops.map((b, i) => {
          // the slide on show and the next one are in the page; the rest load when it's their turn
          const near = i === index || i === (index + 1) % backdrops.length;
          return near ? (
            <div
              key={b.key}
              className={s.billboardFrame}
              data-on={i === index ? 'true' : undefined}
              aria-hidden="true"
            >
              <Art
                src={b.set.src}
                srcSet={b.set.srcSet}
                sizes={HERO_SIZES}
                alt=""
                eager={i === index}
              />
            </div>
          ) : null;
        })}
        <BackgroundTrailer
          key={`${slide.kind}:${item.id}`}
          kind={slide.kind}
          id={item.id}
          onStage={onStage}
        />
        <div className={s.billboardShade} aria-hidden="true" />
      </div>

      <div className={s.billboardBody} key={`${slide.kind}:${item.id}`}>
        <div className={s.billboardWhy}>{slide.why}</div>
        {logo ? (
          <img className={s.billboardLogo} src={logo} alt={item.title} />
        ) : (
          <h1 className={s.billboardTitle} data-long={item.title.length > 22 ? 'true' : undefined}>
            {item.title}
          </h1>
        )}
        <div className={s.billboardMeta}>
          {item.rating ? (
            <span className={s.billboardScore}>★ {item.rating.toFixed(1)}</span>
          ) : null}
          {item.year ? <span>{item.year}</span> : null}
          {slide.kind === 'movie' && slide.item.runtime_seconds ? (
            <span>{runtime(slide.item.runtime_seconds)}</span>
          ) : null}
          {slide.kind === 'show' ? (
            <span>
              {slide.item.episode_count} episode{slide.item.episode_count === 1 ? '' : 's'}
            </span>
          ) : null}
          {item.genres.slice(0, 3).map((g) => (
            <span key={g}>{g}</span>
          ))}
        </div>
        <Story slide={slide} />
        <div className={s.billboardActions}>
          <PlayButton onPlay={() => void playTitle(slide.kind, item.id)} />
          <WatchlistButton kind={slide.kind} id={item.id} compact />
          <PlayerLink href={hrefOf(slide)} className={s.billboardInfo} data-nav>
            <InfoIcon />
            More info
          </PlayerLink>
        </div>
      </div>

      {item.content_rating ? <div className={s.billboardRating}>{item.content_rating}</div> : null}

      {slides.length > 1 ? (
        <div className={s.billboardDots} role="tablist" aria-label="Featured titles">
          {slides.map((x, i) => (
            <button
              key={`${x.kind}:${x.item.id}`}
              type="button"
              role="tab"
              aria-selected={i === index}
              aria-label={x.item.title}
              className={s.billboardDot}
              onClick={() => setIndex(i)}
              data-nav-skip
            >
              {i === index ? (
                <span
                  key={progressKey}
                  className={s.billboardDotFill}
                  data-running={
                    !held && trailer !== 'loading' && trailer !== 'playing' ? 'true' : undefined
                  }
                />
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}
