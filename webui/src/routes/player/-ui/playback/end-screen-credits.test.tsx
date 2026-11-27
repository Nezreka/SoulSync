/**
 * up next at the credits: an episode with a next one queued counts down
 * from where the credits start (not the last 25 seconds), plays on when it
 * runs out, and a scene after the credits holds it.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { PlayerState } from '../../-engine/engine';
import type { Marker } from '../../-engine/timeline';

const settings = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
const engine = vi.hoisted(() => ({ seek: vi.fn(), finishAtCredits: vi.fn(), next: vi.fn() }));

vi.mock('./use-player', () => ({ getEngine: () => engine }));
vi.mock('./play-title', () => ({ usePlayTitle: () => vi.fn() }));
vi.mock('../../-player.api', () => ({
  playerSettingsQuery: () => ({
    queryKey: ['end-credits-settings', JSON.stringify(settings.value)],
    queryFn: async () => ({ settings: settings.value }),
  }),
  continueQuery: () => ({ queryKey: ['end-credits-continue'], queryFn: async () => [] }),
}));

import { EndScreen } from './end-screen';

const CREDITS: Marker = { kind: 'credits', start: 1300, end: 1400, source: 'detected' };
const ITEMS = [
  { type: 'episode', id: '1', title: 'Pilot' },
  { type: 'episode', id: '2', title: 'Second' },
];

function state(over: Partial<PlayerState> = {}): PlayerState {
  return {
    item: ITEMS[0],
    kind: 'video',
    status: 'playing',
    position: 1305,
    duration: 1400,
    chapters: [],
    live: false,
    autoplayNext: true,
    stillWatching: false,
    tracks: { audio: [], subtitles: [], markers: [CREDITS] },
    queue: { items: ITEMS, index: 0, repeat: 'off' },
    ...over,
  } as unknown as PlayerState;
}

let client: QueryClient;
const ui = (s: PlayerState) => (
  <QueryClientProvider client={client}>
    <EndScreen state={s} />
  </QueryClientProvider>
);

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  engine.finishAtCredits.mockClear();
  engine.seek.mockClear();
  settings.value = {};
});

describe('up next at the credits', () => {
  it('counts down from the credits and plays on', async () => {
    const view = render(ui(state()));
    expect(await screen.findByText('Up next in 10')).toBeTruthy();
    view.rerender(ui(state({ position: 1311 })));
    expect(screen.getByText('Up next in 4')).toBeTruthy();
    view.rerender(ui(state({ position: 1315.2 })));
    expect(engine.finishAtCredits).toHaveBeenCalledTimes(1);
  });

  it('"Watch the credits" puts it away until the end', async () => {
    const view = render(ui(state()));
    fireEvent.click(await screen.findByRole('button', { name: 'Watch the credits' }));
    view.rerender(ui(state({ position: 1330 })));
    expect(screen.queryByText(/Up next/)).toBeNull();
    expect(engine.finishAtCredits).not.toHaveBeenCalled();
  });

  it('the setting off waits for the end as before', async () => {
    settings.value = { next_at_credits: false };
    render(ui(state()));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/Up next/)).toBeNull();
  });

  it('a scene after the credits holds the countdown and offers the scene', async () => {
    const withScene: Marker = { ...CREDITS, end: 1370, post_credits: { start: 1370, end: 1400 } };
    const s = state({ tracks: { audio: [], subtitles: [], markers: [withScene] } } as never);
    const view = render(ui(s));
    expect(await screen.findByText('Up next, after the scene')).toBeTruthy();
    view.rerender(ui({ ...s, position: 1340 } as PlayerState));
    expect(engine.finishAtCredits).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Watch the scene' }));
    expect(engine.seek).toHaveBeenCalledWith(1370);
  });
});
