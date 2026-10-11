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

describe('Interactive Search outside the library', () => {
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

  it('opens the library search for a failed download of a library track', async () => {
    server.use(
      http.get('*/api/downloads/task/t1/library-track', () =>
        HttpResponse.json({ success: true, track_id: 1105 }),
      ),
    );
    render(<LibrarySearchHost />);
    await act(() => openDownloadTaskSearch('t1', { name: 'Lost Forever', artist: 'Sergio' }));
    await waitFor(() =>
      expect(screen.getByText('library search for track 1105')).toBeInTheDocument(),
    );
    expect(openDownloadTaskInspector).not.toHaveBeenCalled();
  });

  it('keeps the inspector for a download that is for no library track', async () => {
    server.use(
      http.get('*/api/downloads/task/t2/library-track', () =>
        HttpResponse.json({ success: true, track_id: null }),
      ),
    );
    const track = { name: 'Song', artist: 'Someone' };
    await openDownloadTaskSearch('t2', track);
    expect(openDownloadTaskInspector).toHaveBeenCalledWith('t2', track);
  });
});
