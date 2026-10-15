import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { addArtistToWatchlist, WatchlistAddModal } from './watchlist-add-modal';

/**
 * The artist page's "Add to Watchlist" modal: per-artist settings at add
 * time (toggle pills for the include_* fields, tri-state select for
 * auto-download), then add + configure in that order.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderModal() {
  return render(
    <WatchlistAddModal
      artist={{ id: 'sp1', name: 'Aphex Twin' }}
      onClose={vi.fn()}
      onWatched={vi.fn()}
      onDownloadExisting={vi.fn()}
    />,
  );
}

describe('WatchlistAddModal', () => {
  it('shows the artist photo hero when an image is passed', () => {
    render(
      <WatchlistAddModal
        artist={{ id: 'sp1', name: 'Aphex Twin', imageUrl: 'https://img/artist.jpg' }}
        onClose={vi.fn()}
        onWatched={vi.fn()}
        onDownloadExisting={vi.fn()}
      />,
    );
    const photo = document.querySelector('.watchadd-hero-photo') as HTMLImageElement;
    expect(photo.tagName).toBe('IMG');
    expect(photo.getAttribute('src')).toBe('https://img/artist.jpg');
    expect(document.querySelector('.watchadd-hero-bg')).toBeTruthy();
  });

  it('falls back to the note tile when there is no image', () => {
    renderModal();
    const fallback = document.querySelector('.watchadd-hero-photo-fallback');
    expect(fallback?.textContent).toContain('🎵');
    expect(document.querySelector('.watchadd-hero-bg')).toBeNull();
  });
  it('renders the settings with watchlist defaults', () => {
    renderModal();
    expect(screen.getByText('Watch Aphex Twin')).toBeTruthy();
    const albums = screen.getByRole('button', { name: 'Albums' });
    expect(albums.getAttribute('aria-pressed')).toBe('true');
    const remixes = screen.getByRole('button', { name: 'Remixes' });
    expect(remixes.getAttribute('aria-pressed')).toBe('false');
    const select = screen.getByLabelText('New releases from this artist') as HTMLSelectElement;
    expect(select.value).toBe('');
  });

  it('toggles a pill', () => {
    renderModal();
    const remixes = screen.getByRole('button', { name: 'Remixes' });
    fireEvent.click(remixes);
    expect(remixes.getAttribute('aria-pressed')).toBe('true');
    expect(remixes.className).toContain('active');
  });

  it('watches with the chosen settings, then closes', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(String(input));
        return new Response(JSON.stringify({ success: true, message: 'Added.' }));
      }),
    );
    const onClose = vi.fn();
    const onWatched = vi.fn();
    render(
      <WatchlistAddModal
        artist={{ id: 'sp1', name: 'Aphex Twin' }}
        onClose={onClose}
        onWatched={onWatched}
        onDownloadExisting={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Remixes' }));
    fireEvent.click(screen.getByText('Watch'));
    await vi.waitFor(() => expect(onWatched).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
    expect(calls).toEqual(['/api/watchlist/add', '/api/watchlist/artist/sp1/config']);
  });

  it('shows the error without closing when the add fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ success: false, error: 'nope' }))),
    );
    const onClose = vi.fn();
    render(
      <WatchlistAddModal
        artist={{ id: 'sp1', name: 'Aphex Twin' }}
        onClose={onClose}
        onWatched={vi.fn()}
        onDownloadExisting={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('Watch'));
    await vi.waitFor(() => expect(screen.getByText('nope')).toBeTruthy());
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe('addArtistToWatchlist', () => {
  it('adds before configuring, and posts the settings', async () => {
    const seen: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        seen.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
        return new Response(JSON.stringify({ success: true }));
      }),
    );
    await addArtistToWatchlist('sp1', 'Aphex Twin', {
      include_albums: true,
      include_eps: false,
      include_singles: true,
      include_live: true,
      include_remixes: false,
      include_acoustic: false,
      include_compilations: false,
      include_instrumentals: false,
      include_other_artists: false,
      auto_download_pref: 'on',
    });
    expect(seen.map((s) => s.url)).toEqual([
      '/api/watchlist/add',
      '/api/watchlist/artist/sp1/config',
    ]);
    const config = seen[1].body as Record<string, unknown>;
    expect(config.include_eps).toBe(false);
    expect(config.include_live).toBe(true);
    expect(config.auto_download_pref).toBe('on');
  });
});
