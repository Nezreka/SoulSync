import { describe, expect, it } from 'vitest';

import { computeTrackEditValues } from './-metadata-edit';

const ORIGINAL = {
  title: 'One Dance',
  track_number: 1,
  disc_number: 1,
  bpm: null,
  explicit: null,
  style: null,
  mood: null,
};

const BASE_FORM = {
  trackNumber: '1',
  discNumber: '1',
  bpm: '',
  explicitFlag: '' as const,
  style: '',
  mood: '',
};

describe('computeTrackEditValues', () => {
  it('reports no changes when the form matches the current metadata', () => {
    const result = computeTrackEditValues(ORIGINAL, { ...BASE_FORM, title: 'One Dance' });
    expect(result).toEqual({ values: {}, valid: true });
  });

  it('sets only the changed title (trimmed)', () => {
    const result = computeTrackEditValues(ORIGINAL, {
      ...BASE_FORM,
      title: '  One Dance (Remix)  ',
    });
    expect(result).toEqual({ values: { title: 'One Dance (Remix)' }, valid: true });
  });

  it('is invalid when the required title is blank', () => {
    const result = computeTrackEditValues(ORIGINAL, { ...BASE_FORM, title: '   ' });
    expect(result.valid).toBe(false);
    expect(result.values).toEqual({});
  });

  it('parses a changed track/disc number as an integer', () => {
    const result = computeTrackEditValues(ORIGINAL, {
      ...BASE_FORM,
      title: 'One Dance',
      trackNumber: '3',
      discNumber: '2',
    });
    expect(result).toEqual({ values: { track_number: 3, disc_number: 2 }, valid: true });
  });

  it('rejects a negative or non-integer number', () => {
    expect(
      computeTrackEditValues(ORIGINAL, { ...BASE_FORM, title: 'One Dance', trackNumber: '-2' })
        .valid,
    ).toBe(false);
    expect(
      computeTrackEditValues(ORIGINAL, { ...BASE_FORM, title: 'One Dance', trackNumber: '1.5' })
        .valid,
    ).toBe(false);
  });

  it('treats a blank number field as "leave unchanged"', () => {
    const result = computeTrackEditValues(
      { ...ORIGINAL, track_number: 5 },
      { ...BASE_FORM, title: 'One Dance', trackNumber: '', discNumber: '' },
    );
    expect(result).toEqual({ values: {}, valid: true });
  });

  // --- §48: bpm/explicit/style/mood -----------------------------------------

  it('parses a changed bpm as a float', () => {
    const result = computeTrackEditValues(ORIGINAL, {
      ...BASE_FORM,
      title: 'One Dance',
      bpm: '104.5',
    });
    expect(result).toEqual({ values: { bpm: 104.5 }, valid: true });
  });

  it('rejects a negative bpm but leaves a blank bpm unchanged', () => {
    expect(
      computeTrackEditValues(ORIGINAL, { ...BASE_FORM, title: 'One Dance', bpm: '-1' }).valid,
    ).toBe(false);
    expect(
      computeTrackEditValues(
        { ...ORIGINAL, bpm: 90 },
        { ...BASE_FORM, title: 'One Dance', bpm: '' },
      ),
    ).toEqual({ values: {}, valid: true });
  });

  it('sets explicit true/false only when the flag differs from the baseline', () => {
    const toExplicit = computeTrackEditValues(ORIGINAL, {
      ...BASE_FORM,
      title: 'One Dance',
      explicitFlag: 'yes',
    });
    expect(toExplicit).toEqual({ values: { explicit: true }, valid: true });

    const toClean = computeTrackEditValues(
      { ...ORIGINAL, explicit: true },
      { ...BASE_FORM, title: 'One Dance', explicitFlag: 'no' },
    );
    expect(toClean).toEqual({ values: { explicit: false }, valid: true });

    const backToUnknown = computeTrackEditValues(
      { ...ORIGINAL, explicit: true },
      { ...BASE_FORM, title: 'One Dance', explicitFlag: '' },
    );
    expect(backToUnknown).toEqual({ values: { explicit: null }, valid: true });

    const unchanged = computeTrackEditValues(
      { ...ORIGINAL, explicit: true },
      { ...BASE_FORM, title: 'One Dance', explicitFlag: 'yes' },
    );
    expect(unchanged).toEqual({ values: {}, valid: true });
  });

  it('diffs style/mood as nullable trimmed text, clearing to null when emptied', () => {
    const setBoth = computeTrackEditValues(ORIGINAL, {
      ...BASE_FORM,
      title: 'One Dance',
      style: '  Pop Rap  ',
      mood: 'Chill',
    });
    expect(setBoth).toEqual({ values: { style: 'Pop Rap', mood: 'Chill' }, valid: true });

    const clearBoth = computeTrackEditValues(
      { ...ORIGINAL, style: 'Pop Rap', mood: 'Chill' },
      { ...BASE_FORM, title: 'One Dance', style: '', mood: '' },
    );
    expect(clearBoth).toEqual({ values: { style: null, mood: null }, valid: true });

    const unchanged = computeTrackEditValues(
      { ...ORIGINAL, style: 'Pop Rap' },
      { ...BASE_FORM, title: 'One Dance', style: 'Pop Rap' },
    );
    expect(unchanged).toEqual({ values: {}, valid: true });
  });
});
