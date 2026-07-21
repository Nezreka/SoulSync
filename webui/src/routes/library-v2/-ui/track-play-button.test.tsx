import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { LibraryV2Track } from '../-library-v2.types';

import { TrackPlayButton } from './library-v2-page';

function track(overrides: Partial<LibraryV2Track> = {}): LibraryV2Track {
  return {
    id: 7,
    title: 'Track Title',
    track_number: 1,
    disc_number: 1,
    duration: null,
    bpm: null,
    explicit: null,
    style: null,
    mood: null,
    isrc: null,
    monitored: true,
    quality_profile_id: 1,
    canonical_track_id: null,
    artists: [{ id: 3, name: 'Some Artist', role: 'primary' }],
    file: {
      file_id: 1,
      path: '/music/track.flac',
      size: null,
      bitrate: 1234,
      sample_rate: null,
      bit_depth: null,
      format: null,
      quality_tier: 'unknown',
      verification_status: null,
      import_status: null,
      source: null,
      file_state: null,
      has_replaygain: false,
      has_lyrics: false,
    },
    file_status: 'present',
    metadata_gaps: [],
    meets_profile: null,
    upgrade_candidate: null,
    ...overrides,
  };
}

function renderWithClient(node: React.ReactElement) {
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

describe('library v2 track play button (H1)', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });

  it('reuses the Legacy player via the shell bridge on click', () => {
    renderWithClient(
      <TrackPlayButton
        track={track()}
        albumId={42}
        albumTitle="Some Album"
        artistName="Some Artist"
      />,
    );

    fireEvent.click(screen.getByTitle('Play track'));

    expect(window.SoulSyncWebShellBridge?.playLibraryTrack).toHaveBeenCalledWith(
      {
        id: null,
        lib2_track_id: 7,
        legacy_track_id: null,
        server_track_id: null,
        title: 'Track Title',
        file_path: '/music/track.flac',
        bitrate: 1234,
        artist_id: null,
        album_id: null,
      },
      'Some Album',
      'Some Artist',
    );
  });

  it('is disabled with no file to play', () => {
    renderWithClient(
      <TrackPlayButton
        track={track({ file: null, file_status: 'missing' })}
        albumId={42}
        albumTitle="Some Album"
        artistName="Some Artist"
      />,
    );

    const button = screen.getByTitle('No file available');
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(window.SoulSyncWebShellBridge?.playLibraryTrack).not.toHaveBeenCalled();
  });

  it('is disabled for a missing-placeholder row with no track id', () => {
    renderWithClient(
      <TrackPlayButton
        track={track({ id: null, file: null })}
        albumId={42}
        albumTitle="Some Album"
        artistName="Some Artist"
      />,
    );

    expect(screen.getByTitle('No file available')).toBeDisabled();
  });
});
