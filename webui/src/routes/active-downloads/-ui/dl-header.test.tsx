import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DlHeaderProps, DownloadsMediaState } from './dl-header';

import {
  DlHeader,
  DownloadsMediaContext,
  SPEED_SAMPLES,
  Sparkline,
  quietLine,
  useSpeedSamples,
} from './dl-header';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const base: DlHeaderProps = {
  tabs: [
    { value: 'downloads', label: 'Downloads' },
    { value: 'review', label: 'Review', badge: 3 },
    { value: 'clients', label: 'Clients' },
  ],
  view: 'downloads',
  onView: vi.fn(),
  chips: [{ value: 'all', label: 'All', count: 2 }],
  filter: 'all',
  onFilter: vi.fn(),
  historyCount: null,
  onOpenHistory: null,
  menu: [],
  live: null,
  quiet: 'Nothing downloading.',
};

function withSwitch(over: Partial<DownloadsMediaState> = {}) {
  const ctx: DownloadsMediaState = {
    media: 'music',
    setMedia: vi.fn(),
    active: { music: 3, video: 0 },
    reportActive: vi.fn(),
    ...over,
  };
  render(
    <DownloadsMediaContext.Provider value={ctx}>
      <DlHeader {...base} />
    </DownloadsMediaContext.Provider>,
  );
  return ctx;
}

describe('the Music | Video switch', () => {
  it('shows what each side has moving, idle for a quiet one', () => {
    withSwitch();
    const music = screen.getByRole('tab', { name: 'Music' });
    const video = screen.getByRole('tab', { name: 'Video' });
    expect(music.getAttribute('aria-selected')).toBe('true');
    expect(music.querySelector('.dlh-side-live')?.textContent).toBe('3');
    expect(video.querySelector('.dlh-side-idle')?.textContent).toBe('idle');
  });

  it('says nothing for a side it has not heard from yet', () => {
    withSwitch({ active: { music: 1, video: null } });
    const video = screen.getByRole('tab', { name: 'Video' });
    expect(video.querySelector('.dlh-side-live, .dlh-side-idle')).toBeNull();
  });

  it('switches sides on click', () => {
    const ctx = withSwitch();
    fireEvent.click(screen.getByRole('tab', { name: 'Video' }));
    expect(ctx.setMedia).toHaveBeenCalledWith('video');
  });

  it('is left out when the page renders a side on its own', () => {
    render(<DlHeader {...base} />);
    expect(screen.queryByRole('tablist', { name: 'Downloads media' })).toBeNull();
  });
});

describe('the speed graph', () => {
  it('samples every 2s and keeps one minute', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ s }) => useSpeedSamples(s, true), {
      initialProps: { s: 100 },
    });
    expect(result.current).toEqual([100]);
    rerender({ s: 300 });
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current).toEqual([100, 300]);
    act(() => {
      vi.advanceTimersByTime(2000 * 40);
    });
    expect(result.current).toHaveLength(SPEED_SAMPLES);
  });

  it('stops sampling when nothing moves', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useSpeedSamples(100, false));
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(result.current).toEqual([]);
  });

  it('draws nothing under two points, then a line scaled to the busiest', () => {
    const one = render(<Sparkline samples={[5]} />);
    expect(one.container.querySelector('svg')).toBeNull();
    cleanup();
    const { container } = render(<Sparkline samples={[0, 50, 100]} />);
    const d = container.querySelector('.dlh-spark-line')?.getAttribute('d') ?? '';
    // the busiest sample sits at the top, the idle one at the bottom
    expect(d).toMatch(/L180\.0 5\.0$/);
    expect(d.startsWith('M')).toBe(true);
  });
});

describe('quietLine', () => {
  it('reads like a sentence', () => {
    expect(quietLine(0, '')).toBe('Nothing downloading.');
    expect(quietLine(3, '9:41 PM')).toBe('Nothing downloading. 3 finished today, last at 9:41 PM.');
  });
});
