import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RecommendedArtist } from '../-discover.recommended';
import type { RecommendedModalProps } from './recommended-modal';

import { RecommendedModal } from './recommended-modal';

/**
 * The Recommended Artists modal.
 *
 * It is NOT the carousel card in a bigger box, and the differences are the tests
 * that matter: genre tags, a three-deep source fallback, and a search that
 * filters the grid without changing the count in the header.
 */

afterEach(cleanup);

const artist = (over: Partial<RecommendedArtist> = {}): RecommendedArtist => ({
  artist_id: 'sp1',
  artist_name: 'Aphex Twin',
  image_url: '/img/aphex.jpg',
  ...over,
});

function props(over: Partial<RecommendedModalProps> = {}): RecommendedModalProps {
  return {
    artists: [artist(), artist({ artist_id: 'sp2', artist_name: 'Boards of Canada' })],
    source: 'spotify',
    cachedSource: null,
    watchingIds: new Set<string>(),
    images: {},
    buildDetailPath: (id, source) => `/artist-detail/${source ?? 'none'}/${id}`,
    onClose: vi.fn(),
    onAddToWatchlist: vi.fn(),
    onAddAll: vi.fn(),
    ...over,
  };
}

describe('the modal', () => {
  it('counts the whole set, plural at zero', () => {
    const { rerender } = render(<RecommendedModal {...props()} />);
    expect(screen.getByText('2 artists')).toBeInTheDocument();
    rerender(<RecommendedModal {...props({ artists: [] })} />);
    // Plural at zero, matching the batch footer — "0 artist" reads as a bug.
    expect(screen.getByText('0 artists')).toBeInTheDocument();
    rerender(<RecommendedModal {...props({ artists: [artist()] })} />);
    expect(screen.getByText('1 artist')).toBeInTheDocument();
  });

  it('filters the grid without changing the count', () => {
    // The header states the size of the recommendation SET; typing a query does
    // not make the set smaller.
    const { container } = render(<RecommendedModal {...props()} />);
    fireEvent.change(container.querySelector('#recommended-search-input')!, {
      target: { value: 'boards' },
    });
    expect(container.querySelectorAll('.recommended-artist-card')).toHaveLength(1);
    expect(screen.getByText('2 artists')).toBeInTheDocument();
  });

  it('matches case-insensitively', () => {
    const { container } = render(<RecommendedModal {...props()} />);
    fireEvent.change(container.querySelector('#recommended-search-input')!, {
      target: { value: 'APHEX' },
    });
    expect(container.querySelectorAll('.recommended-artist-card')).toHaveLength(1);
  });

  it('falls back through source, then cached source', () => {
    // Three deep, one more than the carousel: the modal can be opened from a
    // primed cache with no fresh response to read a source off.
    const { container, rerender } = render(
      <RecommendedModal
        {...props({ artists: [artist({ source: 'deezer' })], source: 'spotify' })}
      />,
    );
    expect(container.querySelector('.recommended-card-link')).toHaveAttribute(
      'href',
      '/artist-detail/deezer/sp1',
    );

    rerender(<RecommendedModal {...props({ artists: [artist()], source: 'spotify' })} />);
    expect(container.querySelector('.recommended-card-link')).toHaveAttribute(
      'href',
      '/artist-detail/spotify/sp1',
    );

    rerender(
      <RecommendedModal
        {...props({ artists: [artist()], source: null, cachedSource: 'itunes' })}
      />,
    );
    expect(container.querySelector('.recommended-card-link')).toHaveAttribute(
      'href',
      '/artist-detail/itunes/sp1',
    );
  });

  it('shows at most three genre tags', () => {
    const { container } = render(
      <RecommendedModal
        {...props({
          artists: [artist({ genres: ['idm', 'ambient', 'techno', 'breakcore'] } as never)],
        })}
      />,
    );
    expect(container.querySelectorAll('.recommended-card-genre')).toHaveLength(3);
    expect(screen.queryByText('breakcore')).toBeNull();
  });

  it('carries a similarity line with its long-form title', () => {
    const { container } = render(
      <RecommendedModal
        {...props({ artists: [artist({ because: ['Squarepusher', 'Autechre'] } as never)] })}
      />,
    );
    const line = container.querySelector('.recommended-card-similarity')!;
    expect(line.textContent).toBe('Because you have Squarepusher & Autechre');
    expect(line).toHaveAttribute('title', 'In your library: Squarepusher, Autechre');
  });

  it('closes the modal when a card link is followed', () => {
    // The link navigates away; leaving the modal mounted behind it means it is
    // still there when the user comes back.
    const p = props();
    const { container } = render(<RecommendedModal {...p} />);
    fireEvent.click(container.querySelector('.recommended-card-link')!);
    expect(p.onClose).toHaveBeenCalled();
  });

  it('closes on the backdrop and the × but not on the card body', () => {
    const p = props();
    const { container } = render(<RecommendedModal {...p} />);
    fireEvent.click(container.querySelector('.recommended-modal')!);
    expect(p.onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Close'));
    expect(p.onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(container.querySelector('.modal-overlay')!);
    expect(p.onClose).toHaveBeenCalledTimes(2);
  });

  it('adds one artist by id and name', () => {
    const p = props();
    const { container } = render(<RecommendedModal {...p} />);
    fireEvent.click(container.querySelector('.recommended-card-watchlist-btn')!);
    expect(p.onAddToWatchlist).toHaveBeenCalledWith('sp1', 'Aphex Twin');
  });

  it('marks watched artists and stops re-adding them', () => {
    const p = props({ watchingIds: new Set(['sp1']) });
    const { container } = render(<RecommendedModal {...p} />);
    const buttons = container.querySelectorAll('.recommended-card-watchlist-btn');
    expect(buttons[0].textContent).toBe('Watching');
    expect(buttons[0]).toBeDisabled();
    expect(buttons[1].textContent).toBe('Add to Watchlist');
    expect(buttons[1]).not.toBeDisabled();
  });

  it('adds all, and cannot be fired twice while it runs', () => {
    const p = props();
    const { rerender } = render(<RecommendedModal {...p} />);
    fireEvent.click(screen.getByText('Add All to Watchlist'));
    expect(p.onAddAll).toHaveBeenCalledTimes(1);
    rerender(<RecommendedModal {...props({ ...p, addingAll: true })} />);
    expect(screen.getByText('Add All to Watchlist')).toBeDisabled();
  });

  it('prefers an enriched image and falls back on a dead one', () => {
    const { container } = render(
      <RecommendedModal {...props({ images: { sp1: '/img/enriched.jpg' } })} />,
    );
    const img = container.querySelector('.recommended-card-image img')!;
    expect(img).toHaveAttribute('src', '/img/enriched.jpg');
    fireEvent.error(img);
    expect(container.querySelector('.recommended-card-image-fallback')).not.toBeNull();
  });

  it('lowercases the filter attribute while keeping the label cased', () => {
    // The vanilla's search compares against this attribute, so a cased value
    // silently stops matching. Asserted directly rather than through a CSS
    // attribute selector, whose case rules are not worth relying on.
    const { container } = render(<RecommendedModal {...props()} />);
    const card = container.querySelector('.recommended-artist-card')!;
    expect(card.getAttribute('data-artist-name')).toBe('aphex twin');
    expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
  });

  it('keeps the ids and data hooks the vanilla styling and handlers target', () => {
    const { container } = render(<RecommendedModal {...props()} />);
    for (const sel of [
      '.recommended-modal',
      '#recommended-search-input',
      '#recommended-add-all-btn',
      '#recommended-artists-grid',
      '.recommended-artist-card[data-artist-id="sp1"]',
      '.recommended-artist-card[data-artist-name="aphex twin"]',
    ]) {
      expect(container.querySelector(sel), sel).not.toBeNull();
    }
  });
});
