import { fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LibraryArtist } from '../-library.types';

import { LibraryArtistCard } from './library-artist-card';

/**
 * The card is pure presentation, so it renders standalone — but the details it
 * encodes (the two-stage image fallback, which column the watch badge lands in)
 * are exactly the kind of vanilla behaviour a port drops silently.
 */
function renderCard(
  artist: Partial<LibraryArtist> & { id: LibraryArtist['id'] },
  musicSource?: string,
) {
  return render(
    <LibraryArtistCard
      artist={{ name: 'Aphex Twin', ...artist }}
      index={0}
      musicSource={musicSource}
      href={`/artist-detail/library/${artist.id}`}
    />,
  );
}

const img = () => document.querySelector('.library-artist-image img') as HTMLImageElement | null;
const placeholder = () => document.querySelector('.library-artist-image-fallback');

afterEach(() => vi.unstubAllGlobals());

describe('LibraryArtistCard image fallback', () => {
  it('shows the placeholder outright when there is no image', () => {
    renderCard({ id: 1 });
    expect(img()).toBeNull();
    expect(placeholder()?.textContent).toBe('🎵');
  });

  it('treats a blank image_url as no image', () => {
    renderCard({ id: 1, image_url: '   ' });
    expect(placeholder()).not.toBeNull();
  });

  it('falls back through Deezer before giving up', () => {
    // The vanilla onerror hopped to Deezer's image API once; dropping that hop
    // would lose artwork for every artist whose stored url has rotted.
    renderCard({ id: 1, image_url: 'https://cdn/rotted.jpg', deezer_id: 27 });
    expect(img()!.src).toBe('https://cdn/rotted.jpg');

    fireEvent.error(img()!);
    expect(img()!.src).toBe('https://api.deezer.com/artist/27/image?size=big');

    // ...and only once. A failing Deezer url must not loop.
    fireEvent.error(img()!);
    expect(img()).toBeNull();
    expect(placeholder()).not.toBeNull();
  });

  it('goes straight to the placeholder with no deezer id', () => {
    renderCard({ id: 1, image_url: 'https://cdn/rotted.jpg' });
    fireEvent.error(img()!);
    expect(placeholder()).not.toBeNull();
  });

  it('lazy-loads, so a 75-card page does not fetch 75 images at once', () => {
    renderCard({ id: 1, image_url: 'https://cdn/a.jpg' });
    expect(img()!.getAttribute('loading')).toBe('lazy');
  });
});

describe('LibraryArtistCard badges', () => {
  // Seven — one past MAX_BADGES_PER_COLUMN. Typed so a renamed field breaks the
  // build instead of silently building fewer badges (a spread into the props
  // object skips TypeScript's excess-property check).
  const sevenProviders: Partial<LibraryArtist> = {
    spotify_artist_id: 'sp',
    musicbrainz_id: 'mb',
    deezer_id: 1,
    discogs_id: 'dc',
    audiodb_id: 'ad',
    itunes_artist_id: 'it',
    lastfm_url: 'https://last.fm/x',
  };

  it('renders no badge container for an unenriched, unwatchable artist', () => {
    renderCard({ id: 1 });
    expect(document.querySelector('.card-badge-container')).toBeNull();
  });

  it('keeps one column at or below the cap, with the watch badge LAST', () => {
    renderCard({ id: 1, spotify_artist_id: 'sp', is_watched: true });
    expect(document.querySelector('.badge-overflow-column')).toBeNull();
    const kids = [...document.querySelector('.card-badge-container')!.children];
    expect(kids.at(-1)!.classList.contains('watch-card-icon')).toBe(true);
  });

  it('puts the watch badge in the overflow column, which renders FIRST', () => {
    // The CSS positions the two columns, so their document order is visible.
    renderCard({ id: 1, ...sevenProviders, is_watched: true });
    const container = document.querySelector('.card-badge-container')!;
    expect(container.children[0].classList.contains('badge-overflow-column')).toBe(true);
    expect(container.children[1].classList.contains('badge-primary-column')).toBe(true);
    expect(container.querySelector('.badge-overflow-column .watch-card-icon')).not.toBeNull();
    expect(document.querySelectorAll('.badge-primary-column .source-card-icon')).toHaveLength(6);
  });

  it('offers Watch only when the ACTIVE source has an id for the artist', () => {
    renderCard({ id: 1, spotify_artist_id: 'sp' }, 'spotify');
    expect(document.querySelector('.watch-card-icon[data-unwatched="1"]')).not.toBeNull();

    document.body.innerHTML = '';
    renderCard({ id: 1, lastfm_url: 'https://last.fm/x' }, 'spotify');
    expect(document.querySelector('.watch-card-icon')).toBeNull();
  });

  it('marks an already-watched artist as watching, not watchable', () => {
    renderCard({ id: 1, spotify_artist_id: 'sp', is_watched: true }, 'spotify');
    const badge = document.querySelector('.watch-card-icon')!;
    expect(badge.classList.contains('watched')).toBe(true);
    expect(badge.getAttribute('data-unwatched')).toBeNull();
  });

  it('hides a broken provider logo instead of leaving a gap', () => {
    renderCard({ id: 1, spotify_artist_id: 'sp' });
    const logo = document.querySelector('.source-card-icon img') as HTMLImageElement;
    fireEvent.error(logo);
    expect(logo.style.display).toBe('none');
  });
});

describe('LibraryArtistCard badge clicks', () => {
  /**
   * The whole card is an <a>, so any badge that does not swallow its click
   * navigates to artist detail instead of doing its job. fireEvent.click
   * returns false exactly when preventDefault was called.
   */
  it('opens a provider badge in a new tab instead of following the card link', () => {
    const open = vi.fn();
    vi.stubGlobal('open', open);
    renderCard({ id: 1, spotify_artist_id: 'sp' });

    const badge = document.querySelector('.source-card-icon')!;
    const notPrevented = fireEvent.click(badge);

    expect(open).toHaveBeenCalledWith('https://open.spotify.com/artist/sp', '_blank');
    expect(notPrevented).toBe(false);
  });

  it('swallows the click on a badge that has no link, rather than navigating', () => {
    // Amazon and SoulID render a badge but carry no url.
    renderCard({ id: 1, amazon_id: 'az' });
    expect(fireEvent.click(document.querySelector('.source-card-icon')!)).toBe(false);
  });

  it('toggles the watchlist from the unwatched badge', () => {
    const onToggleWatch = vi.fn();
    render(
      <LibraryArtistCard
        artist={{ id: 1, name: 'Aphex Twin', spotify_artist_id: 'sp' }}
        index={0}
        musicSource="spotify"
        href="/artist-detail/library/1"
        onToggleWatch={onToggleWatch}
      />,
    );
    const badge = document.querySelector('.watch-card-icon')!;
    expect(fireEvent.click(badge)).toBe(false);
    expect(onToggleWatch).toHaveBeenCalledTimes(1);
  });

  it('ignores a second click while the first is still in flight', () => {
    const onToggleWatch = vi.fn();
    render(
      <LibraryArtistCard
        artist={{ id: 1, name: 'Aphex Twin', spotify_artist_id: 'sp' }}
        index={0}
        musicSource="spotify"
        href="/artist-detail/library/1"
        onToggleWatch={onToggleWatch}
        watchPending
      />,
    );
    const badge = document.querySelector('.watch-card-icon')!;
    expect(badge.querySelector('.watch-icon-label')?.textContent).toBe('...');
    fireEvent.click(badge);
    expect(onToggleWatch).not.toHaveBeenCalled();
  });

  it('does nothing on an already-watched badge, but still swallows the click', () => {
    // Vanilla gated the toggle on data-unwatched, so "Watching" was inert —
    // removal happens on the Watchlist page.
    const onToggleWatch = vi.fn();
    render(
      <LibraryArtistCard
        artist={{ id: 1, name: 'Aphex Twin', spotify_artist_id: 'sp', is_watched: true }}
        index={0}
        musicSource="spotify"
        href="/artist-detail/library/1"
        onToggleWatch={onToggleWatch}
      />,
    );
    expect(fireEvent.click(document.querySelector('.watch-card-icon')!)).toBe(false);
    expect(onToggleWatch).not.toHaveBeenCalled();
  });
});

describe('LibraryArtistCard link', () => {
  it('links to the artist detail page and carries the vanilla data attributes', () => {
    // The vanilla delegated click handler reads both attributes.
    renderCard({ id: 42, name: 'Boards of Canada', track_count: 1 });
    const card = document.querySelector('.library-artist-card') as HTMLAnchorElement;
    expect(card.getAttribute('href')).toBe('/artist-detail/library/42');
    expect(card.dataset.artistId).toBe('42');
    expect(card.dataset.artistName).toBe('Boards of Canada');
    expect(document.querySelector('.library-artist-stat')?.textContent).toBe('1 track');
  });

  it('omits the stat line entirely when the artist has no tracks', () => {
    renderCard({ id: 1, track_count: 0 });
    expect(document.querySelector('.library-artist-stat')).toBeNull();
  });
});
