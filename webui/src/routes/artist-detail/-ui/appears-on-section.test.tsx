import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  albumSubLine,
  albumTracks,
  formatDuration,
  queueRow,
  subLine,
  withLine,
} from '../-artist-detail.appears-on';
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

const WTT = {
  id: '30',
  title: 'Watch the Throne',
  thumb_url: '/img/wtt.jpg',
  year: 2011,
  artist_id: '5',
  artist_name: 'JAY-Z',
  credits: ['JAY-Z', 'Kanye West'],
  tracks: [
    { id: '301', title: 'No Church in the Wild', track_number: 1, file_path: '/m/wtt/01.flac' },
    { id: '302', title: 'Lift Off', track_number: 2, file_path: null },
    { id: '303', title: 'Niggas in Paris', track_number: 3, file_path: '/m/wtt/03.flac' },
  ],
};

function stub(tracks: unknown[], albums: unknown[] = []) {
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify({ success: true, tracks, albums }), {
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

describe('collab albums', () => {
  it('describes the album from the other artists point of view', () => {
    expect(albumSubLine(WTT, 'Kanye West')).toBe('with JAY-Z · 2011');
  });

  it('only queues album tracks with a file, carrying the album along', () => {
    const tracks = albumTracks(WTT);
    expect(tracks.map((t) => t.id)).toEqual(['301', '303']);
    expect(tracks[0]).toMatchObject({
      album_title: 'Watch the Throne',
      album_thumb_url: '/img/wtt.jpg',
    });
  });

  it('shows the album and plays it', async () => {
    stub([], [WTT]);
    const play = vi.fn();
    window.playTrackList = play;
    render(<AppearsOnSection artistId="6" artistName="Kanye West" />);
    fireEvent.click(await screen.findByTitle('Play Watch the Throne'));
    const [queue] = play.mock.calls[0] as [Array<{ id: string; artist: string }>];
    expect(queue.map((t) => t.id)).toEqual(['301', '303']);
    expect(queue[0]?.artist).toBe('JAY-Z, Kanye West');
    expect(screen.getByText('with JAY-Z · 2011')).toBeTruthy();
    expect(screen.getByText('1 album')).toBeTruthy();
  });

  it('play all takes the albums first, then the songs', async () => {
    stub([{ ...WFL, id: '100' }], [WTT]);
    const play = vi.fn();
    window.playTrackList = play;
    render(<AppearsOnSection artistId="6" artistName="Kanye West" />);
    await screen.findByText('Watch the Throne');
    expect(screen.getByText('1 album · 1 track')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Play all'));
    const [queue] = play.mock.calls[0] as [Array<{ id: string }>];
    expect(queue.map((t) => t.id)).toEqual(['301', '303', '100']);
  });

  it('hides an album with nothing to play', async () => {
    stub([WFL], [{ ...WTT, tracks: [{ id: '302', title: 'Lift Off', file_path: null }] }]);
    render(<AppearsOnSection artistId="6" artistName="Kanye West" />);
    await screen.findByText('We Found Love');
    expect(screen.queryByText('Watch the Throne')).toBeNull();
  });
});
