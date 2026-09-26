import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ArtistHero } from './artist-hero';

/**
 * The Delete Artist button in the hero row.
 *
 * The behaviour that matters is the order: nothing is requested until the
 * confirm comes back true. A delete that fires on click and asks afterwards
 * would pass a test that only checked "the request happened".
 */

const ARTIST = { id: 42, name: 'Don Felder' } as never;
const EMPTY_DISCOGRAPHY = { albums: [], eps: [], singles: [] } as never;

function renderHero(canDelete = true) {
  return render(
    <ArtistHero
      artist={ARTIST}
      discography={EMPTY_DISCOGRAPHY}
      isSourceArtist={false}
      canDelete={canDelete}
    />,
  );
}

const deleteButton = () =>
  document.getElementById('library-artist-delete-btn') as HTMLButtonElement | null;

/** The hero fetches top tracks and the watchlist state on mount, so a bare
    "fetch was called" assertion would pass on those. Only the delete counts. */
const deleteCalls = (spy: ReturnType<typeof vi.fn>) =>
  spy.mock.calls.filter(([url]) => String(url).includes('/api/library/artist/'));

beforeEach(() => {
  window.showToast = vi.fn() as never;
  window.navigateToPage = vi.fn() as never;
});

afterEach(() => {
  delete window.showToast;
  delete window.navigateToPage;
  delete window.showConfirmDialog;
  vi.unstubAllGlobals();
  cleanup();
});

describe('who sees it', () => {
  it('is offered to an admin on a library artist', () => {
    renderHero(true);
    expect(deleteButton()).not.toBeNull();
    expect(screen.getByText('Delete Artist')).toBeTruthy();
  });

  it('is absent without the permission', () => {
    renderHero(false);
    expect(deleteButton()).toBeNull();
  });
});

describe('the confirm gate', () => {
  it('sends NOTHING when the dialog is declined', async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({})));
    vi.stubGlobal('fetch', fetchSpy);
    window.showConfirmDialog = vi.fn(async () => false) as never;

    renderHero();
    fireEvent.click(deleteButton()!);

    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    expect(deleteCalls(fetchSpy)).toHaveLength(0);
    expect(window.navigateToPage).not.toHaveBeenCalled();
  });

  it('asks with the SoulSync dialog, marked destructive, naming the artist', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({}))),
    );
    window.showConfirmDialog = vi.fn(async () => false) as never;

    renderHero();
    fireEvent.click(deleteButton()!);

    await waitFor(() => expect(window.showConfirmDialog).toHaveBeenCalled());
    const options = (window.showConfirmDialog as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(options.destructive).toBe(true);
    expect(options.title).toBe('Delete Artist');
    expect(options.message).toContain('Don Felder');
    expect(options.message).toContain('Not one file on disk is deleted');
  });
});

describe('a confirmed delete', () => {
  it('sends the DELETE, toasts the counts, and leaves the dead page', async () => {
    const fetchSpy = vi.fn(
      async () =>
        new Response(JSON.stringify({ success: true, albums_deleted: 45, tracks_deleted: 234 })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    window.showConfirmDialog = vi.fn(async () => true) as never;

    renderHero();
    fireEvent.click(deleteButton()!);

    await waitFor(() => expect(window.navigateToPage).toHaveBeenCalledWith('library'));
    const [url, init] = deleteCalls(fetchSpy)[0];
    expect(url).toBe('/api/library/artist/42');
    expect(init).toMatchObject({ method: 'DELETE' });
    expect(window.showToast).toHaveBeenCalledWith(
      expect.stringContaining('45 albums and 234 tracks'),
      'success',
    );
  });

  it('stays put and re-enables the button when the server refuses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ success: false, error: 'Artist not found' }), {
            status: 404,
          }),
      ),
    );
    window.showConfirmDialog = vi.fn(async () => true) as never;

    renderHero();
    fireEvent.click(deleteButton()!);

    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        expect.stringContaining('Artist not found'),
        'error',
      ),
    );
    // a failed delete must not navigate away from a page that still exists
    expect(window.navigateToPage).not.toHaveBeenCalled();
    await waitFor(() => expect(deleteButton()!.disabled).toBe(false));
  });
});
