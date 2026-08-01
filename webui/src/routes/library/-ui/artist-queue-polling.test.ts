import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const pageSource = readFileSync('src/routes/library/-ui/library-v2-page.tsx', 'utf8');

/**
 * find22-15: ONE queue poll per artist, distributed to the album rows.
 *
 * iss29-C06: the previous guard asserted
 * `not.toContain("libraryV2QueueStatusQueryOptions('albums', album.id)")`, but
 * the code spelled it `('albums', albumId)` — the literal never appeared, so
 * the assertion passed vacuously while `AlbumTrackTable` polled every expanded
 * album at 3s. Six open blocks ≈ 140 requests/min against the single-writer
 * SQLite database, all for tracks the artist-wide response already contained.
 *
 * These assertions are written against what the code ACTUALLY spells, and the
 * album-scope call is matched by shape rather than by one exact string, so a
 * rename cannot make them vacuous again.
 */
describe('artist queue-status polling', () => {
  it('polls exactly once at artist scope', () => {
    expect(
      pageSource.match(/libraryV2QueueStatusQueryOptions\('artists',\s*artistId\)/g),
    ).toHaveLength(1);
  });

  it('distributes the artist-scope response as props', () => {
    expect(pageSource).toContain('activeDownloads={queueStatusByAlbum[album.id] ?? 0}');
    expect(pageSource).toContain('queueStatusTracks={queueStatusQuery.data?.tracks ?? {}}');
  });

  it('has exactly one album-scope queue query, and it is opt-out', () => {
    // The standalone album page has no artist-scope query to inherit from, so
    // one album-scope call legitimately remains — but it must be disabled
    // whenever the caller supplied the artist-wide map.
    const albumScopeCalls =
      pageSource.match(/libraryV2QueueStatusQueryOptions\(\s*'albums',[^)]*\)/g) ?? [];
    expect(albumScopeCalls).toHaveLength(1);
    expect(pageSource).toContain('enabled: queueStatusTracks === undefined && albumId > 0');
  });

  it('reads per-track badges from the merged map, not from its own query', () => {
    expect(pageSource).toContain(
      'queueStatus={track.id != null ? queueTracks[track.id] : undefined}',
    );
    expect(pageSource).not.toContain('queueStatusQuery.data?.tracks[track.id]');
  });
});
