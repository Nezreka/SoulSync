import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
  fetchDeezerEditorial,
  fetchDeezerEditorialGenres,
  openDeezerPlaylistInSync,
} from './-discover.deezer-editorial';

const PLAYLIST = {
  id: '1306931615',
  title: 'Rock Essentials',
  creator: 'Rod - Deezer Rock Editor',
  track_count: 100,
  image_url: 'https://cdn/1000.jpg',
  link: 'https://www.deezer.com/playlist/1306931615',
  source: 'deezer',
};

describe('fetchDeezerEditorial', () => {
  it('returns the playlists for a genre', async () => {
    let asked = '';
    server.use(
      http.get('/api/discover/deezer/editorial', ({ request }) => {
        asked = new URL(request.url).searchParams.get('genre') ?? '';
        return HttpResponse.json({ success: true, playlists: [PLAYLIST], count: 1 });
      }),
    );
    const rows = await fetchDeezerEditorial(152);
    expect(asked).toBe('152');
    expect(rows[0].title).toBe('Rock Essentials');
  });

  it('a dead shelf is an empty shelf, never a thrown page', async () => {
    // the row must not be able to take Discover down with it
    server.use(http.get('/api/discover/deezer/editorial', () => HttpResponse.error()));
    await expect(fetchDeezerEditorial(0)).resolves.toEqual([]);
  });

  it('survives a response with no playlists key', async () => {
    server.use(
      http.get('/api/discover/deezer/editorial', () => HttpResponse.json({ success: true })),
    );
    await expect(fetchDeezerEditorial(0)).resolves.toEqual([]);
  });
});

describe('fetchDeezerEditorialGenres', () => {
  it('returns the chips', async () => {
    server.use(
      http.get('/api/discover/deezer/genres', () =>
        HttpResponse.json({ success: true, genres: [{ id: 0, name: 'Top' }] }),
      ),
    );
    await expect(fetchDeezerEditorialGenres()).resolves.toEqual([{ id: 0, name: 'Top' }]);
  });

  it('an unreachable genre list hides the chips, not the shelf', async () => {
    server.use(http.get('/api/discover/deezer/genres', () => HttpResponse.error()));
    await expect(fetchDeezerEditorialGenres()).resolves.toEqual([]);
  });
});

describe('openDeezerPlaylistInSync', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = '<input id="deezer-url-input" />';
  });
  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = '';
    delete (window as { navigateToPage?: unknown }).navigateToPage;
    delete (window as { loadDeezerPlaylist?: unknown }).loadDeezerPlaylist;
  });

  it('hands the playlist to the existing Sync flow rather than loading it itself', () => {
    const navigate = vi.fn();
    const load = vi.fn();
    window.navigateToPage = navigate;
    window.loadDeezerPlaylist = load;

    expect(openDeezerPlaylistInSync(PLAYLIST)).toBe(true);
    expect(navigate).toHaveBeenCalledWith('sync');

    // the input only exists after the page mounts, hence the deferral
    vi.runAllTimers();
    const input = document.getElementById('deezer-url-input') as HTMLInputElement;
    expect(input.value).toBe(PLAYLIST.link);
    expect(load).toHaveBeenCalled();
  });

  it('builds a link when the api did not send one', () => {
    window.navigateToPage = vi.fn();
    window.loadDeezerPlaylist = vi.fn();
    openDeezerPlaylistInSync({ ...PLAYLIST, link: '' });
    vi.runAllTimers();
    const input = document.getElementById('deezer-url-input') as HTMLInputElement;
    expect(input.value).toBe('https://www.deezer.com/playlist/1306931615');
  });

  it('reports failure when the shell cannot navigate', () => {
    expect(openDeezerPlaylistInSync(PLAYLIST)).toBe(false);
  });

  it('does not throw when the Sync page has no url input', () => {
    document.body.innerHTML = '';
    window.navigateToPage = vi.fn();
    window.loadDeezerPlaylist = vi.fn();
    expect(openDeezerPlaylistInSync(PLAYLIST)).toBe(true);
    expect(() => vi.runAllTimers()).not.toThrow();
  });
});
