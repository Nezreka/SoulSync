import { describe, expect, it } from 'vitest';

import {
  explanationLine,
  explanationParts,
  explanationTitle,
  nameList,
  sourceMixLine,
} from './-discover.explanation';

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

describe('explanationParts', () => {
  it('splits the line into lead, up to two names and a remainder', () => {
    expect(explanationParts({ kind: 'similar_to', seeds: seeds('A', 'B', 'C', 'D') })).toEqual({
      lead: 'Because you have',
      names: ['A', 'B'],
      more: 2,
    });
    expect(explanationParts({ kind: 'listened', seeds: seeds('Tool') })).toEqual({
      lead: 'Because you listen to',
      names: ['Tool'],
      more: 0,
    });
  });

  it('is null when there is no name to set apart, or the kind is unknown', () => {
    expect(explanationParts({ kind: 'similar_to', seeds: [] })).toBeNull();
    expect(explanationParts({ kind: 'toString', seeds: seeds('Tool') })).toBeNull();
    expect(explanationParts(undefined)).toBeNull();
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

  it("shows each seed's share when the server sent components", () => {
    expect(
      explanationTitle({
        kind: 'listened',
        seeds: seeds('A', 'B'),
        components: { A: 0.75, B: 0.25 },
      }),
    ).toBe('You listen to: A (75%), B (25%)');
    // a seed with no share still shows
    expect(
      explanationTitle({ kind: 'listened', seeds: seeds('A', 'B'), components: { A: 1 } }),
    ).toBe('You listen to: A (100%), B');
  });
});

describe('sourceMixLine', () => {
  it('names the dominant source', () => {
    expect(sourceMixLine({ kind: 'listened', source_mix: { library: 0.8, discovery: 0.2 } })).toBe(
      'More from your library',
    );
    expect(sourceMixLine({ kind: 'genre', source_mix: { direct: 0.6, genre: 0.4 } })).toBe(
      'More from similar artists',
    );
    expect(
      sourceMixLine({
        kind: 'listened',
        source_mix: { library: 0.4, discovery: 0.3, trending: 0.3 },
      }),
    ).toBe('Some from your library');
  });

  it('renders nothing without a mix', () => {
    expect(sourceMixLine({ kind: 'listened' })).toBe('');
    expect(sourceMixLine({ kind: 'listened', source_mix: {} })).toBe('');
    expect(sourceMixLine(undefined)).toBe('');
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
