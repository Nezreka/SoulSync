import { HttpResponse, http } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import {
  markWishlistedTrack,
  monitorAddedTracks,
  startMonitoredDownloads,
} from './download-modal-library';

describe('the download dialog marks a missing track the user already wants', () => {
  it('draws "On Wishlist" with the library heart', () => {
    document.body.innerHTML = `<table><tbody><tr>
      <td class="track-match-status match-checking" id="match-p1-0">🔍 Pending</td>
    </tr></tbody></table>`;
    const cell = document.getElementById('match-p1-0')!;
    markWishlistedTrack(cell);
    expect(cell.textContent).toBe('On Wishlist');
    expect(cell.className).toBe('track-match-status match-monitored');
    expect(cell.querySelector('svg.match-bookmark')).not.toBeNull();
  });
});

describe('Add to Wishlist downloads what it added right away', () => {
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
      'Added 2 tracks to your Wishlist — the running wishlist pass takes them',
    );
    status = 500;
    expect(await monitorAddedTracks(1, [{ id: 't1' }])).toBe(
      'Added 1 track to your Wishlist — the download could not start, still wanted',
    );
    status = 200;
    expect(await monitorAddedTracks(1, [{ id: 't1' }])).toBe(
      'Added 1 track to your Wishlist — download started',
    );
    expect(await monitorAddedTracks(3, [])).toBe('Added 3 tracks to your Wishlist');
  });

  it('monitors the album itself when every track of it was picked', async () => {
    const rows = [
      { id: 't1::a', album: 7 },
      { id: 't2::a', album: 7 },
      { id: 't3::a', album: 7 },
    ];

    expect(await monitorAddedTracks(3, rows, true)).toBe(
      'Added the album to your Wishlist (3 tracks) — download started',
    );
    expect(releases).toEqual(['7']);
    expect(requests).toEqual([{ track_ids: ['t1::a', 't2::a', 't3::a'] }]);
  });

  it('leaves the album alone when only some tracks were picked', async () => {
    expect(await monitorAddedTracks(1, [{ id: 't1::a', album: 7 }], false)).toBe(
      'Added 1 track to your Wishlist — download started',
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
