import { describe, expect, it } from 'vitest';

import { explanationLine, explanationTitle, nameList } from './-discover.explanation';

const seeds = (...names: string[]) => names.map((name) => ({ name }));

describe('explanationLine', () => {
  it('words every kind from its seeds', () => {
    expect(explanationLine({ kind: 'similar_to', seeds: seeds('Tool') })).toBe(
      'Because you have Tool',
    );
    expect(explanationLine({ kind: 'listened', seeds: seeds('Tool', 'Deftones') })).toBe(
      'Because you listen to Tool & Deftones',
    );
    expect(explanationLine({ kind: 'genre', seeds: seeds('shoegaze') })).toBe(
      'Because you like shoegaze',
    );
    expect(explanationLine({ kind: 'new_release', seeds: seeds('Tool') })).toBe('New from Tool');
    expect(explanationLine({ kind: 'trending', seeds: seeds('Deezer') })).toBe(
      'Trending from Deezer',
    );
  });

  it('says only what the kind is when no seed could be named', () => {
    expect(explanationLine({ kind: 'similar_to', seeds: [] })).toBe('Similar to your library');
    expect(explanationLine({ kind: 'listened' })).toBe('From artists you play often');
    expect(explanationLine({ kind: 'listened', seeds: [{ name: '' }] })).toBe(
      'From artists you play often',
    );
  });

  it('renders nothing it cannot honestly word', () => {
    expect(explanationLine(undefined)).toBe('');
    expect(explanationLine(null)).toBe('');
    expect(explanationLine({ kind: 'because', seeds: seeds('Tool') })).toBe('');
    // a key the prototype carries is not a kind
    expect(explanationLine({ kind: 'toString', seeds: seeds('Tool') })).toBe('');
  });
});

describe('explanationTitle', () => {
  it('lists every seed the line truncates', () => {
    expect(explanationTitle({ kind: 'listened', seeds: seeds('A', 'B', 'C', 'D') })).toBe(
      'You listen to: A, B, C, D',
    );
    expect(explanationTitle({ kind: 'listened', seeds: [] })).toBe('');
    expect(explanationTitle(undefined)).toBe('');
  });
});

describe('nameList', () => {
  it('keeps the visible line short', () => {
    expect(nameList([])).toBe('');
    expect(nameList(['A'])).toBe('A');
    expect(nameList(['A', 'B'])).toBe('A & B');
    expect(nameList(['A', 'B', 'C', 'D'])).toBe('A, B +2 more');
  });
});
