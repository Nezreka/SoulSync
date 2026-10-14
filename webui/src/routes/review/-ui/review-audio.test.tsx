import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ReviewAudio } from './review-audio';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('release preview playback', () => {
  it('resolves Deezer previews at playback time rather than using a stored signed URL', () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const { container } = render(
      <ReviewAudio
        localPath="/music/song.flac"
        localTitle="Song"
        previewSource="deezer"
        previewTrackId="2486074331"
        previewUrl="https://cdnt-preview.dzcdn.net/expired.mp3"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Release preview/ }));
    expect(container.querySelector('audio')?.getAttribute('src')).toBe(
      '/api/repair/release-preview/deezer/2486074331',
    );
  });
});
