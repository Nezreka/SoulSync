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
  afterEach(() => {
    document.body.innerHTML = '';
    delete (window as { SoulSyncWebRouter?: unknown }).SoulSyncWebRouter;
  });

  const TRACKS = [{ id: 't1', name: 'A Song', artists: ['An Artist'], duration_ms: 1000 }];

  function stubLoad(
    playlist: Record<string, unknown>,
    mirror: Record<string, unknown> = { success: true },
  ) {
    const posted: unknown[] = [];
    server.use(
      http.get('/api/deezer/playlist/:id', () => HttpResponse.json(playlist)),
      http.post('/api/mirror-playlist', async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json(mirror);
      }),
    );
    return posted;
  }

  it('mirrors the playlist and lands the user on the mirrored tab', async () => {
    // this is the whole fix: the first version drove the RETIRED vanilla
    // sync page and did nothing at all
    document.body.innerHTML =
      '<button class="sync-tab-button" data-tab="mirrored"></button>';
    const clicked = vi.fn();
    document.querySelector('.sync-tab-button')!.addEventListener('click', clicked);
    const navigate = vi.fn();
    window.SoulSyncWebRouter = { navigateToPage: navigate } as never;

    const posted = stubLoad({
      id: '123', name: 'Rock Essentials', owner: 'Rod', image_url: 'https://cdn/x.jpg',
      tracks: TRACKS,
    });

    await expect(openDeezerPlaylistInSync(PLAYLIST)).resolves.toBeNull();

    expect(posted).toHaveLength(1);
    expect((posted[0] as { name: string }).name).toBe('Rock Essentials');
    expect(navigate).toHaveBeenCalledWith('sync');
    await vi.waitFor(() => expect(clicked).toHaveBeenCalled());
  });

  it('does not navigate when the playlist could not be loaded', async () => {
    const navigate = vi.fn();
    window.SoulSyncWebRouter = { navigateToPage: navigate } as never;
    server.use(
      http.get('/api/deezer/playlist/:id', () =>
        HttpResponse.json({ error: 'Invalid Deezer playlist ID' }, { status: 400 }),
      ),
    );

    await expect(openDeezerPlaylistInSync(PLAYLIST)).resolves.toBe('Invalid Deezer playlist ID');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('refuses an empty playlist rather than mirroring nothing', async () => {
    const navigate = vi.fn();
    window.SoulSyncWebRouter = { navigateToPage: navigate } as never;
    stubLoad({ id: '123', name: 'Empty', tracks: [] });

    await expect(openDeezerPlaylistInSync(PLAYLIST)).resolves.toBe('That playlist came back empty');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('reports a refused mirror instead of pretending it worked', async () => {
    const navigate = vi.fn();
    window.SoulSyncWebRouter = { navigateToPage: navigate } as never;
    stubLoad({ id: '1', name: 'X', tracks: TRACKS }, { success: false, error: 'nope' });

    await expect(openDeezerPlaylistInSync(PLAYLIST)).resolves.toBe('nope');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('survives a transport failure', async () => {
    server.use(http.get('/api/deezer/playlist/:id', () => HttpResponse.error()));
    await expect(openDeezerPlaylistInSync(PLAYLIST)).resolves.toBeTruthy();
  });
});

describe('progress while a playlist loads', () => {
  afterEach(() => {
    delete (window as { SoulSyncWebRouter?: unknown }).SoulSyncWebRouter;
  });

  it('reports the track count as the job reports it', async () => {
    // the whole complaint: the card said "Adding to Sync" for ~2 minutes with
    // no idea what was happening or how long was left
    window.SoulSyncWebRouter = { navigateToPage: vi.fn() } as never;
    let polls = 0;
    server.use(
      http.get('/api/deezer/playlist/:id', () =>
        HttpResponse.json({ pending: true, job_id: 'job-1' }),
      ),
      http.get('/api/deezer/playlist-load/job-1', () => {
        polls += 1;
        if (polls < 3) {
          return HttpResponse.json({
            status: 'running',
            progress: { done: polls * 80, total: 200, phase: 'tracks' },
          });
        }
        return HttpResponse.json({
          status: 'complete',
          playlist: { id: '1', name: 'Calm Piano', tracks: [{ id: 't', name: 'A' }] },
        });
      }),
      http.post('/api/mirror-playlist', () => HttpResponse.json({ success: true })),
    );

    const stages: { phase: string; done?: number; total?: number }[] = [];
    const error = await openDeezerPlaylistInSync(PLAYLIST, (s) => stages.push({ ...s }));

    expect(error).toBeNull();
    expect(stages.some((s) => s.phase === 'loading' && s.done === 80 && s.total === 200)).toBe(true);
    expect(stages.some((s) => s.phase === 'loading' && s.done === 160)).toBe(true);
    // and the final stage is the quick one, so the bar can finish
    expect(stages.at(-1)?.phase).toBe('mirroring');
  });

  it('opens with the count it already knows, before the first poll', async () => {
    window.SoulSyncWebRouter = { navigateToPage: vi.fn() } as never;
    server.use(
      http.get('/api/deezer/playlist/:id', () => HttpResponse.error()),
    );
    const stages: { phase: string; total?: number }[] = [];
    await openDeezerPlaylistInSync({ ...PLAYLIST, track_count: 200 }, (s) => stages.push({ ...s }));
    // the shelf already knows the playlist has 200 tracks; showing that at once
    // beats an empty bar until the job's first frame lands
    expect(stages[0]).toEqual({ phase: 'loading', total: 200 });
  });

  it('surfaces the loader error rather than a generic one', async () => {
    server.use(
      http.get('/api/deezer/playlist/:id', () =>
        HttpResponse.json({ pending: true, job_id: 'job-2' }),
      ),
      http.get('/api/deezer/playlist-load/job-2', () =>
        HttpResponse.json({ status: 'error', error: 'Deezer refused the request' }),
      ),
    );
    await expect(openDeezerPlaylistInSync(PLAYLIST)).resolves.toBe('Deezer refused the request');
  });
});
