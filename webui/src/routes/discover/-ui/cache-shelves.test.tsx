import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CacheItem } from '../-discover.cache-sections';
import type { CacheShelfProps } from './cache-shelves';

import { CACHE_SECTIONS } from '../-discover.cache-sections';
import { CacheShelf, GenreExplorerSection } from './cache-shelves';

afterEach(cleanup);

const item = (i: number, over: Partial<CacheItem> = {}): CacheItem => ({
  name: `Album ${i}`,
  artist_name: `Artist ${i}`,
  image_url: `/img/${i}.jpg`,
  ...over,
});

function props(over: Partial<CacheShelfProps> = {}): CacheShelfProps {
  return {
    def: CACHE_SECTIONS[0], // cache-undiscovered
    items: [item(0), item(1, { in_library: true })],
    expanded: false,
    onToggleExpand: vi.fn(),
    onOpenItem: vi.fn(),
    ...over,
  };
}

describe('cache shelves', () => {
  it('renders nothing at all for an empty section — no titled empty box', () => {
    const { container } = render(<CacheShelf {...props({ items: [] })} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders each section under its own id with the eyebrow ABOVE an h3', () => {
    for (const def of CACHE_SECTIONS) {
      const { container, unmount } = render(<CacheShelf {...props({ def })} />);
      const section = container.querySelector(`#${def.id}.discover-section`)!;
      const header = section.querySelector('.discover-section-header > div')!;
      expect(header.children[0]).toHaveClass('discover-section-subtitle');
      expect(header.children[0].textContent).toBe(def.subtitle);
      expect(header.children[1].tagName).toBe('H3');
      expect(header.children[1].textContent).toBe(def.title);
      unmount();
    }
  });

  it('renders the unified album card, owned tick only where in_library', () => {
    const p = props();
    const { container } = render(<CacheShelf {...p} />);
    const cards = [...container.querySelectorAll('.ya-card.discover-album-card')];
    expect(cards).toHaveLength(2);
    expect(cards[0].querySelector('.ya-card-name')!.textContent).toBe('Album 0');
    expect(cards[0].querySelector('.ya-card-sub')!.textContent).toBe('Artist 0');
    expect(cards[0].querySelector('.discover-album-badge')).toBeNull();
    expect(cards[1].querySelector('.discover-album-badge.owned')!.textContent).toBe('✓');
    fireEvent.click(cards[1]);
    expect(p.onOpenItem).toHaveBeenCalledExactlyOnceWith('undiscovered', 1);
  });

  it('falls back to the placeholder cover for artless items', () => {
    const { container } = render(
      <CacheShelf {...props({ items: [item(0, { image_url: '' })] })} />,
    );
    expect(container.querySelector('.ya-card-img img')).toHaveAttribute(
      'src',
      '/static/placeholder-album.png',
    );
  });

  it('clamps at 12 by hiding cards, with the sibling Show all toggle', () => {
    const many = Array.from({ length: 15 }, (_, i) => item(i));
    const p = props({ items: many });
    const { container, rerender } = render(<CacheShelf {...p} />);
    const wrappers = () =>
      [...container.querySelector('.discover-grid')!.children] as HTMLElement[];
    expect(wrappers()).toHaveLength(15); // hidden, not dropped
    expect(wrappers().filter((w) => w.style.display !== 'none')).toHaveLength(12);
    const toggle = container.querySelector('.discover-show-all')!;
    // A SIBLING of the grid, not a 16th child inside it.
    expect(toggle.parentElement).toBe(container.querySelector('.discover-grid')!.parentElement);
    expect(toggle.textContent).toBe('Show all 15');
    fireEvent.click(toggle);
    expect(p.onToggleExpand).toHaveBeenCalledOnce();

    rerender(<CacheShelf {...p} expanded={true} />);
    expect(wrappers().filter((w) => w.style.display !== 'none')).toHaveLength(15);
    expect(container.querySelector('.discover-show-all')!.textContent).toBe('Show less');
  });

  it('shows no toggle at or below the limit', () => {
    const { container } = render(
      <CacheShelf {...props({ items: Array.from({ length: 12 }, (_, i) => item(i)) })} />,
    );
    expect(container.querySelector('.discover-show-all')).toBeNull();
  });
});

describe('genre explorer', () => {
  const genres = [
    { genre: 'idm', explored: true, artist_count: 1 },
    { genre: 'breakcore', explored: false, artist_count: 12 },
  ];

  it('renders nothing with no genres', () => {
    const { container } = render(<GenreExplorerSection genres={[]} onOpenGenre={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders pills in a BARE genre-explorer-grid — no discover-grid, no clamp', () => {
    const onOpen = vi.fn();
    const { container } = render(<GenreExplorerSection genres={genres} onOpenGenre={onOpen} />);
    const section = container.querySelector('#cache-genre-explorer')!;
    expect(section.querySelector('.discover-grid')).toBeNull();
    expect(section.querySelector('.discover-show-all')).toBeNull();
    const pills = [...section.querySelectorAll('.genre-explorer-grid .genre-explorer-pill')];
    expect(pills[0]).toHaveClass('explored');
    expect(pills[1]).toHaveClass('unexplored');
    expect(pills[0].querySelector('.genre-pill-count')!.textContent).toBe('1 artist');
    expect(pills[1].querySelector('.genre-pill-count')!.textContent).toBe('12 artists');
    // "New" is the inverse of explored.
    expect(pills[0].querySelector('.genre-pill-badge')).toBeNull();
    expect(pills[1].querySelector('.genre-pill-badge')!.textContent).toBe('New');
    fireEvent.click(pills[1]);
    expect(onOpen).toHaveBeenCalledExactlyOnceWith('breakcore');
  });

  it('titles itself Browse Your Sound with the tap hint', () => {
    render(<GenreExplorerSection genres={genres} onOpenGenre={vi.fn()} />);
    expect(screen.getByText('Browse Your Sound').tagName).toBe('H3');
    expect(screen.getByText('Every genre in your collection, one tap deep')).toHaveClass(
      'discover-section-subtitle',
    );
  });
});
