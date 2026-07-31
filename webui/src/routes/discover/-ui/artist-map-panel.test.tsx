import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ArtMapIsland, ArtMapNode } from '../-discover.artist-map';
import type { ArtMapArtistCard, ArtMapPanelModel } from '../-discover.artist-map.panel';

import { artMap } from '../-discover.artist-map';
import { artMapArtistCard, artMapPanelModel, miniStat } from '../-discover.artist-map.panel';
import { ArtMapPanel } from './artist-map-panel';

/**
 * The panel renders models that are already differentially tested against the
 * vanilla, so these cases are about the RENDER: that the two bodies swap
 * correctly, that the coverage bar reflects the model, and that a cached bitmap
 * wins over the image url.
 */

afterEach(() => {
  cleanup();
  artMap.images = {};
});

const node = (over: Partial<ArtMapNode> = {}): ArtMapNode =>
  ({
    id: 1,
    name: 'Aphex Twin',
    x: 0,
    y: 0,
    radius: 40,
    opacity: 1,
    type: 'similar',
    image_url: '',
    genres: [],
    popularity: 50,
    _hue: 200,
    _island: 'Rock',
    ...over,
  }) as ArtMapNode;

function model(over: Partial<ArtMapPanelModel> = {}): ArtMapPanelModel {
  return {
    title: 'Watchlist Map',
    island: null,
    hue: 270,
    scopeTotal: 12,
    scopeWatch: 3,
    coveragePct: 25,
    stats: [miniStat('Artists', 12, 270), miniStat('Watchlist', 3), miniStat('Genres', 4)],
    topArtists: [],
    ...over,
  };
}

function props(over: Partial<Parameters<typeof ArtMapPanel>[0]> = {}) {
  return {
    model: model(),
    card: null,
    isMobile: false,
    open: false,
    onSelectArtist: vi.fn(),
    onBackToList: vi.fn(),
    onCloseSheet: vi.fn(),
    onToggleWatch: vi.fn(),
    onExplore: vi.fn(),
    onOpenDetails: vi.fn(),
    buildArtistDetailPath: (id: string, source: string) => `/artist-detail/${source}/${id}`,
    ...over,
  };
}

describe('the dashboard', () => {
  it('shows the map title and the Overview heading with no island', () => {
    render(<ArtMapPanel {...props()} />);
    expect(screen.getByText('Watchlist Map')).toBeInTheDocument();
    expect(screen.getByText('Overview')).toBeInTheDocument();
  });

  it('names the focused island instead, tinted by its hue', () => {
    const island: ArtMapIsland = { name: 'hip hop', cx: 0, cy: 0, r: 1, hue: 42, count: 9 };
    const { container, rerender } = render(
      <ArtMapPanel {...props({ model: model({ island, hue: 42 }) })} />,
    );
    expect(screen.getByText('hip hop')).toBeInTheDocument();
    const warm = (container.querySelector('.artmap-panel-heading') as HTMLElement).style.color;

    // jsdom normalises hsl() to rgb(), so assert the BEHAVIOUR — the heading is
    // tinted, and a different island hue tints it differently — rather than the
    // serialisation, which would pin a jsdom implementation detail.
    rerender(
      <ArtMapPanel {...props({ model: model({ island: { ...island, hue: 200 }, hue: 200 }) })} />,
    );
    const cool = (container.querySelector('.artmap-panel-heading') as HTMLElement).style.color;
    expect(warm).not.toBe('');
    expect(cool).not.toBe('');
    expect(cool).not.toBe(warm);

    // …and with no island there is no tint at all.
    rerender(<ArtMapPanel {...props({ model: model({ island: null }) })} />);
    expect((container.querySelector('.artmap-panel-heading') as HTMLElement).style.color).toBe('');
  });

  it('renders the three stat tiles in model order', () => {
    const { container } = render(<ArtMapPanel {...props()} />);
    const tiles = [...container.querySelectorAll('.artmap-ministat')];
    expect(tiles.map((t) => t.querySelector('.artmap-ministat-label')?.textContent)).toEqual([
      'Artists',
      'Watchlist',
      'Genres',
    ]);
    expect(tiles.map((t) => t.querySelector('.artmap-ministat-value')?.textContent)).toEqual([
      '12',
      '3',
      '4',
    ]);
  });

  it('sizes the coverage bar from the model, not from the ratio it recomputes', () => {
    const { container } = render(
      <ArtMapPanel
        {...props({ model: model({ coveragePct: 67, scopeWatch: 2, scopeTotal: 3 }) })}
      />,
    );
    const fill = container.querySelector('.artmap-panel-coverage-fill') as HTMLElement;
    expect(fill.style.width).toBe('67%');
    expect(screen.getByText('2/3')).toBeInTheDocument();
  });

  it('lists the top artists in model order and marks the watched ones', () => {
    const topArtists = [
      node({ id: 1, name: 'One', type: 'watchlist' }),
      node({ id: 2, name: 'Two' }),
    ];
    render(<ArtMapPanel {...props({ model: model({ topArtists }) })} />);
    const rows = screen.getAllByRole('button');
    expect(rows.map((r) => r.querySelector('.artmap-panel-row-name')?.textContent)).toEqual([
      'One',
      'Two',
    ]);
    expect(rows[0].querySelector('.artmap-panel-star')).not.toBeNull();
    expect(rows[1].querySelector('.artmap-panel-star')).toBeNull();
  });

  it('shows the empty state rather than a bare list', () => {
    render(<ArtMapPanel {...props()} />);
    expect(screen.getByText('No artists')).toBeInTheDocument();
  });

  it('selects an artist by id', () => {
    const onSelectArtist = vi.fn();
    const topArtists = [node({ id: 7, name: 'Seven' })];
    render(<ArtMapPanel {...props({ model: model({ topArtists }), onSelectArtist })} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onSelectArtist).toHaveBeenCalledWith(7);
  });
});

describe('the artist card', () => {
  function card(over: Partial<ArtMapNode> = {}): ArtMapArtistCard {
    artMap.edges = [];
    artMap._watchSet = undefined;
    return artMapArtistCard(node(over));
  }

  it('replaces the top list when a card is showing', () => {
    render(<ArtMapPanel {...props({ card: card() })} />);
    expect(screen.queryByText('Top artists')).toBeNull();
    expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
  });

  it('shows the two card stats and the popularity bar', () => {
    const c = card({ popularity: 62.6 });
    const { container } = render(<ArtMapPanel {...props({ card: c })} />);
    const tiles = [...container.querySelectorAll('.artmap-ministat')];
    // Three from the header, then the card's two.
    expect(
      tiles.slice(3).map((t) => t.querySelector('.artmap-ministat-label')?.textContent),
    ).toEqual(['Popularity', 'Connections']);
    const bar = container.querySelector(
      '.artmap-card-pop .artmap-panel-coverage-fill',
    ) as HTMLElement;
    expect(bar.style.width).toBe('63%'); //  the model already rounded
  });

  it('renders at most the five genres the model kept', () => {
    const c = card({ genres: ['a', 'b', 'c', 'd', 'e', 'f'] });
    const { container } = render(<ArtMapPanel {...props({ card: c })} />);
    expect(container.querySelectorAll('.artmap-card-genre')).toHaveLength(5);
  });

  it('omits the genre row entirely when there are none', () => {
    const { container } = render(<ArtMapPanel {...props({ card: card() })} />);
    expect(container.querySelector('.artmap-card-genres')).toBeNull();
  });

  it('links to the artist page only when a source id resolved', () => {
    const withId = card({ spotify_id: 'sp' });
    const { container, rerender } = render(<ArtMapPanel {...props({ card: withId })} />);
    expect(container.querySelector('.artmap-card-open')?.getAttribute('href')).toBe(
      '/artist-detail/spotify/sp',
    );
    rerender(<ArtMapPanel {...props({ card: card() })} />);
    expect(container.querySelector('.artmap-card-open')).toBeNull();
  });

  it('paints a cached bitmap into a canvas instead of emitting an img', () => {
    const drawImage = vi.fn();
    const bitmap = {} as CanvasImageSource;
    artMap.images = { 5: bitmap };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      drawImage,
    } as unknown as CanvasRenderingContext2D);

    const c = card({ id: 5, image_url: '/a.jpg' });
    const { container } = render(<ArtMapPanel {...props({ card: c })} />);
    expect(container.querySelector('canvas.artmap-card-canvas')).not.toBeNull();
    expect(container.querySelector('img.artmap-card-img')).toBeNull();
    expect(drawImage).toHaveBeenCalledWith(bitmap, 0, 0, 120, 120);
    vi.restoreAllMocks();
  });

  it('falls back to the url, then to a glyph', () => {
    const withUrl = card({ id: 6, image_url: '/a.jpg' });
    const { container, rerender } = render(<ArtMapPanel {...props({ card: withUrl })} />);
    expect(container.querySelector('img.artmap-card-img')?.getAttribute('src')).toBe('/a.jpg');
    rerender(<ArtMapPanel {...props({ card: card({ id: 6 }) })} />);
    expect(container.querySelector('.artmap-card-glyph')?.textContent).toBe('♫');
  });

  it('wires explore, details, watch and back', () => {
    const onExplore = vi.fn();
    const onOpenDetails = vi.fn();
    const onToggleWatch = vi.fn();
    const onBackToList = vi.fn();
    const c = card({ id: 9 });
    render(
      <ArtMapPanel
        {...props({ card: c, onExplore, onOpenDetails, onToggleWatch, onBackToList })}
      />,
    );
    fireEvent.click(screen.getByText('Explore from here →'));
    expect(onExplore).toHaveBeenCalledWith('Aphex Twin');
    fireEvent.click(screen.getByText('Details'));
    expect(onOpenDetails).toHaveBeenCalledWith(9);
    fireEvent.click(screen.getByText(c.watch.label));
    expect(onToggleWatch).toHaveBeenCalledWith(9);
    fireEvent.click(screen.getByText('← Top artists'));
    expect(onBackToList).toHaveBeenCalledTimes(1);
  });

  it('shows the watch button in the state the model reports', () => {
    artMap.edges = [];
    const watched = artMapArtistCard(node({ type: 'watchlist' }));
    render(<ArtMapPanel {...props({ card: watched })} />);
    expect(screen.getByText('★ On watchlist')).toBeInTheDocument();
  });
});

describe('the mobile bottom sheet', () => {
  it('slides off screen when closed and up when open', () => {
    const { container, rerender } = render(
      <ArtMapPanel {...props({ isMobile: true, open: false })} />,
    );
    const panel = container.querySelector('#artmap-info-panel') as HTMLElement;
    expect(panel.style.transform).toBe('translateY(100%)');
    rerender(<ArtMapPanel {...props({ isMobile: true, open: true })} />);
    expect((container.querySelector('#artmap-info-panel') as HTMLElement).style.transform).toBe(
      'translateY(0)',
    );
  });

  it('has no grip and no transform on desktop', () => {
    const { container } = render(<ArtMapPanel {...props({ isMobile: false })} />);
    expect(container.querySelector('#artmap-panel-grip')).toBeNull();
    expect((container.querySelector('#artmap-info-panel') as HTMLElement).style.transform).toBe('');
  });

  it('closes the sheet from the grip', () => {
    const onCloseSheet = vi.fn();
    render(<ArtMapPanel {...props({ isMobile: true, open: true, onCloseSheet })} />);
    fireEvent.click(screen.getByLabelText('Close panel'));
    expect(onCloseSheet).toHaveBeenCalledTimes(1);
  });
});

describe('the model it renders', () => {
  it('is the one the differential suite pins, not a re-derivation', () => {
    // Guards against the component quietly computing its own numbers: the panel
    // is handed artMapPanelModel's output and renders it verbatim.
    artMap.placed = [
      node({ id: 1, type: 'watchlist', popularity: 90 }),
      node({ id: 2, type: 'similar', popularity: 10 }),
    ];
    artMap._islands = [];
    artMap._oneIsland = false;
    artMap._mapTitle = 'Genre Map';
    const m = artMapPanelModel();
    render(<ArtMapPanel {...props({ model: m })} />);
    expect(screen.getByText('Genre Map')).toBeInTheDocument();
    expect(screen.getByText(`${m.scopeWatch}/${m.scopeTotal}`)).toBeInTheDocument();
    expect(screen.getAllByRole('button')).toHaveLength(m.topArtists.length);
  });
});
