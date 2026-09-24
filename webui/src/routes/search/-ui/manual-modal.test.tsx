import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { BasicAlbum, BasicTrack } from '../-basic.types';

import * as api from '../-basic.enriched';
import { titleFromFilename } from '../-basic.enriched';
import { ManualModal } from './manual-modal';

vi.mock('../-basic.enriched', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../-basic.enriched')>()),
  startManual: vi.fn(),
}));

function track(over: Partial<BasicTrack> = {}): BasicTrack {
  return {
    result_type: 'track',
    username: 'deadair',
    filename: 'Radiohead\\2003-06-28 Glastonbury\\02 - Lucky.flac',
    size: 30,
    bitrate: 1411,
    duration: 259_000,
    quality: 'flac',
    free_upload_slots: 1,
    upload_speed: 1,
    queue_length: 0,
    sample_rate: 44100,
    bit_depth: 16,
    artist: 'Radiohead',
    title: null,
    album: null,
    track_number: 2,
    quality_score: 1,
    ...over,
  };
}

const album: BasicAlbum = {
  result_type: 'album',
  username: 'deadair',
  album_path: 'Radiohead\\2003-06-28 Glastonbury',
  album_title: '2003-06-28 Glastonbury',
  artist: 'Radiohead',
  track_count: 2,
  total_size: 60,
  dominant_quality: 'flac',
  year: '2003',
  free_upload_slots: 1,
  upload_speed: 1,
  queue_length: 0,
  quality_score: 1,
  tracks: [
    track({ filename: 'Radiohead\\2003-06-28 Glastonbury\\01 - 2 + 2 = 5.flac', track_number: 1 }),
    track(),
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Reflect.deleteProperty(window, 'showConfirmDialog');
});

describe('titleFromFilename', () => {
  it.each([
    ['Music\\Glasto\\02 - Lucky.flac', '', 'Lucky'],
    ['01. Airbag.mp3', '', 'Airbag'],
    ['1-03 Karma Police.flac', '', 'Karma Police'],
    ['Radiohead - Lucky.flac', 'Radiohead', 'Lucky'],
    ['Nosferatu_-_Beaver_Cleaver.flac', '', 'Nosferatu - Beaver Cleaver'],
    ['7 rings.flac', '', '7 rings'],
    ['02 Lucky.flac', '', 'Lucky'],
    ['22 Acacia Avenue.mp3', '', '22 Acacia Avenue'],
  ])('%s → %s', (filename, artist, expected) => {
    expect(titleFromFilename(filename, artist)).toBe(expected);
  });
});

describe('prefilled from the result', () => {
  it('an album brings its name, artist, year and a row per file', () => {
    render(<ManualModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    expect((screen.getByLabelText('Album') as HTMLInputElement).value).toBe(
      '2003-06-28 Glastonbury',
    );
    expect((screen.getByLabelText('Album artist') as HTMLInputElement).value).toBe('Radiohead');
    expect((screen.getByLabelText('Date') as HTMLInputElement).value).toBe('2003');
    const titles = screen.getAllByLabelText(/^Title for/) as HTMLInputElement[];
    // untagged files: titles come from the filenames
    expect(titles.map((t) => t.value)).toEqual(['2 + 2 = 5', 'Lucky']);
    expect(screen.getByRole('radio', { name: 'Album' }).getAttribute('aria-checked')).toBe('true');
  });

  it('a lone file starts as a single', () => {
    render(<ManualModal target={{ kind: 'track', track: track() }} onClose={vi.fn()} />);
    expect(screen.getByRole('radio', { name: 'Single' }).getAttribute('aria-checked')).toBe('true');
    expect((screen.getByLabelText('Album') as HTMLInputElement).value).toBe('Lucky');
  });
});

describe('sending it', () => {
  it('sends exactly what the user typed, per file', async () => {
    vi.mocked(api.startManual).mockResolvedValue({
      success: true,
      message: 'Downloading 2 tracks',
    });
    const onClose = vi.fn();
    render(<ManualModal target={{ kind: 'album', album }} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText('Album'), {
      target: { value: 'Live at Glastonbury 2003' },
    });
    fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2003-06-28' } });
    fireEvent.change(screen.getByLabelText('Genre'), { target: { value: 'Alternative Rock' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Live' }));
    fireEvent.change(screen.getAllByLabelText(/^Title for/)[1], {
      target: { value: 'Lucky (live)' },
    });
    fireEvent.change(screen.getByLabelText('Cover art link'), {
      target: { value: 'http://cover' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Download and tag' }));
    await waitFor(() => expect(api.startManual).toHaveBeenCalled());
    const payload = vi.mocked(api.startManual).mock.calls[0][0];
    expect(payload.album).toEqual({
      name: 'Live at Glastonbury 2003',
      artist: 'Radiohead',
      date: '2003-06-28',
      genre: 'Alternative Rock',
      type: 'live',
      image_url: 'http://cover',
    });
    expect(payload.tracks).toEqual([
      {
        file_key: 'deadair::Radiohead\\2003-06-28 Glastonbury\\01 - 2 + 2 = 5.flac',
        title: '2 + 2 = 5',
        track_number: 1,
        disc_number: 1,
      },
      {
        file_key: 'deadair::Radiohead\\2003-06-28 Glastonbury\\02 - Lucky.flac',
        title: 'Lucky (live)',
        track_number: 2,
        disc_number: 1,
      },
    ]);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('will not send an album with no name, and says why', () => {
    render(<ManualModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Album'), { target: { value: '  ' } });
    expect(
      (screen.getByRole('button', { name: 'Download and tag' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText('Give the album a name.')).toBeTruthy();
  });

  it('will not send a track with no title', () => {
    render(<ManualModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    fireEvent.change(screen.getAllByLabelText(/^Title for/)[0], { target: { value: '' } });
    expect(
      (screen.getByRole('button', { name: 'Download and tag' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText('Every track needs a title.')).toBeTruthy();
  });

  it('asks before a blocklisted artist and retries with the override', async () => {
    vi.mocked(api.startManual)
      .mockResolvedValueOnce({ success: false, blocked: true, blocked_name: 'Radiohead' })
      .mockResolvedValueOnce({ success: true });
    window.showConfirmDialog = vi.fn(async () => true);
    render(<ManualModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Download and tag' }));
    await waitFor(() => expect(api.startManual).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.startManual).mock.calls[1][0]).toMatchObject({ ignore_blocklist: true });
  });

  it('shows the server sentence when it refuses', async () => {
    vi.mocked(api.startManual).mockResolvedValue({
      success: false,
      error: 'That cover is too big.',
    });
    render(<ManualModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Download and tag' }));
    expect(await screen.findByText('That cover is too big.')).toBeTruthy();
  });
});
