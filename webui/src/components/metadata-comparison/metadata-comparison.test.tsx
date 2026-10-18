import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';

import { MetadataComparison } from './metadata-comparison';
afterEach(cleanup);
it('distinguishes zero, missing values, arrays and literal markup in the diff', () => {
  render(
    <MetadataComparison
      rows={[
        { field: 'Number', current: 0, planned: 1 },
        { field: 'Artist', current: null, planned: ['A', 'B'] },
        { field: 'Title', current: '<script>', planned: 'New' },
      ]}
    />,
  );
  expect(screen.getByText('0')).toBeTruthy();
  expect(screen.getByText('Not set')).toBeTruthy();
  expect(screen.getByText('["A","B"]')).toBeTruthy();
  expect(screen.getByText('<script>')).toBeTruthy();
  expect(document.querySelector('script')).toBeNull();
});
it('provides copy actions for long exact values without shortening their evidence', () => {
  const path = '/music/' + 'long album name '.repeat(8) + '/01.flac';
  render(
    <MetadataComparison
      rows={[{ field: 'Filename · file', current: path, planned: '/music/new.flac' }]}
    />,
  );
  expect(screen.getByText(path)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Copy Current Filename · file' })).toBeTruthy();
});

it('retains replacement, addition, removal and unchanged cues across consumers', () => {
  render(
    <MetadataComparison
      rows={[
        { field: 'Replaced', current: 'Old title', planned: 'New title' },
        { field: 'Added', current: '', planned: 'New credit' },
        { field: 'Removed', current: 'Old credit', planned: '' },
        { field: 'Same', current: 'Kept', planned: 'Kept', changed: false },
      ]}
    />,
  );
  expect(screen.getByText('Old title').tagName).toBe('DEL');
  expect(screen.getByLabelText('Changed').textContent).toBe('→');
  expect(screen.getByLabelText('Added').textContent).toBe('→');
  expect(screen.getByLabelText('Removed').textContent).toBe('→');
  expect(screen.getByLabelText('Unchanged').textContent).toBe('✓');
  expect(screen.getAllByText('Kept').every((node) => node.tagName !== 'DEL')).toBe(true);
});
