import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';

import { Artwork } from './artwork';
afterEach(cleanup);
it('shows loading, successful artwork, failure, and a fresh image after changing albums', () => {
  const { container, rerender } = render(<Artwork title="First" url="/api/image-cache/first" />);
  expect(container.querySelector('[data-loading]')).toBeTruthy();
  fireEvent.load(screen.getByRole('img', { name: 'First' }));
  expect(container.querySelector('[data-loading]')).toBeNull();
  fireEvent.error(screen.getByRole('img', { name: 'First' }));
  expect(screen.getByTitle('Artwork could not be loaded')).toBeTruthy();
  rerender(<Artwork title="Second" url="/api/image-cache/second" />);
  expect(screen.getByRole('img', { name: 'Second' })).toBeTruthy();
  expect(container.querySelector('[data-loading]')).toBeTruthy();
});
