import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const pageSource = readFileSync('src/routes/library-v2/-ui/library-v2-page.tsx', 'utf8');

describe('artist queue-status polling', () => {
  it('polls once at artist scope and distributes album counts as props', () => {
    expect(
      pageSource.match(/libraryV2QueueStatusQueryOptions\('artists', artistId\)/g),
    ).toHaveLength(1);
    expect(pageSource).not.toContain("libraryV2QueueStatusQueryOptions('albums', album.id)");
    expect(pageSource).toContain('activeDownloads={queueStatusByAlbum[album.id] ?? 0}');
  });
});
