/**
 * The five card shapes. They look interchangeable and are not, so the
 * differences are asserted individually and the class names are checked
 * against the real stylesheet.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import {
  BeatportChartCard,
  BeatportHeroSlide,
  BeatportHypePickCard,
  BeatportHypePickPlaceholder,
  BeatportReleaseCard,
  BeatportReleasePlaceholder,
  heroSlideAttributes,
} from './beatport-cards';

describe('the hero slide', () => {
  it('renders the track over a background and gradient', () => {
    render(<BeatportHeroSlide track={{ title: 'Nights', artist: 'Frank Ocean' }} />);
    expect(document.querySelector('.beatport-rebuild-slide-background')).not.toBeNull();
    expect(document.querySelector('.beatport-rebuild-slide-gradient')).not.toBeNull();
    expect(document.querySelector('.beatport-rebuild-track-title')?.textContent).toBe('Nights');
    expect(document.querySelector('.beatport-rebuild-artist-name')?.textContent).toBe(
      'Frank Ocean',
    );
    // 99: fixed caption, not a field.
    expect(document.querySelector('.beatport-rebuild-album-name')?.textContent).toBe(
      'New on Beatport',
    );
  });

  it('emits both slide attributes UNCONDITIONALLY, as the vanilla does', () => {
    expect(heroSlideAttributes({ url: 'http://u', image_url: 'http://i.jpg' })).toEqual({
      'data-url': 'http://u',
      'data-image': 'http://i.jpg',
      style: { '--slide-bg-image': "url('http://i.jpg')" },
    });
  });

  it('still emits them when there is no artwork, so [data-image] keeps matching', () => {
    // The selector matches and paints nothing. Emitting them conditionally
    // would be a different behaviour, not a tidier one.
    expect(heroSlideAttributes({})).toEqual({
      'data-url': '',
      'data-image': '',
      style: { '--slide-bg-image': "url('')" },
    });
  });
});

describe('the release card', () => {
  const RELEASE = {
    title: 'Nights',
    artist: 'Frank Ocean',
    label: 'Blonded',
    url: 'http://r',
    image_url: 'http://a.jpg',
  };

  it('renders artwork, the three lines and their truncation tooltips', () => {
    render(<BeatportReleaseCard release={RELEASE} />);
    const card = document.querySelector('.beatport-release-card') as HTMLElement;
    expect(card.getAttribute('data-url')).toBe('http://r');
    expect(card.style.getPropertyValue('--card-bg-image')).toBe("url('http://a.jpg')");
    expect(card.querySelector('img')?.getAttribute('src')).toBe('http://a.jpg');
    expect(document.querySelector('.beatport-release-title')?.getAttribute('title')).toBe('Nights');
    expect(document.querySelector('.beatport-release-label')?.textContent).toBe('Blonded');
  });

  it('sets the background property even with NO artwork', () => {
    // 439 sets it unconditionally, so an artless release gets url(''). Hype
    // picks omit it entirely — the two cards genuinely differ here.
    render(<BeatportReleaseCard release={{ title: 'x' }} />);
    const card = document.querySelector('.beatport-release-card') as HTMLElement;
    expect(card.style.getPropertyValue('--card-bg-image')).toBe("url('')");
    expect(card.querySelector('img')).toBeNull();
  });

  it('does not default its text, unlike the hype pick card', () => {
    render(<BeatportReleaseCard release={{}} />);
    expect(document.querySelector('.beatport-release-title')?.textContent).toBe('');
  });

  it('fires its click handler', () => {
    const onClick = vi.fn();
    render(<BeatportReleaseCard release={RELEASE} onClick={onClick} />);
    fireEvent.click(document.querySelector('.beatport-release-card') as Element);
    expect(onClick).toHaveBeenCalled();
  });

  it('pads with a captioned filler card', () => {
    render(<BeatportReleasePlaceholder />);
    expect(screen.getByText('More Releases')).toBeInTheDocument();
    expect(screen.getByText('Coming Soon')).toBeInTheDocument();
    expect(screen.getByText('Beatport')).toBeInTheDocument();
    expect(screen.getByText('📀')).toBeInTheDocument();
  });
});

describe('the hype pick card', () => {
  it('OMITS the background property when there is no artwork', () => {
    render(<BeatportHypePickCard release={{ title: 'x' }} />);
    const card = document.querySelector('.beatport-hype-pick-card') as HTMLElement;
    expect(card.getAttribute('style')).toBeNull();
  });

  it('sets it when there is', () => {
    render(<BeatportHypePickCard release={{ title: 'x', image_url: 'http://a.jpg' }} />);
    const card = document.querySelector('.beatport-hype-pick-card') as HTMLElement;
    expect(card.style.getPropertyValue('--card-bg-image')).toBe("url('http://a.jpg')");
  });

  it("defaults its three lines, and the label's default is 'Hype Pick'", () => {
    // Not cosmetic: the vanilla's click handler reads this text back out as the
    // track's metadata, so these strings can reach the download engine.
    render(<BeatportHypePickCard release={{}} />);
    expect(document.querySelector('.beatport-hype-pick-title')?.textContent).toBe('Unknown Title');
    expect(document.querySelector('.beatport-hype-pick-artist')?.textContent).toBe(
      'Unknown Artist',
    );
    expect(document.querySelector('.beatport-hype-pick-label')?.textContent).toBe('Hype Pick');
  });

  it("alt-texts an untitled release as 'Release'", () => {
    render(<BeatportHypePickCard release={{ image_url: 'http://a.jpg' }} />);
    expect(document.querySelector('img')?.getAttribute('alt')).toBe('Release');
  });

  it('pads with a bare icon, no copy', () => {
    render(<BeatportHypePickPlaceholder />);
    expect(screen.getByText('🔥')).toBeInTheDocument();
    expect(document.querySelector('.beatport-hype-pick-info')).toBeNull();
  });
});

describe('the two chart cards', () => {
  const CHART = { name: 'Peak Hour', creator: 'DJ X', url: 'http://c', image: 'http://i.jpg' };

  it('uses its own class family and custom property per variant', () => {
    const { unmount } = render(<BeatportChartCard chart={CHART} variant="chart" />);
    let card = document.querySelector('.beatport-chart-card') as HTMLElement;
    expect(card.style.getPropertyValue('--chart-bg-image')).toBe("url('http://i.jpg')");
    expect(document.querySelector('.beatport-chart-name')?.textContent).toBe('Peak Hour');
    unmount();

    render(<BeatportChartCard chart={CHART} variant="dj" />);
    card = document.querySelector('.beatport-dj-card') as HTMLElement;
    // A different property, not the same one under a different class.
    expect(card.style.getPropertyValue('--dj-bg-image')).toBe("url('http://i.jpg')");
    expect(card.style.getPropertyValue('--chart-bg-image')).toBe('');
    expect(document.querySelector('.beatport-dj-creator')?.textContent).toBe('DJ X');
  });

  it('omits the style with no image', () => {
    render(<BeatportChartCard chart={{ name: 'n' }} variant="chart" />);
    expect(
      (document.querySelector('.beatport-chart-card') as HTMLElement).getAttribute('style'),
    ).toBeNull();
  });

  it('defaults a nameless chart and creator', () => {
    render(<BeatportChartCard chart={{}} variant="dj" />);
    expect(document.querySelector('.beatport-dj-name')?.textContent).toBe('Unknown Chart');
    expect(document.querySelector('.beatport-dj-creator')?.textContent).toBe('Unknown Creator');
  });

  it('empties data-url rather than dropping it', () => {
    render(<BeatportChartCard chart={{}} variant="chart" />);
    // The vanilla writes data-url="" and the click wiring tests for '' — a
    // missing attribute would read as null instead.
    expect(document.querySelector('.beatport-chart-card')?.getAttribute('data-url')).toBe('');
  });
});

describe('the card class names', () => {
  it('all exist in the vanilla stylesheet', () => {
    const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
    const required = [
      'beatport-rebuild-slide-background',
      'beatport-rebuild-slide-gradient',
      'beatport-rebuild-slide-content',
      'beatport-rebuild-track-info',
      'beatport-rebuild-track-title',
      'beatport-rebuild-artist-name',
      'beatport-rebuild-album-name',
      'beatport-release-card',
      'beatport-release-card-content',
      'beatport-release-artwork',
      'beatport-release-info',
      'beatport-release-title',
      'beatport-release-artist',
      'beatport-release-label',
      'beatport-release-placeholder',
      'beatport-hype-pick-card',
      'beatport-hype-pick-card-content',
      'beatport-hype-pick-artwork',
      'beatport-hype-pick-info',
      'beatport-hype-pick-title',
      'beatport-hype-pick-artist',
      'beatport-hype-pick-label',
      'beatport-hype-pick-placeholder',
      'beatport-chart-card',
      'beatport-chart-card-content',
      'beatport-chart-name',
      'beatport-chart-creator',
      'beatport-dj-card',
      'beatport-dj-card-content',
      'beatport-dj-name',
      'beatport-dj-creator',
      'placeholder-icon',
    ];
    for (const className of required) {
      expect(
        new RegExp(`\\.${className}[\\s,:{.[]`).test(css),
        `.${className} is not in static/style.css`,
      ).toBe(true);
    }
  });
});
