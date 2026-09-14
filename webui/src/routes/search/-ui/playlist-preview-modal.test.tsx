import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SearchPlaylist } from '../-search.types';
import { PlaylistPreviewModal } from './playlist-preview-modal';

const mockFetchDeezerLinkPlaylist = vi.fn();
const mockPostMirrorPlaylist = vi.fn();
const mockBuildMirrorPayload = vi.fn();

vi.mock('@/routes/sync/-sync.api', () => ({
  fetchDeezerLinkPlaylist: (...args: unknown[]) => mockFetchDeezerLinkPlaylist(...args),
  postMirrorPlaylist: (...args: unknown[]) => mockPostMirrorPlaylist(...args),
}));

vi.mock('@/routes/sync/-sync.import', () => ({
  buildMirrorPayload: (...args: unknown[]) => mockBuildMirrorPayload(...args),
}));

const samplePlaylist: SearchPlaylist = {
  id: '3155776842',
  name: 'Top Deezer Hits',
  creator: 'Deezer Editor',
  track_count: 2,
  image_url: 'https://e-cdns-images.dzcdn.net/images/playlist/cover.jpg',
  source: 'deezer',
  link: 'https://www.deezer.com/playlist/3155776842',
};

const sampleTracks = [
  {
    id: 1,
    name: 'Hit Track 1',
    artist: 'Artist One',
    album: 'Album One',
    cover: 'https://cover1.jpg',
    durationMs: 180000,
  },
  {
    id: 2,
    name: 'Hit Track 2',
    artist: 'Artist Two',
    album: 'Album Two',
    cover: 'https://cover2.jpg',
    durationMs: 200000,
  },
];

describe('PlaylistPreviewModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchDeezerLinkPlaylist.mockResolvedValue({
      id: '3155776842',
      name: 'Top Deezer Hits',
      creator: 'Deezer Editor',
      tracks: sampleTracks,
    });
    mockBuildMirrorPayload.mockReturnValue({
      source: 'deezer',
      source_id: '3155776842',
      playlist_name: 'Top Deezer Hits',
    });
    mockPostMirrorPlaylist.mockResolvedValue({ success: true });
    window.showToast = vi.fn();
  });

  afterEach(cleanup);

  it('renders loading state initially and then displays tracks', async () => {
    render(
      <PlaylistPreviewModal playlist={samplePlaylist} onClose={vi.fn()} />,
    );

    expect(screen.getByText('Top Deezer Hits')).toBeInTheDocument();
    expect(screen.getByText(/Deezer Editor/)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Hit Track 1')).toBeInTheDocument();
      expect(screen.getByText('Hit Track 2')).toBeInTheDocument();
    });

    expect(mockFetchDeezerLinkPlaylist).toHaveBeenCalledWith('3155776842');
  });

  it('mirrors playlist when "Add to Playlists" button is clicked', async () => {
    render(
      <PlaylistPreviewModal playlist={samplePlaylist} onClose={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByText('Hit Track 1')).toBeInTheDocument();
    });

    const addBtn = screen.getByRole('button', { name: /\+ Add to Playlists/i });
    expect(addBtn).not.toBeDisabled();

    fireEvent.click(addBtn);

    await waitFor(() => {
      expect(mockBuildMirrorPayload).toHaveBeenCalledWith(
        'deezer',
        '3155776842',
        'Top Deezer Hits',
        sampleTracks,
        expect.objectContaining({
          owner: 'Deezer Editor',
          image_url: samplePlaylist.image_url,
        }),
      );
      expect(mockPostMirrorPlaylist).toHaveBeenCalled();
      expect(screen.getByText('Added ✓')).toBeInTheDocument();
      expect(window.showToast).toHaveBeenCalledWith(
        expect.stringContaining('Added "Top Deezer Hits" to Playlists!'),
        'success',
      );
    });
  });

  it('closes when close button is clicked', async () => {
    const onClose = vi.fn();
    render(<PlaylistPreviewModal playlist={samplePlaylist} onClose={onClose} />);

    await waitFor(() => {
      expect(screen.getByText('Hit Track 1')).toBeInTheDocument();
    });

    const closeBtn = screen.getByRole('button', { name: 'Close' });
    fireEvent.click(closeBtn);

    expect(onClose).toHaveBeenCalled();
  });
});
