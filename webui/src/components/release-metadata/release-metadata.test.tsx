import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
  expect(screen.getByText('SU')).toBeTruthy();
  expect(document.querySelector('[data-country]')).toBeNull();
});

it.each([
  ['US', 'United States'],
  ['AU', 'Australia'],
  ['BG', 'Bulgaria'],
  ['ZA', 'South Africa'],
])('renders the SVG for %s beside the matching country name', (country, name) => {
  render(<ReleaseBadges country={country} />);
  expect(screen.getByText(name).querySelector('svg')).toHaveAttribute('data-country', country);
  expect(screen.getByText(name).textContent).toBe(name);
});

it('explains release details on keyboard focus', async () => {
  render(<ReleaseBadges format="Digital Media" country="US" disambiguation="explicit" />);
  fireEvent.focus(screen.getByText('Digital Media'));
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    "catalog edition's medium, not the local audio file format",
  );
});
