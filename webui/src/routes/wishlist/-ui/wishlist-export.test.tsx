import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { WishlistExportDialog, wishlistExportFileName, wishlistExportUrl } from './wishlist-export';

/** c5pie: export the wishlist as JSON or CSV, all of it or movies / TV only. */

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe('wishlist export endpoints', () => {
  it('slices the video wishlist by kind and the music one by category', () => {
    expect(wishlistExportUrl('video', 'all', 'csv')).toBe('/api/video/wishlist/export?format=csv');
    expect(wishlistExportUrl('video', 'movie', 'json')).toBe(
      '/api/video/wishlist/export?format=json&kind=movie',
    );
    expect(wishlistExportUrl('music', 'singles', 'csv')).toBe(
      '/api/wishlist/export?format=csv&category=singles',
    );
    expect(wishlistExportFileName('video', 'show', 'csv')).toBe('video_wishlist_show.csv');
  });
});

describe('WishlistExportDialog', () => {
  it('opens on the slice it was given and refetches when the slice or format changes', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string) => {
        calls.push(input);
        return new Response('type,title\nmovie,Heat\n', { headers: { 'X-Export-Count': '1' } });
      }),
    );
    render(<WishlistExportDialog side="video" initialScope="show" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('1')).toBeTruthy());
    expect(calls[0]).toBe('/api/video/wishlist/export?format=csv&kind=show');
    expect(screen.getByText(/movie,Heat/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Movies' }));
    await waitFor(() =>
      expect(calls.at(-1)).toBe('/api/video/wishlist/export?format=csv&kind=movie'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'JSON' }));
    await waitFor(() =>
      expect(calls.at(-1)).toBe('/api/video/wishlist/export?format=json&kind=movie'),
    );
  });

  it('says so when the export fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 500 })),
    );
    render(<WishlistExportDialog side="music" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/Export failed/)).toBeTruthy());
  });
});
