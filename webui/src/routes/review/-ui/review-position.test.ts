import { expect, it } from 'vitest';

import { formatTrackPosition } from './review-position';
it('falls back to the release position when display numbers are absent or blank', () => {
  expect(formatTrackPosition('2', '3', 'release', '')).toBe('2.3');
  expect(formatTrackPosition('2', '3', 'digital', '')).toBe('3');
  expect(formatTrackPosition('2', '3', 'digital', 15)).toBe('15');
  expect(formatTrackPosition('', '', 'release', '')).toBe('—');
  expect(formatTrackPosition(1, 7, 'release')).toBe('7');
});
