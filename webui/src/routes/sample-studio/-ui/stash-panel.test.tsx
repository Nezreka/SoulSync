import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { StashEntry } from '../-sample-studio.types';

import { StashPanel } from './stash-panel';

const entry: StashEntry = {
  id: 11,
  name: 'killer break',
  tags: ['drums', 'loop'],
  track_id: 7,
  track_title: 'Midnight Groove',
  artist_name: 'Test Artist',
  start_s: 4,
  end_s: 6.5,
  pitch_st: 2,
  target_bpm: 128,
  format: 'wav16',
  file_path: '/x/chop.wav',
  folder: '/samples',
  created_at: 1,
};

function renderPanel(over: Partial<Parameters<typeof StashPanel>[0]> = {}) {
  const props = {
    entries: undefined,
    isLoading: false,
    error: null,
    onPlay: vi.fn(),
    onDelete: vi.fn(),
    onRestore: vi.fn(),
    ...over,
  };
  render(<StashPanel {...props} />);
  return props;
}

describe('StashPanel', () => {
  it('shows the loading state', () => {
    renderPanel({ isLoading: true });
    expect(screen.getByText('Loading stash…')).toBeInTheDocument();
  });

  it('shows the error state', () => {
    renderPanel({ error: new Error('disk exploded') });
    expect(screen.getByText(/Couldn't load the stash: disk exploded/)).toBeInTheDocument();
  });

  it('shows the empty state', () => {
    renderPanel({ entries: [] });
    expect(screen.getByText('Nothing stashed yet')).toBeInTheDocument();
  });

  it('renders entries with meta, fx and tags, and counts them', () => {
    renderPanel({ entries: [entry] });
    expect(screen.getByText('Sample Stash')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('killer break')).toBeInTheDocument();
    expect(screen.getByText(/Midnight Groove · Test Artist/)).toBeInTheDocument();
    expect(screen.getByText(/WAV 16-bit · \+2 st · → 128 BPM/)).toBeInTheDocument();
    expect(screen.getByText('drums')).toBeInTheDocument();
    expect(screen.getByText('loop')).toBeInTheDocument();
  });

  it('plays, re-opens and deletes entries', () => {
    const props = renderPanel({ entries: [entry] });
    fireEvent.click(screen.getByRole('button', { name: 'Play killer break' }));
    expect(props.onPlay).toHaveBeenCalledTimes(1);
    expect(props.onPlay).toHaveBeenCalledWith(entry);

    fireEvent.click(screen.getByRole('button', { name: /Re-open/ }));
    expect(props.onRestore).toHaveBeenCalledTimes(1);
    expect(props.onRestore).toHaveBeenCalledWith(entry);

    fireEvent.click(screen.getByTitle('Delete killer break'));
    expect(props.onDelete).toHaveBeenCalledTimes(1);
    expect(props.onDelete).toHaveBeenCalledWith(11);
  });

  it('shows the persisted FX recipe on the entry', () => {
    renderPanel({
      entries: [
        {
          ...entry,
          stem: 'drums',
          normalize: 'peak',
          reverse: true,
          fade_ms: 10,
          space: 0.5,
          delay: { time: '1/8', feedback: 0.35, mix: 0.2 },
        },
      ],
    });
    expect(
      screen.getByText(
        /Drums stem · peak normalize · reversed · 10 ms fade · 0\.5 s space · 1\/8 delay/,
      ),
    ).toBeInTheDocument();
  });

  it('links the ZIP export', () => {
    renderPanel({ entries: [entry] });
    const link = screen.getByRole('link', { name: /Export ZIP/ });
    expect(link).toHaveAttribute('href', '/api/sample/stash/export');
  });

  it('hides the export while the stash is empty (the server refuses an empty zip)', () => {
    renderPanel({ entries: [] });
    expect(screen.queryByRole('link', { name: /Export ZIP/ })).not.toBeInTheDocument();
  });
});
