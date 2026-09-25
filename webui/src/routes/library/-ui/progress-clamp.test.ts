import { describe, expect, it } from 'vitest';

import { clampPercent } from './library-v2-page';

describe('clampPercent (P2-20)', () => {
  it('clamps overflow above 100', () => {
    expect(clampPercent(143)).toBe(100);
  });

  it('clamps underflow below 0', () => {
    expect(clampPercent(-7)).toBe(0);
  });

  it('rounds fractional values within range', () => {
    expect(clampPercent(33.6)).toBe(34);
  });

  it('passes through in-range values unchanged', () => {
    expect(clampPercent(57)).toBe(57);
  });

  it('treats missing/nullish progress as 0', () => {
    expect(clampPercent(null)).toBe(0);
    expect(clampPercent(undefined)).toBe(0);
  });

  it('treats NaN as 0', () => {
    expect(clampPercent(Number.NaN)).toBe(0);
  });
});
