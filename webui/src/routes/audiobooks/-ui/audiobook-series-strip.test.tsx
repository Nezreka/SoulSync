import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AudiobookItem } from '../-audiobooks.types';

import { AudiobookSeriesStrip, seriesAsinFor, seriesOwnership } from './audiobook-series-strip';

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <a className={className}>{children}</a>
  ),
}));

function book(asin: string, title: string, sequence: string, owned = false): AudiobookItem {
  return {
    asin,
    title,
    subtitle: '',
    authors: [],
    narrators: [],
    author_names: [],
    narrator_names: [],
    series: [{ title: 'Zodiac Academy', sequence } as AudiobookItem['series'][number]],
    publisher: '',
    summary: '',
    short_summary: '',
    runtime_formatted: '',
    genres: [],
    language: 'english',
    format_type: 'unabridged',
    is_adult: false,
    owned,
    source: 'audible',
  };
}

// the shape from the report: the novel, a dramatized version and its two
// halves all sit at number 6
const SERIES = [
  book('a5', 'Cursed Fates', '5'),
  book('a6', 'Fated Throne', '6', true),
  book('a6p1', 'Fated Throne (1 of 2) [Dramatized Adaptation]', '6'),
  book('a6p2', 'Fated Throne (2 of 2) [Dramatized Adaptation]', '6'),
  book('a7', 'Heartless Sky', '7', true),
];

describe('seriesOwnership', () => {
  it('owned by asin, and another edition at the same number counts as having it', () => {
    const own = seriesOwnership(SERIES);
    expect(own.get('a6')).toBe('owned');
    expect(own.get('a6p1')).toBe('edition');
    expect(own.get('a6p2')).toBe('edition');
    expect(own.get('a7')).toBe('owned');
    expect(own.get('a5')).toBeNull();
  });

  it('a book with no number never borrows ownership', () => {
    const own = seriesOwnership([book('x', 'Owned', '', true), book('y', 'Companion', '')]);
    expect(own.get('y')).toBeNull();
  });
});

describe('AudiobookSeriesStrip', () => {
  it('marks each owned book and counts them in the header', () => {
    render(<AudiobookSeriesStrip seriesTitle="Zodiac Academy" books={SERIES} currentAsin="a6p1" />);
    expect(screen.getByText('5 books in reading order · 2 in your library')).toBeInTheDocument();
    expect(screen.getAllByText('Owned')).toHaveLength(2);
    expect(screen.getAllByText('Other edition')).toHaveLength(2);
  });

  it('says nothing about ownership when none are owned', () => {
    render(
      <AudiobookSeriesStrip
        seriesTitle="Zodiac Academy"
        books={[book('a', 'One', '1'), book('b', 'Two', '2')]}
        currentAsin="a"
      />,
    );
    expect(screen.getByText('2 books in reading order')).toBeInTheDocument();
    expect(screen.queryByText('Owned')).toBeNull();
  });
});

describe('seriesAsinFor', () => {
  const entry = (title: string, asin?: string) =>
    ({ title, asin, sequence: '1' }) as AudiobookItem['series'][number];

  it('takes the ASIN of the series the strip is about, not the first listed', () => {
    const lostMetal = { ...book('b1', 'The Lost Metal', '1') } as AudiobookItem;
    lostMetal.series = [entry('Mistborn Saga', 'SAGA'), entry('Wax & Wayne', 'WAX')];

    expect(seriesAsinFor([lostMetal], 'Wax & Wayne')).toBe('WAX');
    expect(seriesAsinFor([lostMetal], 'Mistborn Saga')).toBe('SAGA');
  });

  it('looks past a book that does not carry an ASIN for it', () => {
    const bare = { ...book('b1', 'One', '1') } as AudiobookItem;
    bare.series = [entry('Zodiac Academy')];
    const withAsin = { ...book('b2', 'Two', '2') } as AudiobookItem;
    withAsin.series = [entry('Zodiac Academy', 'ZA')];

    expect(seriesAsinFor([bare, withAsin], 'Zodiac Academy')).toBe('ZA');
  });

  it('is empty when nothing matches, so the scan falls back to the name', () => {
    expect(seriesAsinFor([book('b1', 'One', '1')], 'Another Series')).toBe('');
    expect(seriesAsinFor([], 'Zodiac Academy')).toBe('');
  });
});
