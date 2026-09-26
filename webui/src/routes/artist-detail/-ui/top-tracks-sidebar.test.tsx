import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createShellBridge } from '@/test/shell-bridge';

import { TopTracksSidebar } from './top-tracks-sidebar';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let calls: { url: string; body: unknown }[] = [];

function stubRoutes(routes: Record<string, () => Response | Promise<Response>>) {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
      for (const [fragment, handler] of Object.entries(routes)) {
        if (url.includes(fragment)) return handler();
      }
      return json({ success: false });
    }),
  );
}

const SOURCE_TRACKS = {
  success: true,
  source: 'spotify',
  tracks: [
    { name: 'Xtal', artists: [{ name: 'AFX' }] },
    { name: 'Ageispolis', artists: [] },
  ],
};

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
  delete window.openDownloadMissingModalForArtistAlbum;
});

describe('TopTracksSidebar', () => {
  it('stays out of the DOM entirely when nothing loads', async () => {
    // The vanilla left the panel display:none rather than showing an empty box.
    stubRoutes({});
    render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(document.getElementById('artist-hero-sidebar')).toBeNull();
  });

  it('renders numbered rows with the source heading', async () => {
    stubRoutes({ '/top-tracks': () => json(SOURCE_TRACKS) });
    render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);

    await screen.findByText('Xtal');
    expect(document.getElementById('hero-sidebar-title')?.textContent).toBe('Top Tracks (Spotify)');
    expect([...document.querySelectorAll('.hero-top-track-num')].map((n) => n.textContent)).toEqual(
      ['1', '2'],
    );
  });

  it('offers a download per row plus Download All for the source pass', async () => {
    stubRoutes({ '/top-tracks': () => json(SOURCE_TRACKS) });
    render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);

    await screen.findByText('Xtal');
    expect(document.querySelectorAll('.hero-top-track-download').length).toBe(2);
    expect(document.getElementById('hero-top-tracks-download-all')).not.toBeNull();
    expect(document.querySelector('.hero-top-track-plays')).toBeNull();
  });

  it('is display-only for the Last.fm pass — playcounts, no downloads', async () => {
    stubRoutes({
      'lastfm-top-tracks': () =>
        json({ success: true, tracks: [{ name: 'A', playcount: 2400000 }] }),
    });
    render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);

    await screen.findByText('A');
    expect(document.querySelector('.hero-top-track-plays')?.textContent).toBe('2.4M');
    expect(document.querySelector('.hero-top-track-download')).toBeNull();
    expect(document.getElementById('hero-top-tracks-download-all')).toBeNull();
  });

  it('wishlists a single row with its own metadata', async () => {
    stubRoutes({
      '/top-tracks': () => json(SOURCE_TRACKS),
      'add-album-to-wishlist': () => json({ success: true }),
    });
    render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);
    await screen.findByText('Xtal');

    fireEvent.click(document.querySelectorAll('.hero-top-track-download')[0]);

    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Added "Xtal" to wishlist', 'success'),
    );
    const posted = calls.find((c) => c.url.includes('add-album-to-wishlist'))?.body as {
      track: { name: string; artists: { name: string }[] };
      source_type: string;
    };
    expect(posted.track.name).toBe('Xtal');
    expect(posted.track.artists).toEqual([{ name: 'AFX' }]);
    expect(posted.source_type).toBe('top_tracks');
  });

  it('surfaces a wishlist failure instead of claiming success', async () => {
    stubRoutes({
      '/top-tracks': () => json(SOURCE_TRACKS),
      'add-album-to-wishlist': () => json({ success: false, error: 'nope' }),
    });
    render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);
    await screen.findByText('Xtal');

    fireEvent.click(document.querySelectorAll('.hero-top-track-download')[0]);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Failed to wishlist "Xtal": nope', 'error'),
    );
  });

  it('opens the bulk download in PLAYLIST context', async () => {
    // contextType 'playlist' is what renders the playlist hero and keeps each
    // track routed to its own album folder.
    window.openDownloadMissingModalForArtistAlbum = vi.fn();
    stubRoutes({ '/top-tracks': () => json(SOURCE_TRACKS) });
    render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);
    await screen.findByText('Xtal');

    fireEvent.click(document.getElementById('hero-top-tracks-download-all') as HTMLElement);

    expect(window.openDownloadMissingModalForArtistAlbum).toHaveBeenCalledWith(
      'top_tracks_spotify_42',
      'Aphex Twin — Top Tracks',
      SOURCE_TRACKS.tracks,
      expect.objectContaining({ album_type: 'compilation' }),
      expect.objectContaining({ name: 'Aphex Twin' }),
      true,
      'playlist',
    );
  });

  it('plays a row through the library-first resolver', async () => {
    stubRoutes({
      '/top-tracks': () => json(SOURCE_TRACKS),
      'resolve-track': () =>
        json({ success: true, track: { id: 7, title: 'Xtal', file_path: '/x.flac' } }),
    });
    render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);
    await screen.findByText('Xtal');

    fireEvent.click(document.querySelectorAll('.hero-top-track-play')[0]);

    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge?.playLibraryTrack).toHaveBeenCalledWith(
        expect.objectContaining({ id: 7 }),
        '',
        'AFX',
      ),
    );
  });

  it('plays a Last.fm row under the PAGE artist, not a track artist', async () => {
    stubRoutes({
      'lastfm-top-tracks': () =>
        json({ success: true, tracks: [{ name: 'A', artists: [{ name: 'Wrong' }] }] }),
      'resolve-track': () => json({ success: true, track: { id: 1, title: 'A', file_path: '/a' } }),
    });
    render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);
    await screen.findByText('A');

    fireEvent.click(document.querySelector('.hero-top-track-play') as HTMLElement);

    await waitFor(() =>
      expect(window.SoulSyncWebShellBridge?.playLibraryTrack).toHaveBeenCalledWith(
        expect.anything(),
        '',
        'Aphex Twin',
      ),
    );
  });

  it('clears the previous artist rows BEFORE the next load lands', async () => {
    stubRoutes({ '/top-tracks': () => json(SOURCE_TRACKS) });
    const { rerender } = render(<TopTracksSidebar artistId={42} artistName="Aphex Twin" />);
    await screen.findByText('Xtal');

    // The next artist's request never answers. Without the up-front reset the
    // previous artist's tracks would sit under the new artist's name for as
    // long as the request takes — which is exactly why the vanilla hid the
    // sidebar on entry rather than on response.
    let release: (r: Response) => void = () => {};
    stubRoutes({ '/top-tracks': () => new Promise<Response>((r) => (release = r)) });
    rerender(<TopTracksSidebar artistId={99} artistName="Boards of Canada" />);

    await waitFor(() => expect(screen.queryByText('Xtal')).toBeNull());
    release(json({ success: false }));
  });
});
