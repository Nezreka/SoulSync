/**
 * a library with its own page takes its titles out of Movies / TV; with no
 * such library every title stays where it always was.
 */

import { describe, expect, it } from 'vitest';

import { decodeCatalog } from './catalog';
import { libraryTitles, validateBrowseSearch } from './video';

const items = [
  { id: 1, library_id: 1 },
  { id: 2, library_id: null },
  { id: 3, library_id: 6 },
  { id: 4, library_id: 7 },
  { id: 5 },
];
const ids = (list: { id: number }[]) => list.map((x) => x.id);

describe('the titles a browse page shows', () => {
  it('is everything while no library has a page of its own', () => {
    const libs = [
      { id: 1, own_page: false },
      { id: 6, own_page: false },
    ];
    expect(libraryTitles(items, libs, undefined)).toBe(items);
    expect(libraryTitles(items, [], undefined)).toBe(items);
  });

  it("leaves a library's titles to its own page", () => {
    const libs = [
      { id: 1, own_page: false },
      { id: 6, own_page: true },
      { id: 7, own_page: false },
    ];
    expect(ids(libraryTitles(items, libs, undefined))).toEqual([1, 2, 4, 5]);
    expect(ids(libraryTitles(items, libs, 6))).toEqual([3]);
    // a library page that isn't one (hidden, or gone): nothing, never everything
    expect(libraryTitles(items, libs, 99)).toEqual([]);
  });

  it('reads the library from the address, and only a real id', () => {
    expect(validateBrowseSearch({ library: '6' }).library).toBe(6);
    expect(validateBrowseSearch({ library: 6, tab: 'library' })).toEqual({
      library: 6,
      tab: 'library',
    });
    for (const bad of ['x', 0, -2, 1.5, '', null])
      expect(validateBrowseSearch({ library: bad }).library).toBeUndefined();
  });

  it("decodes each title's library and the list of libraries", () => {
    const out = decodeCatalog({
      fields: { movie: ['id', 'title', 'library_id'], show: ['id', 'title', 'library_id'] },
      movies: [
        [1, 'A', 6],
        [2, 'B', null],
      ],
      shows: [[10, 'S', 5]],
      libraries: [{ id: 6, kind: 'movie', name: 'Kids', own_page: true, main: false }],
    });
    expect(out.movies.map((m) => m.library_id)).toEqual([6, null]);
    expect(out.shows[0].library_id).toBe(5);
    expect(out.libraries).toEqual([
      { id: 6, kind: 'movie', name: 'Kids', own_page: true, main: false },
    ]);
    // an older server sends neither
    const old = decodeCatalog({ fields: { movie: ['id', 'title'] }, movies: [[1, 'A']] });
    expect(old.movies[0].library_id).toBeNull();
    expect(old.libraries).toEqual([]);
  });
});
