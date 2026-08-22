/** The list view — display-only twin of the nebula (no new functionality). */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ParsedWishlistTrack, WishlistArtistGroup } from '../-wishlist.types';

import { WishlistList } from './wishlist-list';

afterEach(() => {
  cleanup();
  delete window._searchWishlistTrackManually;
  delete window._navigateToArtistFromWishlist;
});

function track(over: Partial<ParsedWishlistTrack>): ParsedWishlistTrack {
  return {
    track: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW 85-92',
    image: 'https://img/cover.jpg',
    type: 'album',
    id: 't1',
    retry: 0,
    failing: false,
    lastTried: '',
    failReason: '',
    upgrade: false,
    currentQuality: '',
    imageFallback: '',
    ...over,
  };
}

function group(over: Partial<WishlistArtistGroup>): WishlistArtistGroup {
  return { name: 'Aphex Twin', albums: [], singles: [], total: 1, failingCount: 0, ...over };
}

const GROUPS: WishlistArtistGroup[] = [
  group({ name: 'Calm Artist', total: 2, singles: [track({ id: 's1', type: 'single' })] }),
  group({
    name: 'Stuck Artist',
    total: 1,
    failingCount: 1,
    albums: [
      {
        name: 'Lost Album',
        image: '',
        imageFallback: '',
        tracks: [
          track({ id: 'f1', track: 'Ghost', retry: 7, failing: true, failReason: 'no sources' }),
        ],
      },
    ],
  }),
];

describe('WishlistList', () => {
  it('sorts failing artists first by default and badges them', () => {
    render(
      <WishlistList
        groups={GROUPS}
        artistImages={new Map([['stuck artist', 'https://img/stuck.jpg']])}
        onRemoveAlbum={vi.fn()}
        onRemoveTrack={vi.fn()}
      />,
    );
    const names = screen.getAllByTitle('Open artist').map((el) => el.textContent);
    expect(names).toEqual(['Stuck Artist', 'Calm Artist']);
    // The avatar map is keyed by LOWERCASED name — the group name must be
    // normalized on lookup or no avatar ever renders (the shipped v1 bug).
    const avatars = document.querySelectorAll('img.wl-list-avatar');
    expect(avatars).toHaveLength(1);
    expect((avatars[0] as HTMLImageElement).src).toBe('https://img/stuck.jpg');
    expect(screen.getByText('⚠ 1 failing')).toBeInTheDocument();
    // Collapsed by default: the index shows, the rows don't — until expanded.
    expect(screen.queryByText('⚠ 7 tries')).toBeNull();
    fireEvent.click(screen.getByText('Expand all'));
    expect(screen.getByText('⚠ 7 tries')).toBeInTheDocument();
  });

  it('re-sorts alphabetically on A–Z', () => {
    render(
      <WishlistList
        groups={GROUPS}
        artistImages={new Map()}
        onRemoveAlbum={vi.fn()}
        onRemoveTrack={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('A–Z'));
    const names = screen.getAllByTitle('Open artist').map((el) => el.textContent);
    expect(names).toEqual(['Calm Artist', 'Stuck Artist']);
  });

  it('routes every action through the EXISTING seams', () => {
    const onRemoveAlbum = vi.fn();
    const onRemoveTrack = vi.fn();
    const search = vi.fn();
    const nav = vi.fn();
    window._searchWishlistTrackManually = search;
    window._navigateToArtistFromWishlist = nav;

    render(
      <WishlistList
        groups={GROUPS}
        artistImages={new Map()}
        onRemoveAlbum={onRemoveAlbum}
        onRemoveTrack={onRemoveTrack}
      />,
    );

    fireEvent.click(screen.getByText('Stuck Artist'));
    expect(nav).toHaveBeenCalledWith('Stuck Artist');
    // The name click NAVIGATES without toggling; rows appear via the
    // separator (collapsed by default) — expand both groups for the rest.
    fireEvent.click(screen.getAllByTitle('Expand')[0]);
    fireEvent.click(screen.getAllByTitle('Expand')[0]);

    fireEvent.click(screen.getAllByTitle('Search manually')[0]);
    expect(search).toHaveBeenCalledWith('Aphex Twin', 'Ghost');

    // The album remove lives on the row's album cell now (flat table).
    fireEvent.click(screen.getByTitle('Remove all tracks from "SAW 85-92"'));
    expect(onRemoveAlbum).toHaveBeenCalledWith('SAW 85-92');

    fireEvent.click(screen.getAllByTitle('Remove from wishlist')[0]);
    expect(onRemoveTrack).toHaveBeenCalledWith('f1');
  });
});
