import { render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { RING_CIRCUMFERENCE, RING_RADIUS } from '../-artist-detail.enrichment';
import { EnrichmentCoverage } from './enrichment-coverage';

afterEach(() => {
  delete window.filterJiosaavnServiceEntries;
});

const COVERAGE = { total_tracks: 120, spotify: 100, musicbrainz: 50, deezer: 0 };

describe('EnrichmentCoverage', () => {
  it('renders nothing without coverage data', () => {
    const { container } = render(<EnrichmentCoverage enrichment={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing for an artist with no tracks', () => {
    // A coverage ring over zero tracks is a meaningless 0%.
    const { container } = render(
      <EnrichmentCoverage enrichment={{ total_tracks: 0, spotify: 90 }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders one ring per visible service, in declaration order', () => {
    render(<EnrichmentCoverage enrichment={COVERAGE} />);
    const labels = [...document.querySelectorAll('.artist-enrich-label')].map((n) => n.textContent);
    // JioSaavn is filtered out by default (experimental, opt-in).
    expect(labels).toEqual([
      'Spotify',
      'MusicBrainz',
      'Deezer',
      'Last.fm',
      'iTunes',
      'AudioDB',
      'Discogs',
      'Genius',
      'Tidal',
      'Qobuz',
      'Bandcamp',
    ]);
  });

  it('includes JioSaavn when the shared helper says it is enabled', () => {
    window.filterJiosaavnServiceEntries = (entries) => entries as never[];
    render(<EnrichmentCoverage enrichment={COVERAGE} />);
    const labels = [...document.querySelectorAll('.artist-enrich-label')].map((n) => n.textContent);
    expect(labels).toContain('JioSaavn');
  });

  it('empties the ring in proportion to the missing coverage', () => {
    render(<EnrichmentCoverage enrichment={COVERAGE} />);
    const fills = [...document.querySelectorAll('.ring-fill')] as SVGElement[];

    // Compared numerically: jsdom normalises the "0.0" the vanilla emitted to
    // "0", exactly as a real browser does.
    const offset = (el: SVGElement) => parseFloat(el.style.strokeDashoffset);
    // 100% coverage leaves nothing empty; 0% leaves the whole circumference.
    expect(offset(fills[0])).toBe(0);
    expect(offset(fills[2])).toBeCloseTo(RING_CIRCUMFERENCE, 1);
    // Half-covered sits halfway, and not at either extreme.
    expect(offset(fills[1])).toBeCloseTo(RING_CIRCUMFERENCE / 2, 1);
    expect(fills[0].getAttribute('r')).toBe(String(RING_RADIUS));
  });

  it('shows a rounded percentage, and 0 for a service with no key', () => {
    render(<EnrichmentCoverage enrichment={{ total_tracks: 10, spotify: 66.6 }} />);
    const pcts = [...document.querySelectorAll('.ring-pct')].map((n) => n.textContent);
    expect(pcts[0]).toBe('67');
    expect(pcts[1]).toBe('0');
  });

  it('staggers the ring animations so they sweep in one after another', () => {
    render(<EnrichmentCoverage enrichment={COVERAGE} />);
    const fills = [...document.querySelectorAll('.ring-fill')] as SVGElement[];
    expect(fills[0].style.animation).toContain('0.00s');
    expect(fills[1].style.animation).toContain('0.08s');
    expect(fills[2].style.animation).toContain('0.16s');

    // The number fades in 0.3s after its own ring starts.
    const pcts = [...document.querySelectorAll('.ring-pct')] as HTMLElement[];
    expect(pcts[1].style.animation).toContain('0.38s');
  });
});
