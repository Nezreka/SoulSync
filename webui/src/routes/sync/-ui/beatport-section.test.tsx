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

  it('draws nothing at all while loading', () => {
    const { container } = renderSection('releases', () => new Promise(() => []), 'pending');
    // The vanilla's spinner lives in the page markup and is overwritten when
    // the data arrives; the section never draws one itself.
    expect(container).toBeEmptyDOMElement();
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

  it('renders NOTHING when charts fail, which is what the vanilla does', async () => {
    const { container } = renderSection(
      'charts',
      async () => {
        throw new Error('network down');
      },
      'charts-err',
      'Error Loading Charts',
    );
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    // No error block, no title — loadBeatportFeaturedCharts has no error
    // renderer at all, it just returns false.
    expect(screen.queryByText('❌ Error Loading Charts')).not.toBeInTheDocument();
    expect(document.querySelector('.beatport-charts-loading')).toBeNull();
  });

  it('renders NOTHING when the DJ charts fail either', async () => {
    const { container } = renderSection('dj', async () => [], 'dj-err');
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('renders nothing when the hero fails, leaving its static markup alone', async () => {
    const { container } = renderSection('hero', async () => null, 'hero-err');
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    // setupBeatportSliderWithPlaceholders (163-168) wires up the placeholder
    // slides that are already in index.html — it renders no error.
    expect(document.querySelector('.beatport-rebuild-loading')).toBeNull();
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
