import { describe, expect, it } from 'vitest';

import { thumb } from './artwork-thumb';

/**
 * The rule that keeps this safe to sprinkle across call sites: it only ever
 * touches SoulSync's own cache URLs, and it never has to know whether
 * thumbnails are enabled — the server decides that.
 */
describe('thumb', () => {
  it('asks for a variant on a cached image', () => {
    expect(thumb('/api/image-cache/abc123', 'grid')).toBe('/api/image-cache/abc123?v=grid');
    expect(thumb('/api/image-cache/abc123', 'card')).toBe('/api/image-cache/abc123?v=card');
    expect(thumb('/api/image-cache/abc123', 'hero')).toBe('/api/image-cache/abc123?v=hero');
  });

  it('leaves a remote URL completely alone', () => {
    // Only our endpoint understands ?v= — appending it to a CDN URL could
    // change or break the request.
    const cdn = 'https://i.scdn.co/image/ab6761610000e5eb';
    expect(thumb(cdn, 'grid')).toBe(cdn);
  });

  it('leaves other local paths alone', () => {
    expect(thumb('/static/placeholder-album.png', 'grid')).toBe('/static/placeholder-album.png');
    expect(thumb('/api/video/poster/movie/12?w=300', 'grid')).toBe(
      '/api/video/poster/movie/12?w=300',
    );
  });

  it('does not add a second query string', () => {
    const already = '/api/image-cache/abc123?v=hero';
    expect(thumb(already, 'grid')).toBe(already);
  });

  it('passes null and undefined straight through', () => {
    // Call sites render placeholders for missing art; this must not invent a URL.
    expect(thumb(null, 'grid')).toBeNull();
    expect(thumb(undefined, 'grid')).toBeUndefined();
    expect(thumb('', 'grid')).toBe('');
  });
});
