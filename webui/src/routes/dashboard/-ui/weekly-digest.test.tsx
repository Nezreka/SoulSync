import { fireEvent, render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { WeeklyDigest, weekTrend } from './weekly-digest';

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

describe('the week on the board', () => {
  it('leads with the hours, across how many tracks', async () => {
    serve(DIGEST);
    const { container } = render(<WeeklyDigest />);
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(container.querySelector('.wd-lead .wd-num')!.textContent).toBe('38h 24m');
    expect(container.querySelector('.wd-lead .wd-label')!.textContent).toBe(
      'listened across 214 tracks',
    );
    expect(
      Array.from(container.querySelectorAll('.wd-chip')).map((chip) => chip.textContent),
    ).toEqual(['Tame Impalatop artist', '9new discoveries', '12-daystreak']);
  });

  it("shows the top artist's picture when the library has one, and drops a broken one", async () => {
    serve({ ...DIGEST, top_artist_image: '/api/library/v2/artwork/artist/7' });
    const { container } = render(<WeeklyDigest />);
    await waitFor(() => expect(banner()).toBeTruthy());
    const picture = container.querySelector<HTMLImageElement>('.wd-chip--artist img')!;
    expect(picture.getAttribute('src')).toBe('/api/library/v2/artwork/artist/7');
    fireEvent.error(picture);
    expect(container.querySelector('.wd-chip--artist img')).toBeNull();
    expect(container.querySelector('.wd-chip--artist')!.textContent).toContain('Tame Impala');
  });

  it('has no picture slot when the payload names none', async () => {
    serve(DIGEST);
    const { container } = render(<WeeklyDigest />);
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(container.querySelector('.wd-chip--artist img')).toBeNull();
  });
});

describe('the week, day by day', () => {
  const RICH = {
    ...DIGEST,
    previous_time_ms: DIGEST.time_ms - 2 * 3600000,
    daily: DIGEST.daily.map((day, n) => ({
      ...day,
      hours: n === 2 ? 0 : day.hours,
      tracks: n === 2 ? 0 : 10 + n,
      album: n === 0 ? 'Currents' : null,
      cover: n === 0 ? '/api/library/v2/artwork/album/3' : null,
    })),
    on_repeat: [
      { name: 'Currents', artist: 'Tame Impala', play_count: 31, image_url: '/api/art/1' },
      { name: 'Lonerism', artist: 'Tame Impala', play_count: 1, image_url: null },
    ],
  };

  it('each bar says what it is worth, and the busiest and today stand out', async () => {
    serve(RICH);
    const { container } = render(<WeeklyDigest />);
    await waitFor(() => expect(banner()).toBeTruthy());
    const cols = Array.from(container.querySelectorAll<HTMLElement>('.wd-bar-col'));
    expect(cols.map((col) => col.querySelector('.wd-val')!.textContent)).toEqual([
      '4.2h',
      '6.1h',
      '0',
      '7.8h',
      '5.5h',
      '8.9h',
      '2.5h',
    ]);
    expect(cols[5].className).toContain('wd-bar-col--best');
    expect(cols[6].className).toContain('wd-bar-col--today');
    expect(cols[2].className).toContain('wd-bar-col--none');
    expect(cols[2].title).toBe('Nothing played');
    expect(cols[0].title).toBe('4h 12m, 10 plays, mostly Currents');
    expect(cols[0].querySelector('img.wd-bar-cover')!.getAttribute('src')).toBe(
      '/api/library/v2/artwork/album/3',
    );
    expect(cols[1].querySelector('.wd-bar-cover')).toBeNull();
  });

  it('compares the week with the one before, and lists what was on repeat', async () => {
    serve(RICH);
    const { container } = render(<WeeklyDigest />);
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(container.querySelector('.wd-trend')!.textContent).toBe('2h 0m more than last week');
    const rows = Array.from(container.querySelectorAll('.wd-repeat-row'));
    expect(rows.map((row) => row.querySelector('.wd-repeat-plays')!.textContent)).toEqual([
      '31 plays',
      '1 play',
    ]);
    expect(rows[0].querySelector('b')!.textContent).toBe('Currents');
  });

  it('says nothing about last week or repeats when the server sends neither', async () => {
    serve(DIGEST);
    const { container } = render(<WeeklyDigest />);
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(container.querySelector('.wd-trend')).toBeNull();
    expect(container.querySelector('.wd-repeat')).toBeNull();
  });

  it('puts the comparison into words', () => {
    expect(weekTrend(3600000, undefined)).toBeNull();
    expect(weekTrend(3600000, 0)).toBeNull();
    expect(weekTrend(3600000, 3600000 + 60000)?.tone).toBe('same');
    expect(weekTrend(3600000, 7200000)).toEqual({
      text: '1h 0m less than last week',
      tone: 'down',
    });
  });
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
