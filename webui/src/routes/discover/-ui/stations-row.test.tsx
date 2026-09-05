/** Recommended Stations: two named actions, real states, honest subtitles. */

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Station } from '../-discover.stations';

import { fetchStations, stationSubtitle } from '../-discover.stations';
import { StationsRow } from './stations-row';

const STATIONS: Station[] = [
  {
    artist_id: '7',
    name: 'bbno$',
    image_url: 'http://b.jpg',
    with: ['Yung Gravy', 'Y2K'],
    related: [],
  },
  { artist_id: '9', name: 'Kick Bong', image_url: '', with: [], related: [] },
];

function stubFetch(payload: unknown, ok = true) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok, status: ok ? 200 : 500, json: async () => payload })),
  );
}

function renderRow(over: Partial<React.ComponentProps<typeof StationsRow>> = {}) {
  return render(
    <StationsRow stations={STATIONS} onView={vi.fn()} onPlayRadio={vi.fn()} {...over} />,
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('StationsRow', () => {
  it('gives every card TWO named controls, not one ambiguous click', () => {
    const onView = vi.fn();
    const onPlayRadio = vi.fn();
    renderRow({ onView, onPlayRadio });

    fireEvent.click(screen.getByLabelText('View the bbno$ station'));
    expect(onView).toHaveBeenCalledWith(STATIONS[0]);
    fireEvent.click(screen.getByLabelText('Play bbno$ radio'));
    expect(onPlayRadio).toHaveBeenCalledWith(STATIONS[0]);
  });

  it('is not itself a button, so neither action fires by accident', () => {
    const { container } = renderRow();
    const card = container.querySelector('.discover-station-card')!;
    expect(card.tagName).toBe('DIV');
    // and no control is nested inside another
    expect(card.querySelector('button button')).toBeNull();
  });

  it('shows a pending state on the clicked card only', () => {
    renderRow({ pendingId: '7' });
    expect(screen.getByText('Opening…')).toBeInTheDocument();
    expect(screen.getByLabelText('View the Kick Bong station')).not.toBeDisabled();
  });

  it('reports a failure next to the control that failed', () => {
    renderRow({ cardErrors: { '7': 'No playable tracks' } });
    expect(screen.getByRole('alert')).toHaveTextContent('No playable tracks');
  });

  it('disappears entirely with no stations (the empty-section rule)', () => {
    const { container } = renderRow({ stations: [] });
    expect(container.querySelector('#recommended-stations-section')).toBeNull();
  });

  it('KEEPS the row on failure, with a retry — an error is not an empty library', () => {
    // The first version collapsed every failure to an empty array, which
    // rendered exactly like "you have no stations".
    const onRetry = vi.fn();
    render(
      <StationsRow
        stations={null}
        error="Could not load your stations."
        onRetry={onRetry}
        onView={vi.fn()}
        onPlayRadio={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load your stations.');
    fireEvent.click(screen.getByText('Try again'));
    expect(onRetry).toHaveBeenCalled();
  });

  it('shows skeletons only while the first fetch is in flight', () => {
    const { container } = render(
      <StationsRow stations={null} loading onView={vi.fn()} onPlayRadio={vi.fn()} />,
    );
    expect(container.querySelectorAll('.discover-station-card--loading')).toHaveLength(4);
  });
});

describe('stationSubtitle', () => {
  it('claims "With" only for artists the library can play', () => {
    expect(stationSubtitle(STATIONS[0])).toBe('With Yung Gravy, Y2K and more');
  });

  it('falls back to a WEAKER label for unverified companions', () => {
    // naming an artist the library cannot play promised something the radio
    // tiers were never going to deliver
    expect(
      stationSubtitle({
        artist_id: '1',
        name: 'X',
        image_url: '',
        with: [],
        related: ['SebastiAn'],
      }),
    ).toBe('Related artists: SebastiAn');
  });

  it('says only what is true for a station with no companions', () => {
    expect(stationSubtitle(STATIONS[1])).toBe('Artist radio from your library');
  });
});

describe('fetchStations', () => {
  it('unwraps the stations list', async () => {
    stubFetch({ success: true, stations: STATIONS });
    expect(await fetchStations()).toEqual(STATIONS);
  });

  it('THROWS on failure rather than returning an empty list', async () => {
    stubFetch({ success: false, error: 'boom' });
    await expect(fetchStations()).rejects.toThrow('boom');
    stubFetch({}, false);
    await expect(fetchStations()).rejects.toThrow();
  });
});

it('guards radio startup and makes a rejected start retryable', async () => {
  let reject!: (error: Error) => void;
  const play = vi.fn(
    () =>
      new Promise<void>((_resolve, fail) => {
        reject = fail;
      }),
  );
  renderRow({ onPlayRadio: play });
  const button = screen.getByRole('button', { name: 'Play bbno$ radio' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(play).toHaveBeenCalledTimes(1);
  expect(button).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Play Kick Bong radio' })).not.toBeDisabled();
  reject(new Error('Temporary playback failure'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Temporary playback failure');
  expect(button).not.toBeDisabled();
});
