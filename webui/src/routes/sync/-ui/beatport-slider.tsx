/**
 * The one slider component, driven by BEATPORT_SLIDERS.
 *
 * The vanilla has five of these, each commented "copied from" its predecessor
 * and each since drifted — five autoplay delays, four slide layouts, two
 * re-entry mechanisms, three failure behaviours (see the comparison table in
 * SYNC_PORT_AUDIT.md). One component is the right shape, but ONLY because every
 * one of those differences is a prop rather than an assumption.
 *
 * What React removes for free, and why that is not a divergence:
 *   - the `dataset.initialized` / `isInitialized` re-entry guards existed to
 *     stop duplicate listeners on a re-rendered DOM; effects handle that,
 *   - `cloneNode` on the nav buttons (releases/hype/charts/dj) existed for the
 *     same reason,
 *   - the autoplay-not-restarted-on-return bug those guards caused was fixed in
 *     the vanilla first (commit 44f60b3fc), so the two sides agree today.
 */

import type { ReactNode } from 'react';

import { useCallback, useEffect, useMemo, useState } from 'react';

import type { BeatportSliderConfig } from '../-beatport.core';

import {
  beatportSliderClasses,
  slideCount,
  slidePosition,
  wrapSlideIndex,
} from '../-beatport.core';

export interface BeatportSliderProps<T> {
  config: BeatportSliderConfig;
  items: readonly T[];
  renderItem: (item: T, index: number) => ReactNode;
  /**
   * Only consulted when `config.padsLastSlide`. The releases slider pads with
   * captioned cards and hype picks pads with a bare icon, so the filler is the
   * caller's, not the component's.
   */
  renderPlaceholder?: (index: number) => ReactNode;
  /** The id the vanilla put on the track element, kept for the CSS contract. */
  trackId?: string;
  prevButtonId?: string;
  nextButtonId?: string;
  indicatorsId?: string;
}

export function BeatportSlider<T>({
  config,
  items,
  renderItem,
  renderPlaceholder,
  trackId,
  prevButtonId,
  nextButtonId,
  indicatorsId,
}: BeatportSliderProps<T>) {
  const classes = beatportSliderClasses(config.slug);
  const [currentSlide, setCurrentSlide] = useState(0);
  const [paused, setPaused] = useState(false);
  /** Bumped by any manual navigation, to restart the timer the way
   *  reset…AutoPlay does (289-291 and its four twins). */
  const [restartToken, setRestartToken] = useState(0);

  const totalSlides = slideCount(items.length, config.cardsPerSlide);

  const slides = useMemo(() => {
    const out: T[][] = [];
    for (let i = 0; i < items.length; i += config.cardsPerSlide) {
      out.push(items.slice(i, i + config.cardsPerSlide));
    }
    return out;
  }, [items, config.cardsPerSlide]);

  // A shrinking list must not strand the view past the end.
  useEffect(() => {
    setCurrentSlide((current) => (current >= totalSlides ? 0 : current));
  }, [totalSlides]);

  const goTo = useCallback(
    (index: number) => {
      setCurrentSlide(wrapSlideIndex(index, totalSlides));
      setRestartToken((token) => token + 1);
    },
    [totalSlides],
  );

  useEffect(() => {
    // 277-283: one slide is nothing to advance through, and a paused slider
    // holds no timer at all rather than ticking into a no-op.
    if (paused || totalSlides <= 1) return;
    const id = setInterval(() => {
      setCurrentSlide((current) => wrapSlideIndex(current + 1, totalSlides));
    }, config.autoPlayDelay);
    return () => clearInterval(id);
  }, [paused, totalSlides, config.autoPlayDelay, restartToken]);

  if (items.length === 0) return null;

  return (
    <div
      className={classes.container}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {/* index.html 2832-2837: the two buttons sit inside a slider-nav
          wrapper, which is what positions them. Rendering them bare loses the
          layout silently — CSS has no way to complain about a missing box. */}
      <div className={classes.nav}>
        <button
          type="button"
          id={prevButtonId}
          className={`${classes.navButton} beatport-${config.slug}-prev-btn`}
          onClick={(event) => {
            // 197-198: the slide itself is click-to-open, so paging must not
            // reach it.
            event.preventDefault();
            event.stopPropagation();
            goTo(currentSlide - 1);
          }}
        >
          ‹
        </button>
        <button
          type="button"
          id={nextButtonId}
          className={`${classes.navButton} beatport-${config.slug}-next-btn`}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            goTo(currentSlide + 1);
          }}
        >
          ›
        </button>
      </div>

      <div className={classes.track} id={trackId}>
        {slides.map((slideItems, slideIndex) => {
          const padding =
            config.padsLastSlide && renderPlaceholder
              ? config.cardsPerSlide - slideItems.length
              : 0;
          const body = slideItems.map((item, indexInSlide) =>
            renderItem(item, slideIndex * config.cardsPerSlide + indexInSlide),
          );
          const padded =
            padding > 0
              ? [...body, ...Array.from({ length: padding }, (_, i) => renderPlaceholder?.(i))]
              : body;
          return (
            <div
              key={slideIndex}
              className={`${classes.slide} ${slidePosition(slideIndex, currentSlide)}`}
              data-slide={slideIndex}
            >
              {config.hasGrid ? <div className={classes.grid}>{padded}</div> : padded}
            </div>
          );
        })}
      </div>

      <div className={classes.indicators} id={indicatorsId}>
        {slides.map((_, slideIndex) => (
          <button
            type="button"
            key={slideIndex}
            className={`${classes.indicator}${slideIndex === currentSlide ? ' active' : ''}`}
            data-slide={slideIndex}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              goTo(slideIndex);
            }}
          />
        ))}
      </div>
    </div>
  );
}
