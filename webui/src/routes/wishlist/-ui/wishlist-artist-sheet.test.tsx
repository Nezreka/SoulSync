import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { ParsedWishlistTrack, WishlistArtistGroup } from '../-wishlist.types';

import { WishlistArtistSheet } from './wishlist-artist-sheet';

vi.mock('../../library/-ui/interactive-search-host', () => ({ openWishlistSearch: vi.fn() }));

function track(over: Partial<ParsedWishlistTrack>): ParsedWishlistTrack {
  return {
    track: 'Chicken Fried',
    artist: 'Zac Brown Band',
    album: '',
    image: '',
    type: 'single',
    id: 'cf',
    retry: 5,
    failing: true,
    lastTried: '2026-10-29 18:00:00',
    failReason: '',
    ...over,
  };
}

function sheet(singles: ParsedWishlistTrack[]) {
  const group: WishlistArtistGroup = {
    name: 'Zac Brown Band',
    albums: [],
    singles,
    total: singles.length,
    failingCount: singles.filter((t) => t.failing).length,
  };
  const noop = () => {};
  return render(
    <WishlistArtistSheet
      group={group}
      artistImages={new Map()}
      onClose={noop}
      onGrabArtist={noop}
      onGrabAlbum={noop}
      onRetry={noop}
      onRemoveAlbum={noop}
      onRemoveTrack={noop}
      onRemoveArtist={noop}
    />,
  );
}

describe('a stuck track says why on screen (TheHomeGuy)', () => {
  it('shows the last failure note under the row, not only in a tooltip', () => {
    sheet([
      track({ failReason: 'Download failed: tidal stream returned 401' }),
      track({ id: 'wb', track: 'Where the Boat Leaves From' }),
    ]);
    expect(screen.getByText(/tidal stream returned 401/)).toBeInTheDocument();
  });

  it('adds nothing when no reason was recorded', () => {
    sheet([track({ failReason: '' }), track({ id: 'wb', track: 'Where the Boat Leaves From' })]);
    expect(screen.queryByText(/Last note/)).toBeNull();
  });
});
