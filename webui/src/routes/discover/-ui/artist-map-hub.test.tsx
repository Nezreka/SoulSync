import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ArtistMapHub } from './artist-map-hub';

/**
 * The hub is three cards and nothing else, so what matters is that the copy and
 * the class names survived the move — `style.css` still owns the look, and a
 * renamed class is an invisible regression until someone opens the page.
 */
const SHELL = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

afterEach(cleanup);

describe('ArtistMapHub', () => {
  function setup() {
    const onOpenWatchlist = vi.fn();
    const onOpenGenre = vi.fn();
    const onOpenExplorer = vi.fn();
    render(
      <ArtistMapHub
        onOpenWatchlist={onOpenWatchlist}
        onOpenGenre={onOpenGenre}
        onOpenExplorer={onOpenExplorer}
      />,
    );
    return { onOpenWatchlist, onOpenGenre, onOpenExplorer };
  }

  it('keeps the heading and subtitle', () => {
    setup();
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Artist Map');
    expect(screen.getByText('Explore the connections between your artists')).toBeInTheDocument();
  });

  it('keeps all three cards, in order, with their copy', () => {
    setup();
    const headings = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(['Watchlist', 'Genres', 'Explorer']);
    expect(
      screen.getByText('Your watched artists and their similar connections'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Artists clustered by genre across your library and cache'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Pick any artist and explore outward through their connections'),
    ).toBeInTheDocument();
  });

  it('keeps the class names style.css targets', () => {
    const { container } = render(
      <ArtistMapHub onOpenWatchlist={vi.fn()} onOpenGenre={vi.fn()} onOpenExplorer={vi.fn()} />,
    );
    for (const cls of [
      'artmap-hub',
      'artmap-hub-bg',
      'artmap-hub-content',
      'artmap-hub-header',
      'artmap-hub-icon',
      'artmap-hub-title',
      'artmap-hub-subtitle',
      'artmap-hub-cards',
      'artmap-hub-card',
      'artmap-hub-card-icon',
      'artmap-hub-card-text',
      'artmap-hub-card-arrow',
    ]) {
      expect(container.querySelector(`.${cls}`), cls).not.toBeNull();
      // …and the vanilla still uses the same one, so neither side drifted alone.
      expect(SHELL).toContain(cls);
    }
  });

  it('routes each card to its own opener', () => {
    const { onOpenWatchlist, onOpenGenre, onOpenExplorer } = setup();
    const [watchlist, genres, explorer] = screen.getAllByRole('button');
    fireEvent.click(watchlist);
    expect(onOpenWatchlist).toHaveBeenCalledTimes(1);
    expect(onOpenGenre).not.toHaveBeenCalled();
    expect(onOpenExplorer).not.toHaveBeenCalled();
    fireEvent.click(genres);
    expect(onOpenGenre).toHaveBeenCalledTimes(1);
    fireEvent.click(explorer);
    expect(onOpenExplorer).toHaveBeenCalledTimes(1);
  });

  it('makes the cards real buttons, which the vanilla divs were not', () => {
    // The vanilla used `<div onclick>`, so the cards were unreachable by keyboard
    // and invisible to a screen reader. Buttons cost nothing and fix both.
    setup();
    expect(screen.getAllByRole('button')).toHaveLength(3);
  });
});
