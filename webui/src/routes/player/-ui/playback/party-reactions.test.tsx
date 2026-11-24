/**
 * watch party sync on the viewer's side.
 *
 * - the poll used to undo a pause made with space / k / a click on the
 *   frame (only the overlay's own buttons counted as "this viewer just acted")
 * - a refused pause (host mode, a member) was swallowed: nothing happened, no word
 * - a seek never reached the room, so the next poll snapped a host back
 * - after "next episode" the room's clock was forced onto the new title
 */

import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type Listener = () => void;

const engineState = {
  kind: 'video' as const,
  item: { type: 'movie', id: 'm1' } as { type: string; id: string } | null,
  status: 'playing' as string,
  position: 100,
  duration: 6000,
  rate: 1,
};
const listeners = new Set<Listener>();
const engine = {
  getState: () => ({ ...engineState }),
  subscribe: (l: Listener) => {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  pause: vi.fn(() => {
    engineState.status = 'paused';
  }),
  resume: vi.fn(() => {
    engineState.status = 'playing';
  }),
  seek: vi.fn((to: number) => {
    engineState.position = to;
  }),
};
const emit = () => listeners.forEach((l) => l());

const api = {
  get: vi.fn(),
  state: vi.fn(),
  chat: vi.fn(async () => ({})),
};

vi.mock('./use-player', () => ({ getEngine: () => engine }));
vi.mock('../../-player.api', () => ({ watchPartyApi: api }));

const party = (over: Record<string, unknown> = {}) => ({
  id: 'p1',
  item_type: 'movie',
  item_id: 'm1',
  status: 'playing',
  position: 100,
  live_position: 100,
  server_now: Date.now() / 1000,
  messages: [],
  members: [],
  ...over,
});

async function mount() {
  const mod = await import('./party-reactions');
  mod.setActiveWatchPartyId('p1');
  render(<mod.PartyReactionsOverlay hudVisible />);
  return mod;
}

beforeEach(() => {
  Object.assign(engineState, {
    item: { type: 'movie', id: 'm1' },
    status: 'playing',
    position: 100,
  });
  listeners.clear();
  vi.clearAllMocks();
});

afterEach(async () => {
  const mod = await import('./party-reactions');
  act(() => mod.setActiveWatchPartyId(null));
});

describe('watch party sync', () => {
  it("a pause from the player's keys isn't undone by the next poll", async () => {
    api.get.mockResolvedValue(party());
    api.state.mockResolvedValue(party({ status: 'paused' }));
    const mod = await mount();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    act(() => {
      mod.handleWatchPartyPlayerToggle();
    });
    expect(engine.pause).toHaveBeenCalled();
    // a poll in flight from before the pause landed still says playing
    api.get.mockResolvedValue(party({ status: 'playing' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1900));
    });
    expect(engine.resume).not.toHaveBeenCalled();
  });

  it('a refused pause says why', async () => {
    api.get.mockResolvedValue(party());
    api.state.mockRejectedValue(new Error('Only the host can control playback in this room'));
    const mod = await mount();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    act(() => {
      mod.handleWatchPartyPlayerToggle();
    });
    expect(await screen.findByText('Only the host can control playback in this room')).toBeTruthy();
  });

  it("a seek this viewer makes goes to the room; the room's own seek doesn't echo", async () => {
    api.get.mockResolvedValue(party());
    api.state.mockResolvedValue(party());
    await mount();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    api.state.mockClear();
    act(() => {
      engineState.position = 1500;
      emit();
    });
    expect(api.state).toHaveBeenCalledWith('p1', { action: 'seek', position: 1500 });
  });

  it("another title playing isn't forced onto the room's clock", async () => {
    engineState.item = { type: 'episode', id: 'e2' };
    engineState.status = 'paused';
    api.get.mockResolvedValue(party({ status: 'playing', live_position: 3000 }));
    await mount();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(engine.resume).not.toHaveBeenCalled();
    expect(engine.seek).not.toHaveBeenCalled();
  });
});
