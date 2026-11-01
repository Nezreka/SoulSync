/**
 * dev's grid eased each cover in from the dark tile once its bytes arrived.
 * library v2's covers popped in; they fade again.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Artwork } from './library-v2-page';

describe('Artwork fade-in', () => {
  it('waits hidden until the image has loaded, then shows it', () => {
    render(<Artwork src="https://cdn.example/a.jpg" alt="Kavinsky" className="" />);
    const img = screen.getByAltText('Kavinsky');
    expect(img).toHaveAttribute('data-loaded', 'false');

    fireEvent.load(img);

    expect(img).toHaveAttribute('data-loaded', 'true');
  });

  it('a new picture fades in again', () => {
    const { rerender } = render(
      <Artwork src="https://cdn.example/a.jpg" alt="Kavinsky" className="" />,
    );
    fireEvent.load(screen.getByAltText('Kavinsky'));

    rerender(<Artwork src="https://cdn.example/b.jpg" alt="Kavinsky" className="" />);

    expect(screen.getByAltText('Kavinsky')).toHaveAttribute('data-loaded', 'false');
  });
});
