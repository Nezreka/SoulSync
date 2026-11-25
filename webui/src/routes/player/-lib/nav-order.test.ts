import { describe, expect, it } from 'vitest';

import { arrangeNav, moveNav } from './nav-order';

const items = ['/player', '/player/movies', 'lib:5', '/player/tv', '/player/youtube'].map((id) => ({
  id,
}));
const ids = (list: { id: string }[]) => list.map((x) => x.id);

describe("a profile's sidebar order", () => {
  it('is the usual order until one is saved', () => {
    expect(ids(arrangeNav(items, [], []))).toEqual(ids(items));
    expect(ids(arrangeNav(items, undefined, undefined))).toEqual(ids(items));
  });

  it('puts the saved places first, in their order, and the rest after in their usual order', () => {
    expect(ids(arrangeNav(items, ['/player/tv', 'lib:5'], []))).toEqual([
      '/player/tv',
      'lib:5',
      '/player',
      '/player/movies',
      '/player/youtube',
    ]);
  });

  it('skips a saved place the player no longer has (a library that was removed)', () => {
    expect(ids(arrangeNav(items, ['lib:99', '/player/youtube'], []))[0]).toBe('/player/youtube');
  });

  it('leaves out what is hidden, but never Home', () => {
    expect(ids(arrangeNav(items, [], ['/player/tv', '/player', 'lib:5']))).toEqual([
      '/player',
      '/player/movies',
      '/player/youtube',
    ]);
  });

  it('moves one place up or down, and not past the ends', () => {
    const order = ['a', 'b', 'c'];
    expect(moveNav(order, 'b', -1)).toEqual(['b', 'a', 'c']);
    expect(moveNav(order, 'b', 1)).toEqual(['a', 'c', 'b']);
    expect(moveNav(order, 'a', -1)).toEqual(order);
    expect(moveNav(order, 'c', 1)).toEqual(order);
    expect(moveNav(order, 'zzz', 1)).toEqual(order);
    expect(order).toEqual(['a', 'b', 'c']);
  });
});
