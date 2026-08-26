import { describe, expect, it } from 'vitest';

import { browserSafeImageUrl, imageProxyUrl, thumb } from './artwork-thumb';

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

  it('proxies Cover Art Archive URLs when no service worker controls the page', () => {
    const cover = 'https://coverartarchive.org/release-group/abc/front-250';
    expect(browserSafeImageUrl(cover, false)).toBe(imageProxyUrl(cover));
    expect(thumb(cover, 'grid', false)).toBe(imageProxyUrl(cover));
  });

  it('leaves Cover Art Archive URLs alone once the service worker can handle them', () => {
    const cover = 'https://coverartarchive.org/release-group/abc/front-250';
    expect(browserSafeImageUrl(cover, true)).toBe(cover);
    expect(thumb(cover, 'grid', true)).toBe(cover);
  });

  it('also proxies archive.org redirect targets without a controller', () => {
    const cover = 'https://ia800100.us.archive.org/12/items/mbid-a/mbid-a_thumb250.jpg';
    expect(browserSafeImageUrl(cover, false)).toBe(imageProxyUrl(cover));
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
