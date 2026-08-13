import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ArtistPool, RelatedArtist } from '../-discover.your-artists-actions';
import type { ArtistInfoModalProps } from './artist-info-modal';

import { ArtistInfoModal } from './artist-info-modal';

/**
 * One modal, three entry points (card, related strip, artist map adapter).
 * What matters most here is the vanilla's split of pool vs enrichment: the
 * HERO renders from the pool row and never blanks, while the BODY walks the
 * loading/error/ready states of the info fetch.
 */

afterEach(cleanup);

const pool = (over: Partial<ArtistPool> = {}): ArtistPool => ({
  id: 7,
  artist_name: 'Aphex Twin',
  image_url: '/img/a.jpg',
  active_source: 'spotify',
  active_source_id: 'sp1',
  on_watchlist: 0,
  source_services: ['spotify', 'lastfm'],
  spotify_artist_id: 'sp1',
  deezer_artist_id: 'dz1',
  _related: [],
  ...over,
});

function props(over: Partial<ArtistInfoModalProps> = {}): ArtistInfoModalProps {
  return {
    pool: pool(),
    info: {},
    phase: 'ready',
    logos: { spotify: '/logos/sp.png' },
    buildDetailPath: (id, source) => `/artist/${id}?source=${String(source)}`,
    onClose: vi.fn(),
    onToggleWatchlist: vi.fn(),
    onExplore: vi.fn(),
    onOpenRelated: vi.fn(),
    onViewDiscography: vi.fn(),
    ...over,
  };
}

describe('artist info modal — shell and hero', () => {
  it('renders the overlay ABOVE the Your Artists modal, closing from backdrop and \u00d7', () => {
    const p = props();
    const { container } = render(<ArtistInfoModal {...p} />);
    const overlay = container.querySelector('#ya-info-modal-overlay') as HTMLElement;
    expect(overlay).toHaveClass('modal-overlay');
    expect(overlay.style.zIndex).toBe('10001');
    fireEvent.click(container.querySelector('.ya-info-modal')!);
    expect(p.onClose).not.toHaveBeenCalled();
    fireEvent.click(overlay);
    fireEvent.click(container.querySelector('.watch-all-close')!);
    expect(p.onClose).toHaveBeenCalledTimes(2);
  });

  it('renders the hero from the POOL row, with the bg image and origins', () => {
    const { container } = render(<ArtistInfoModal {...props()} />);
    const bg = container.querySelector('.ya-info-hero-bg') as HTMLElement;
    // jsdom CSSOM normalises url('x') to url("x").
    expect(bg.style.backgroundImage).toBe('url("/img/a.jpg")');
    expect(container.querySelector('.ya-info-hero-img img')).toHaveAttribute('src', '/img/a.jpg');
    expect(container.querySelector('.ya-info-name')!.textContent).toBe('Aphex Twin');
    // ', ' join — the section subtitle joins with ' and '; this one does not.
    expect(container.querySelector('.ya-info-origin')!.textContent).toBe(
      'Followed on Spotify, Last.fm',
    );
  });

  it('falls back to the ♫ block without an image, and drops the empty origin line', () => {
    const { container } = render(
      <ArtistInfoModal {...props({ pool: pool({ image_url: '', source_services: [] }) })} />,
    );
    expect(container.querySelector('.ya-info-hero-img img')).toBeNull();
    expect(container.querySelector('.ya-info-img-fallback')!.textContent).toBe('♫');
    expect((container.querySelector('.ya-info-hero-bg') as HTMLElement).style.backgroundImage).toBe(
      '',
    );
    expect(container.querySelector('.ya-info-origin')).toBeNull();
  });

  it('renders "Matched on" badges only for held ids, falling back when the logo dies', () => {
    const { container } = render(<ArtistInfoModal {...props()} />);
    const badges = [...container.querySelectorAll('.ya-info-badge')];
    expect(badges.map((b) => b.getAttribute('title'))).toEqual([
      'Matched on Spotify',
      'Matched on Deezer',
    ]);
    expect(badges[0].querySelector('img')).toHaveAttribute('src', '/logos/sp.png');
    // No deezer logo passed → span fallback immediately.
    expect(badges[1].textContent).toBe('Dz');
    fireEvent.error(badges[0].querySelector('img')!);
    expect(badges[0].textContent).toBe('SP');
  });
});

describe('artist info modal — body states', () => {
  it('shows the loading spinner with the vanilla wording, and NO footer yet', () => {
    const { container } = render(<ArtistInfoModal {...props({ phase: 'loading', info: null })} />);
    expect(
      container.querySelector('.cache-health-loading .watch-all-loading-spinner'),
    ).not.toBeNull();
    expect(screen.getByText('Loading artist info...')).toBeInTheDocument();
    expect(container.querySelector('.ya-info-footer')!.children).toHaveLength(0);
  });

  it('words the error and empty states differently, footer empty on error', () => {
    const { container, rerender } = render(<ArtistInfoModal {...props({ phase: 'error' })} />);
    expect(container.querySelector('.ya-info-empty')!.textContent).toBe(
      'Could not load artist info',
    );
    expect(container.querySelector('.ya-info-footer')!.children).toHaveLength(0);
    rerender(<ArtistInfoModal {...props()} />);
    expect(container.querySelector('.ya-info-empty')!.textContent).toBe(
      'No additional info available',
    );
    expect(container.querySelector('.ya-info-footer')!.children).not.toHaveLength(0);
  });

  it('renders only the stats that are non-zero, with thousands separators', () => {
    const { container } = render(
      <ArtistInfoModal {...props({ info: { lastfm_listeners: 1234567, popularity: 82 } })} />,
    );
    const statLabels = [...container.querySelectorAll('.ya-info-stat-label')].map(
      (l) => l.textContent,
    );
    expect(statLabels).toEqual(['listeners', 'popularity']);
    expect(container.querySelector('.ya-info-stat-value')!.textContent).toBe(
      (1234567).toLocaleString(),
    );

    // Each stat's own guard: a playcount-only payload must not render a zero
    // listeners stat (the vanilla's per-field ternaries, 5432-5434).
    cleanup();
    const { container: c2 } = render(
      <ArtistInfoModal {...props({ info: { lastfm_playcount: 5 } })} />,
    );
    expect([...c2.querySelectorAll('.ya-info-stat-label')].map((l) => l.textContent)).toEqual([
      'plays',
    ]);
  });

  it('strips the Last.fm link from the bio WITH its text, then truncates at 600', () => {
    const long = 'x'.repeat(700);
    const { container } = render(
      <ArtistInfoModal
        {...props({
          info: { summary: `<p>${long}</p> <a href="https://last.fm">Read more on Last.fm</a>` },
        })}
      />,
    );
    const bio = container.querySelector('.ya-info-bio')!.textContent!;
    expect(bio).toBe('x'.repeat(600) + '...');

    // On a SHORT bio the anchor text would survive a plain tag-strip — the
    // anchor must go first, WITH its text (the cleanBio ordering).
    cleanup();
    const { container: c2 } = render(
      <ArtistInfoModal
        {...props({
          info: { summary: 'Short bio. <a href="https://last.fm">Read more on Last.fm</a>' },
        })}
      />,
    );
    expect(c2.querySelector('.ya-info-bio')!.textContent).toBe('Short bio.');
  });

  it('labels related by watchlist state, caps at 12 with a +N more tail', () => {
    const related: RelatedArtist[] = Array.from({ length: 15 }, (_, i) => ({
      id: i,
      name: `R${i}`,
      type: i === 0 ? 'watchlist' : 'related',
    }));
    const p = props({ pool: pool({ on_watchlist: 1, _related: related }) });
    const { container, rerender } = render(<ArtistInfoModal {...p} />);
    expect(container.querySelector('.ya-info-section-title')!.textContent).toBe('Similar Artists');
    expect(container.querySelectorAll('.ya-info-related-item')).toHaveLength(12);
    expect(container.querySelector('.ya-info-related-more')!.textContent).toBe('+3 more');
    // ★ only on the watchlist member.
    expect(container.querySelectorAll('.ya-info-related-badge')).toHaveLength(1);
    fireEvent.click(container.querySelectorAll('.ya-info-related-item')[1]);
    expect(p.onOpenRelated).toHaveBeenCalledWith(related[1]);

    rerender(
      <ArtistInfoModal {...props({ pool: pool({ on_watchlist: 0, _related: related }) })} />,
    );
    expect(container.querySelector('.ya-info-section-title')!.textContent).toBe('Connected To');

    // Under the cap there is NO tail at all — not a '+0 more'.
    rerender(<ArtistInfoModal {...props({ pool: pool({ _related: related.slice(0, 3) }) })} />);
    expect(container.querySelectorAll('.ya-info-related-item')).toHaveLength(3);
    expect(container.querySelector('.ya-info-related-more')).toBeNull();
  });
});

describe('artist info modal — footer', () => {
  it('one-shots the watch toggle: label flips to the done text and disables', () => {
    const p = props();
    render(<ArtistInfoModal {...p} />);
    const btn = screen.getByText('Add to Watchlist');
    fireEvent.click(btn);
    expect(p.onToggleWatchlist).toHaveBeenCalledOnce();
    expect(btn).toHaveTextContent('Added!');
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(p.onToggleWatchlist).toHaveBeenCalledOnce();
  });

  it('words the remove path from the pool flag', () => {
    render(<ArtistInfoModal {...props({ pool: pool({ on_watchlist: 1 }) })} />);
    const btn = screen.getByText('Remove from Watchlist');
    fireEvent.click(btn);
    expect(btn).toHaveTextContent('Done');
  });

  it('explores and links the discography from the active source', () => {
    const p = props();
    const { container } = render(<ArtistInfoModal {...p} />);
    fireEvent.click(screen.getByText('Explore'));
    expect(p.onExplore).toHaveBeenCalledOnce();
    const link = container.querySelector('a.ya-viewall-btn')!;
    expect(link).toHaveAttribute('href', '/artist/sp1?source=spotify');
    fireEvent.click(link);
    expect(p.onViewDiscography).toHaveBeenCalledOnce();
  });

  it('passes a NULL source to the path builder when the pool has none', () => {
    // buildArtistDetailPath(artistId, pool.active_source || null) (5502) — the
    // empty string must not reach the builder as a source.
    const build = vi.fn(() => '/artist/x');
    render(
      <ArtistInfoModal {...props({ pool: pool({ active_source: '' }), buildDetailPath: build })} />,
    );
    expect(build).toHaveBeenCalledWith('sp1', null);
  });
});
