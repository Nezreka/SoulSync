import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { GenreDiveData, GenreDiveModalProps } from './genre-dive-modal';

import { GenreDiveModal } from './genre-dive-modal';

afterEach(cleanup);

const fullData = (): GenreDiveData => ({
  related_genres: [{ genre: 'idm' }, { genre: 'ambient techno' }],
  artists: [
    {
      name: 'Aphex Twin',
      entity_id: 'sp1',
      source: 'Spotify',
      image_url: '/img/a.jpg',
      followers: 1_234_567,
      library_id: 9,
    },
    { name: 'Unknown Local', source: 'deezer', followers: 45_000 },
  ],
  tracks: [
    {
      name: 'Xtal',
      artist_name: 'Aphex Twin',
      album_name: 'SAW 85-92',
      source: 'spotify',
      image_url: '/img/x.jpg',
      duration_ms: 294_000,
    },
    { name: 'Untitled', artist_name: 'Someone', source: '' },
  ],
  albums: [{ name: 'SAW 85-92', artist_name: 'Aphex Twin', in_library: true }],
});

function props(over: Partial<GenreDiveModalProps> = {}): GenreDiveModalProps {
  return {
    genre: 'braindance',
    data: fullData(),
    phase: 'ready',
    buildDetailPath: (id, source) => `/artist/${id}?source=${String(source)}`,
    onOpenGenre: vi.fn(),
    onFollowArtist: vi.fn(),
    onOpenTrack: vi.fn(),
    onOpenAlbum: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
}

describe('genre deep dive modal — shell', () => {
  it('renders the overlay, closing from backdrop and ×, not inner clicks', () => {
    const p = props();
    const { container } = render(<GenreDiveModal {...p} />);
    const overlay = container.querySelector('#genre-deep-dive-modal')!;
    expect(overlay).toHaveClass('genre-dive-overlay');
    expect(container.querySelector('.genre-dive-title')!.textContent).toBe('braindance');
    fireEvent.click(container.querySelector('.genre-dive-modal')!);
    expect(p.onClose).not.toHaveBeenCalled();
    fireEvent.click(overlay);
    fireEvent.click(container.querySelector('.genre-dive-close')!);
    expect(p.onClose).toHaveBeenCalledTimes(2);
  });

  it('subtitles the default while loading and the counts once ready', () => {
    const { container, rerender } = render(
      <GenreDiveModal {...props({ phase: 'loading', data: null })} />,
    );
    const subtitle = () => container.querySelector('.genre-dive-subtitle')!.textContent;
    expect(subtitle()).toBe('Genre Deep Dive');
    expect(container.querySelector('.genre-dive-loading')!.textContent).toBe(
      'Exploring braindance...',
    );
    expect(container.querySelector('.genre-dive-loading .genre-dive-spinner')).not.toBeNull();
    rerender(<GenreDiveModal {...props()} />);
    expect(subtitle()).toBe('2 artists · 2 tracks · 1 album');

    // A RE-dive from a related pill keeps the component mounted with stale
    // data while the new fetch runs — the subtitle must revert to the default,
    // as the vanilla's remove-and-reopen does.
    rerender(<GenreDiveModal {...props({ phase: 'loading', data: fullData() })} />);
    expect(subtitle()).toBe('Genre Deep Dive');
  });

  it('words the error inline and keeps the default subtitle', () => {
    const { container } = render(<GenreDiveModal {...props({ phase: 'error', data: null })} />);
    expect(screen.getByText('Failed to load genre data')).toBeInTheDocument();
    expect(container.querySelector('.genre-dive-subtitle')!.textContent).toBe('Genre Deep Dive');
    expect(container.querySelector('.genre-dive-empty')).toBeNull();
  });

  it('renders the 🔍 empty state when the dive found nothing', () => {
    const { container } = render(<GenreDiveModal {...props({ data: {} })} />);
    const empty = container.querySelector('.genre-dive-empty')!;
    expect(empty.querySelector('.genre-dive-empty-icon')!.textContent).toBe('🔍');
    expect(empty.textContent).toContain('No cached data found for this genre yet');
    expect(empty.querySelector('.genre-dive-empty-hint')!.textContent).toBe(
      'Search for artists in this genre to build up the cache',
    );
  });
});

describe('genre deep dive modal — sections', () => {
  it('re-dives from a related-genre pill', () => {
    const p = props();
    const { container } = render(<GenreDiveModal {...p} />);
    expect(container.querySelector('.genre-dive-related-label')!.textContent).toBe(
      'Related Genres',
    );
    fireEvent.click(screen.getByText('ambient techno'));
    expect(p.onOpenGenre).toHaveBeenCalledExactlyOnceWith('ambient techno');
  });

  it('links artists only when they have an entity_id, with dot/meta/badge', () => {
    const p = props();
    const { container } = render(<GenreDiveModal {...p} />);
    expect(container.querySelector('.genre-dive-section-title')!.textContent).toBe(
      '🎤 Artists in braindance',
    );
    const cards = [...container.querySelectorAll('a.genre-dive-artist')] as HTMLAnchorElement[];
    expect(cards[0].getAttribute('href')).toBe('/artist/sp1?source=Spotify');
    expect(cards[1].getAttribute('href')).toBe('#');
    // The source class is LOWERCASED from the payload's casing.
    expect(cards[0].querySelector('.genre-dive-src-dot')).toHaveClass('genre-dive-src-spotify');
    // 1.2M — one decimal at millions; 45K — none at thousands.
    expect(cards[0].querySelector('.genre-dive-artist-meta')!.textContent).toBe('1.2M followers');
    expect(cards[1].querySelector('.genre-dive-artist-meta')!.textContent).toBe('45K followers');
    expect(cards[0].querySelector('.genre-dive-artist-badge')!.textContent).toBe('In Library');
    expect(cards[1].querySelector('.genre-dive-artist-badge')).toBeNull();
    // Art: bg-image when present, 🎤 span when not.
    expect(
      (cards[0].querySelector('.genre-dive-artist-img') as HTMLElement).style.backgroundImage,
    ).toBe('url("/img/a.jpg")');
    expect(cards[1].querySelector('.genre-dive-artist-img span')!.textContent).toBe('🎤');
    fireEvent.click(cards[0]);
    expect(p.onFollowArtist).toHaveBeenCalledOnce();

    // No follower count → the whole meta line is omitted, not ' followers'.
    cleanup();
    const { container: c2 } = render(
      <GenreDiveModal
        {...props({ data: { artists: [{ name: 'Nobody', entity_id: 'x', source: 's' }] } })}
      />,
    );
    expect(c2.querySelector('.genre-dive-artist-meta')).toBeNull();
  });

  it('renders track rows numbered from 1, with subtitle join and duration', () => {
    const p = props();
    const { container } = render(<GenreDiveModal {...p} />);
    const rows = [...container.querySelectorAll('.genre-dive-track')];
    expect(rows[0].querySelector('.genre-dive-track-num')!.textContent).toBe('1');
    expect(rows[1].querySelector('.genre-dive-track-num')!.textContent).toBe('2');
    expect(rows[0].querySelector('.genre-dive-track-artist')!.textContent).toBe(
      'Aphex Twin · SAW 85-92',
    );
    // No album → artist alone, no dangling separator; no length → blank, not 0:00.
    expect(rows[1].querySelector('.genre-dive-track-artist')!.textContent).toBe('Someone');
    expect(rows[0].querySelector('.genre-dive-track-duration')!.textContent).toBe('4:54');
    expect(rows[1].querySelector('.genre-dive-track-duration')!.textContent).toBe('');
    fireEvent.click(rows[1]);
    expect(p.onOpenTrack).toHaveBeenCalledExactlyOnceWith(1);
  });

  it('renders albums as unified cards in a CAROUSEL, not a grid', () => {
    const p = props();
    const { container } = render(<GenreDiveModal {...p} />);
    const carousel = container.querySelector('.discover-carousel')!;
    const card = carousel.querySelector('.ya-card.discover-album-card')!;
    expect(card.querySelector('.ya-card-name')!.textContent).toBe('SAW 85-92');
    expect(card.querySelector('.discover-album-badge.owned')).not.toBeNull();
    fireEvent.click(card);
    expect(p.onOpenAlbum).toHaveBeenCalledExactlyOnceWith(0);
  });

  it('omits every section it has no data for', () => {
    const { container } = render(
      <GenreDiveModal {...props({ data: { tracks: fullData().tracks } })} />,
    );
    expect(container.querySelector('.genre-dive-related')).toBeNull();
    expect(container.querySelector('.genre-dive-artists')).toBeNull();
    expect(container.querySelector('.discover-carousel')).toBeNull();
    expect(container.querySelector('.genre-dive-tracks')).not.toBeNull();
    expect(container.querySelector('.genre-dive-empty')).toBeNull();

    // The empty state needs EVERY section empty — artists alone must not
    // trip it just because there are no tracks.
    cleanup();
    const { container: c2 } = render(
      <GenreDiveModal {...props({ data: { artists: fullData().artists } })} />,
    );
    expect(c2.querySelector('.genre-dive-empty')).toBeNull();
    expect(c2.querySelector('.genre-dive-artists')).not.toBeNull();
  });
});
