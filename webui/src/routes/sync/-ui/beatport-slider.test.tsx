/**
 * The shared Beatport slider.
 *
 * The class names are the CSS contract and are asserted as literals — and the
 * final test reads style.css directly, because a derived class name that does
 * not exist produces an unstyled slider rather than an error.
 */

import { act, fireEvent, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BEATPORT_SLIDERS, beatportSliderClasses } from '../-beatport.core';
import { BeatportSlider } from './beatport-slider';

const ITEMS = Array.from({ length: 25 }, (_, i) => `item-${i}`);

function renderSlider(configKey: keyof typeof BEATPORT_SLIDERS, items = ITEMS) {
  return render(
    <BeatportSlider
      config={BEATPORT_SLIDERS[configKey]}
      items={items}
      renderItem={(item) => (
        <span key={item} className="card">
          {item}
        </span>
      )}
      renderPlaceholder={(i) => <span key={`ph-${i}`} className="placeholder" />}
    />,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('BeatportSlider', () => {
  it('pages by the config, not by a shared default', () => {
    // 25 items: ten per slide for releases, three for DJ.
    renderSlider('releases');
    expect(document.querySelectorAll('.beatport-releases-slide')).toHaveLength(3);
    expect(document.querySelectorAll('.beatport-releases-indicator')).toHaveLength(3);
  });

  it('gives the DJ slider three cards a slide', () => {
    renderSlider('dj');
    expect(document.querySelectorAll('.beatport-dj-slide')).toHaveLength(9);
  });

  it('gives the hero one item a slide and NO grid wrapper', () => {
    renderSlider('hero', ITEMS.slice(0, 4));
    expect(document.querySelectorAll('.beatport-rebuild-slide')).toHaveLength(4);
    // The hero's item fills the slide itself — every other slider wraps.
    expect(document.querySelector('.beatport-rebuild-grid')).toBeNull();
    expect(document.querySelectorAll('.beatport-releases-grid')).toHaveLength(0);
  });

  it('wraps the cards in a grid for every slider that has one', () => {
    renderSlider('charts');
    expect(document.querySelectorAll('.beatport-charts-grid')).toHaveLength(3);
  });

  it('marks each slide active/prev/next, because the CSS needs the direction', () => {
    renderSlider('releases');
    const slides = document.querySelectorAll('.beatport-releases-slide');
    expect(slides[0].className).toContain('active');
    expect(slides[1].className).toContain('next');
    fireEvent.click(document.querySelectorAll('.beatport-releases-indicator')[2]);
    const after = document.querySelectorAll('.beatport-releases-slide');
    expect(after[0].className).toContain('prev');
    expect(after[2].className).toContain('active');
  });

  it('pads the last slide only where the vanilla pads it', () => {
    // 25 releases over three slides leaves five empties on the last.
    renderSlider('releases');
    expect(document.querySelectorAll('.placeholder')).toHaveLength(5);
  });

  it('does NOT pad charts, which the vanilla leaves short', () => {
    renderSlider('charts');
    expect(document.querySelectorAll('.placeholder')).toHaveLength(0);
    expect(document.querySelectorAll('.card')).toHaveLength(25);
  });

  it('wraps in both directions', () => {
    renderSlider('releases');
    const prev = document.querySelector('.beatport-releases-prev-btn') as Element;
    fireEvent.click(prev);
    expect(document.querySelectorAll('.beatport-releases-slide')[2].className).toContain('active');
    const next = document.querySelector('.beatport-releases-next-btn') as Element;
    fireEvent.click(next);
    expect(document.querySelectorAll('.beatport-releases-slide')[0].className).toContain('active');
  });

  it('advances on its own delay, and each slider has its own', () => {
    renderSlider('releases');
    // 8000 for releases: still on slide 0 at 7999.
    act(() => {
      vi.advanceTimersByTime(7999);
    });
    expect(document.querySelectorAll('.beatport-releases-slide')[0].className).toContain('active');
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(document.querySelectorAll('.beatport-releases-slide')[1].className).toContain('active');
  });

  it('uses 4000 for hype picks — the fastest of the five', () => {
    renderSlider('hypePicks');
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(document.querySelectorAll('.beatport-hype-picks-slide')[1].className).toContain(
      'active',
    );
  });

  it('pauses on hover and resumes on leave', () => {
    renderSlider('releases');
    const container = document.querySelector('.beatport-releases-slider-container') as Element;
    fireEvent.mouseEnter(container);
    act(() => {
      vi.advanceTimersByTime(20000);
    });
    expect(document.querySelectorAll('.beatport-releases-slide')[0].className).toContain('active');

    fireEvent.mouseLeave(container);
    act(() => {
      vi.advanceTimersByTime(8000);
    });
    expect(document.querySelectorAll('.beatport-releases-slide')[1].className).toContain('active');
  });

  it('restarts the timer after a manual move, rather than firing mid-interval', () => {
    renderSlider('releases');
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    fireEvent.click(document.querySelector('.beatport-releases-next-btn') as Element);
    // 6s were already spent; a slider that did not reset would advance in 2s.
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(document.querySelectorAll('.beatport-releases-slide')[1].className).toContain('active');
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(document.querySelectorAll('.beatport-releases-slide')[2].className).toContain('active');
  });

  it('holds no timer at all when there is only one slide', () => {
    renderSlider('releases', ITEMS.slice(0, 4));
    expect(document.querySelectorAll('.beatport-releases-slide')).toHaveLength(1);
    // DECLARED DIVERGENCE: the vanilla starts an interval regardless and lets
    // each tick wrap 0 -> 0, so the screen agrees either way. Asserting the
    // TIMER COUNT is the only way to pin the difference — a rendered-output
    // check passes with the guard removed.
    expect(vi.getTimerCount()).toBe(0);
    act(() => {
      vi.advanceTimersByTime(80000);
    });
    expect(document.querySelectorAll('.beatport-releases-slide')[0].className).toContain('active');
  });

  it('holds no timer while paused either', () => {
    renderSlider('releases');
    expect(vi.getTimerCount()).toBe(1);
    fireEvent.mouseEnter(document.querySelector('.beatport-releases-slider-container') as Element);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('renders nothing at all with no items', () => {
    const { container } = renderSlider('releases', []);
    expect(container).toBeEmptyDOMElement();
  });

  it('keeps the ids the vanilla markup and CSS use', () => {
    render(
      <BeatportSlider
        config={BEATPORT_SLIDERS.releases}
        items={ITEMS}
        renderItem={(item) => <span key={item}>{item}</span>}
        trackId="beatport-releases-slider-track"
        prevButtonId="beatport-releases-prev-btn"
        nextButtonId="beatport-releases-next-btn"
        indicatorsId="beatport-releases-slider-indicators"
      />,
    );
    expect(document.getElementById('beatport-releases-slider-track')).not.toBeNull();
    expect(document.getElementById('beatport-releases-prev-btn')).not.toBeNull();
    expect(document.getElementById('beatport-releases-next-btn')).not.toBeNull();
    expect(document.getElementById('beatport-releases-slider-indicators')).not.toBeNull();
  });

  it('does not strand the view past the end when the list shrinks', () => {
    const { rerender } = renderSlider('releases');
    fireEvent.click(document.querySelectorAll('.beatport-releases-indicator')[2]);
    rerender(
      <BeatportSlider
        config={BEATPORT_SLIDERS.releases}
        items={ITEMS.slice(0, 5)}
        renderItem={(item) => <span key={item}>{item}</span>}
      />,
    );
    expect(document.querySelectorAll('.beatport-releases-slide')[0].className).toContain('active');
  });

  it('a nav click never reaches the slide behind it (197-198)', () => {
    const onSlideClick = vi.fn();
    render(
      <div onClick={onSlideClick}>
        <BeatportSlider
          config={BEATPORT_SLIDERS.releases}
          items={ITEMS}
          renderItem={(item) => <span key={item}>{item}</span>}
        />
      </div>,
    );
    // All THREE controls, not just the two obvious ones — the prev button is
    // the one a reader skips.
    fireEvent.click(document.querySelector('.beatport-releases-prev-btn') as Element);
    fireEvent.click(document.querySelector('.beatport-releases-next-btn') as Element);
    fireEvent.click(document.querySelectorAll('.beatport-releases-indicator')[1]);
    expect(onSlideClick).not.toHaveBeenCalled();
  });

  it('every derived class name exists in the vanilla stylesheet', () => {
    // The whole point of deriving from a slug is that a typo is silent: a
    // missing class renders an unstyled slider, not an error. So check.
    const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
    for (const [name, config] of Object.entries(BEATPORT_SLIDERS)) {
      const classes = beatportSliderClasses(config.slug);
      const required = [classes.container, classes.track, classes.slide, classes.indicator];
      if (config.hasGrid) required.push(classes.grid);
      for (const className of required) {
        expect(
          new RegExp(`\\.${className}[\\s,:{.]`).test(css),
          `${name}: .${className} is not in static/style.css`,
        ).toBe(true);
      }
      // …and the hero must NOT have a grid class, since it has no grid.
      if (!config.hasGrid) {
        expect(new RegExp(`\\.${classes.grid}[\\s,:{.]`).test(css)).toBe(false);
      }
    }
  });
});
