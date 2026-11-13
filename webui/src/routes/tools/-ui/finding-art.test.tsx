import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { FindingArt } from './finding-art';

// the picture beside a finding row: its cover, else its artist, else a glyph
describe('FindingArt', () => {
  const img = (container: HTMLElement) => container.querySelector('img');

  it('shows the album cover when there is one', () => {
    const { container } = render(<FindingArt album="/cover.jpg" artist="/artist.jpg" />);
    expect(img(container)?.getAttribute('src')).toBe('/cover.jpg');
    expect(container.querySelector('.repair-art')?.classList.contains('round')).toBe(false);
  });

  it('falls back to the artist, drawn round, when the cover is missing', () => {
    const { container } = render(<FindingArt album={null} artist="/artist.jpg" />);
    expect(img(container)?.getAttribute('src')).toBe('/artist.jpg');
    expect(container.querySelector('.repair-art')?.classList.contains('round')).toBe(true);
  });

  it('moves on to the artist when the cover fails to load', () => {
    const { container } = render(<FindingArt album="/gone.jpg" artist="/artist.jpg" />);
    fireEvent.error(img(container) as Element);
    expect(img(container)?.getAttribute('src')).toBe('/artist.jpg');
  });

  it('keeps the box, with a glyph, when nothing loads', () => {
    const { container } = render(<FindingArt album="/gone.jpg" />);
    fireEvent.error(img(container) as Element);
    expect(img(container)).toBeNull();
    const box = container.querySelector('.repair-art') as HTMLElement;
    expect(box.classList.contains('empty')).toBe(true);
    expect(box.querySelector('svg')).not.toBeNull();
  });

  it('starts over from the cover when the row in the slot changes', () => {
    const { container, rerender } = render(<FindingArt album="/gone.jpg" />);
    fireEvent.error(img(container) as Element);
    rerender(<FindingArt album="/next.jpg" />);
    expect(img(container)?.getAttribute('src')).toBe('/next.jpg');
  });
});
