import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { formatDuration, queueRow, subLine, withLine } from '../-artist-detail.appears-on';
import { AppearsOnSection } from './appears-on-section';

/**
 * "appears on": features and collabs filed under another artist. "We Found
 * Love" lives under calvin harris, rihanna's page shows it here.
 */

const WFL = {
  id: '100',
  title: 'We Found Love',
  duration: 215000,
  file_path: '/music/Calvin Harris/18 Months/01 We Found Love.flac',
  album_id: '10',
  album_title: '18 Months',
  album_thumb_url: '/img/18.jpg',
  year: 2012,
  artist_id: '1',
  artist_name: 'Calvin Harris',
  credits: ['Calvin Harris', 'Rihanna'],
};

function stub(tracks: unknown[]) {
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify({ success: true, tracks }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as { playTrackList?: unknown }).playTrackList;
});

describe('helpers', () => {
  it('names everyone else on the track, not the page artist', () => {
    expect(withLine(WFL, 'Rihanna')).toBe('Calvin Harris');
    expect(withLine({ ...WFL, credits: ['A', 'B', 'rihanna'] }, 'Rihanna')).toBe('A, B');
  });

  it('falls back to the artist the track is filed under', () => {
    expect(withLine({ ...WFL, credits: [] }, 'Rihanna')).toBe('Calvin Harris');
  });

  it('skips missing pieces of the sub line', () => {
    expect(subLine(WFL, 'Rihanna')).toBe('with Calvin Harris · 18 Months · 2012');
    expect(subLine({ ...WFL, year: null, credits: [], artist_name: '' }, 'Rihanna')).toBe(
      '18 Months',
    );
  });

  it('queues the radio-row shape with the full credit', () => {
    const row = queueRow(WFL, 'Rihanna');
    expect(row).toMatchObject({
      id: '100',
      title: 'We Found Love',
      artist: 'Calvin Harris, Rihanna',
      album: '18 Months',
      file_path: WFL.file_path,
      is_library: true,
      image_url: '/img/18.jpg',
    });
  });

  it('formats durations', () => {
    expect(formatDuration(215000)).toBe('3:35');
    expect(formatDuration(null)).toBe('');
  });
});

describe('AppearsOnSection', () => {
  it("renders nothing when the artist is on nobody else's tracks", async () => {
    const fetchMock = stub([]);
    const { container } = render(<AppearsOnSection artistId="2" artistName="Rihanna" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
  });

  it('asks for this artist and lists the feature', async () => {
    const fetchMock = stub([WFL]);
    render(<AppearsOnSection artistId="2" artistName="Rihanna" />);
    expect(await screen.findByText('We Found Love')).toBeTruthy();
    expect(String(fetchMock.mock.calls[0]?.[0 as never])).toBe('/api/artist/2/appears-on');
    expect(screen.getByText('with Calvin Harris · 18 Months · 2012')).toBeTruthy();
    expect(screen.getByText('1 track')).toBeTruthy();
  });

  it('leaves out rows with no file to play', async () => {
    stub([{ ...WFL, id: '99', title: 'Gone', file_path: null }, WFL]);
    render(<AppearsOnSection artistId="2" artistName="Rihanna" />);
    await screen.findByText('We Found Love');
    expect(screen.queryByText('Gone')).toBeNull();
    expect(screen.getByText('1 track')).toBeTruthy();
  });

  it('plays from the clicked track with the rest as the queue', async () => {
    const second = { ...WFL, id: '101', title: 'Where Have You Been' };
    stub([WFL, second]);
    const play = vi.fn();
    window.playTrackList = play;
    render(<AppearsOnSection artistId="2" artistName="Rihanna" />);
    fireEvent.click(await screen.findByTitle('Play Where Have You Been'));
    expect(play).toHaveBeenCalledTimes(1);
    const [queue, context] = play.mock.calls[0] as [Array<{ id: string }>, string];
    expect(queue.map((t) => t.id)).toEqual(['101', '100']);
    expect(context).toBe('Rihanna, appears on');
  });

  it('collapses long lists behind show all', async () => {
    stub(Array.from({ length: 12 }, (_, i) => ({ ...WFL, id: String(i), title: `Song ${i}` })));
    render(<AppearsOnSection artistId="2" artistName="Rihanna" />);
    await screen.findByText('Song 0');
    expect(screen.queryByText('Song 8')).toBeNull();
    fireEvent.click(screen.getByText('Show all 12'));
    expect(screen.getByText('Song 11')).toBeTruthy();
  });
});
