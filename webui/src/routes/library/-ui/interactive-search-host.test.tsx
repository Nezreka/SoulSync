import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { openWishlistInspector } from '@/features/downloads/inspector-modal';

import { LibrarySearchHost, openWishlistSearch } from './interactive-search-host';

vi.mock('@/features/downloads/inspector-modal', () => ({ openWishlistInspector: vi.fn() }));
vi.mock('./interactive-search', () => ({
  InteractiveSearchModal: ({ entity }: { entity: { trackId: number } }) => (
    <div>library search for track {entity.trackId}</div>
  ),
}));

afterEach(() => {
  cleanup();
  vi.mocked(openWishlistInspector).mockClear();
});

const wish = { id: 'sp1', track: 'Kernkraft 400', artist: 'Topic', album: 'Kernkraft 400' };

describe('wishlist Interactive Search', () => {
  it('opens the library search for a wish linked to a library track', () => {
    render(<LibrarySearchHost />);
    act(() => openWishlistSearch({ ...wish, lib2TrackId: 1726 }));
    expect(screen.getByText('library search for track 1726')).toBeInTheDocument();
    expect(openWishlistInspector).not.toHaveBeenCalled();
  });

  it('falls back to the candidate inspector for a wish without one', () => {
    render(<LibrarySearchHost />);
    act(() => openWishlistSearch(wish));
    expect(openWishlistInspector).toHaveBeenCalledWith({
      id: 'sp1',
      name: 'Kernkraft 400',
      artist: 'Topic',
      album: 'Kernkraft 400',
    });
  });
});
