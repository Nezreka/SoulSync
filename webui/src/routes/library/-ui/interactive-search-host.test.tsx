import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  openDownloadTaskInspector,
  openWishlistInspector,
} from '@/features/downloads/inspector-modal';
import { HttpResponse, http, server } from '@/test/msw';

import {
  LibrarySearchHost,
  openDownloadTaskSearch,
  openWishlistSearch,
} from './interactive-search-host';

vi.mock('@/features/downloads/inspector-modal', () => ({
  openWishlistInspector: vi.fn(),
  openDownloadTaskInspector: vi.fn(),
}));
vi.mock('./interactive-search', () => ({
  InteractiveSearchModal: ({ entity }: { entity: { trackId: number } }) => (
    <div>library search for track {entity.trackId}</div>
  ),
}));

afterEach(() => {
  cleanup();
  vi.mocked(openWishlistInspector).mockClear();
  vi.mocked(openDownloadTaskInspector).mockClear();
});

const wish = { id: 'sp1', track: 'Kernkraft 400', artist: 'Topic', album: 'Kernkraft 400' };

function canGrab(value: boolean) {
  server.use(
    http.get('*/api/library/v2/enabled', () =>
      HttpResponse.json({ success: true, can_grab: value }),
    ),
  );
}

describe('Interactive Search outside the library', () => {
  it('opens the library search for a wish linked to a library track', async () => {
    canGrab(true);
    render(<LibrarySearchHost />);
    await act(() => openWishlistSearch({ ...wish, lib2TrackId: 1726 }));
    await waitFor(() =>
      expect(screen.getByText('library search for track 1726')).toBeInTheDocument(),
    );
    expect(openWishlistInspector).not.toHaveBeenCalled();
  });

  it('gives a profile that may not grab from it the inspector instead', async () => {
    // a household member in the shared library: the library grab answers 403,
    // the inspector only needs download permission (3.6.1)
    canGrab(false);
    render(<LibrarySearchHost />);
    await act(() => openWishlistSearch({ ...wish, lib2TrackId: 1726 }));
    expect(openWishlistInspector).toHaveBeenCalled();
  });

  it('falls back to the candidate inspector for a wish without one', async () => {
    render(<LibrarySearchHost />);
    await act(() => openWishlistSearch(wish));
    expect(openWishlistInspector).toHaveBeenCalledWith({
      id: 'sp1',
      name: 'Kernkraft 400',
      artist: 'Topic',
      album: 'Kernkraft 400',
    });
  });

  it('a failed download always opens its own task inspector, to retry it in its batch', async () => {
    const track = { name: 'Lost Forever', artist: 'Sergio' };
    await openDownloadTaskSearch('t1', track);
    expect(openDownloadTaskInspector).toHaveBeenCalledWith('t1', track);
  });
});
