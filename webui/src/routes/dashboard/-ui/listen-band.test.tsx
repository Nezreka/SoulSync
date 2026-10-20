import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DiscoverMix } from '../../discover/-discover.mixes';

import { fetchFlow } from '../../discover/-discover.api';
import { playMixNow } from '../../discover/-discover.playable';
import { ListenBand } from './listen-band';

vi.mock('../../discover/-discover.playable', () => ({
  beginPlayIntent: vi.fn(() => ({ isCurrent: () => true, cancel: vi.fn() })),
  playMixNow: vi.fn(() => Promise.resolve({ matched: 10, total: 10 })),
  playTrackNow: vi.fn(() => Promise.resolve({ matched: 1, total: 1 })),
}));

vi.mock('../../discover/-discover.api', () => ({
  fetchFlow: vi.fn(() => Promise.resolve({ tracks: [{ id: 1, title: 'Flow Track' }] })),
}));

const mockMixes: DiscoverMix[] = [
  {
    key: 'on_repeat',
    title: 'On Repeat',
    tracks: [{ album: { images: [{ url: '/repeat.jpg' }] }, name: 'Track 1' }],
  },
  {
    key: 'daily_mix_1',
    title: 'Daily Mix 1',
    tracks: [{ album: { images: [{ url: '/daily1.jpg' }] }, name: 'Track 2' }],
  },
];

vi.mock('../../discover/-discover.use-mixes', () => ({
  useDiscoverMixes: vi.fn(() => ({
    mixes: mockMixes,
    moodMixes: [],
    registry: Object.fromEntries(mockMixes.map((m) => [m.key, m])),
    isLoading: false,
  })),
}));

describe('ListenBand', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    delete window.startLibraryRadio;
    delete window.navigateToPage;
    delete window.showToast;
  });

  it('renders the Library Radio card and triggers startLibraryRadio on click', () => {
    const startRadio = vi.fn();
    window.startLibraryRadio = startRadio;

    render(<ListenBand />);

    expect(screen.getByText('Library Radio')).toBeInTheDocument();
    expect(screen.getByText('On air — your collection')).toBeInTheDocument();

    const hero = screen.getByText('Library Radio').closest('button')!;
    fireEvent.click(hero);

    expect(startRadio).toHaveBeenCalledTimes(1);
  });

  it('renders the root article with data-card="listen"', () => {
    const { container } = render(<ListenBand />);
    const article = container.querySelector('article[data-card="listen"]');
    expect(article).not.toBeNull();
  });

  it('renders Flow and the Discover quick mixes', () => {
    render(<ListenBand />);

    expect(screen.getByText('Flow')).toBeInTheDocument();
    expect(screen.getByText('On Repeat')).toBeInTheDocument();
    expect(screen.getByText('Daily Mix 1')).toBeInTheDocument();
  });

  it('plays Flow when the Flow button is clicked', async () => {
    render(<ListenBand />);

    const flowBtn = screen.getByText('Flow').closest('button')!;
    await act(async () => {
      fireEvent.click(flowBtn);
    });

    expect(fetchFlow).toHaveBeenCalledTimes(1);
    expect(playMixNow).toHaveBeenCalledWith(
      [{ id: 1, title: 'Flow Track' }],
      'Flow',
      expect.anything(),
    );
  });

  it('plays a mix when its play button is clicked', async () => {
    render(<ListenBand />);

    const playRepeatBtn = screen.getByLabelText('Play On Repeat');
    await act(async () => {
      fireEvent.click(playRepeatBtn);
    });

    expect(playMixNow).toHaveBeenCalledWith(mockMixes[0].tracks, 'On Repeat', expect.anything());
  });

  it('opens the playlist preview modal when a mix tile card body is clicked and closes on close', () => {
    render(<ListenBand />);

    const mixTile = screen.getByText('On Repeat');
    fireEvent.click(mixTile);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveTextContent('On Repeat');

    const closeBtn = screen.getByLabelText('Close');
    fireEvent.click(closeBtn);

    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
