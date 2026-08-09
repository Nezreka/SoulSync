/**
 * The section wrapper — and specifically its three failure arms, which is the
 * behaviour that differs between the five vanilla sections and is invisible
 * until Beatport is actually down.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import { BEATPORT_SLIDERS, beatportSliderClasses } from '../-beatport.core';
import { resetBeatportSectionCache } from '../-beatport.use-section';
import { BeatportSection } from './beatport-section';

afterEach(() => {
  resetBeatportSectionCache();
});

function renderSection(
  configKey: keyof typeof BEATPORT_SLIDERS,
  load: () => Promise<string[] | null>,
  sectionKey: string,
  errorTitle?: string,
) {
  return render(
    <BeatportSection
      sectionKey={sectionKey}
      config={BEATPORT_SLIDERS[configKey]}
      load={load}
      renderItem={(item) => (
        <span key={item} className="card">
          {item}
        </span>
      )}
      errorTitle={errorTitle}
      defaultErrorMessage="No releases available"
    />,
  );
}

describe('BeatportSection', () => {
  it('renders the slider once the data lands', async () => {
    renderSection('releases', async () => ['a', 'b'], 'ok');
    await waitFor(() => expect(document.querySelectorAll('.card')).toHaveLength(2));
    expect(document.querySelector('.beatport-releases-slider-container')).not.toBeNull();
  });

  it('draws its OWN placeholder while loading', () => {
    renderSection('releases', () => new Promise(() => []), 'pending');
    // In the vanilla this block is page markup, present before any script runs
    // and overwritten when the data lands. The flip deletes that markup, so the
    // port has to draw it or the section is a blank strip while Beatport is
    // being scraped.
    expect(screen.getByText('📀 Loading New Releases...')).toBeInTheDocument();
    expect(screen.getByText('Fetching the latest albums and EPs')).toBeInTheDocument();
  });

  it('renders the error block for a section that has one', async () => {
    renderSection(
      'releases',
      async () => {
        throw new Error('network down');
      },
      'err',
      'Error Loading Releases',
    );
    await waitFor(() => expect(screen.getByText('❌ Error Loading Releases')).toBeInTheDocument());
    expect(screen.getByText('network down')).toBeInTheDocument();
    expect(document.querySelector('.beatport-releases-loading')).not.toBeNull();
    expect(document.querySelector('.beatport-releases-loading-content')).not.toBeNull();
  });

  it('uses the section-specific error copy', async () => {
    renderSection('hypePicks', async () => [], 'hype-err', 'Error Loading Hype Picks');
    await waitFor(() =>
      expect(screen.getByText('❌ Error Loading Hype Picks')).toBeInTheDocument(),
    );
    // The empty-list arm falls back to the default message, not a thrown one.
    expect(screen.getByText('No releases available')).toBeInTheDocument();
    expect(document.querySelector('.beatport-hype-picks-loading')).not.toBeNull();
  });

  it('KEEPS the placeholder when charts fail, and shows no error', async () => {
    renderSection(
      'charts',
      async () => {
        throw new Error('network down');
      },
      'charts-err',
      'Error Loading Charts',
    );
    // loadBeatportFeaturedCharts has no error renderer at all — it returns
    // false and replaces nothing, so what stays on screen is the placeholder.
    await waitFor(() =>
      expect(screen.getByText('📊 Loading Featured Charts...')).toBeInTheDocument(),
    );
    expect(screen.queryByText('❌ Error Loading Charts')).not.toBeInTheDocument();
    expect(screen.queryByText('network down')).not.toBeInTheDocument();
  });

  it('keeps the DJ placeholder too, with its own copy', async () => {
    renderSection('dj', async () => [], 'dj-err');
    await waitFor(() => expect(screen.getByText('🎧 Loading DJ Charts...')).toBeInTheDocument());
    expect(screen.getByText('Fetching curated DJ selections')).toBeInTheDocument();
  });

  it('keeps the hero placeholder, which is all the vanilla leaves behind', async () => {
    renderSection('hero', async () => null, 'hero-err');
    // A correction to the P0 read: index.html has NO hero placeholder slides —
    // `totalSlides: 4` is a dead initial value. What a failed hero actually
    // leaves on screen is this loading block, permanently.
    await waitFor(() =>
      expect(screen.getByText('🎯 Loading Fresh Beatport Tracks...')).toBeInTheDocument(),
    );
    expect(document.querySelector('.beatport-rebuild-loading')).not.toBeNull();
  });

  it('passes the ids through to the slider', async () => {
    render(
      <BeatportSection
        sectionKey="ids"
        config={BEATPORT_SLIDERS.releases}
        load={async () => ['a']}
        renderItem={(item) => <span key={item}>{item}</span>}
        trackId="beatport-releases-slider-track"
        indicatorsId="beatport-releases-slider-indicators"
      />,
    );
    await waitFor(() =>
      expect(document.getElementById('beatport-releases-slider-track')).not.toBeNull(),
    );
    expect(document.getElementById('beatport-releases-slider-indicators')).not.toBeNull();
  });

  it('pads through to the slider for the sections that pad', async () => {
    render(
      <BeatportSection
        sectionKey="pad"
        config={BEATPORT_SLIDERS.releases}
        load={async () => ['a', 'b']}
        renderItem={(item) => <span key={item}>{item}</span>}
        renderPlaceholder={(i) => <span key={`p-${i}`} className="ph" />}
      />,
    );
    // Two items on a ten-per-slide layout leaves eight fillers.
    await waitFor(() => expect(document.querySelectorAll('.ph')).toHaveLength(8));
  });

  it('frames each section in its section+header, with the vanilla copy', async () => {
    // THE TITLES. index.html wraps each grid slider in
    // `.beatport-{slug}-section > .beatport-{slug}-header > h2 + p` (2936,
    // 2973, 3011, 3048) and the port rendered none of it, so every section
    // heading was simply absent on screen. Nothing else here notices: a
    // section with no title still loads, still pages, still downloads.
    for (const [name, config] of Object.entries(BEATPORT_SLIDERS)) {
      if (config.sectionHeading === null) continue;
      const { container, unmount } = renderSection(
        name as keyof typeof BEATPORT_SLIDERS,
        async () => ['a'],
        `frame-${name}`,
      );
      await waitFor(() => expect(container.querySelector('.card')).not.toBeNull());
      const classes = beatportSliderClasses(config.slug);

      const section = container.firstElementChild;
      expect(section?.className, `${name}: outermost box`).toBe(classes.section);

      const header = section?.firstElementChild;
      expect(header?.className, `${name}: header box`).toBe(classes.header);

      const title = header?.querySelector(`h2.${classes.title}`);
      const subtitle = header?.querySelector(`p.${classes.subtitle}`);
      // The TAG matters as much as the class: these are h2 in the markup and
      // the stylesheet sizes them by class, but the page's heading outline is
      // what a screen reader walks.
      expect(title?.textContent, `${name}: title`).toBe(config.sectionHeading.title);
      expect(subtitle?.textContent, `${name}: subtitle`).toBe(config.sectionHeading.subtitle);

      unmount();
      resetBeatportSectionCache();
    }
  });

  it('gives the HERO no section frame, because the vanilla gives it none', async () => {
    // `#beatport-rebuild-content` opens straight onto the slider container
    // (2817-2819) and style.css has no `.beatport-rebuild-header/-title/
    // -subtitle` rule at all. Emitting the frame "for consistency" would put
    // three unstyled boxes and an invented heading on the page.
    const { container } = renderSection('hero', async () => ['a'], 'hero-frame');
    await waitFor(() => expect(container.querySelector('.card')).not.toBeNull());
    expect(container.querySelector('.beatport-rebuild-section')).toBeNull();
    expect(container.querySelector('.beatport-rebuild-header')).toBeNull();
    expect(container.firstElementChild?.className).toBe('beatport-rebuild-slider-container');
  });

  it('nests the placeholder inside container > slider > track, as the vanilla does', () => {
    // `sliderTrack.innerHTML = '<div class="beatport-{slug}-loading">…'`
    // (beatport-ui.js 644-647 and its twins) — the block goes IN the track, and
    // the static markup nests it there too. Rendered bare it loses the
    // container's width and the slider's height, so a loading or failed section
    // collapsed exactly the way the hero did.
    const { container } = renderSection('releases', () => new Promise(() => []), 'nest-loading');
    const classes = beatportSliderClasses('releases');
    // Past the section frame — releases has one, so the container is the
    // header's sibling rather than the root.
    const outer = container.querySelector(`.${classes.section} > .${classes.container}`);
    expect(outer).not.toBeNull();
    const slider = outer?.firstElementChild;
    expect(slider?.className).toBe(classes.slider);
    const track = slider?.firstElementChild;
    expect(track?.className).toBe(classes.track);
    expect(track?.firstElementChild?.className).toBe(classes.loading);
  });

  it('nests the ERROR block in the same three boxes', async () => {
    const { container } = renderSection(
      'releases',
      async () => {
        throw new Error('network down');
      },
      'nest-error',
      'Error Loading Releases',
    );
    await waitFor(() => expect(screen.getByText('❌ Error Loading Releases')).toBeInTheDocument());
    const classes = beatportSliderClasses('releases');
    expect(
      container.querySelector(
        `.${classes.container} > .${classes.slider} > .${classes.track} > .${classes.loading}`,
      ),
    ).not.toBeNull();
  });

  it('uses each section OWN heading level for the placeholder', () => {
    // h2 for the hero (2824), h3 for the four grid sections (2949, 2987, 3024,
    // 3060) — and `.beatport-*-loading-content h2` and `… h3` are sized
    // separately, so rendering h3 everywhere styled the hero as a subheading.
    const hero = renderSection('hero', () => new Promise(() => []), 'lvl-hero');
    expect(hero.container.querySelector('.beatport-rebuild-loading-content > h2')).not.toBeNull();
    expect(hero.container.querySelector('.beatport-rebuild-loading-content > h3')).toBeNull();
    hero.unmount();
    resetBeatportSectionCache();

    const rel = renderSection('releases', () => new Promise(() => []), 'lvl-rel');
    expect(rel.container.querySelector('.beatport-releases-loading-content > h3')).not.toBeNull();
    expect(rel.container.querySelector('.beatport-releases-loading-content > h2')).toBeNull();
  });

  it('every class the section frame emits exists in the stylesheet', () => {
    // The four frame classes are derived from the slug like everything else, so
    // a wrong one is silent. Checked against the real file, and checked for the
    // hero in the NEGATIVE — no rule exists for its frame, which is the
    // evidence that not emitting one is right rather than merely convenient.
    const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
    for (const [name, config] of Object.entries(BEATPORT_SLIDERS)) {
      const classes = beatportSliderClasses(config.slug);
      const frame = [classes.section, classes.header, classes.title, classes.subtitle];
      const wanted = config.sectionHeading !== null;
      for (const className of frame) {
        expect(
          new RegExp(`\\.${className}[\\s,:{.]`).test(css),
          `${name}: .${className} ${wanted ? 'is not in' : 'unexpectedly IS in'} static/style.css`,
        ).toBe(wanted);
      }
    }
  });

  it('every error-block class it can emit exists in the stylesheet', () => {
    // Same reasoning as the slider's check: a missing class renders unstyled
    // rather than failing, so it is verified against the real file.
    const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
    for (const [name, config] of Object.entries(BEATPORT_SLIDERS)) {
      const classes = beatportSliderClasses(config.slug);
      for (const className of [classes.loading, classes.loadingContent]) {
        expect(
          new RegExp(`\\.${className}[\\s,:{.]`).test(css),
          `${name}: .${className} is not in static/style.css`,
        ).toBe(true);
      }
    }
  });
});
