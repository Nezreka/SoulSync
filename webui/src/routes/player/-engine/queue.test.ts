import { describe, expect, it } from 'vitest';

import type { PlayItem } from './types';

import {
  append,
  current,
  EMPTY_QUEUE,
  move,
  nextIndex,
  play,
  playNext,
  previousIndex,
  remove,
  setRepeat,
  setShuffle,
  upcoming,
} from './queue';

const t = (id: string): PlayItem => ({ type: 'track', id, title: id });
const ids = (items: PlayItem[]) => items.map((x) => x.id);

describe('queue', () => {
  it('plays from a start and moves along', () => {
    const q = play(EMPTY_QUEUE, [t('a'), t('b'), t('c')], 1);
    expect(current(q)?.id).toBe('b');
    expect(nextIndex(q, true)).toBe(2);
    expect(previousIndex(q)).toBe(0);
    expect(nextIndex({ ...q, index: 2 }, true)).toBeNull();
    expect(previousIndex({ ...q, index: 0 })).toBeNull();
  });

  it('repeats', () => {
    const q = setRepeat(play(EMPTY_QUEUE, [t('a'), t('b')], 1), 'all');
    expect(nextIndex(q, true)).toBe(0);
    expect(previousIndex({ ...q, index: 0 })).toBe(1);
    const one = setRepeat(q, 'one');
    expect(nextIndex(one, true)).toBe(1); // ended by itself: again
    expect(nextIndex(one, false)).toBe(0); // next pressed: on (wraps under repeat)
  });

  it('adds next and last, removes and moves, keeping what plays', () => {
    let q = play(EMPTY_QUEUE, [t('a'), t('b'), t('c')], 0);
    q = playNext(q, [t('x')]);
    expect(ids(q.items)).toEqual(['a', 'x', 'b', 'c']);
    q = append(q, [t('z')]);
    expect(ids(q.items)).toEqual(['a', 'x', 'b', 'c', 'z']);
    q = { ...q, index: 2 };
    q = remove(q, 0);
    expect(current(q)?.id).toBe('b');
    expect(remove(q, q.index)).toBe(q); // the playing one stays
    q = move(q, 3, 0);
    expect(ids(q.items)).toEqual(['z', 'x', 'b', 'c']);
    expect(current(q)?.id).toBe('b');
    expect(ids(upcoming(q).map((u) => u.item))).toEqual(['c']);
  });

  it('shuffles what is to come and puts it back', () => {
    const items = 'abcdefgh'.split('').map(t);
    const q = play(EMPTY_QUEUE, items, 2);
    const s = setShuffle(q, true, 7);
    expect(current(s)?.id).toBe('c');
    expect(s.index).toBe(0);
    expect(ids(s.items).sort()).toEqual(ids(items).sort());
    expect(ids(s.items)).not.toEqual(ids(items));
    expect(ids(setShuffle(s, true, 7).items)).toEqual(ids(s.items)); // already on: unchanged
    const back = setShuffle({ ...s, index: 3 }, false);
    expect(ids(back.items)).toEqual(ids(items));
    expect(current(back)?.id).toBe(s.items[3].id);
  });

  it('an empty queue is quiet', () => {
    expect(current(EMPTY_QUEUE)).toBeNull();
    expect(nextIndex(EMPTY_QUEUE, true)).toBeNull();
    expect(play(EMPTY_QUEUE, []).items).toEqual([]);
  });
});
