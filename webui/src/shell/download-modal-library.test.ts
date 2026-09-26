import { HttpResponse, http } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import {
  hydrateDownloadModalLibraryStatus,
  monitorAddedTracks,
  startMonitoredDownloads,
} from './download-modal-library';

/** The status cells and counters every variant of the download dialog renders. */
function renderDialog(id: string, rows: number) {
  document.body.innerHTML = `
    <span id="stat-found-${id}">-</span>
    <span id="stat-missing-${id}">-</span>
    <table><tbody>${Array.from(
      { length: rows },
      (_, i) =>
        `<tr><td class="track-match-status match-checking" id="match-${id}-${i}">🔍 Pending</td></tr>`,
    ).join('')}</tbody></table>`;
}

const cell = (id: string, i: number) => document.getElementById(`match-${id}-${i}`)!;

describe('the download dialog knows the library as soon as it opens', () => {
  let checks: Array<{ tracks: Array<{ name: string; artist: string }> }>;

  beforeEach(() => {
    checks = [];
    server.use(
      http.post('/api/enhanced-search/library-check', async ({ request }) => {
        const body = (await request.json()) as (typeof checks)[number];
        checks.push(body);
        return HttpResponse.json({
          albums: [],
          tracks: body.tracks.map((t) =>
            t.name === 'Owned'
              ? { in_library: true, in_wishlist: false }
              : t.name === 'Wanted'
                ? { in_library: false, in_wishlist: true }
                : { in_library: false, in_wishlist: false },
          ),
        });
      }),
    );
  });

  it('marks owned, monitored and missing tracks and fills the counters', async () => {
    renderDialog('p1', 3);

    await hydrateDownloadModalLibraryStatus('p1', {
      tracks: [
        { name: 'Owned', artists: [{ name: 'Boards of Canada' }, { name: 'Guest' }] },
        { name: 'Wanted', artists: ['Boards of Canada'] },
        { title: 'Gone', artist_name: 'Boards of Canada' },
      ],
    });

    expect(checks[0]!.tracks).toEqual([
      { name: 'Owned', artist: 'Boards of Canada' },
      { name: 'Wanted', artist: 'Boards of Canada' },
      { name: 'Gone', artist: 'Boards of Canada' },
    ]);
    expect(cell('p1', 0).textContent).toBe('✅ In library');
    expect(cell('p1', 0).className).toBe('track-match-status match-found');
    expect(cell('p1', 1).textContent).toBe('🔖 Monitored');
    expect(cell('p1', 1).className).toBe('track-match-status match-monitored');
    expect(cell('p1', 2).textContent).toBe('❌ Missing');
    expect(cell('p1', 2).className).toBe('track-match-status match-missing');
    expect(document.getElementById('stat-found-p1')!.textContent).toBe('1');
    expect(document.getElementById('stat-missing-p1')!.textContent).toBe('2');
  });

  it("finds a guest's song on an album where the library files it: under the album artist", async () => {
    server.use(
      http.post('/api/enhanced-search/library-check', async ({ request }) => {
        const body = (await request.json()) as (typeof checks)[number];
        checks.push(body);
        return HttpResponse.json({
          albums: [],
          tracks: body.tracks.map((t) => ({
            in_library: t.artist === 'Michael Jackson',
            in_wishlist: false,
          })),
        });
      }),
    );
    renderDialog('p6', 2);

    await hydrateDownloadModalLibraryStatus('p6', {
      artist: { name: 'Michael Jackson' },
      tracks: [
        { name: "Somebody's Watching Me", artists: ['Rockwell'] },
        { name: 'Scream', artists: ['Michael Jackson', 'Janet Jackson'] },
      ],
    });

    // Asked twice for the guest, once for the album artist's own song.
    expect(checks[0]!.tracks).toEqual([
      { name: "Somebody's Watching Me", artist: 'Rockwell' },
      { name: "Somebody's Watching Me", artist: 'Michael Jackson' },
      { name: 'Scream', artist: 'Michael Jackson' },
    ]);
    expect(cell('p6', 0).textContent).toBe('✅ In library');
    expect(cell('p6', 1).textContent).toBe('✅ In library');
    expect(document.getElementById('stat-missing-p6')!.textContent).toBe('0');
  });

  it('leaves what Begin Analysis has already answered alone', async () => {
    renderDialog('p2', 1);
    cell('p2', 0).className = 'track-match-status match-found';
    cell('p2', 0).textContent = '✅ Found';
    document.getElementById('stat-found-p2')!.textContent = '1';

    await hydrateDownloadModalLibraryStatus('p2', { tracks: [{ name: 'Gone', artists: ['X'] }] });

    expect(cell('p2', 0).textContent).toBe('✅ Found');
    expect(document.getElementById('stat-found-p2')!.textContent).toBe('1');
  });

  it('asks a long playlist in chunks', async () => {
    renderDialog('p3', 450);

    await hydrateDownloadModalLibraryStatus('p3', {
      tracks: Array.from({ length: 450 }, (_, i) => ({
        name: i === 449 ? 'Owned' : `T${i}`,
        artists: ['A'],
      })),
    });

    expect(checks.map((c) => c.tracks.length)).toEqual([200, 200, 50]);
    expect(cell('p3', 449).textContent).toBe('✅ In library');
  });

  it('keeps the dialog as it was when the check fails', async () => {
    server.use(
      http.post('/api/enhanced-search/library-check', () =>
        HttpResponse.json({ error: 'down' }, { status: 500 }),
      ),
    );
    renderDialog('p4', 1);

    await hydrateDownloadModalLibraryStatus('p4', { tracks: [{ name: 'Owned', artists: ['A'] }] });

    expect(cell('p4', 0).textContent).toBe('🔍 Pending');
    expect(document.getElementById('stat-found-p4')!.textContent).toBe('-');
  });

  it('does nothing without tracks', async () => {
    await hydrateDownloadModalLibraryStatus('p5', undefined);
    await hydrateDownloadModalLibraryStatus('p5', { tracks: [] });
    expect(checks).toHaveLength(0);
  });
});

describe('Monitor downloads what it added right away', () => {
  let requests: Array<{ track_ids: string[] }>;
  let releases: string[];
  let status: number;

  beforeEach(() => {
    requests = [];
    releases = [];
    status = 200;
    server.use(
      http.post('/api/library/v2/albums/:id/monitor', async ({ params, request }) => {
        expect(await request.json()).toEqual({ monitored: true });
        releases.push(String(params.id));
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/wishlist/download_missing', async ({ request }) => {
        requests.push((await request.json()) as { track_ids: string[] });
        return status === 409
          ? HttpResponse.json({ error: 'busy', retry_after: 30 }, { status: 409 })
          : HttpResponse.json({ success: status === 200, batch_id: 'b1' }, { status });
      }),
    );
  });

  it('starts one batch for exactly the added tracks', async () => {
    expect(await startMonitoredDownloads(['t1', 't2', 't1', null, ''])).toBe('started');
    expect(requests).toEqual([{ track_ids: ['t1', 't2'] }]);
  });

  it('asks nothing when nothing was added', async () => {
    expect(await startMonitoredDownloads([null, undefined])).toBe('nothing');
    expect(requests).toHaveLength(0);
  });

  it('says so when the wishlist run is busy or the start fails', async () => {
    status = 409;
    expect(await monitorAddedTracks(2, [{ id: 't1' }, { id: 't2' }])).toBe(
      'Monitoring 2 tracks — the running wishlist pass takes them',
    );
    status = 500;
    expect(await monitorAddedTracks(1, [{ id: 't1' }])).toBe(
      'Monitoring 1 track — the download could not start, still wanted',
    );
    status = 200;
    expect(await monitorAddedTracks(1, [{ id: 't1' }])).toBe(
      'Monitoring 1 track — download started',
    );
    expect(await monitorAddedTracks(3, [])).toBe('Monitoring 3 tracks');
  });

  it('monitors the album itself when every track of it was picked', async () => {
    const rows = [
      { id: 't1::a', album: 7 },
      { id: 't2::a', album: 7 },
      { id: 't3::a', album: 7 },
    ];

    expect(await monitorAddedTracks(3, rows, true)).toBe(
      'Monitoring the album (3 tracks) — download started',
    );
    expect(releases).toEqual(['7']);
    expect(requests).toEqual([{ track_ids: ['t1::a', 't2::a', 't3::a'] }]);
  });

  it('leaves the album alone when only some tracks were picked', async () => {
    expect(await monitorAddedTracks(1, [{ id: 't1::a', album: 7 }], false)).toBe(
      'Monitoring 1 track — download started',
    );
    expect(releases).toHaveLength(0);
  });

  it('never monitors an album for tracks from several releases', async () => {
    await monitorAddedTracks(
      2,
      [
        { id: 'x', album: 7 },
        { id: 'y', album: 8 },
      ],
      true,
    );
    await monitorAddedTracks(1, [{ id: 'z', album: null }], true);
    expect(releases).toHaveLength(0);
  });
});
