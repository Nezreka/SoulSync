import { describe, expect, it } from 'vitest';

import { matchParts, shuffled } from './-library.helpers';

/** The songs view's helpers (#1906). */

describe('matchParts', () => {
  it('marks the hit, ignoring case', () => {
    expect(matchParts('You Proof', 'proof')).toEqual([
      { text: 'You ', hit: false },
      { text: 'Proof', hit: true },
    ]);
  });

  it('ignores accents the way the search does', () => {
    expect(matchParts('Sánd in My Boots', 'sand')).toEqual([
      { text: 'Sánd', hit: true },
      { text: ' in My Boots', hit: false },
    ]);
  });

  it('marks every hit', () => {
    expect(matchParts('La La Land', 'la').filter((p) => p.hit).map((p) => p.text)).toEqual([
      'La',
      'La',
      'La',
    ]);
  });

  it('no query, or no hit, marks nothing', () => {
    expect(matchParts('Neon', '')).toEqual([{ text: 'Neon', hit: false }]);
    expect(matchParts('Neon', 'zz')).toEqual([{ text: 'Neon', hit: false }]);
  });
});

describe('shuffled', () => {
  it('is a reordering of the same songs, leaving the list alone', () => {
    const list = [1, 2, 3, 4, 5];
    const out = shuffled(list, () => 0);
    expect([...out].sort()).toEqual(list);
    expect(out).not.toEqual(list);
    expect(list).toEqual([1, 2, 3, 4, 5]);
  });
});
