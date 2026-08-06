/**
 * The shared pool chrome, exercised directly — the markup both modals inherit
 * (stats-automations.js 1246-1305).
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PoolCategoryCard, PoolEmpty, PoolModal } from './pool-modal';

describe('PoolModal', () => {
  const shell = (over: Partial<Parameters<typeof PoolModal>[0]> = {}) => {
    const onClose = vi.fn();
    const onPlaylistFilter = vi.fn();
    render(
      <PoolModal
        id="test-pool-overlay"
        title="Test Pool"
        chips={<span className="playlist-track-count">7 things</span>}
        playlists={[
          { id: 3, name: 'Road Trip' },
          { id: 4, name: 'Chill' },
        ]}
        playlistFilter=""
        onPlaylistFilter={onPlaylistFilter}
        onClose={onClose}
        cards={<div data-testid="cards" />}
        list={null}
        {...over}
      />,
    );
    return { onClose, onPlaylistFilter };
  };

  it('renders the vanilla shell: header, chips, filter, grid, footer', () => {
    shell();
    expect(document.querySelector('#test-pool-overlay')!.className).toBe('modal-overlay');
    expect(screen.getByText('Test Pool')).toBeInTheDocument();
    expect(screen.getByText('7 things')).toBeInTheDocument();
    expect(document.querySelector('.modal-container.playlist-modal')).not.toBeNull();
    expect(document.querySelector('.pool-category-grid')).not.toBeNull();
    expect(document.querySelector('.pool-list-view')).toBeNull();
    expect(screen.getByText('Close')).toBeInTheDocument();
  });

  it('the filter lists All Playlists first and reports the raw value', () => {
    const { onPlaylistFilter } = shell();
    const options = [...document.querySelectorAll('.pool-playlist-filter option')];
    expect(options.map((o) => o.textContent)).toEqual(['All Playlists', 'Road Trip', 'Chill']);
    expect(options[0].getAttribute('value')).toBe('');
    fireEvent.change(document.querySelector('.pool-playlist-filter')!, { target: { value: '4' } });
    expect(onPlaylistFilter).toHaveBeenCalledWith('4');
  });

  it('the list view replaces the grid and wires back, title and search', () => {
    const onBack = vi.fn();
    const onQuery = vi.fn();
    shell({
      list: {
        title: 'Failed Tracks',
        query: 'gho',
        onQuery,
        onBack,
        children: <div data-testid="rows" />,
      },
    });
    expect(document.querySelector('.pool-category-grid')).toBeNull();
    expect(screen.getByText('Failed Tracks')).toBeInTheDocument();
    expect((document.querySelector('.pool-list-search') as HTMLInputElement).value).toBe('gho');
    expect(screen.getByTestId('rows')).toBeInTheDocument();
    fireEvent.change(document.querySelector('.pool-list-search')!, { target: { value: 'x' } });
    expect(onQuery).toHaveBeenCalledWith('x');
    fireEvent.click(screen.getByText('← Back'));
    expect(onBack).toHaveBeenCalled();
  });

  it('only the backdrop closes — the ×, Close and the backdrop, not the body', () => {
    const { onClose } = shell();
    fireEvent.click(document.querySelector('.modal-container')!);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(document.querySelector('#test-pool-overlay')!);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(document.querySelector('.playlist-modal-close')!);
    expect(onClose).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByText('Close'));
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});

describe('PoolCategoryCard', () => {
  it('carries its tone onto the card, count and top bar', () => {
    const onOpen = vi.fn();
    render(
      <PoolCategoryCard
        tone="failed"
        icon="⚠"
        count={3}
        label="tracks need attention"
        onOpen={onOpen}
      />,
    );
    expect(document.querySelector('.pool-category-card')!.className).toBe(
      'pool-category-card failed',
    );
    expect(document.querySelector('.pool-category-count')!.className).toBe(
      'pool-category-count failed',
    );
    expect(document.querySelector('.pool-category-top-bar')!.className).toBe(
      'pool-category-top-bar failed',
    );
    expect(document.querySelector('.pool-category-fallback')!.className).toBe(
      'pool-category-fallback failed',
    );
    expect(screen.getByText('⚠')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    fireEvent.click(document.querySelector('.pool-category-card')!);
    expect(onOpen).toHaveBeenCalled();
  });

  it('a mosaic REPLACES the flat gradient, with per-row speed, delay and direction', () => {
    render(
      <PoolCategoryCard
        tone="matched"
        icon="✓"
        count={9}
        label="cached matches"
        backgroundId="pool-matched-bg"
        onOpen={vi.fn()}
        mosaic={[
          { scrollRight: false, speedSeconds: 25, delaySeconds: 0, tiles: ['a.jpg', 'b.jpg'] },
          { scrollRight: true, speedSeconds: 30, delaySeconds: 0.15, tiles: ['c.jpg'] },
        ]}
      />,
    );
    expect(document.querySelector('.pool-category-fallback')).toBeNull();
    const bg = document.querySelector('.wishlist-mosaic-background') as HTMLElement;
    expect(bg.id).toBe('pool-matched-bg');
    const rows = [...bg.querySelectorAll('.wishlist-mosaic-row')] as HTMLElement[];
    expect(rows).toHaveLength(2);
    expect(rows[0].className).toBe('wishlist-mosaic-row');
    expect(rows[1].className).toBe('wishlist-mosaic-row scroll-right');
    expect(rows[0].style.getPropertyValue('--speed')).toBe('25s');
    expect(rows[1].style.animationDelay).toBe('0.15s');
    expect(rows[0].querySelectorAll('.wishlist-mosaic-tile')).toHaveLength(2);
    // jsdom normalises the quote style, so compare on the url itself.
    expect(
      (rows[0].querySelector('.wishlist-mosaic-image') as HTMLElement).style.backgroundImage,
    ).toBe('url("a.jpg")');
  });
});

describe('PoolEmpty', () => {
  it('is the vanilla’s .pool-empty div', () => {
    render(<PoolEmpty>No failed tracks match your filter.</PoolEmpty>);
    expect(document.querySelector('.pool-empty')!.textContent).toBe(
      'No failed tracks match your filter.',
    );
  });
});
