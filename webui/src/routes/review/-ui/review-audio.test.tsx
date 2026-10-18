import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { ReviewAudio } from './review-audio';

afterEach(() => {
  cleanup();
  delete window.playTrackList;
  vi.restoreAllMocks();
});

it('plays the exact local file through the shared player without another audio element', async () => {
  window.playTrackList = vi.fn().mockResolvedValue({ status: 'played' });
  const { container } = render(
    <ReviewAudio
      localPath="/music/song.flac"
      localTitle="Song"
      artist="Artist"
      localDurationMs={200000}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /Play local file/ }));
  await waitFor(() =>
    expect(window.playTrackList).toHaveBeenCalledWith(
      [{ file_path: '/music/song.flac', title: 'Song', artist: 'Artist', duration: 200 }],
      'Review Queue',
    ),
  );
  expect(container.querySelector('audio')).toBeNull();
});

it('offers searches but no local playback for missing slots', () => {
  render(<ReviewAudio localPath="" localTitle="" comparisonTitle="Missing song" />);
  expect(screen.queryByRole('button', { name: /Play local/ })).toBeNull();
  expect(screen.getByRole('link', { name: /Search Spotify/ })).toBeTruthy();
});

it('opens a freshly resolved Deezer preview rather than an expired signed URL', () => {
  render(
    <ReviewAudio
      localPath="/music/song.flac"
      localTitle="Song"
      previewSource="deezer"
      previewTrackId="2486074331"
      previewUrl="https://cdnt-preview.dzcdn.net/expired.mp3"
    />,
  );
  expect(screen.getByRole('link', { name: /Open release preview/ }).getAttribute('href')).toBe(
    '/api/repair/release-preview/deezer/2486074331',
  );
});
