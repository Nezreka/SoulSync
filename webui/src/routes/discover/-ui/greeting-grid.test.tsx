import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { QuickTile } from '../-discover.greeting';

import { coverOf, GreetingGrid } from './greeting-grid';

const track = (cover: string) => ({ album: { images: [{ url: cover }] }, name: 't' });
const TILES: QuickTile[] = [
  { kind: 'flow' },
  { kind: 'mix', mix: { key: 'on_repeat', title: 'On Repeat', tracks: [track('/r.jpg')] } },
  { kind: 'mix', mix: { key: 'daily_mix_1', title: 'Daily Mix 1', tracks: [] } },
];

function props(over: Partial<Parameters<typeof GreetingGrid>[0]> = {}) {
  return {
    name: 'Boulder Badge Dad',
    hour: 19,
    tiles: TILES,
    onOpenMix: vi.fn(),
    onPlayMix: vi.fn(),
    onPlayFlow: vi.fn(),
    ...over,
  };
}

describe('GreetingGrid', () => {
  it('greets by the hour and the first name only', () => {
    render(<GreetingGrid {...props()} />);
    expect(screen.getByRole('heading').textContent).toBe('Good evening, Boulder');
  });

  it('greets without a name rather than inventing one', () => {
    render(<GreetingGrid {...props({ name: null, hour: 9 })} />);
    expect(screen.getByRole('heading').textContent).toBe('Good morning');
  });

  it('plays flow in one tap, and says it is starting', () => {
    const p = props();
    const { rerender } = render(<GreetingGrid {...p} />);
    fireEvent.click(screen.getByText('Flow'));
    expect(p.onPlayFlow).toHaveBeenCalledTimes(1);
    rerender(<GreetingGrid {...p} flowBusy />);
    expect(screen.getByText('Starting…').closest('button')).toBeDisabled();
  });

  it('opens a mix from its tile and plays it from the play button', () => {
    const p = props();
    render(<GreetingGrid {...p} />);
    fireEvent.click(screen.getByText('On Repeat'));
    expect(p.onOpenMix).toHaveBeenCalledWith('on_repeat');
    fireEvent.click(screen.getByLabelText('Play Daily Mix 1'));
    expect(p.onPlayMix).toHaveBeenCalledWith('daily_mix_1');
    expect(p.onOpenMix).toHaveBeenCalledTimes(1);
  });

  it('shows the busy mix as busy', () => {
    render(<GreetingGrid {...props({ playingKey: 'on_repeat' })} />);
    expect(screen.getByLabelText('Play On Repeat')).toBeDisabled();
  });
});

describe('coverOf', () => {
  it('is the first real cover, or nothing', () => {
    expect(coverOf([track('/a.jpg'), track('/b.jpg')])).toBe('/a.jpg');
    expect(coverOf(['/a', '/b', '/c', '/d'].map(track))).toBe('/a');
    expect(coverOf([])).toBeNull();
    expect(coverOf(undefined)).toBeNull();
  });
});
