import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { YourArtist } from '../-discover.your-artists';
import type { YourAlbum, YourAlbumsShelfProps } from './your-albums-shelf';
import type { YourArtistsShelfProps } from './your-artists-shelf';

import { YourAlbumsShelf } from './your-albums-shelf';
import { ArrowIcon, GearIcon, RefreshIcon, YourArtistsShelf } from './your-artists-shelf';

/**
 * The two owned-library shelves.
 *
 * The artist card carries two different marks that look interchangeable: the
 * BADGES mean "there is an id for this provider" in a fixed order, and the
 * ORIGIN DOTS mean "this artist came from this service". Most of the cases here
 * are about keeping those apart, and about the image fallback CHAIN, which tries
 * Deezer before it gives up.
 */

afterEach(cleanup);

// ── The shared header icons ──────────────────────────────────────────────────

describe('the shared header icons', () => {
  // Exported because the albums shelf reuses them. Transcribed SVG paths: a
  // wrong `d` is a silently wrong glyph that no behavioural test would notice.
  it.each([
    [RefreshIcon, 'M23 4v6h-6'],
    [GearIcon, 'M19.4 15a1.65 1.65 0 0 0 .33 1.82'],
    [ArrowIcon, 'M12 5l7 7-7 7'],
  ])('draws its own path', (Icon, path) => {
    const { container } = render(<Icon />);
    const paths = [...container.querySelectorAll('path')].map((p) => p.getAttribute('d') ?? '');
    expect(paths.some((d) => d.startsWith(path))).toBe(true);
  });
});

// ── Your Artists ─────────────────────────────────────────────────────────────

const artist = (over: Partial<YourArtist> = {}): YourArtist => ({
  id: 7,
  artist_name: 'Aphex Twin',
  image_url: '/img/aphex.jpg',
  spotify_artist_id: 'sp1',
  source_services: ['spotify', 'lastfm'],
  active_source: 'spotify',
  ...over,
});

function artistProps(over: Partial<YourArtistsShelfProps> = {}): YourArtistsShelfProps {
  return {
    artists: [artist()],
    loaded: true,
    subtitle: 'Artists you follow across your music services',
    logos: { spotify: '/logo/spotify.png' },
    buildDetailPath: (id, source) => `/artist-detail/${source}/${id}`,
    onRefresh: vi.fn(),
    onConfigureSources: vi.fn(),
    onViewAll: vi.fn(),
    onOpenInfo: vi.fn(),
    onToggleWatchlist: vi.fn(),
    ...over,
  };
}

describe('Your Artists', () => {
  it('renders the shelf with its carousel id', () => {
    const { container } = render(<YourArtistsShelf {...artistProps()} />);
    expect(container.querySelector('#your-artists-section')).not.toBeNull();
    expect(container.querySelector('#your-artists-carousel')).not.toBeNull();
    expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
  });

  it('shows a source badge per known id, in the fixed order', () => {
    // The order does NOT follow the active source — the badges say "we have an
    // id here", so it is stable regardless of which one is in use.
    const { container } = render(
      <YourArtistsShelf
        {...artistProps({
          artists: [
            artist({
              active_source: 'deezer',
              deezer_artist_id: 'dz1',
              itunes_artist_id: 'it1',
            }),
          ],
        })}
      />,
    );
    const titles = [...container.querySelectorAll('.ya-badge')].map((b) => b.getAttribute('title'));
    expect(titles).toEqual(['Spotify', 'Apple Music', 'Deezer']);
  });

  it('falls back to a two-letter chip when a logo will not load', () => {
    const { container } = render(<YourArtistsShelf {...artistProps()} />);
    const img = container.querySelector('.ya-badge img')!;
    fireEvent.error(img);
    expect(container.querySelector('.ya-badge span')!.textContent).toBe('SP');
  });

  it('uses the chip immediately when there is no logo url at all', () => {
    const { container } = render(<YourArtistsShelf {...artistProps({ logos: {} })} />);
    expect(container.querySelector('.ya-badge img')).toBeNull();
    expect(container.querySelector('.ya-badge span')!.textContent).toBe('SP');
  });

  it('colours one origin dot per source service', () => {
    const { container } = render(<YourArtistsShelf {...artistProps()} />);
    const dots = [...container.querySelectorAll('.ya-origin-dot')] as HTMLElement[];
    expect(dots).toHaveLength(2);
    expect(dots[0].style.background).toBe('rgb(29, 185, 84)'); //  spotify
    expect(dots[1].style.background).toBe('rgb(213, 16, 7)'); //  last.fm
    expect(dots[0].getAttribute('title')).toBe('From spotify');
  });

  it('falls back to grey for a service with no assigned colour', () => {
    const { container } = render(
      <YourArtistsShelf {...artistProps({ artists: [artist({ source_services: ['qobuz'] })] })} />,
    );
    expect((container.querySelector('.ya-origin-dot') as HTMLElement).style.background).toBe(
      'rgb(102, 102, 102)',
    );
  });

  it('retries a dead image against Deezer before giving up', () => {
    // A stale Spotify url is the common case, and Deezer will usually still
    // serve a picture for the same artist — one retry saves most of them.
    const { container } = render(
      <YourArtistsShelf {...artistProps({ artists: [artist({ deezer_artist_id: '1234' })] })} />,
    );
    const img = () => container.querySelector('.ya-card-img img') as HTMLImageElement | null;
    fireEvent.error(img()!);
    expect(img()!.src).toContain('api.deezer.com/artist/1234/image');

    fireEvent.error(img()!);
    expect(img()).toBeNull();
    expect(container.querySelector('.ya-card-placeholder')).toBeInTheDocument();
  });

  it('gives up immediately when there is no Deezer id to retry with', () => {
    const { container } = render(<YourArtistsShelf {...artistProps()} />);
    fireEvent.error(container.querySelector('.ya-card-img img')!);
    expect(container.querySelector('.ya-card-img img')).toBeNull();
  });

  it('links the name to the resolved detail source', () => {
    const { container } = render(<YourArtistsShelf {...artistProps()} />);
    expect(container.querySelector('a.ya-card-name')).toHaveAttribute(
      'href',
      '/artist-detail/spotify/sp1',
    );
  });

  it('renders an unlinked name when nothing resolves', () => {
    const { container } = render(
      <YourArtistsShelf
        {...artistProps({
          artists: [artist({ spotify_artist_id: undefined, active_source: undefined })],
        })}
      />,
    );
    expect(container.querySelector('a.ya-card-name')).toBeNull();
    expect(container.querySelector('div.ya-card-name')!.textContent).toBe('Aphex Twin');
  });

  it('opens the info modal on the card, but not on a card that resolves nothing', () => {
    const p = artistProps();
    const { container, rerender } = render(<YourArtistsShelf {...p} />);
    fireEvent.click(container.querySelector('.ya-card')!);
    expect(p.onOpenInfo).toHaveBeenCalledWith(artist());

    const p2 = artistProps({
      artists: [artist({ spotify_artist_id: undefined, active_source: undefined })],
    });
    rerender(<YourArtistsShelf {...p2} />);
    fireEvent.click(container.querySelector('.ya-card')!);
    // A dead card that swallows the click is worse than an inert one.
    expect(p2.onOpenInfo).not.toHaveBeenCalled();
  });

  it('toggles the watchlist WITHOUT also opening the info modal', () => {
    // Without stopPropagation the card's own handler fires too, and the modal
    // lands on top of the toggle the user just pressed.
    const p = artistProps();
    const { container } = render(<YourArtistsShelf {...p} />);
    fireEvent.click(container.querySelector('.ya-watchlist-btn')!);
    expect(p.onToggleWatchlist).toHaveBeenCalledWith(artist());
    expect(p.onOpenInfo).not.toHaveBeenCalled();
  });

  it('does not open the info modal when the NAME link is followed', () => {
    const p = artistProps();
    const { container } = render(<YourArtistsShelf {...p} />);
    fireEvent.click(container.querySelector('a.ya-card-name')!);
    expect(p.onOpenInfo).not.toHaveBeenCalled();
  });

  it('marks and titles a watched artist', () => {
    const { container, rerender } = render(<YourArtistsShelf {...artistProps()} />);
    expect(container.querySelector('.ya-watchlist-btn')).not.toHaveClass('active');
    expect(container.querySelector('.ya-watchlist-btn')).toHaveAttribute(
      'title',
      'Add to watchlist',
    );
    rerender(<YourArtistsShelf {...artistProps({ artists: [artist({ on_watchlist: true })] })} />);
    expect(container.querySelector('.ya-watchlist-btn')).toHaveClass('active');
    expect(container.querySelector('.ya-watchlist-btn')).toHaveAttribute('title', 'On watchlist');
  });

  it('wires the three header buttons', () => {
    const p = artistProps();
    render(<YourArtistsShelf {...p} />);
    fireEvent.click(screen.getByLabelText('Refresh from services'));
    fireEvent.click(screen.getByLabelText('Configure sources'));
    fireEvent.click(screen.getByText('View All'));
    expect(p.onRefresh).toHaveBeenCalled();
    expect(p.onConfigureSources).toHaveBeenCalled();
    expect(p.onViewAll).toHaveBeenCalled();
  });

  it('cannot fire a second refresh while one is running', () => {
    render(<YourArtistsShelf {...artistProps({ refreshing: true })} />);
    expect(screen.getByLabelText('Refresh from services')).toBeDisabled();
  });
});

// ── Your Albums ──────────────────────────────────────────────────────────────

const album = (over: Partial<YourAlbum> = {}): YourAlbum => ({
  id: 1,
  album_name: 'Selected Ambient Works',
  artist_name: 'Aphex Twin',
  image_url: '/img/saw.jpg',
  in_library: false,
  ...over,
});

function albumProps(over: Partial<YourAlbumsShelfProps> = {}): YourAlbumsShelfProps {
  return {
    albums: [album()],
    total: 1,
    page: 1,
    loaded: true,
    subtitle: '1 albums · 0 owned · 1 missing',
    query: '',
    status: 'all',
    sort: 'artist_name',
    canDownloadMissing: false,
    onRefresh: vi.fn(),
    onConfigureSources: vi.fn(),
    onDownloadMissing: vi.fn(),
    onQueryChange: vi.fn(),
    onStatusChange: vi.fn(),
    onSortChange: vi.fn(),
    onPrevPage: vi.fn(),
    onNextPage: vi.fn(),
    onOpenAlbum: vi.fn(),
    ...over,
  };
}

describe('Your Albums', () => {
  it('renders the grid, filters and the SHARED album card', () => {
    // The same .ya-card the other album shelves use (1467) — the first draft
    // invented a .spotify-album-card family that exists in no stylesheet.
    const { container } = render(<YourAlbumsShelf {...albumProps()} />);
    expect(container.querySelector('#your-albums-grid')).not.toBeNull();
    expect(container.querySelector('#your-albums-filters')).not.toBeNull();
    const card = container.querySelector('.ya-card.discover-album-card')!;
    expect(card).not.toBeNull();
    expect(card.querySelector('.ya-card-name')!.textContent).toBe('Selected Ambient Works');
    expect(card.querySelector('.ya-card-sub')!.textContent).toBe('Aphex Twin');
    // Titled "Album — Artist" for the hover tooltip (1468).
    expect(card).toHaveAttribute('title', 'Selected Ambient Works — Aphex Twin');
  });

  it('badges owned and missing differently, on the shared badge element', () => {
    const { container, rerender } = render(<YourAlbumsShelf {...albumProps()} />);
    const badge = () => container.querySelector('.discover-album-badge')!;
    expect(badge()).toHaveClass('missing');
    expect(badge().textContent).toBe('↓');
    rerender(<YourAlbumsShelf {...albumProps({ albums: [album({ in_library: true })] })} />);
    expect(badge()).toHaveClass('owned');
    expect(badge().textContent).toBe('✓');
  });

  it('falls back to the placeholder cover', () => {
    const { container } = render(
      <YourAlbumsShelf {...albumProps({ albums: [album({ image_url: undefined })] })} />,
    );
    expect(container.querySelector('.ya-card-img img')).toHaveAttribute(
      'src',
      '/static/placeholder-album.png',
    );
  });

  it('shows a spinner instead of an empty grid while loading', () => {
    const { container } = render(<YourAlbumsShelf {...albumProps({ loading: true })} />);
    expect(container.querySelector('.loading-spinner')).not.toBeNull();
    expect(container.querySelector('.discover-album-card')).toBeNull();
  });

  it('keeps the SECTION when a filter matches nothing, and says so in the grid', () => {
    // count comes from the TOTAL: hiding the whole section — filters, stats and
    // all — because a filter matched nothing would strand the user with no way
    // to clear it. The vanilla keeps the section and puts the message in the
    // grid (1457-1459).
    const { container } = render(<YourAlbumsShelf {...albumProps({ albums: [], total: 120 })} />);
    expect(container.querySelector('#your-albums-section')).not.toBeNull();
    expect(container.querySelector('.spotify-library-empty p')!.textContent).toBe(
      'No albums found',
    );
  });

  it('reports filter, status and sort changes', () => {
    const p = albumProps();
    const { container } = render(<YourAlbumsShelf {...p} />);
    fireEvent.change(container.querySelector('#your-albums-search')!, {
      target: { value: 'ambient' },
    });
    fireEvent.change(container.querySelector('#your-albums-status-filter')!, {
      target: { value: 'missing' },
    });
    fireEvent.change(container.querySelector('#your-albums-sort')!, {
      target: { value: 'release_date' },
    });
    expect(p.onQueryChange).toHaveBeenCalledWith('ambient');
    expect(p.onStatusChange).toHaveBeenCalledWith('missing');
    expect(p.onSortChange).toHaveBeenCalledWith('release_date');
  });

  it('hides the pager entirely when everything fits on one page', () => {
    const { container } = render(<YourAlbumsShelf {...albumProps({ total: 48 })} />);
    expect(container.querySelector('#your-albums-pagination')).toBeNull();
  });

  it('renders the pager with the vanilla classes, range and arrows', () => {
    const { container, rerender } = render(
      <YourAlbumsShelf {...albumProps({ total: 130, page: 1 })} />,
    );
    const btns = () =>
      [...container.querySelectorAll('.spotify-library-page-btn')] as HTMLButtonElement[];
    expect(btns()[0].textContent).toBe('← Previous');
    expect(btns()[1].textContent).toBe('Next →');
    expect(container.querySelector('.spotify-library-page-info')!.textContent).toBe(
      '1–48 of 130', //  48 to a page
    );
    expect(btns()[0].disabled).toBe(true);
    expect(btns()[1].disabled).toBe(false);

    rerender(<YourAlbumsShelf {...albumProps({ total: 130, page: 3 })} />);
    expect(container.querySelector('.spotify-library-page-info')!.textContent).toBe(
      '97–130 of 130', //  the last of 3 pages
    );
    expect(btns()[1].disabled).toBe(true);
    expect(btns()[0].disabled).toBe(false);
  });

  it('steps pages', () => {
    const p = albumProps({ total: 130, page: 2 });
    render(<YourAlbumsShelf {...p} />);
    fireEvent.click(screen.getByText('← Previous'));
    fireEvent.click(screen.getByText('Next →'));
    expect(p.onPrevPage).toHaveBeenCalled();
    expect(p.onNextPage).toHaveBeenCalled();
  });

  it('offers Download Missing only when something is missing', () => {
    const { rerender } = render(<YourAlbumsShelf {...albumProps()} />);
    expect(screen.queryByLabelText('Download missing albums')).toBeNull();
    rerender(<YourAlbumsShelf {...albumProps({ canDownloadMissing: true })} />);
    fireEvent.click(screen.getByLabelText('Download missing albums'));
    expect(screen.getByLabelText('Download missing albums')).toBeInTheDocument();
  });

  it('opens an album BY INDEX', () => {
    // The download flow resolves the album from the module list (1468).
    const p = albumProps({ albums: [album(), album({ id: 2, album_name: 'Drukqs' })], total: 2 });
    const { container } = render(<YourAlbumsShelf {...p} />);
    fireEvent.click(container.querySelectorAll('.discover-album-card')[1]);
    expect(p.onOpenAlbum).toHaveBeenCalledWith(1);
  });
});
