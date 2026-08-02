import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  applyArtRequest,
  artistArtAppliedMessage,
  blacklistSourceRequest,
  deleteLibraryAlbumRequest,
  deleteLibraryTrackRequest,
  fetchArtOptions,
  fetchTrackSourceInfo,
  SOURCE_SERVICES,
  sourceInfoRows,
} from './-artist-detail.manage-actions';

/**
 * The delete/source-info/art API layer. What these pin is the vanilla's exact
 * toast wording and request shapes — the part a live user actually sees, and
 * the part a port silently drifts on.
 */

function stubFetch(body: unknown) {
  const spy = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(body), { status: 200 }),
  );
  vi.stubGlobal('fetch', spy);
  return spy;
}

afterEach(() => vi.unstubAllGlobals());

describe('deleteLibraryTrackRequest', () => {
  it('db_only sends no delete_file param and words the toast plainly', async () => {
    const spy = stubFetch({ success: true });
    const toast = await deleteLibraryTrackRequest(7, 'db_only');
    expect(String(spy.mock.calls[0][0])).toBe('/api/library/track/7?');
    expect(toast).toEqual({
      message: 'Track removed from library',
      tone: 'success',
      extra: undefined,
    });
  });

  it('delete_file carries the param and reports disk deletion', async () => {
    const spy = stubFetch({ success: true, file_deleted: true });
    const toast = await deleteLibraryTrackRequest(7, 'delete_file');
    expect(String(spy.mock.calls[0][0])).toContain('delete_file=true');
    expect(toast.message).toBe('Track deleted from library and disk');
  });

  it('a file error downgrades to warning and carries the long-lived extra toast', async () => {
    stubFetch({ success: true, file_error: 'Permission denied' });
    const toast = await deleteLibraryTrackRequest(7, 'delete_file');
    expect(toast.tone).toBe('warning');
    expect(toast.message).toBe('Track removed from library but file could not be deleted');
    expect(toast.extra).toBe('Permission denied');
  });

  it('appends the blacklisted suffix and throws on failure', async () => {
    stubFetch({ success: true, blacklisted: true });
    expect((await deleteLibraryTrackRequest(7, 'db_only')).message).toBe(
      'Track removed from library (source blacklisted)',
    );
    stubFetch({ success: false, error: 'nope' });
    await expect(deleteLibraryTrackRequest(7, 'db_only')).rejects.toThrow('nope');
  });
});

describe('deleteLibraryAlbumRequest', () => {
  it('db_only reports the track count', async () => {
    const spy = stubFetch({ success: true, tracks_deleted: 12 });
    const toast = await deleteLibraryAlbumRequest(3, 'db_only');
    expect(String(spy.mock.calls[0][0])).toBe('/api/library/album/3');
    expect(toast).toEqual({ message: 'Album removed from library (12 tracks)', tone: 'success' });
  });

  it('delete_files reports disk removals and downgrades on partial failure', async () => {
    stubFetch({ success: true, files_deleted: 10, files_failed: 2 });
    const toast = await deleteLibraryAlbumRequest(3, 'delete_files');
    expect(toast.message).toBe(
      'Album deleted — 10 files removed from disk (2 files could not be deleted)',
    );
    expect(toast.tone).toBe('warning');
  });
});

describe('source info', () => {
  it('an unsuccessful or empty response yields no downloads', async () => {
    stubFetch({ success: false });
    expect(await fetchTrackSourceInfo(1)).toEqual([]);
    stubFetch({ success: true, downloads: [{ source_service: 'tidal' }] });
    expect(await fetchTrackSourceInfo(1)).toHaveLength(1);
  });

  it('rows: soulseek gets a User row, filenames reduce to basenames across path styles', () => {
    const rows = sourceInfoRows({
      source_service: 'soulseek',
      source_username: 'peer42',
      source_filename: 'C:\\music\\Album\\01 - Song.flac',
      source_size: 31457280,
      bit_depth: 16,
      sample_rate: 44100,
      bitrate: 1411000,
      status: 'completed',
    });
    expect(rows[0].value).toBe('🔍 Soulseek');
    expect(rows[1]).toMatchObject({ label: 'User', value: 'peer42', mono: true });
    expect(rows[2]).toMatchObject({ label: 'Original File', value: '01 - Song.flac' });
    expect(rows.find((r) => r.label === 'Size')?.value).toBe('30.0 MB');
    expect(rows.find((r) => r.label === 'Audio')?.value).toBe('16-bit · 44.1kHz · 1411kbps');
    // completed status shows NO status row
    expect(rows.some((r) => r.label === 'Status')).toBe(false);
  });

  it('a non-completed status surfaces as an error row; unknown services fall back', () => {
    const rows = sourceInfoRows({ source_service: 'mystery', status: 'failed' });
    expect(rows[0].value).toBe('📦 mystery');
    expect(rows.at(-1)).toMatchObject({ label: 'Status', value: 'failed', tone: 'error' });
  });

  it('covers every advertised provenance service with a paired icon+label', () => {
    for (const entry of Object.values(SOURCE_SERVICES)) {
      expect(entry.icon).toBeTruthy();
      expect(entry.label).toBeTruthy();
    }
    expect(Object.keys(SOURCE_SERVICES)).toHaveLength(13);
  });

  it('blacklist posts the user_rejected payload with the download fields', async () => {
    const spy = stubFetch({ success: true });
    await blacklistSourceRequest(
      { source_filename: 'f.flac', source_username: 'peer', track_title: 'DB Title' },
      'Fallback',
    );
    const body = JSON.parse(String(spy.mock.calls[0][1]?.body));
    expect(body).toEqual({
      track_title: 'DB Title',
      track_artist: '',
      blocked_filename: 'f.flac',
      blocked_username: 'peer',
      reason: 'user_rejected',
    });
  });
});

describe('art pickers', () => {
  it('album options carry artist+album query context; artist options carry none', async () => {
    const spy = stubFetch({ candidates: [{ url: 'u', source: 'spotify' }] });
    await fetchArtOptions({ kind: 'album', id: 5, artistName: 'A B', albumTitle: 'C&D' });
    expect(String(spy.mock.calls[0][0])).toBe('/api/album/5/art-options?artist=A%20B&album=C%26D');
    await fetchArtOptions({ kind: 'artist', id: 9 });
    expect(String(spy.mock.calls[1][0])).toBe('/api/artist/9/art-options');
  });

  it('apply posts the chosen url to the matching endpoint', async () => {
    const spy = stubFetch({ success: true });
    await applyArtRequest({ kind: 'artist', id: 9 }, 'https://img');
    expect(String(spy.mock.calls[0][0])).toBe('/api/artist/9/art');
    expect(JSON.parse(String(spy.mock.calls[0][1]?.body))).toEqual({
      url: 'https://img',
    });
  });

  it('the artist apply toast lists exactly what else was updated', () => {
    expect(artistArtAppliedMessage({ success: true })).toBe('Artist photo updated');
    expect(
      artistArtAppliedMessage({ success: true, server_updated: true, disk_written: true }),
    ).toBe('Artist photo updated (also updated: server, artist.jpg)');
  });
});
