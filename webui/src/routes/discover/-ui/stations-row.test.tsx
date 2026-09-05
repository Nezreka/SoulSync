/** the recommended stations row: fetch, render, one-click radio. */

import { render, waitFor, fireEvent } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { StationsRow, fetchStations, stationSubtitle } from './stations-row';

const STATIONS = [
  { artist_id: '7', name: 'bbno$', image_url: 'http://b.jpg', with: ['Yung Gravy', 'Y2K'] },
  { artist_id: '9', name: 'Kick Bong', image_url: '', with: [] },
];

let radioCalls: unknown[][] = [];

beforeEach(() => {
  radioCalls = [];
  window.startArtistRadioById = vi.fn((...args: unknown[]) => {
    radioCalls.push(args);
  });
});

function stubFetch(payload: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ json: async () => payload })),
  );
}

describe('StationsRow', () => {
  it('renders a card per station and starts radio on click', async () => {
    stubFetch({ success: true, stations: STATIONS });
    const { container } = render(<StationsRow />);
    await waitFor(() => expect(container.textContent).toContain('bbno$'));
    expect(container.textContent).toContain('With Yung Gravy, Y2K and more');
    expect(container.textContent).toContain('Artist radio from your library');
    fireEvent.click(container.querySelectorAll('.discover-station-card')[0]);
    expect(radioCalls).toEqual([['7', 'bbno$']]);
  });

  it('disappears entirely with no stations (the empty-section rule)', async () => {
    stubFetch({ success: true, stations: [] });
    const { container } = render(<StationsRow />);
    await waitFor(() =>
      expect(container.querySelector('#recommended-stations-section')).toBeNull(),
    );
  });

  it('a failed fetch also renders nothing rather than a broken row', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('down');
      }),
    );
    const { container } = render(<StationsRow />);
    await waitFor(() =>
      expect(container.querySelector('#recommended-stations-section')).toBeNull(),
    );
  });
});

describe('stationSubtitle', () => {
  it('joins companions, falls back for loners', () => {
    expect(stationSubtitle(STATIONS[0])).toBe('With Yung Gravy, Y2K and more');
    expect(stationSubtitle(STATIONS[1])).toBe('Artist radio from your library');
  });
});

describe('fetchStations', () => {
  it('unwraps the stations list and treats failure as none', async () => {
    stubFetch({ success: true, stations: STATIONS });
    expect(await fetchStations()).toEqual(STATIONS);
    stubFetch({ success: false });
    expect(await fetchStations()).toEqual([]);
  });
});
