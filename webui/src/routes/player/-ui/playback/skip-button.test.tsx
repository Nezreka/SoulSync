/**
 * the skip button: a marker from the server shows it, a click jumps past,
 * the profile's "automatically" counts down on the button and then skips
 * with a "Watch it" undo, "never" shows nothing, credits with a scene after
 * them say so and land on the scene.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PlayerState } from '../../-engine/engine';
import type { Marker } from '../../-engine/timeline';

const settings = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
const engine = vi.hoisted(() => ({ seek: vi.fn(), finishAtCredits: vi.fn() }));

vi.mock('./use-player', () => ({ getEngine: () => engine }));
vi.mock('../../-player.api', () => ({
  playerSettingsQuery: () => ({
    queryKey: ['skip-settings-test', JSON.stringify(settings.value)],
    queryFn: async () => ({ settings: settings.value }),
  }),
}));

import { resetSitting } from '../../-engine/skip';
import { SkipButton } from './skip-button';

const MARKERS: Marker[] = [
  { kind: 'intro', start: 61, end: 151, source: 'detected', label: 'Skip intro' },
  {
    kind: 'credits',
    start: 1300,
    end: 1370,
    source: 'detected',
    label: 'Skip credits',
    post_credits: { start: 1370, end: 1400 },
  },
];

function state(over: Partial<PlayerState> = {}): PlayerState {
  return {
    item: { type: 'episode', id: '9', title: 'Pilot' },
    kind: 'video',
    status: 'playing',
    position: 70,
    duration: 1400,
    chapters: [],
    live: false,
    tracks: { audio: [], subtitles: [], markers: MARKERS },
    queue: { items: [{ type: 'episode', id: '9', title: 'Pilot' }], index: 0 },
    ...over,
  } as unknown as PlayerState;
}

let client: QueryClient;
function mount(s: PlayerState) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SkipButton state={s} />
    </QueryClientProvider>,
  );
}
function rerender(view: ReturnType<typeof render>, s: PlayerState) {
  view.rerender(
    <QueryClientProvider client={client}>
      <SkipButton state={s} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  engine.seek.mockClear();
  resetSitting();
  settings.value = {};
});
afterEach(() => vi.useRealTimers());

describe('the skip button', () => {
  it('shows while the intro plays, and a click lands after it', async () => {
    mount(state());
    const btn = await screen.findByRole('button', { name: /skip intro/i });
    fireEvent.click(btn);
    expect(engine.seek).toHaveBeenCalledWith(151);
  });

  it('S skips from the keyboard', async () => {
    mount(state());
    await screen.findByRole('button', { name: /skip intro/i });
    fireEvent.keyDown(window, { key: 's' });
    expect(engine.seek).toHaveBeenCalledWith(151);
  });

  it('nothing outside a marker, nothing when set to never', async () => {
    const view = mount(state({ position: 500 }));
    await act(async () => {});
    expect(screen.queryByRole('button', { name: /skip/i })).toBeNull();
    view.unmount();
    settings.value = { skip_intro: 'never' };
    mount(state());
    await act(async () => {});
    expect(screen.queryByRole('button', { name: /skip intro/i })).toBeNull();
  });

  it('automatically: counts down on the button, skips, and offers to watch it', async () => {
    settings.value = { skip_intro: 'auto' };
    // an earlier episode started the sitting: this one's intro may go by itself
    const first = mount(state({ item: { type: 'episode', id: '8', title: 'Before' } } as never));
    first.unmount();
    const view = mount(state({ position: 62 }));
    expect(await screen.findByLabelText('skipping in 5')).toBeTruthy();
    const btn = screen.getByRole('button', { name: /skip intro/i });
    expect(btn.getAttribute('data-auto')).toBe('true');
    rerender(view, state({ position: 64.5 }));
    expect(screen.getByLabelText('skipping in 3')).toBeTruthy();
    rerender(view, state({ position: 67.1 }));
    expect(engine.seek).toHaveBeenCalledWith(151);
    const undo = await screen.findByRole('button', { name: 'Watch it' });
    fireEvent.click(undo);
    expect(engine.seek).toHaveBeenLastCalledWith(61);
  });

  it("the sitting's first episode keeps its intro: a plain button", async () => {
    settings.value = { skip_intro: 'auto' };
    mount(state({ position: 62 }));
    const btn = await screen.findByRole('button', { name: /skip intro/i });
    await act(async () => {});
    expect(btn.getAttribute('data-auto')).toBeNull();
    expect(screen.queryByLabelText(/skipping in/)).toBeNull();
  });

  it('credits with a scene after them say so, and skipping lands on the scene', async () => {
    mount(
      state({
        item: { type: 'movie', id: '3', title: 'Heat' },
        position: 1310,
        queue: { items: [{ type: 'movie', id: '3', title: 'Heat' }], index: 0 },
      } as never),
    );
    expect(await screen.findByText(/scene after the credits/i)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /skip to the scene/i }));
    expect(engine.seek).toHaveBeenCalledWith(1370);
  });

  it('without markers, a chapter named Intro still gets its button', async () => {
    mount(
      state({
        tracks: { audio: [], subtitles: [] },
        chapters: [
          { start: 0, title: 'Intro' },
          { start: 90, title: 'Part One' },
        ],
        position: 10,
      } as never),
    );
    fireEvent.click(await screen.findByRole('button', { name: /skip intro/i }));
    expect(engine.seek).toHaveBeenCalledWith(90);
  });
});
