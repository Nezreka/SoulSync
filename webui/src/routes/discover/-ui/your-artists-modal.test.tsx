import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { YourArtist } from '../-discover.your-artists';
import type { YourArtistsModalProps } from './your-artists-modal';

import { YourArtistsModal } from './your-artists-modal';
// The grid must render the SAME card component the shelf uses —
// `YourArtistCard` — because the vanilla calls one renderer from both places
// (5259, 5807) and a re-transcription is how the two would drift.
import { YourArtistCard } from './your-artists-shelf';

afterEach(cleanup);

const artist = (over: Partial<YourArtist> = {}): YourArtist =>
  ({
    id: 7,
    artist_name: 'Aphex Twin',
    image_url: '/img/a.jpg',
    spotify_artist_id: 'sp1',
    active_source_id: 'sp1',
    active_source: 'spotify',
    source_services: ['spotify'],
    on_watchlist: 0,
    ...over,
  }) as YourArtist;

function props(over: Partial<YourArtistsModalProps> = {}): YourArtistsModalProps {
  return {
    state: { page: 1, source: '', sort: 'name', search: '' },
    total: 61,
    artists: [artist()],
    phase: 'ready',
    logos: { spotify: '/logos/sp.png' },
    buildDetailPath: (id, source) => `/artist/${id}?source=${source}`,
    onFilter: vi.fn(),
    onPage: vi.fn(),
    onClose: vi.fn(),
    onOpenInfo: vi.fn(),
    onToggleWatchlist: vi.fn(),
    ...over,
  };
}

describe('Your Artists modal', () => {
  it('renders the vanilla shell: overlay id, title, search, sort', () => {
    const { container } = render(<YourArtistsModal {...props()} />);
    const overlay = container.querySelector('#your-artists-modal-overlay')!;
    expect(overlay).toHaveClass('modal-overlay');
    expect(overlay.querySelector('.ya-modal-title')!.textContent).toBe('Your Artists');
    expect(overlay.querySelector('#ya-modal-search')).toHaveAttribute(
      'placeholder',
      'Search artists...',
    );
    const sort = overlay.querySelector('#ya-modal-sort')!;
    expect([...sort.querySelectorAll('option')].map((o) => [o.value, o.textContent])).toEqual([
      ['name', 'A-Z'],
      ['recent', 'Recently Added'],
      ['source', 'By Source'],
    ]);
  });

  it('subtitles Loading... before the first response, then the matched count', () => {
    const { rerender } = render(<YourArtistsModal {...props({ total: null })} />);
    expect(document.getElementById('ya-modal-subtitle')!.textContent).toBe('Loading...');
    rerender(<YourArtistsModal {...props()} />);
    expect(document.getElementById('ya-modal-subtitle')!.textContent).toBe('61 artists matched');
  });

  it('renders the five filter pills with the empty-source pill as All', () => {
    const { container } = render(<YourArtistsModal {...props()} />);
    const pills = [...container.querySelectorAll('.ya-filter-btn')];
    expect(pills.map((p) => [p.getAttribute('data-source'), p.textContent])).toEqual([
      ['', 'All'],
      ['spotify', 'Spotify'],
      ['tidal', 'Tidal'],
      ['lastfm', 'Last.fm'],
      ['deezer', 'Deezer'],
    ]);
    expect(pills[0]).toHaveClass('active');
    expect(pills[1]).not.toHaveClass('active');
  });

  it('routes every toolbar change through onFilter', () => {
    const p = props({ state: { page: 3, source: 'tidal', sort: 'recent', search: '' } });
    const { container } = render(<YourArtistsModal {...p} />);
    expect(container.querySelector('[data-source="tidal"]')).toHaveClass('active');
    fireEvent.click(container.querySelector('[data-source="lastfm"]')!);
    expect(p.onFilter).toHaveBeenCalledWith({ source: 'lastfm' });
    fireEvent.change(container.querySelector('#ya-modal-search')!, { target: { value: 'aph' } });
    expect(p.onFilter).toHaveBeenCalledWith({ search: 'aph' });
    fireEvent.change(container.querySelector('#ya-modal-sort')!, { target: { value: 'source' } });
    expect(p.onFilter).toHaveBeenCalledWith({ sort: 'source' });
  });

  it('renders the grid with the shelf CARD, wired through', () => {
    const p = props();
    const { container } = render(<YourArtistsModal {...p} />);
    const card = container.querySelector('.ya-modal-grid .ya-card')!;
    expect(card.querySelector('.ya-card-name')!.textContent).toBe('Aphex Twin');
    fireEvent.click(card);
    expect(p.onOpenInfo).toHaveBeenCalledWith(p.artists[0]);
    fireEvent.click(card.querySelector('.ya-watchlist-btn')!);
    expect(p.onToggleWatchlist).toHaveBeenCalledWith(p.artists[0]);
    // ONE info-open — the watchlist click must not bubble into the card.
    expect(p.onOpenInfo).toHaveBeenCalledTimes(1);
  });

  it('words the three body states like the vanilla', () => {
    const { container, rerender } = render(<YourArtistsModal {...props({ phase: 'loading' })} />);
    expect(
      container.querySelector('.cache-health-loading .watch-all-loading-spinner'),
    ).not.toBeNull();
    rerender(<YourArtistsModal {...props({ phase: 'error', artists: [] })} />);
    expect(container.querySelector('.failed-mb-empty')!.textContent).toBe('Failed to load');
    rerender(<YourArtistsModal {...props({ artists: [] })} />);
    expect(container.querySelector('.failed-mb-empty')!.textContent).toBe('No artists found');
  });

  it('hides the pager at one page, on empty results, and while loading', () => {
    const { container, rerender } = render(<YourArtistsModal {...props({ total: 60 })} />);
    expect(container.querySelector('.failed-mb-pagination')).toBeNull();
    rerender(<YourArtistsModal {...props({ total: 61, artists: [] })} />);
    expect(container.querySelector('.failed-mb-pagination')).toBeNull();
    rerender(<YourArtistsModal {...props({ total: 61, phase: 'loading' })} />);
    expect(container.querySelector('.failed-mb-pagination')).toBeNull();
  });

  it('pages relative to the current page, with the rails disabled at the ends', () => {
    const p = props({ total: 121, state: { page: 2, source: '', sort: 'name', search: '' } });
    const { container } = render(<YourArtistsModal {...p} />);
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument();
    const [prev, next] = [...container.querySelectorAll('.failed-mb-btn-sm')];
    expect(prev).toBeEnabled();
    expect(next).toBeEnabled();
    fireEvent.click(prev);
    expect(p.onPage).toHaveBeenCalledWith(1);
    fireEvent.click(next);
    expect(p.onPage).toHaveBeenCalledWith(3);

    cleanup();
    const q = props({ total: 121, state: { page: 3, source: '', sort: 'name', search: '' } });
    const { container: c2 } = render(<YourArtistsModal {...q} />);
    const [prev2, next2] = [...c2.querySelectorAll('.failed-mb-btn-sm')];
    expect(prev2).toBeEnabled();
    expect(next2).toBeDisabled();
  });

  it('closes on the backdrop and the &times; button, not on inner clicks', () => {
    const p = props();
    const { container } = render(<YourArtistsModal {...p} />);
    fireEvent.click(container.querySelector('.ya-modal')!);
    expect(p.onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector('#your-artists-modal-overlay')!);
    fireEvent.click(container.querySelector('.watch-all-close')!);
    expect(p.onClose).toHaveBeenCalledTimes(2);
  });
});

describe('the modal grid IS the shelf card', () => {
  it('renders markup identical to a directly-rendered YourArtistCard', () => {
    // The differential that keeps the two call sites from drifting: the card
    // inside the modal grid and the exported card, given the same input, must
    // be the same markup node for node.
    const p = props();
    const { container: modal } = render(<YourArtistsModal {...p} />);
    const { container: direct } = render(
      <YourArtistCard
        artist={p.artists[0]}
        logos={p.logos}
        buildDetailPath={p.buildDetailPath}
        onOpenInfo={p.onOpenInfo}
        onToggleWatchlist={p.onToggleWatchlist}
      />,
    );
    expect(modal.querySelector('.ya-modal-grid .ya-card')!.outerHTML).toBe(
      direct.querySelector('.ya-card')!.outerHTML,
    );
  });
});
