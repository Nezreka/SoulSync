import { describe, expect, it } from 'vitest';

import type { VideoMovieItem } from './-video.types';

import { movieCardMeta, movieMeta, movieTarget } from './-video.helpers';

/**
 * #2029: a movie can be wanted in two libraries (Movies and Movies 4K). Each
 * library's row is its own card, and its actions name that row alone.
 */

const base: VideoMovieItem = { tmdb_id: 9, title: 'Heat', year: 1995, status: 'wanted' };

describe('movieTarget', () => {
  it('acts as it always did on a row the server named no library for', () => {
    expect(movieTarget(base)).toEqual({ scope: 'movie', tmdb_id: 9 });
  });

  it("names the title's own row as 0 and a copy by its library", () => {
    expect(movieTarget({ ...base, video_library_id: null })).toEqual({
      scope: 'movie',
      tmdb_id: 9,
      video_library_id: 0,
    });
    expect(movieTarget({ ...base, video_library_id: 4 })).toEqual({
      scope: 'movie',
      tmdb_id: 9,
      video_library_id: 4,
    });
  });
});

describe('movieCardMeta', () => {
  it("puts a copy's library first", () => {
    const copy = { ...base, video_library_id: 4, video_library_name: 'Movies 4K' };
    expect(movieCardMeta(copy)).toBe(`Movies 4K · ${movieMeta(copy)}`);
  });

  it('is the plain line for the title row', () => {
    expect(movieCardMeta({ ...base, video_library_id: null })).toBe(movieMeta(base));
  });
});
