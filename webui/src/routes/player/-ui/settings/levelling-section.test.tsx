/**
 * volume levelling in Settings, You, Music: the choices save with the
 * profile, the help says what the mode does, and the pre-amp is heard as
 * it's dragged and saved once the hand stops.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  settings: {
    loudness_mode: 'smart',
    loudness_target: -18,
    loudness_preamp: 0,
    loudness_prevent_clipping: true,
    video_loudness: false,
  } as Record<string, unknown>,
  puts: [] as Record<string, unknown>[],
}));

vi.mock('@/app/api-client', () => ({
  refusalText: async (_e: unknown, fallback: string) => fallback,
  apiClient: {
    get: () => ({ json: async () => ({ settings: { ...api.settings } }) }),
    put: (_url: string, opts: { json: Record<string, unknown> }) => {
      api.puts.push(opts.json);
      Object.assign(api.settings, opts.json);
      return { json: async () => ({ settings: { ...api.settings } }) };
    },
  },
}));

const engine = vi.hoisted(() => ({ levelling: [] as Record<string, unknown>[] }));
vi.mock('../playback/use-player', () => ({
  getEngine: () => ({
    configureLevelling: (p: Record<string, unknown>) => engine.levelling.push(p),
  }),
  usePlayer: () => ({}),
}));

const { LevellingSection } = await import('./you');

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LevellingSection />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  api.puts = [];
  engine.levelling = [];
  vi.useRealTimers();
});

describe('volume levelling settings', () => {
  it('shows smart by default and saves a new mode with the profile', async () => {
    mount();
    const smart = await screen.findByRole('radio', { name: 'Smart' });
    await waitFor(() => expect(smart).toHaveAttribute('aria-checked', 'true'));
    await waitFor(() => expect(smart).not.toBeDisabled());
    expect(screen.getByText(/song levelling on shuffle/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Album' }));
    await waitFor(() => expect(api.puts).toContainEqual({ loudness_mode: 'album' }));
    await waitFor(() =>
      expect(screen.getByRole('radio', { name: 'Album' })).toHaveAttribute('aria-checked', 'true'),
    );
  });

  it('target, clipping and video save their own keys', async () => {
    mount();
    const loud = await screen.findByRole('radio', { name: 'Loud' });
    await waitFor(() => expect(loud).not.toBeDisabled());
    fireEvent.click(loud);
    await waitFor(() => expect(api.puts).toContainEqual({ loudness_target: -14 }));
    fireEvent.click(screen.getByRole('switch', { name: 'Level video loudness' }));
    await waitFor(() => expect(api.puts).toContainEqual({ video_loudness: true }));
    fireEvent.click(screen.getByRole('switch', { name: 'Prevent clipping' }));
    await waitFor(() => expect(api.puts).toContainEqual({ loudness_prevent_clipping: false }));
  });

  it('the pre-amp is heard while dragged and saved once, after the hand stops', async () => {
    mount();
    const slider = await screen.findByRole('slider', { name: 'Pre-amp' });
    await waitFor(() => expect(slider).not.toBeDisabled());
    vi.useFakeTimers();
    fireEvent.change(slider, { target: { value: '1.5' } });
    fireEvent.change(slider, { target: { value: '3' } });
    expect(engine.levelling).toEqual([{ preamp: 1.5 }, { preamp: 3 }]);
    expect(api.puts).toEqual([]);
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    vi.useRealTimers();
    await waitFor(() => expect(api.puts).toEqual([{ loudness_preamp: 3 }]));
    expect(screen.getByText('+3.0 dB')).toBeInTheDocument();
  });
});
