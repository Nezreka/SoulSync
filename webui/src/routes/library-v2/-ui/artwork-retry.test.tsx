import { fireEvent, render, screen, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Artwork } from './library-v2-page';

describe('Artwork placeholder retry (perf25-02)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('retries a locally cached cover that is still being resolved', () => {
    render(<Artwork src="/api/library/v2/artwork/artist/7" alt="Artist" className="c" />);

    const image = screen.getByAltText('Artist') as HTMLImageElement;
    expect(image.getAttribute('src')).toBe('/api/library/v2/artwork/artist/7');

    fireEvent.error(image);
    // While the background build runs the user sees the placeholder, not a
    // broken image.
    expect(screen.getByLabelText('Artist').textContent).toBe('♪');

    act(() => void vi.advanceTimersByTime(2000));

    const retried = screen.getByAltText('Artist') as HTMLImageElement;
    expect(retried.getAttribute('src')).toContain('retry=1');
  });

  it('gives up after the bounded number of retries', () => {
    render(<Artwork src="/api/library/v2/artwork/album/3" alt="Album" className="c" />);

    for (let attempt = 0; attempt < 4; attempt += 1) {
      const image = screen.queryByAltText('Album');
      if (!image) break;
      fireEvent.error(image);
      act(() => void vi.advanceTimersByTime(30000));
    }

    expect(screen.queryByAltText('Album')).toBeNull();
    expect(screen.getByLabelText('Album').textContent).toBe('♪');
  });

  it('does not retry remote provider images', () => {
    render(<Artwork src="https://cdn.test/cover.jpg" alt="Remote" className="c" />);

    fireEvent.error(screen.getByAltText('Remote'));
    act(() => void vi.advanceTimersByTime(30000));

    expect(screen.queryByAltText('Remote')).toBeNull();
    expect(screen.getByLabelText('Remote').textContent).toBe('♪');
  });

  it('appends the thumb variant before the retry marker', () => {
    render(
      <Artwork src="/api/library/v2/artwork/artist/9?v=42" alt="Thumb" className="c" thumb />,
    );

    const image = screen.getByAltText('Thumb') as HTMLImageElement;
    expect(image.getAttribute('src')).toBe('/api/library/v2/artwork/artist/9?v=42&size=thumb');

    fireEvent.error(image);
    act(() => void vi.advanceTimersByTime(2000));

    expect((screen.getByAltText('Thumb') as HTMLImageElement).getAttribute('src')).toBe(
      '/api/library/v2/artwork/artist/9?v=42&size=thumb&retry=1',
    );
  });
});
