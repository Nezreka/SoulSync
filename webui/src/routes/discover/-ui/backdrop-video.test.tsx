import { act, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BACKDROP_FADE_DELAY_MS, BackdropVideo } from './backdrop-video';

afterEach(() => vi.useRealTimers());

describe('BackdropVideo', () => {
  it('mounts no player unless its banner holds the stage', () => {
    const { container, rerender } = render(<BackdropVideo videoId="v1" playing={false} />);
    expect(container.querySelector('iframe')).toBeNull();
    rerender(<BackdropVideo videoId={null} playing />);
    expect(container.querySelector('iframe')).toBeNull();
    rerender(<BackdropVideo videoId="v1" playing />);
    expect(container.querySelector('iframe')!.getAttribute('src')).toContain('/embed/v1');
  });

  it('fades in a moment after loading, over the photo', () => {
    vi.useFakeTimers();
    const { container } = render(<BackdropVideo videoId="v1" playing />);
    const wrap = container.querySelector('.dsc-backdrop-video')!;
    fireEvent.load(container.querySelector('iframe')!);
    expect(wrap).not.toHaveClass('ready');
    act(() => {
      vi.advanceTimersByTime(BACKDROP_FADE_DELAY_MS + 10);
    });
    expect(wrap).toHaveClass('ready');
  });

  it('is decoration: hidden from assistive tech and out of the tab order', () => {
    const { container } = render(<BackdropVideo videoId="v1" playing />);
    expect(container.querySelector('.dsc-backdrop-video')).toHaveAttribute('aria-hidden', 'true');
    expect(container.querySelector('iframe')).toHaveAttribute('tabindex', '-1');
  });
});
