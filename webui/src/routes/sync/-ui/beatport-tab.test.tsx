/**
 * The Beatport tab — a COMPOSITION, so these tests are about composition:
 * which blocks are present, in what order, and what the three nav buttons are
 * wired to. Each section has its own tests; none of them can catch this file
 * putting them in the wrong order or handing a button the wrong argument.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BeatportGenre } from '../-beatport.api';

const openTop100 = vi.fn();

vi.mock('../-beatport.downloads', () => ({
  defaultBeatportDownloadEnv: () => ({ tag: 'env' }),
  openBeatportTop100: (variant: string, env: unknown) => openTop100(variant, env),
}));

/** Each section is stubbed to a marker: this file's job is order, not content. */
vi.mock('./beatport-sections', () => ({
  BeatportHeroSection: () => <div data-block="hero" />,
  BeatportNewReleasesSection: () => <div data-block="new-releases" />,
  BeatportHypePicksSection: () => <div data-block="hype-picks" />,
  BeatportFeaturedChartsSection: () => <div data-block="featured-charts" />,
  BeatportDJChartsSection: () => <div data-block="dj-charts" />,
}));

vi.mock('./beatport-top10', () => ({
  BeatportTop10Lists: () => <div data-block="top10-lists" />,
  BeatportTop10Releases: () => <div data-block="top10-releases" />,
}));

const GENRE = { name: 'Techno', slug: 'techno' } as BeatportGenre;

vi.mock('./genre-browser-modal', () => ({
  GenreBrowserModal: ({
    open,
    onClose,
    onSelectGenre,
    genreView,
  }: {
    open: boolean;
    onClose: () => void;
    onSelectGenre: (genre: BeatportGenre) => void;
    genreView?: React.ReactNode;
  }) =>
    open ? (
      <div data-testid="genre-modal">
        {genreView ?? (
          <button type="button" onClick={() => onSelectGenre(GENRE)}>
            pick-genre
          </button>
        )}
        <button type="button" onClick={onClose}>
          close-modal
        </button>
      </div>
    ) : null,
}));

vi.mock('./genre-page', () => ({
  GenrePage: ({ genre, onBack }: { genre: BeatportGenre; onBack: () => void }) => (
    <div data-testid="genre-page">
      {genre.name}
      <button type="button" onClick={onBack}>
        back-to-genres
      </button>
    </div>
  ),
}));

import { BeatportTab } from './beatport-tab';

beforeEach(() => {
  openTop100.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the pane', () => {
  it('renders every block in the markup order', () => {
    // index.html 2818-3085. A section stack in the wrong order looks plausible
    // in review and is wrong on screen; nothing else asserts it.
    const { container } = render(<BeatportTab />);
    const order = Array.from(container.querySelectorAll('[data-block]')).map((node) =>
      node.getAttribute('data-block'),
    );
    expect(order).toEqual([
      'hero',
      'top10-lists',
      'top10-releases',
      'new-releases',
      'hype-picks',
      'featured-charts',
      'dj-charts',
    ]);
  });

  it('keeps the download-bubble div, hidden, for the vanilla to paint into', () => {
    // registerBeatportDownload writes into a top-level `let` no module can
    // reach; without this div its bubbles have nowhere to render.
    const { container } = render(<BeatportTab />);
    const section = container.querySelector('#beatport-downloads-section');
    expect(section).not.toBeNull();
    expect(section?.className).toBe('artist-downloads-section');
    expect((section as HTMLElement).style.display).toBe('none');
  });

  it('does NOT render the dead inner tab strip or its two unreachable panes', () => {
    const { container } = render(<BeatportTab />);
    expect(container.querySelector('.beatport-tabs')).toBeNull();
    expect(container.querySelector('#beatport-browse-content')).toBeNull();
    expect(container.querySelector('#beatport-playlists-content')).toBeNull();
  });
});

describe('the three nav buttons', () => {
  it('sends each Top 100 button its OWN variant', () => {
    // Both buttons call the same function and differ only by this argument, so
    // a copy-paste gives you Beatport's chart under the Hype button.
    render(<BeatportTab />);

    fireEvent.click(screen.getByText('Beatport Top 100'));
    expect(openTop100).toHaveBeenCalledWith('beatport', { tag: 'env' });

    fireEvent.click(screen.getByText('Hype Top 100'));
    expect(openTop100).toHaveBeenLastCalledWith('hype', { tag: 'env' });
    expect(openTop100).toHaveBeenCalledTimes(2);
  });

  it('opens the genre browser, and neither Top 100 does', () => {
    render(<BeatportTab />);
    expect(screen.queryByTestId('genre-modal')).toBeNull();

    fireEvent.click(screen.getByText('Beatport Top 100'));
    expect(screen.queryByTestId('genre-modal')).toBeNull();

    fireEvent.click(screen.getByText('Browse by Genre'));
    expect(screen.getByTestId('genre-modal')).toBeTruthy();
    expect(openTop100).toHaveBeenCalledTimes(1);
  });
});

describe('the genre view', () => {
  it('swaps the grid for the genre page, and Back swaps it straight back', () => {
    render(<BeatportTab />);
    fireEvent.click(screen.getByText('Browse by Genre'));

    fireEvent.click(screen.getByText('pick-genre'));
    expect(screen.getByTestId('genre-page').textContent).toContain('Techno');

    fireEvent.click(screen.getByText('back-to-genres'));
    // Back returns to the GRID — the modal stays open (2745-2760).
    expect(screen.queryByTestId('genre-page')).toBeNull();
    expect(screen.getByTestId('genre-modal')).toBeTruthy();
  });

  it('closing from inside a genre does not leave that genre latched', () => {
    // Close clears both, or reopening the browser would land on the last genre
    // instead of the grid — a stale view that looks like the modal ignoring you.
    render(<BeatportTab />);
    fireEvent.click(screen.getByText('Browse by Genre'));
    fireEvent.click(screen.getByText('pick-genre'));
    fireEvent.click(screen.getByText('close-modal'));

    fireEvent.click(screen.getByText('Browse by Genre'));
    expect(screen.queryByTestId('genre-page')).toBeNull();
    expect(screen.getByText('pick-genre')).toBeTruthy();
  });
});
