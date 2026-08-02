import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RecentAlbum } from '../-discover.recent-releases';
import type { SeasonalAlbum, SeasonData } from '../-discover.seasonal';

import { DiscoverAlbumCard, RecentReleasesShelf, SeasonalAlbumsShelf } from './album-shelves';

/**
 * The two album shelves.
 *
 * Their cards are byte-identical in the vanilla apart from the handler, so they
 * are one component here. The cases that matter are the ones where the shelves
 * genuinely differ: Recent Releases stays and explains when empty, Seasonal
 * disappears completely out of season.
 */

afterEach(cleanup);

const recent = (over: Partial<RecentAlbum> = {}): RecentAlbum => ({
  album_name: 'Syro',
  artist_name: 'Aphex Twin',
  album_cover_url: '/img/syro.jpg',
  ...over,
});

// ── The shared card ──────────────────────────────────────────────────────────

describe('the album card', () => {
  const card = (over: Partial<Parameters<typeof DiscoverAlbumCard>[0]> = {}) => ({
    cover: '/img/syro.jpg',
    albumName: 'Syro',
    artistName: 'Aphex Twin',
    onOpen: vi.fn(),
    ...over,
  });

  it('shows the cover, with the album as the NAME and the artist beneath', () => {
    const { container } = render(<DiscoverAlbumCard {...card()} />);
    expect(container.querySelector('.ya-card-img img')).toHaveAttribute('src', '/img/syro.jpg');
    // Which line holds which matters: swapped, both strings are still on the
    // card and it reads as an album called "Aphex Twin".
    expect(container.querySelector('.ya-card-name')!.textContent).toBe('Syro');
    expect(container.querySelector('.ya-card-sub')!.textContent).toBe('Aphex Twin');
  });

  it('hides the placeholder until the cover actually fails', () => {
    const { container } = render(<DiscoverAlbumCard {...card()} />);
    const placeholder = () => container.querySelector('.ya-card-placeholder') as HTMLElement;
    expect(placeholder().style.display).toBe('none');

    fireEvent.error(container.querySelector('.ya-card-img img')!);
    expect(container.querySelector('.ya-card-img img')).toBeNull();
    expect(placeholder().style.display).toBe('');
  });

  it('opens on click', () => {
    const p = card();
    const { container } = render(<DiscoverAlbumCard {...p} />);
    fireEvent.click(container.querySelector('.discover-album-card')!);
    expect(p.onOpen).toHaveBeenCalled();
  });
});

// ── Recent Releases ──────────────────────────────────────────────────────────

describe('Recent Releases', () => {
  it('renders its grid and cards', () => {
    const { container } = render(
      <RecentReleasesShelf albums={[recent()]} loaded onOpenAlbum={vi.fn()} />,
    );
    expect(container.querySelector('#recent-releases')).not.toBeNull();
    expect(container.querySelector('#recent-releases-carousel')).not.toBeNull();
    expect(container.querySelectorAll('.discover-album-card')).toHaveLength(1);
  });

  it('stays and explains when there is nothing new', () => {
    // This shelf is not hide-when-empty: "no recent releases" is information.
    render(<RecentReleasesShelf albums={[]} loaded onOpenAlbum={vi.fn()} />);
    expect(screen.getByText('No recent releases found')).toBeInTheDocument();
  });

  it('falls back to the placeholder cover', () => {
    const { container } = render(
      <RecentReleasesShelf
        albums={[recent({ album_cover_url: undefined })]}
        loaded
        onOpenAlbum={vi.fn()}
      />,
    );
    expect(container.querySelector('.ya-card-img img')).toHaveAttribute(
      'src',
      '/static/placeholder-album.png',
    );
  });

  it('opens an album BY INDEX', () => {
    // The download modal resolves the album out of the module's own list, and
    // several of these have no id at all until the source is queried.
    const onOpenAlbum = vi.fn();
    const { container } = render(
      <RecentReleasesShelf
        albums={[recent(), recent({ album_name: 'Drukqs' })]}
        loaded
        onOpenAlbum={onOpenAlbum}
      />,
    );
    fireEvent.click(container.querySelectorAll('.discover-album-card')[1]);
    expect(onOpenAlbum).toHaveBeenCalledWith(1);
  });
});

// ── Seasonal Albums ──────────────────────────────────────────────────────────

describe('Seasonal Albums', () => {
  const season = (over: Partial<SeasonData> = {}): SeasonData => ({
    success: true,
    season: 'winter',
    name: 'Winter',
    icon: '❄️',
    description: 'Cold-weather listening',
    ...over,
  });

  const album = (over: Partial<SeasonalAlbum> = {}): SeasonalAlbum => ({
    album_name: 'Music Has the Right to Children',
    artist_name: 'Boards of Canada',
    album_cover_url: '/img/mhtrtc.jpg',
    ...over,
  });

  it('titles itself from the season', () => {
    render(
      <SeasonalAlbumsShelf season={season()} albums={[album()]} loaded onOpenAlbum={vi.fn()} />,
    );
    expect(screen.getByText('❄️ Winter')).toBeInTheDocument();
    expect(screen.getByText('Cold-weather listening')).toBeInTheDocument();
  });

  it('disappears entirely out of season', () => {
    // Not an empty state — there is nothing seasonal in March, and a titled box
    // saying so is noise. A response can SUCCEED and carry no season.
    const { container, rerender } = render(
      <SeasonalAlbumsShelf season={null} albums={[]} loaded onOpenAlbum={vi.fn()} />,
    );
    expect(container.querySelector('.discover-section')).toBeNull();

    rerender(
      <SeasonalAlbumsShelf
        season={season({ season: undefined })}
        albums={[album()]}
        loaded
        onOpenAlbum={vi.fn()}
      />,
    );
    expect(container.querySelector('.discover-section')).toBeNull();
  });

  it('stays and explains when the season has no albums', () => {
    render(<SeasonalAlbumsShelf season={season()} albums={[]} loaded onOpenAlbum={vi.fn()} />);
    expect(screen.getByText('No seasonal albums found')).toBeInTheDocument();
  });

  it('renders its own carousel id', () => {
    const { container } = render(
      <SeasonalAlbumsShelf season={season()} albums={[album()]} loaded onOpenAlbum={vi.fn()} />,
    );
    expect(container.querySelector('#seasonal-albums-carousel')).not.toBeNull();
    // Not the recent shelf's — two grids sharing one id breaks both.
    expect(container.querySelector('#recent-releases-carousel')).toBeNull();
  });

  it('opens a seasonal album by index', () => {
    const onOpenAlbum = vi.fn();
    const { container } = render(
      <SeasonalAlbumsShelf
        season={season()}
        albums={[album(), album({ album_name: 'Geogaddi' })]}
        loaded
        onOpenAlbum={onOpenAlbum}
      />,
    );
    fireEvent.click(container.querySelectorAll('.discover-album-card')[1]);
    expect(onOpenAlbum).toHaveBeenCalledWith(1);
  });

  it('falls back to the placeholder cover', () => {
    const { container } = render(
      <SeasonalAlbumsShelf
        season={season()}
        albums={[album({ album_cover_url: undefined })]}
        loaded
        onOpenAlbum={vi.fn()}
      />,
    );
    expect(container.querySelector('.ya-card-img img')).toHaveAttribute(
      'src',
      '/static/placeholder-album.png',
    );
  });
});
