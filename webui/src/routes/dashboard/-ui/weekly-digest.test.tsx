import { fireEvent, render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { WeeklyDigest } from './weekly-digest';

const DIGEST = {
  success: true,
  tracks_played: 214,
  time_ms: 138240000,
  top_artist: 'Tame Impala',
  discoveries: 9,
  streak_days: 12,
  start_date: '2026-09-28',
  end_date: '2026-10-04',
  daily: [
    { date: '2026-09-28', day: 'm', hours: 4.2 },
    { date: '2026-09-29', day: 't', hours: 6.1 },
    { date: '2026-09-30', day: 'w', hours: 3.4 },
    { date: '2026-10-01', day: 't', hours: 7.8 },
    { date: '2026-10-02', day: 'f', hours: 5.5 },
    { date: '2026-10-03', day: 's', hours: 8.9 },
    { date: '2026-10-04', day: 's', hours: 2.5 },
  ],
};

function serve(payload: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => payload })) as never);
}

const banner = () => document.querySelector('[data-card="weekly-digest"]');

beforeEach(() => {
  vi.unstubAllGlobals();
  window.navigateToPage = vi.fn();
});

describe('the weekly digest banner', () => {
  it('renders the stats and the 7-day chart when plays exist', async () => {
    serve(DIGEST);
    const { container } = render(<WeeklyDigest />);
    await waitFor(() => expect(banner()).toBeTruthy());

    const text = container.textContent ?? '';
    expect(text).toContain('38h 24m');
    expect(text).toContain('214');
    expect(text).toContain('Tame Impala');
    expect(text).toContain('12-day');
    expect(container.querySelectorAll('.wd-bar-col').length).toBe(7);
  });

  it('renders nothing when there are no plays in the window', async () => {
    serve({ success: true, tracks_played: 0 });
    render(<WeeklyDigest />);
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    // let the promise chain settle
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(banner()).toBeNull();
  });

  it('renders nothing when the fetch fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('down');
      }) as never,
    );
    render(<WeeklyDigest />);
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(banner()).toBeNull();
  });

  it('sends "full stats" to the stats page', async () => {
    serve(DIGEST);
    const { container } = render(<WeeklyDigest />);
    await waitFor(() => expect(banner()).toBeTruthy());

    fireEvent.click(container.querySelector('.wd-link') as HTMLElement);
    expect(window.navigateToPage).toHaveBeenCalledWith('stats');
  });
});
