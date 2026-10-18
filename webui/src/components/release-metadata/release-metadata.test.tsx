import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';

import { ReleaseBadges } from './release-metadata';
afterEach(cleanup);
it('shows descriptive metadata without claiming review success', () => {
  render(<ReleaseBadges format="CD" country="XW" disambiguation="explicit" />);
  expect(screen.getByText(/Worldwide/)).toBeTruthy();
  expect(screen.getByText('explicit').getAttribute('data-tone')).toBe('neutral');
  expect(screen.getByText('CD')).toBeTruthy();
});
it('does not invent a modern national flag for historical areas', () => {
  render(<ReleaseBadges country="SU" />);
  expect(screen.getByText('🌐 SU')).toBeTruthy();
});
