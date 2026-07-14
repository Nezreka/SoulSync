import { describe, expect, it } from 'vitest';

import { computeTrackEditValues } from './-metadata-edit';

const ORIGINAL = { title: 'One Dance', track_number: 1, disc_number: 1 };

describe('computeTrackEditValues', () => {
  it('reports no changes when the form matches the current metadata', () => {
    const result = computeTrackEditValues(ORIGINAL, {
      title: 'One Dance',
      trackNumber: '1',
      discNumber: '1',
    });
    expect(result).toEqual({ values: {}, valid: true });
  });

  it('sets only the changed title (trimmed)', () => {
    const result = computeTrackEditValues(ORIGINAL, {
      title: '  One Dance (Remix)  ',
      trackNumber: '1',
      discNumber: '1',
    });
    expect(result).toEqual({ values: { title: 'One Dance (Remix)' }, valid: true });
  });

  it('is invalid when the required title is blank', () => {
    const result = computeTrackEditValues(ORIGINAL, {
      title: '   ',
      trackNumber: '1',
      discNumber: '1',
    });
    expect(result.valid).toBe(false);
    expect(result.values).toEqual({});
  });

  it('parses a changed track/disc number as an integer', () => {
    const result = computeTrackEditValues(ORIGINAL, {
      title: 'One Dance',
      trackNumber: '3',
      discNumber: '2',
    });
    expect(result).toEqual({ values: { track_number: 3, disc_number: 2 }, valid: true });
  });

  it('rejects a negative or non-integer number', () => {
    expect(
      computeTrackEditValues(ORIGINAL, { title: 'One Dance', trackNumber: '-2', discNumber: '1' })
        .valid,
    ).toBe(false);
    expect(
      computeTrackEditValues(ORIGINAL, { title: 'One Dance', trackNumber: '1.5', discNumber: '1' })
        .valid,
    ).toBe(false);
  });

  it('treats a blank number field as "leave unchanged"', () => {
    const result = computeTrackEditValues(
      { title: 'One Dance', track_number: 5, disc_number: 1 },
      { title: 'One Dance', trackNumber: '', discNumber: '' },
    );
    expect(result).toEqual({ values: {}, valid: true });
  });
});
