import { describe, expect, it } from 'vitest';

import { editDistance, fold, parseQuery, rank, scoreHit, scoreText } from './search';

describe('search folding', () => {
  it('drops accents, punctuation and a leading the', () => {
    expect(fold('Amélie')).toBe('amelie');
    expect(fold('Spider-Man: No Way Home')).toBe('spider man no way home');
    expect(fold('The Office')).toBe('office');
    expect(fold('Law & Order')).toBe('law and order');
    expect(fold("Schindler's List")).toBe('schindlers list');
  });

  it('pulls a trailing year out of the query', () => {
    expect(parseQuery('Alien 1979')).toEqual({ text: 'alien', words: ['alien'], year: 1979 });
    expect(parseQuery('1917')).toEqual({ text: '1917', words: ['1917'], year: null });
    expect(parseQuery('blade runner 2049').year).toBe(2049);
  });
});

describe('search scoring', () => {
  const q = (s: string) => parseQuery(s);

  it('ranks whole, then start, then word start, then anywhere', () => {
    expect(scoreText('Alien', q('alien'))).toBe(100);
    expect(scoreText('Aliens', q('alien'))).toBe(80);
    expect(scoreText('Cowboys & Aliens', q('alien'))).toBe(65);
    expect(scoreText('Ratatouille', q('tatou'))).toBe(45);
    expect(scoreText('Up', q('alien'))).toBe(0);
  });

  it('finds words in any order, spaces or not, and a typo', () => {
    expect(scoreText('The Lord of the Rings', q('rings lord'))).toBe(55);
    expect(scoreText('Spider-Man', q('spiderman'))).toBe(100);
    expect(scoreText('JAY-Z', q('jay z'))).toBe(100);
    expect(scoreText('Interstellar', q('intersteller'))).toBe(25);
    expect(scoreText('Breaking Bad', q('braking bad'))).toBe(25);
    // short words don't get typo leeway: "cat" is not "car"
    expect(scoreText('Cars', q('cat'))).toBe(0);
  });

  it('weighs other fields less and uses the year', () => {
    expect(Math.round(scoreHit(q('radiohead'), 'OK Computer', ['Radiohead']))).toBe(70);
    expect(Math.round(scoreHit(q('alien 1979'), 'Alien', [], 1979))).toBe(130);
    expect(Math.round(scoreHit(q('alien 1979'), 'Alien', [], 2017))).toBe(80);
  });

  it('ranks best first and keeps ties in order', () => {
    const titles = ['Aliens', 'Alien', 'Cowboys & Aliens', 'Up', 'Alien Nation'];
    expect(rank(titles, (t) => scoreHit(q('alien'), t))).toEqual([
      'Alien',
      'Aliens',
      'Alien Nation',
      'Cowboys & Aliens',
    ]);
  });

  it('measures edits and stops early', () => {
    expect(editDistance('kitten', 'sitting', 3)).toBe(3);
    expect(editDistance('abc', 'abcdefgh', 2)).toBe(3);
  });
});
