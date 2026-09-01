import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Video grids have to ask for a THUMBNAIL, not the original.
 *
 * `/api/video/poster/...` returns the full-size art when no `?w=` is given, and
 * Plex's originals are around 2000x3000. The browser decodes one of those to
 * ~24MB of bitmap and then scales it into a 158px card. Sixty cards is over a
 * gigabyte of texture behind a grid of thumbnails, which is why Boulder's
 * watchlist took most of a second to repaint a hover.
 *
 * The wishlist page sized its images; the watchlist and the detail page's
 * season rail did not. These run the REAL helper out of each file - a
 * source-level "it says ?w=" check would pass just as happily on a helper that
 * had stopped working.
 */

const FILES = {
  watchlist: 'static/video/video-watchlist.js',
  wishlist: 'static/video/video-wishlist.js',
  detail: 'static/video/video-detail.js',
} as const;

function source(file: string): string {
  return readFileSync(resolve(process.cwd(), file), 'utf8');
}

/** Pull one named function out of a classic script and make it callable. */
function extract(file: string, name: string): (url: string | null, w: number) => string {
  const text = source(file);
  const at = text.indexOf(`    function ${name}(url, w) {`);
  expect(at, `${name} is gone from ${file}`).toBeGreaterThan(-1);
  const end = text.indexOf('\n    }\n', at) + '\n    }\n'.length;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${text.slice(at, end)}\n return ${name};`)() as (
    url: string | null,
    w: number,
  ) => string;
}

describe('the thumbnail helper', () => {
  for (const [page, file] of Object.entries(FILES)) {
    const name = page === 'detail' ? 'sizedArt' : 'sized';

    it(`${page}: adds a width to a proxied poster`, () => {
      const sized = extract(file, name);
      expect(sized('/api/video/poster/show/12', 342)).toBe('/api/video/poster/show/12?w=342');
    });

    it(`${page}: keeps an existing query string intact`, () => {
      const sized = extract(file, name);
      expect(sized('/api/video/poster/show/12?x=1', 342)).toBe('/api/video/poster/show/12?x=1&w=342');
    });

    it(`${page}: rewrites a TMDB size segment rather than appending a param`, () => {
      const sized = extract(file, name);
      // TMDB ignores ?w= — the size lives in the path, so appending would have
      // left the original streaming while looking like it had been handled.
      expect(sized('https://image.tmdb.org/t/p/original/abc.jpg', 342)).toBe(
        'https://image.tmdb.org/t/p/w342/abc.jpg',
      );
    });

    it(`${page}: leaves anything else alone`, () => {
      const sized = extract(file, name);
      expect(sized('https://i.ytimg.com/vi/x/hq.jpg', 342)).toBe('https://i.ytimg.com/vi/x/hq.jpg');
      expect(sized('', 342)).toBe('');
      expect(sized(null, 342)).toBe(null as unknown as string);
    });
  }
});

describe('the grids use it', () => {
  it('the watchlist card image is sized', () => {
    expect(source(FILES.watchlist)).toContain("src=\"' + esc(sized(it.poster_url, 342))");
  });

  it("the detail page's season rail and episode thumbs are sized", () => {
    const text = source(FILES.detail);
    expect(text).toContain("src=\"' + sizedArt(art, 342)");
    expect(text).toContain("src=\"' + sizedArt(stillSrc, 342)");
  });

  it('no video grid asks for an unsized proxy poster in an <img>', () => {
    // The regression that started this: an <img src> pointing straight at
    // /api/video/poster/... with no width.
    for (const file of Object.values(FILES)) {
      const offenders = [...source(file).matchAll(/src="[^"]*\+ ?'?\/api\/video\/(poster|backdrop)\//g)];
      expect(offenders.map((m) => m[0]), `${file} builds an unsized <img src>`).toEqual([]);
    }
  });
});
