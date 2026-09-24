import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BasicAlbum, BasicTrack } from '../-basic.types';

import * as api from '../-basic.enriched';
import { EnrichedModal } from './enriched-modal';

vi.mock('../-basic.enriched', async (importOriginal) => {
  const real = await importOriginal<typeof import('../-basic.enriched')>();
  return {
    ...real,
    fetchProviders: vi.fn(),
    searchProvider: vi.fn(),
    matchRelease: vi.fn(),
    startEnriched: vi.fn(),
  };
});

function track(over: Partial<BasicTrack> = {}): BasicTrack {
  return {
    result_type: 'track',
    username: 'peer',
    filename: 'Music\\Glasto\\02 - Lucky.flac',
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
    title: 'Lucky',
    album: 'Glastonbury 2003',
    track_number: 2,
    quality_score: 1,
    ...over,
  };
}

const album: BasicAlbum = {
  result_type: 'album',
  username: 'peer',
  album_path: 'Music\\Glasto',
  album_title: 'Glastonbury 2003',
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
    track(),
    track({ filename: 'Music\\Glasto\\01 - 2 + 2 = 5.flac', title: '2 + 2 = 5', track_number: 1 }),
  ],
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api.fetchProviders).mockResolvedValue([
    { source: 'spotify', label: 'Spotify' },
    { source: 'deezer', label: 'Deezer', active: true },
  ]);
  vi.mocked(api.searchProvider).mockResolvedValue({
    albums: [
      {
        id: 'al1',
        name: 'Glastonbury 2003',
        artist: 'Radiohead',
        release_date: '2003-06-28',
        total_tracks: 2,
      },
      { id: 'al2', name: 'Hail to the Thief', artist: 'Radiohead', total_tracks: 14 },
    ],
    tracks: [
      { id: 'tr1', name: 'Lucky', artist: 'Radiohead', album: 'OK Computer', duration_ms: 259_000 },
    ],
  });
  window.showToast = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  Reflect.deleteProperty(window, 'showConfirmDialog');
});

async function settle() {
  await act(async () => {
    vi.advanceTimersByTime(500);
  });
}

describe('picking the release', () => {
  it('opens on the primary provider and searches it for the album', async () => {
    render(<EnrichedModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    await settle();
    await waitFor(() => expect(api.searchProvider).toHaveBeenCalled());
    const [query, source] = vi.mocked(api.searchProvider).mock.calls.at(-1)!;
    expect(query).toBe('Radiohead Glastonbury 2003');
    expect(source).toBe('deezer');
    expect(screen.getByRole('button', { name: 'Deezer' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(await screen.findByText('Best match')).toBeTruthy();
  });

  it('switching provider searches that provider, never a mix', async () => {
    render(<EnrichedModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    await settle();
    fireEvent.click(await screen.findByRole('button', { name: 'Spotify' }));
    await settle();
    await waitFor(() =>
      expect(vi.mocked(api.searchProvider).mock.calls.at(-1)?.[1]).toBe('spotify'),
    );
  });

  it('says so when the provider cannot answer, instead of showing another', async () => {
    vi.mocked(api.searchProvider).mockRejectedValue(new api.ProviderUnavailable('x'));
    render(<EnrichedModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    await settle();
    expect(await screen.findByText(/Deezer isn't available right now/)).toBeTruthy();
  });
});

describe('an album', () => {
  it('Next loads the server mapping, and Download sends the assignments', async () => {
    vi.mocked(api.matchRelease).mockResolvedValue({
      success: true,
      album: { id: 'al1', name: 'Glastonbury 2003' },
      tracks: [
        { index: 0, name: '2 + 2 = 5', track_number: 1, disc_number: 1, duration_ms: 199_000 },
        { index: 1, name: 'Lucky', track_number: 2, disc_number: 1, duration_ms: 259_000 },
      ],
      assignments: [
        { file_key: 'peer::Music\\Glasto\\02 - Lucky.flac', track_index: 1, confidence: 0.95 },
        { file_key: 'peer::Music\\Glasto\\01 - 2 + 2 = 5.flac', track_index: 0, confidence: 0.55 },
      ],
    });
    vi.mocked(api.startEnriched).mockResolvedValue({
      success: true,
      message: 'Downloading 2 tracks',
    });
    const onClose = vi.fn();
    render(<EnrichedModal target={{ kind: 'album', album }} onClose={onClose} />);
    await settle();
    await screen.findByText('Best match');

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Check the tracks')).toBeTruthy();
    expect(vi.mocked(api.matchRelease).mock.calls[0][0]).toMatchObject({
      source: 'deezer',
      album_id: 'al1',
      artist: 'Radiohead',
    });
    // each file's picker holds the track the server matched it to
    const pickers = screen.getAllByRole('combobox') as HTMLSelectElement[];
    expect(pickers.map((p) => p.value)).toEqual(['1', '0']);
    // the low one is flagged, the sure one shows its score
    expect(screen.getByText('Check')).toBeTruthy();
    expect(screen.getByText('95%')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Download 2 tracks' }));
    await waitFor(() => expect(api.startEnriched).toHaveBeenCalled());
    expect(vi.mocked(api.startEnriched).mock.calls[0][0]).toMatchObject({
      source: 'deezer',
      album_id: 'al1',
      assignments: [
        { file_key: 'peer::Music\\Glasto\\02 - Lucky.flac', track_index: 1 },
        { file_key: 'peer::Music\\Glasto\\01 - 2 + 2 = 5.flac', track_index: 0 },
      ],
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('a skipped file is left out, and the count follows', async () => {
    vi.mocked(api.matchRelease).mockResolvedValue({
      success: true,
      tracks: [{ index: 0, name: 'Lucky', track_number: 2, disc_number: 1, duration_ms: 0 }],
      assignments: [
        { file_key: 'peer::Music\\Glasto\\02 - Lucky.flac', track_index: 0, confidence: 0.9 },
        { file_key: 'peer::Music\\Glasto\\01 - 2 + 2 = 5.flac', track_index: null, confidence: 0 },
      ],
    });
    render(<EnrichedModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    await settle();
    await screen.findByText('Best match');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByRole('button', { name: 'Download 1 track' })).toBeTruthy();
    expect(screen.getByText('Skipped')).toBeTruthy();
  });

  it('shows the server sentence when the release cannot load', async () => {
    vi.mocked(api.matchRelease).mockResolvedValue({
      success: false,
      error: 'deezer didn’t answer. Try another source.',
    });
    render(<EnrichedModal target={{ kind: 'album', album }} onClose={vi.fn()} />);
    await settle();
    await screen.findByText('Best match');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText(/Try another source/)).toBeTruthy();
  });
});

describe('a single track', () => {
  it('searches tracks and downloads the pick straight away', async () => {
    vi.mocked(api.startEnriched).mockResolvedValue({ success: true });
    render(<EnrichedModal target={{ kind: 'track', track: track() }} onClose={vi.fn()} />);
    await settle();
    expect(await screen.findByText('Which track is this?')).toBeTruthy();
    await screen.findByText('Best match');
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(api.startEnriched).toHaveBeenCalled());
    expect(vi.mocked(api.startEnriched).mock.calls[0][0]).toMatchObject({
      source: 'deezer',
      track_id: 'tr1',
      files: [expect.objectContaining({ title: 'Lucky' })],
    });
  });

  it('asks before a blocklisted artist, and retries with the override on yes', async () => {
    vi.mocked(api.startEnriched)
      .mockResolvedValueOnce({ success: false, blocked: true, blocked_name: 'Radiohead' })
      .mockResolvedValueOnce({ success: true });
    window.showConfirmDialog = vi.fn(async () => true);
    render(<EnrichedModal target={{ kind: 'track', track: track() }} onClose={vi.fn()} />);
    await settle();
    await screen.findByText('Best match');
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(api.startEnriched).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.startEnriched).mock.calls[1][0]).toMatchObject({ ignore_blocklist: true });
  });
});
