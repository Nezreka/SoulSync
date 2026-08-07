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
