import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RecommendedArtist } from '../-discover.recommended';
import type { RecommendedShelfProps } from './recommended-shelf';

import { RecommendedShelf } from './recommended-shelf';

/**
 * The two recommendation shelves.
 *
 * They are one card with two reason functions, so the cases that matter are the
 * ones where the two DIVERGE (copy, the View All that only one has) and the ones
 * where a card's own data overrides the section's (source, image).
 */

afterEach(cleanup);

const artist = (over: Partial<RecommendedArtist> = {}): RecommendedArtist => ({
  artist_id: 'sp1',
  artist_name: 'Aphex Twin',
  image_url: '/img/aphex.jpg',
  ...over,
});

function props(over: Partial<RecommendedShelfProps> = {}): RecommendedShelfProps {
  return {
    kind: 'recommended',
    artists: [artist()],
    source: 'spotify',
    loaded: true,
    watchingIds: new Set<string>(),
    images: {},
    buildDetailPath: (id, source) => `/artist-detail/${source ?? 'none'}/${id}`,
    onAddToWatchlist: vi.fn(),
    ...over,
  };
}

describe('the shelf', () => {
  it('titles each kind differently', () => {
    const { rerender } = render(<RecommendedShelf {...props()} />);
    expect(screen.getByText("Artists You'll Like")).toBeInTheDocument();
    rerender(<RecommendedShelf {...props({ kind: 'listening' })} />);
    expect(screen.getByText('Based On Your Listening')).toBeInTheDocument();
  });

  it('keeps the carousel ids the enrichment pass targets', () => {
    const { container, rerender } = render(<RecommendedShelf {...props()} />);
    expect(container.querySelector('#recommended-artists-carousel')).not.toBeNull();
    rerender(<RecommendedShelf {...props({ kind: 'listening' })} />);
    expect(container.querySelector('#listening-recs-carousel')).not.toBeNull();
  });

  it('gives each shelf its own SECTION id', () => {
    // Two sections sharing one id would collapse the page's layout ordering and
    // every stylesheet rule that distinguishes them.
    const { container, rerender } = render(<RecommendedShelf {...props()} />);
    expect(container.querySelector('#recommended-artists-section')).not.toBeNull();
    expect(container.querySelector('#listening-recs-section')).toBeNull();
    rerender(<RecommendedShelf {...props({ kind: 'listening' })} />);
    expect(container.querySelector('#listening-recs-section')).not.toBeNull();
    expect(container.querySelector('#recommended-artists-section')).toBeNull();
  });

  it('offers View All only where the vanilla has one', () => {
    const onViewAll = vi.fn();
    const { rerender } = render(<RecommendedShelf {...props({ onViewAll })} />);
    fireEvent.click(screen.getByText('View All'));
    expect(onViewAll).toHaveBeenCalled();
    // The listening shelf has no View All in the markup, and inventing one
    // would open a modal built for the other section's data.
    rerender(<RecommendedShelf {...props({ kind: 'listening' })} />);
    expect(screen.queryByText('View All')).toBeNull();
  });

  it('shows at most eighteen cards', () => {
    const artists = Array.from({ length: 30 }, (_, i) =>
      artist({ artist_id: `id${i}`, artist_name: `Artist ${i}` }),
    );
    const { container } = render(<RecommendedShelf {...props({ artists })} />);
    expect(container.querySelectorAll('.recommended-artist-card')).toHaveLength(18);
  });

  it('vanishes entirely when it has nothing', () => {
    // Both shelves hide when empty: a user who has not run a scan should not see
    // a titled box explaining that they have nothing.
    const { container } = render(<RecommendedShelf {...props({ artists: [] })} />);
    expect(container.querySelector('.discover-section')).toBeNull();
  });
});

describe('the card', () => {
  it('lowercases the filter name, and keeps the display name cased', () => {
    // The search filter matches against the data attribute, not the label.
    const { container } = render(<RecommendedShelf {...props()} />);
    const card = container.querySelector('.recommended-artist-card')!;
    expect(card).toHaveAttribute('data-artist-name', 'aphex twin');
    expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
  });

  it("lets a card's own source beat the section's", () => {
    // A mixed-source response still has to link each card to the right provider.
    const { container } = render(
      <RecommendedShelf {...props({ artists: [artist({ source: 'deezer' })] })} />,
    );
    expect(container.querySelector('.recommended-card-link')).toHaveAttribute(
      'href',
      '/artist-detail/deezer/sp1',
    );
    expect(container.querySelector('.recommended-artist-card')).toHaveAttribute(
      'data-artist-source',
      'deezer',
    );
  });

  it("falls back to the section's source", () => {
    const { container } = render(<RecommendedShelf {...props()} />);
    expect(container.querySelector('.recommended-card-link')).toHaveAttribute(
      'href',
      '/artist-detail/spotify/sp1',
    );
  });

  it('prefers an enriched image over the payload one', () => {
    const { container } = render(
      <RecommendedShelf {...props({ images: { sp1: '/img/enriched.jpg' } })} />,
    );
    expect(container.querySelector('.recommended-card-image img')).toHaveAttribute(
      'src',
      '/img/enriched.jpg',
    );
  });

  it('falls back to the glyph with no image at all', () => {
    const { container } = render(
      <RecommendedShelf {...props({ artists: [artist({ image_url: undefined })] })} />,
    );
    expect(container.querySelector('.recommended-card-image-fallback')).not.toBeNull();
  });

  it('falls back to the glyph when the image fails to load', () => {
    // A dead url otherwise leaves a hole in the grid.
    const { container } = render(<RecommendedShelf {...props()} />);
    const img = container.querySelector('.recommended-card-image img')!;
    fireEvent.error(img);
    expect(container.querySelector('.recommended-card-image img')).toBeNull();
    expect(container.querySelector('.recommended-card-image-fallback')).not.toBeNull();
  });

  it('shows why-chips instead of the reason line when it has them', () => {
    const { container } = render(
      <RecommendedShelf
        {...props({
          artists: [
            artist({
              why: [
                { type: 'genre', label: 'shares 3 genres' },
                { type: 'obscure', label: 'deep cut' },
                { type: 'consensus', label: 'third' },
              ],
            }),
          ],
        })}
      />,
    );
    // At most two, and they REPLACE the plain reason line.
    expect(container.querySelectorAll('.ya-why-chip')).toHaveLength(2);
    expect(container.querySelector('.ya-card-sub')).toBeNull();
    expect(screen.getByText(/shares 3 genres/)).toBeInTheDocument();
    expect(screen.queryByText(/third/)).toBeNull();
  });

  it('gives each chip its type class and icon', () => {
    const { container } = render(
      <RecommendedShelf
        {...props({ artists: [artist({ why: [{ type: 'genre', label: 'g' }] })] })}
      />,
    );
    const chip = container.querySelector('.ya-why-chip')!;
    expect(chip).toHaveClass('ya-why-genre');
    expect(chip.textContent).toContain('🎯');
  });

  it('shows the reason line when there are no chips', () => {
    const { container } = render(<RecommendedShelf {...props()} />);
    expect(container.querySelector('.ya-card-sub')).not.toBeNull();
    expect(container.querySelector('.ya-card-why')).toBeNull();
  });

  it("carries the full provenance in the reason line's title", () => {
    // The visible line truncates to two names; the title is the only place the
    // rest of the list exists.
    const { container } = render(
      <RecommendedShelf
        {...props({
          artists: [
            artist({
              because: ['Squarepusher', 'Autechre', 'Plaid'],
            } as Partial<RecommendedArtist>),
          ],
        })}
      />,
    );
    const sub = container.querySelector('.ya-card-sub')!;
    expect(sub.textContent).toBe('Because you have Squarepusher, Autechre +1 more');
    expect(sub).toHaveAttribute('title', 'In your library: Squarepusher, Autechre, Plaid');
  });

  it('reads the reason differently per shelf', () => {
    // The two shelves inject different reason functions; sharing one would make
    // the listening shelf explain itself as a similarity match.
    const a = artist({ occurrence_count: 4 } as Partial<RecommendedArtist>);
    const { container, rerender } = render(<RecommendedShelf {...props({ artists: [a] })} />);
    const first = container.querySelector('.ya-card-sub')!.textContent;
    rerender(<RecommendedShelf {...props({ kind: 'listening', artists: [a] })} />);
    expect(container.querySelector('.ya-card-sub')!.textContent).not.toBe(first);
  });

  it('adds to the watchlist with the id and the name', () => {
    const p = props();
    const { container } = render(<RecommendedShelf {...p} />);
    fireEvent.click(container.querySelector('.recommended-card-watchlist-btn')!);
    expect(p.onAddToWatchlist).toHaveBeenCalledWith('sp1', 'Aphex Twin');
  });

  it('marks an already-watched artist and stops re-adding them', () => {
    const p = props({ watchingIds: new Set(['sp1']) });
    const { container } = render(<RecommendedShelf {...p} />);
    const btn = container.querySelector('.recommended-card-watchlist-btn') as HTMLButtonElement;
    expect(btn.textContent).toBe('Watching');
    expect(btn).toHaveClass('watching');
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(p.onAddToWatchlist).not.toHaveBeenCalled();
  });

  it('refuses a card with no id or no name', () => {
    // The request would have nothing to identify the artist by.
    const { container, rerender } = render(
      <RecommendedShelf {...props({ artists: [artist({ artist_id: '' })] })} />,
    );
    expect(container.querySelector('.recommended-card-watchlist-btn')).toBeDisabled();
    rerender(<RecommendedShelf {...props({ artists: [artist({ artist_name: '' })] })} />);
    expect(container.querySelector('.recommended-card-watchlist-btn')).toBeDisabled();
  });

  it('keeps the data hooks the watchlist handler relies on', () => {
    const { container } = render(<RecommendedShelf {...props()} />);
    const btn = container.querySelector('.recommended-card-watchlist-btn')!;
    expect(btn).toHaveAttribute('data-artist-id', 'sp1');
    expect(btn).toHaveAttribute('data-artist-name', 'Aphex Twin');
  });
});
