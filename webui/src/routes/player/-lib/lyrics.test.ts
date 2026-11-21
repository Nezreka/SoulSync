import { describe, expect, it } from 'vitest';

import { activeLine, parseLrc, stanzas } from './lyrics';

describe('lyrics', () => {
  it('reads stamped lines, one per stamp, in order; tags are not lyrics', () => {
    const lines = parseLrc('[ti:Song]\n[00:12.50]second\n[00:01.00][01:00.00]chorus\n[00:05]third');
    expect(lines).toEqual([
      { time: 1, text: 'chorus' },
      { time: 5, text: 'third' },
      { time: 12.5, text: 'second' },
      { time: 60, text: 'chorus' },
    ]);
    expect(parseLrc(null)).toEqual([]);
  });

  it('knows the line being sung', () => {
    const lines = parseLrc('[00:01.00]a\n[00:05.00]b\n[00:09.00]c');
    expect(activeLine(lines, 0.5)).toBe(-1);
    expect(activeLine(lines, 1)).toBe(0);
    expect(activeLine(lines, 7)).toBe(1);
    expect(activeLine(lines, 99)).toBe(2);
  });

  it('splits plain lyrics into stanzas', () => {
    expect(stanzas('one\ntwo\n\nthree\r\n\r\nfour')).toEqual([['one', 'two'], ['three'], ['four']]);
  });
});
