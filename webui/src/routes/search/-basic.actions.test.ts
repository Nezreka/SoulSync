import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { BasicAlbum, BasicTrack } from './-basic.types';

import {
  downloadAlbum,
  downloadAlbumTrack,
  downloadTrack,
  downloadUnmatched,
  matchedDownloadAlbum,
  matchedDownloadAlbumTrack,
  matchedDownloadTrack,
  streamAlbumTrack,
  streamTrack,
} from './-basic.actions';

let toasts: { message: string; type?: string }[] = [];

beforeEach(() => {
  toasts = [];
  window.showToast = vi.fn((message: string, type?: string) => {
    toasts.push({ message, type });
  });
  window.openMatchingModal = vi.fn();
  window.startStream = vi.fn();
  window.getFileExtension = (filename: string) => filename.split('.').pop() ?? '';
  // Default: everything plays. Individual tests override.
  window.isAudioFormatSupported = () => true;
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  delete window.showToast;
  delete window.openMatchingModal;
  delete window.startStream;
  delete window.getFileExtension;
  delete window.isAudioFormatSupported;
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

describe('matched downloads', () => {
  // These three are declared twice in the vanilla — downloads.js and
  // wishlist-tools.js, with different behaviour. wishlist-tools.js loads
  // second, so ITS versions are the ones that have been running.
  it('sends a single track with no album context', () => {
    const row = track();
    matchedDownloadTrack(row);
    expect(window.openMatchingModal).toHaveBeenCalledWith(row, false, null);
  });

  it('identifies an album by its FIRST TRACK, with the album as context', () => {
    // A folder has no tags worth matching on; the modal searches with a real
    // track's metadata and applies the answer to the album.
    const row = album();
    matchedDownloadAlbum(row);
    expect(window.openMatchingModal).toHaveBeenCalledWith(row.tracks[0], true, row);
  });

  it('falls back to the album itself when it carries no tracks', () => {
    const row = album({ tracks: [] });
    matchedDownloadAlbum(row);
    expect(window.openMatchingModal).toHaveBeenCalledWith(row, true, row);
  });

  it('treats an album track as a single track, album passed only as context', () => {
    // `false` matters: `true` would make the modal ask the user to choose an
    // album for a file they already located inside one.
    const row = album();
    matchedDownloadAlbumTrack(row, 1);
    expect(window.openMatchingModal).toHaveBeenCalledWith(row.tracks[1], false, row);
  });

  it('ignores a track index that is not there', () => {
    matchedDownloadAlbumTrack(album(), 99);
    expect(window.openMatchingModal).not.toHaveBeenCalled();
  });
});

describe('streamTrack', () => {
  it('streams a playable file', async () => {
    const row = track();
    await streamTrack(row);
    expect(window.startStream).toHaveBeenCalledWith(row);
  });

  it('refuses a format the browser cannot play, naming it', async () => {
    window.isAudioFormatSupported = () => false;
    await streamTrack(track({ filename: 'music/a.wma' }));
    expect(window.startStream).not.toHaveBeenCalled();
    expect(toasts[0].message).toContain('WMA');
    expect(toasts[0].message).toContain('not supported');
  });

  it('skips the codec check for streaming sources', async () => {
    // Their "filename" is an opaque id with no extension, so the check would
    // reject every one of them.
    window.isAudioFormatSupported = () => false;
    for (const username of ['youtube', 'tidal', 'qobuz', 'hifi']) {
      await streamTrack(track({ username, filename: 'abc123' }));
    }
    expect(window.startStream).toHaveBeenCalledTimes(4);
    expect(toasts).toEqual([]);
  });

  it('streams a result with no filename rather than checking nothing', async () => {
    window.isAudioFormatSupported = () => false;
    await streamTrack(track({ filename: '' }));
    expect(window.startStream).toHaveBeenCalled();
  });

  it('reports a player failure', async () => {
    window.startStream = vi.fn(() => {
      throw new Error('player down');
    });
    await streamTrack(track());
    expect(toasts).toEqual([{ message: 'Failed to start track stream', type: 'error' }]);
  });
});

describe('streamAlbumTrack', () => {
  it('streams the track, filling gaps from the album', async () => {
    const row = album();
    await streamAlbumTrack(row, 1);
    expect(window.startStream).toHaveBeenCalledWith(
      expect.objectContaining({ filename: 'music/b.mp3', username: 'peer', artist: 'Aphex Twin' }),
    );
  });

  it("falls back to the album's artist and title for a bare track", async () => {
    const row = album({
      artist: 'Album Artist',
      album_title: 'The Album',
      tracks: [track({ artist: null, album: null, username: '' })],
    });
    await streamAlbumTrack(row, 0);
    expect(window.startStream).toHaveBeenCalledWith(
      expect.objectContaining({ artist: 'Album Artist', album: 'The Album', username: 'peer' }),
    );
  });

  it('treats a streaming-source result as the track itself', async () => {
    // Those sources return FLAT rows — the "album" IS the track, with no
    // tracks array — so indexing into one would find nothing.
    const flat = album({ username: 'youtube', tracks: [] }) as unknown as Record<string, unknown>;
    flat.title = 'Some Video';
    flat.filename = 'yt-id-123';

    await streamAlbumTrack(flat as unknown as BasicAlbum, 0);

    expect(window.startStream).toHaveBeenCalledWith(
      expect.objectContaining({ filename: 'yt-id-123', album: 'Some Video' }),
    );
  });

  it('says so when the track is not in the album', async () => {
    await streamAlbumTrack(album(), 99);
    expect(window.startStream).not.toHaveBeenCalled();
    expect(toasts).toEqual([{ message: 'Track not found in album', type: 'error' }]);
  });

  it('refuses an unplayable album track', async () => {
    window.isAudioFormatSupported = () => false;
    await streamAlbumTrack(album({ tracks: [track({ filename: 'a.ape' })] }), 0);
    expect(window.startStream).not.toHaveBeenCalled();
    expect(toasts[0].message).toContain('APE');
  });
});

describe('downloadUnmatched', () => {
  // The "Skip Matching" button. Its old path could not work for three
  // independent reasons — see the doc comment on downloadUnmatched.
  it('downloads a track', async () => {
    const bodies = stubDownload();
    await downloadUnmatched(track());
    expect(bodies[0].result_type).toBe('track');
    expect(toasts).toEqual([{ message: 'Download started: Xtal', type: 'success' }]);
  });

  it('actually downloads an album instead of only claiming to', async () => {
    // The vanilla's album branch toasted "Starting album download (unmatched)"
    // above a comment reading "This would need to be implemented".
    const bodies = stubDownload({ success: true, message: 'Started 2 tracks' });
    await downloadUnmatched(album());
    expect(bodies).toHaveLength(1);
    expect(bodies[0].result_type).toBe('album');
    expect(toasts).toEqual([{ message: 'Started 2 tracks', type: 'success' }]);
  });

  it('does nothing when handed nothing', async () => {
    const bodies = stubDownload();
    await downloadUnmatched(null as unknown as BasicTrack);
    expect(bodies).toEqual([]);
  });
});
