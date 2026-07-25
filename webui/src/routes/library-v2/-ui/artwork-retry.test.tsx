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

  it('rev25-12: does not commit a stale retry suffix onto a new base on src change', () => {
    const { rerender } = render(
      <Artwork src="/api/library/v2/artwork/artist/7" alt="Artist" className="c" />,
    );
    fireEvent.error(screen.getByAltText('Artist'));
    act(() => void vi.advanceTimersByTime(2000));
    // handleError briefly swaps in the placeholder <div> before the retry
    // fires, so the retried <img> is a fresh DOM node — re-query for it.
    const image = screen.getByAltText('Artist') as HTMLImageElement;
    expect(image.getAttribute('src')).toContain('retry=1');

    // React flushes the base-keyed useEffect synchronously inside act(), so
    // asserting only on the settled DOM after rerender() can't see a frame
    // that briefly existed mid-commit. A real `<img>` element starts loading
    // whatever `src` it's given the instant the attribute is set, before any
    // later effect corrects it — so what matters is every value the element
    // was ever pointed at, not just the last one. Capture that directly.
    const observedSrc: string[] = [];
    const originalSetAttribute = image.setAttribute.bind(image);
    image.setAttribute = ((name: string, value: string) => {
      if (name === 'src') observedSrc.push(value);
      return originalSetAttribute(name, value);
    }) as typeof image.setAttribute;

    rerender(<Artwork src="/api/library/v2/artwork/artist/7?v=99" alt="Artist" className="c" />);

    // No value the element was ever pointed at during this update may still
    // carry the previous base's retry suffix — that would force a second,
    // guaranteed cache-missing image load for a cover that just arrived.
    expect(observedSrc.some((value) => value.includes('retry='))).toBe(false);
    expect(image.getAttribute('src')).toBe('/api/library/v2/artwork/artist/7?v=99');
  });

  it('rev25-12: never points the element at a truthy garbage src when src goes empty mid-retry', () => {
    const { rerender } = render(
      <Artwork src="/api/library/v2/artwork/artist/7" alt="Artist" className="c" />,
    );
    fireEvent.error(screen.getByAltText('Artist'));
    act(() => void vi.advanceTimersByTime(2000));
    const image = screen.getByAltText('Artist') as HTMLImageElement;
    expect(image.getAttribute('src')).toContain('retry=1');

    // Empty base + a leftover retry count used to compute `'' + '?retry=1'`
    // — a non-empty, truthy string that `<img>` resolves against the current
    // document (visible as a broken-image flash) instead of the placeholder.
    const observedSrc: string[] = [];
    const originalSetAttribute = image.setAttribute.bind(image);
    image.setAttribute = ((name: string, value: string) => {
      if (name === 'src') observedSrc.push(value);
      return originalSetAttribute(name, value);
    }) as typeof image.setAttribute;

    rerender(<Artwork src="" alt="Artist" className="c" />);

    expect(observedSrc.some((value) => value.startsWith('?'))).toBe(false);
    expect(screen.queryByAltText('Artist')).toBeNull();
    expect(screen.getByLabelText('Artist').textContent).toBe('♪');
  });

  it('appends the thumb variant before the retry marker', () => {
    render(<Artwork src="/api/library/v2/artwork/artist/9?v=42" alt="Thumb" className="c" thumb />);

    const image = screen.getByAltText('Thumb') as HTMLImageElement;
    expect(image.getAttribute('src')).toBe('/api/library/v2/artwork/artist/9?v=42&size=thumb');

    fireEvent.error(image);
    act(() => void vi.advanceTimersByTime(2000));

    expect((screen.getByAltText('Thumb') as HTMLImageElement).getAttribute('src')).toBe(
      '/api/library/v2/artwork/artist/9?v=42&size=thumb&retry=1',
    );
  });
});
