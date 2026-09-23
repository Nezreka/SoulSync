import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { BasicAlbum, BasicTrack } from './-basic.types';

import { downloadAlbum, downloadAlbumTrack, downloadTrack, startDownload } from './-basic.actions';

let toasts: { message: string; type?: string }[] = [];

beforeEach(() => {
  toasts = [];
  window.showToast = vi.fn((message: string, type?: string) => {
    toasts.push({ message, type });
  });
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  delete window.showToast;
  Reflect.deleteProperty(window, 'showConfirmDialog');
  vi.restoreAllMocks();
});

function track(over: Partial<BasicTrack> = {}): BasicTrack {
  return {
    result_type: 'track',
    username: 'peer',
    filename: 'music/a.mp3',
    size: 1000,
    bitrate: 320,
    duration: 200_000,
    quality: 'mp3',
    free_upload_slots: 1,
    upload_speed: 100,
    queue_length: 0,
    sample_rate: null,
    bit_depth: null,
    artist: 'Aphex Twin',
    title: 'Xtal',
    album: 'SAW',
    track_number: 1,
    quality_score: 0.8,
    ...over,
  };
}

function album(over: Partial<BasicAlbum> = {}): BasicAlbum {
  return {
    result_type: 'album',
    username: 'peer',
    album_path: '/music/saw',
    album_title: 'Selected Ambient Works',
    artist: 'Aphex Twin',
    track_count: 2,
    total_size: 5000,
    tracks: [track(), track({ title: 'Tha', filename: 'music/b.mp3', track_number: 2 })],
    dominant_quality: 'mp3',
    year: '1992',
    free_upload_slots: 1,
    upload_speed: 100,
    queue_length: 0,
    quality_score: 0.7,
    ...over,
  };
}

/** Capture what reaches /api/download, and choose the reply. */
function stubDownload(reply: Record<string, unknown> = { success: true }) {
  const bodies: Record<string, unknown>[] = [];
  server.use(
    http.post('/api/download', async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(reply);
    }),
  );
  return bodies;
}

describe('downloadTrack', () => {
  it('posts the track and names it in the toast', async () => {
    const bodies = stubDownload();
    await downloadTrack(track());
    expect(bodies[0]).toMatchObject({ result_type: 'track', filename: 'music/a.mp3' });
    expect(toasts).toEqual([{ message: 'Download started: Xtal', type: 'success' }]);
  });

  it('reports the server"s reason for a refusal', async () => {
    stubDownload({ success: false, error: 'no slots' });
    await downloadTrack(track());
    expect(toasts).toEqual([{ message: 'Download failed: no slots', type: 'error' }]);
  });

  it('survives a transport failure', async () => {
    server.use(http.post('/api/download', () => HttpResponse.error()));
    await downloadTrack(track());
    expect(toasts).toEqual([{ message: 'Failed to start download', type: 'error' }]);
  });
});

describe('downloadAlbum', () => {
  it('posts the album whole, tracks included', async () => {
    // The server iterates `tracks` itself; stripping them would queue nothing.
    const bodies = stubDownload({ success: true, message: 'Started 2 tracks' });
    await downloadAlbum(album());
    expect(bodies[0].result_type).toBe('album');
    expect((bodies[0].tracks as unknown[]).length).toBe(2);
  });

  it('shows the server"s own summary rather than a generic line', async () => {
    stubDownload({ success: true, message: 'Started 12 of 14 tracks' });
    await downloadAlbum(album());
    expect(toasts).toEqual([{ message: 'Started 12 of 14 tracks', type: 'success' }]);
  });

  it('reports a refusal as an album failure', async () => {
    stubDownload({ success: false, error: 'peer offline' });
    await downloadAlbum(album());
    expect(toasts).toEqual([{ message: 'Album download failed: peer offline', type: 'error' }]);
  });
});

describe('downloadAlbumTrack', () => {
  it('overrides result_type so the server takes the track branch', async () => {
    // Without it the server looks for a `tracks` array on a single file and
    // rejects the whole request.
    const bodies = stubDownload();
    await downloadAlbumTrack(album(), 1);
    expect(bodies[0].result_type).toBe('track');
    expect(bodies[0].filename).toBe('music/b.mp3');
    expect(toasts).toEqual([{ message: 'Download started: Tha', type: 'success' }]);
  });

  it('does nothing for an index that is not there', async () => {
    const bodies = stubDownload();
    await downloadAlbumTrack(album(), 99);
    expect(bodies).toEqual([]);
    expect(toasts).toEqual([]);
  });
});

/** a blocklisted artist: 409 {blocked} first, then whatever the override gets */
function stubBlocked(after: Record<string, unknown> = { success: true }) {
  const bodies: Record<string, unknown>[] = [];
  server.use(
    http.post('/api/download', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      bodies.push(body);
      if (!body.ignore_blocklist) {
        return HttpResponse.json(
          {
            success: false,
            blocked: true,
            blocked_entity_type: 'artist',
            blocked_name: 'Aphex Twin',
          },
          { status: 409 },
        );
      }
      return HttpResponse.json(after);
    }),
  );
  return bodies;
}

describe('blocklisted downloads', () => {
  it('asks, and on yes sends it again with ignore_blocklist', async () => {
    const bodies = stubBlocked();
    window.showConfirmDialog = vi.fn(async () => true);
    await downloadTrack(track());
    expect(window.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('Aphex Twin') }),
    );
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toMatchObject({ ignore_blocklist: true, filename: 'music/a.mp3' });
    expect(toasts).toEqual([{ message: 'Download started: Xtal', type: 'success' }]);
  });

  it('on no, skips it and says why instead of a generic failure', async () => {
    const bodies = stubBlocked();
    window.showConfirmDialog = vi.fn(async () => false);
    await downloadTrack(track());
    expect(bodies).toHaveLength(1);
    expect(toasts).toEqual([{ message: 'Skipped, Aphex Twin is blocklisted', type: 'info' }]);
  });

  it('works for a track taken out of an album too', async () => {
    const bodies = stubBlocked();
    window.showConfirmDialog = vi.fn(async () => true);
    await downloadAlbumTrack(album(), 1);
    expect(bodies[1]).toMatchObject({ ignore_blocklist: true, result_type: 'track', title: 'Tha' });
  });

  it('a 409 without blocked is still a failure', async () => {
    server.use(
      http.post('/api/download', () => HttpResponse.json({ error: 'conflict' }, { status: 409 })),
    );
    await downloadTrack(track());
    expect(toasts).toEqual([{ message: 'Failed to start download', type: 'error' }]);
  });
});

describe('startDownload', () => {
  it('a track posts the download', async () => {
    const bodies = stubDownload();
    startDownload({ kind: 'track', track: track() });
    await vi.waitFor(() => expect(bodies).toHaveLength(1));
  });

  it('an album posts the whole album', async () => {
    const bodies = stubDownload({ success: true, message: 'Started 2 downloads' });
    startDownload({ kind: 'album', album: album() });
    await vi.waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ result_type: 'album' });
  });

  it('a track out of an album posts just that track', async () => {
    const bodies = stubDownload();
    startDownload({ kind: 'albumTrack', album: album(), trackIndex: 1 });
    await vi.waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ result_type: 'track', title: 'Tha' });
  });
});
