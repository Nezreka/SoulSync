import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useAdventurousness } from './-discover.use-recommended';
import { AdventurousnessDial } from './-ui/adventurousness-dial';

function Dial() {
  const dial = useAdventurousness(0.3);
  return <AdventurousnessDial value={dial.value} onChange={dial.change} onCommit={dial.commit} />;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('dial with its real persistence controller', () => {
  it('one continuous gesture sends one final save and only then refreshes shelves', async () => {
    vi.useFakeTimers();
    // Typed args: mock.calls[0][1] is `unknown` otherwise, and reading .body
    // off it is a type error rather than a check of what was sent.
    const fetch = vi.fn(async (_url: string, _init: { body: string }) => ({
      ok: true,
      json: async () => ({ success: true }),
    }));
    vi.stubGlobal('fetch', fetch);
    const client = new QueryClient();
    const refetch = vi.spyOn(client, 'refetchQueries');
    render(
      <QueryClientProvider client={client}>
        <Dial />
      </QueryClientProvider>,
    );
    const slider = screen.getByRole('slider');
    for (let value = 31; value <= 40; value++) {
      fireEvent.change(slider, { target: { value: value / 100 } });
      await act(() => vi.advanceTimersByTimeAsync(100));
    }
    expect(fetch).not.toHaveBeenCalled();
    expect(refetch).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(320));
    expect(fetch).toHaveBeenCalledOnce();
    expect(JSON.parse(fetch.mock.calls[0]![1].body)).toEqual({ value: 0.4 });
    expect(refetch).toHaveBeenCalledTimes(2);
  });

  it('a rejected save restores the last acknowledged preference', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false })),
    );
    window.showToast = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Dial />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByRole('slider'), { target: { value: '0.8' } });
    await act(() => vi.advanceTimersByTimeAsync(400));
    expect(screen.getByRole('slider')).toHaveValue('0.3');
    expect(window.showToast).toHaveBeenCalledWith(
      expect.stringContaining('previous setting'),
      'error',
    );
  });
});
