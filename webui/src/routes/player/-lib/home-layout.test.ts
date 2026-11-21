import { describe, expect, it } from 'vitest';

import { genreRowId, moveRow, parseGenreRow, rowDef, type HomeLayout } from './home-layout';

const catalog = {
  version: 1,
  styles: [],
  rows: [
    {
      id: 'continue',
      label: 'Continue watching',
      hint: '',
      styles: ['wide' as const],
      style: 'wide' as const,
      on: true,
    },
  ],
};

describe('home layout', () => {
  it('knows catalog rows and genre rows', () => {
    expect(rowDef(catalog, 'continue')?.label).toBe('Continue watching');
    expect(rowDef(catalog, genreRowId('movie', 'Horror'))?.label).toBe('Horror movies');
    expect(rowDef(catalog, 'nope')).toBeNull();
    expect(parseGenreRow('genre:show:Comedy')).toEqual({ kind: 'show', genre: 'Comedy' });
    expect(parseGenreRow('genre:book:x')).toBeNull();
  });

  it('moves rows within bounds', () => {
    const layout: HomeLayout = {
      billboard: true,
      rows: [
        { id: 'a', style: 'standard', on: true },
        { id: 'b', style: 'standard', on: true },
      ],
    };
    expect(moveRow(layout, 1, -1).rows.map((r) => r.id)).toEqual(['b', 'a']);
    expect(moveRow(layout, 0, -1)).toBe(layout);
  });
});
