import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { StudioFilters, StudioTrack } from '../-sample-studio.types';

import { DEFAULT_FILTERS } from '../-sample-studio.types';
import { LibraryPanel } from './library-panel';

const tracks: StudioTrack[] = [
  {
    id: 1,
    title: 'Hi-Res Song',
    artist_name: 'A',
    duration: 90,
    file_path: '/m/a.flac',
    bitrate: 4000,
    bpm: 128,
  },
  {
    id: 2,
    title: 'MP3 Song',
    artist_name: 'B',
    duration: 200,
    file_path: '/m/b.mp3',
    bitrate: 320,
    bpm: 95,
  },
  {
    id: 3,
    title: 'Long Lossless',
    artist_name: 'C',
    duration: 400,
    file_path: '/m/c.flac',
    bitrate: 900,
    bpm: 150,
  },
];

function renderPanel(over: Partial<Parameters<typeof LibraryPanel>[0]> = {}) {
  const props = {
    tracks,
    isLoading: false,
    query: '',
    onQueryChange: vi.fn(),
    filters: DEFAULT_FILTERS,
    onFiltersChange: vi.fn(),
    selectedId: null,
    onSelect: vi.fn(),
    ...over,
  };
  const utils = render(<LibraryPanel {...props} />);
  return { ...utils, props };
}

describe('LibraryPanel', () => {
  it('shows the track count and quality badges', () => {
    renderPanel();
    expect(screen.getByText('3 tracks')).toBeInTheDocument();
    expect(screen.getByText('Hi-Res Song')).toBeInTheDocument();
    const badges = [...document.querySelectorAll('[data-tier]')].map((b) => b.textContent);
    expect(badges).toContain('Hi-Res 24-bit');
    expect(badges).toContain('320 kbps+');
    expect(badges).toContain('Lossless 16-bit+');
  });

  it('shows shimmer rows while loading', () => {
    const { container } = renderPanel({ isLoading: true });
    expect(container.querySelectorAll('[class*="shimmer"]')).toHaveLength(3);
  });

  it('shows the browse hint when there are no tracks', () => {
    renderPanel({ tracks: [] });
    expect(
      screen.getByText(/Type to search your library — or pick from your recently added tracks/),
    ).toBeInTheDocument();
  });

  it('shows the no-match hint when a query finds nothing', () => {
    renderPanel({ tracks: [], query: 'zzz' });
    expect(screen.getByText(/No tracks match/)).toBeInTheDocument();
  });

  it('reports search input changes', () => {
    const { props } = renderPanel();
    fireEvent.change(screen.getByLabelText('Search library'), { target: { value: 'rock' } });
    expect(props.onQueryChange).toHaveBeenCalledWith('rock');
  });

  it('filters by quality', () => {
    const { props, unmount } = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Hi-Res 24-bit' }));
    expect(props.onFiltersChange).toHaveBeenCalledWith({ ...DEFAULT_FILTERS, quality: 'hires' });

    const hires: StudioFilters = { ...DEFAULT_FILTERS, quality: 'hires' };
    unmount();
    render(<LibraryPanel {...props} filters={hires} onFiltersChange={vi.fn()} />);
    expect(screen.queryByText('MP3 Song')).not.toBeInTheDocument();
    expect(screen.getByText('Hi-Res Song')).toBeInTheDocument();
    expect(screen.getByText('1 tracks')).toBeInTheDocument();
  });

  it('filters by tempo and length', () => {
    const { props, unmount } = renderPanel();
    const tempo: StudioFilters = { ...DEFAULT_FILTERS, tempo: 'slow' };
    const length: StudioFilters = { ...DEFAULT_FILTERS, length: 'long' };
    unmount();
    const { rerender } = render(
      <LibraryPanel {...props} filters={tempo} onFiltersChange={vi.fn()} />,
    );
    expect(screen.queryByText('MP3 Song')).toBeInTheDocument(); // 95 bpm = slow
    expect(screen.queryByText('Hi-Res Song')).not.toBeInTheDocument(); // 128 = fast

    rerender(<LibraryPanel {...props} filters={length} onFiltersChange={vi.fn()} />);
    expect(screen.queryByText('Long Lossless')).toBeInTheDocument(); // 400s = long
    expect(screen.queryByText('Hi-Res Song')).not.toBeInTheDocument(); // 90s = short
  });

  it('selects a track and marks it selected', () => {
    const { props, unmount } = renderPanel();
    fireEvent.click(screen.getByText('MP3 Song'));
    expect(props.onSelect).toHaveBeenCalledTimes(1);
    expect(props.onSelect).toHaveBeenCalledWith(tracks[1]);

    unmount();
    const { container } = render(
      <LibraryPanel {...props} selectedId={2} onFiltersChange={vi.fn()} />,
    );
    const selected = container.querySelector('[data-selected="true"]');
    expect(selected?.textContent).toContain('MP3 Song');
  });

  it('shows the disabled key-filter pill', () => {
    renderPanel();
    const key = screen.getByRole('button', { name: 'Key · soon' });
    expect(key).toBeDisabled();
    expect(key).toHaveAttribute('title', 'Key detection lands in a later phase');
  });
});
