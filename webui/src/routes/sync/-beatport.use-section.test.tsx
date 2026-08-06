/**
 * The shared section load lifecycle.
 *
 * The cache assertions matter more than they look: these endpoints scrape
 * Beatport, so "does a tab switch re-fetch" is a question about someone else's
 * server, not about render performance.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BEATPORT_SLIDERS } from './-beatport.core';
import {
  hasLoadedBeatportSection,
  resetBeatportSectionCache,
  useBeatportSection,
} from './-beatport.use-section';

afterEach(() => {
  resetBeatportSectionCache();
});

function renderSection(
  configKey: keyof typeof BEATPORT_SLIDERS,
  load: (signal: AbortSignal) => Promise<string[] | null>,
  sectionKey = 'test-section',
) {
  return renderHook(() =>
    useBeatportSection<string>({
      sectionKey,
      config: BEATPORT_SLIDERS[configKey],
      load,
      defaultErrorMessage: 'No releases available',
    }),
  );
}

describe('useBeatportSection', () => {
  it('loads and reports ready', async () => {
    const { result } = renderSection('releases', async () => ['a', 'b']);
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.items).toEqual(['a', 'b']);
    expect(result.current.errorMessage).toBeNull();
  });

  it('treats an EMPTY list as a failure, as every vanilla loader does', async () => {
    // `data.success && data.tracks && data.tracks.length > 0` — an empty
    // successful response takes the failure arm.
    const { result } = renderSection('releases', async () => []);
    await waitFor(() => expect(result.current.status).toBe('failed'));
    expect(result.current.errorMessage).toBe('No releases available');
  });

  it('surfaces the thrown message for an error-block section', async () => {
    const { result } = renderSection('releases', async () => {
      throw new Error('network down');
    });
    await waitFor(() => expect(result.current.errorMessage).toBe('network down'));
  });

  it('sets NO message for charts and DJ, which render nothing on failure', async () => {
    const { result } = renderSection('charts', async () => {
      throw new Error('network down');
    });
    await waitFor(() => expect(result.current.status).toBe('failed'));
    // The vanilla's charts/DJ loaders have no error renderer at all.
    expect(result.current.errorMessage).toBeNull();
  });

  it('sets no message for the hero either — it keeps its static markup', async () => {
    const { result } = renderSection('hero', async () => null);
    await waitFor(() => expect(result.current.status).toBe('failed'));
    expect(result.current.errorMessage).toBeNull();
  });

  it('does not re-fetch on a second mount after success', async () => {
    const load = vi.fn(async () => ['a']);
    const first = renderSection('releases', load);
    await waitFor(() => expect(first.result.current.status).toBe('ready'));
    first.unmount();

    // A tab switch and back. These endpoints scrape Beatport; the vanilla
    // guards against exactly this and so must the port.
    const second = renderSection('releases', load);
    await waitFor(() => expect(second.result.current.status).toBe('ready'));
    expect(load).toHaveBeenCalledTimes(1);
    // …and it comes back WITH ITS ITEMS. Caching only the flag would give a
    // 'ready' section holding nothing — an empty slider. The vanilla avoids
    // this by hiding the rendered DOM rather than removing it.
    expect(second.result.current.items).toEqual(['a']);
  });

  it('hydrates from the cache on the FIRST render, with no empty frame', async () => {
    const load = vi.fn(async () => ['a', 'b']);
    const first = renderSection('releases', load);
    await waitFor(() => expect(first.result.current.status).toBe('ready'));
    first.unmount();

    // Not after an effect — on the very first render, or the slider would
    // flash empty on every tab visit.
    const second = renderSection('releases', load);
    expect(second.result.current.status).toBe('ready');
    expect(second.result.current.items).toEqual(['a', 'b']);
  });

  it('a hero that failed comes back ready but EMPTY, keeping its placeholders', async () => {
    const load = vi.fn(async () => {
      throw new Error('down');
    });
    const first = renderSection('hero', load);
    await waitFor(() => expect(first.result.current.status).toBe('failed'));
    first.unmount();

    const second = renderSection('hero', load);
    // It claimed the slot with an empty list before fetching, which is right:
    // nothing to show, and the static markup stands in.
    expect(second.result.current.items).toEqual([]);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('DOES re-fetch after a failure, for the four that mark on success only', async () => {
    const load = vi.fn(async () => {
      throw new Error('down');
    });
    const first = renderSection('charts', load);
    await waitFor(() => expect(first.result.current.status).toBe('failed'));
    first.unmount();

    const second = renderSection('charts', load);
    await waitFor(() => expect(second.result.current.status).toBe('failed'));
    // Charts and DJ never mark themselves loaded on failure, which makes them
    // the only self-healing arms in the family.
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('does NOT re-fetch after a failure for the hero, which claims the slot first', async () => {
    const load = vi.fn(async () => {
      throw new Error('down');
    });
    const first = renderSection('hero', load);
    await waitFor(() => expect(first.result.current.status).toBe('failed'));
    first.unmount();

    const second = renderSection('hero', load);
    await waitFor(() => expect(second.result.current.status).toBe('ready'));
    // 31-34: marked before the fetch, so the failure is permanent for the
    // session and the section simply keeps its placeholders.
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('keys the cache per section, so one section does not satisfy another', async () => {
    const load = vi.fn(async () => ['a']);
    const a = renderSection('releases', load, 'section-a');
    await waitFor(() => expect(a.result.current.status).toBe('ready'));
    const b = renderSection('releases', load, 'section-b');
    await waitFor(() => expect(b.result.current.status).toBe('ready'));
    expect(load).toHaveBeenCalledTimes(2);
    expect(hasLoadedBeatportSection('section-a')).toBe(true);
    expect(hasLoadedBeatportSection('section-b')).toBe(true);
  });

  it('reload() clears the guard and fetches again', async () => {
    const load = vi.fn(async () => ['a']);
    const { result } = renderSection('releases', load);
    await waitFor(() => expect(result.current.status).toBe('ready'));
    act(() => {
      result.current.reload();
    });
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });

  it('aborts the in-flight request when the section unmounts', async () => {
    let seen: AbortSignal | undefined;
    const { unmount } = renderSection('releases', async (signal) => {
      seen = signal;
      return new Promise(() => []);
    });
    await waitFor(() => expect(seen).toBeDefined());
    expect(seen?.aborted).toBe(false);
    unmount();
    expect(seen?.aborted).toBe(true);
  });

  it('a STALE load settling after a reload cannot clobber the new one', async () => {
    // The reachable abort case, and the one the guards actually exist for:
    // reload() aborts the in-flight controller and starts a fresh load, so the
    // old promise settles late against a live, mounted component.
    const settles: {
      resolve: (v: string[]) => void;
      reject: (e: Error) => void;
    }[] = [];
    const load = vi.fn(
      () =>
        new Promise<string[]>((resolve, reject) => {
          settles.push({ resolve, reject });
        }),
    );
    const { result } = renderSection('releases', load);
    await waitFor(() => expect(settles).toHaveLength(1));

    act(() => {
      result.current.reload();
    });
    await waitFor(() => expect(settles).toHaveLength(2));

    // The first load now rejects with the abort it was given. `await act`
    // rather than a sync act: the rejection lands in a microtask, and a
    // synchronous act asserts before the catch has even run.
    await act(async () => {
      settles[0].reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    });
    expect(result.current.status).toBe('loading');
    expect(result.current.errorMessage).toBeNull();

    await act(async () => {
      settles[1].resolve(['fresh']);
    });
    await waitFor(() => expect(result.current.items).toEqual(['fresh']));
  });

  it('a stale load that SUCCEEDS late cannot paint over the new one', async () => {
    // Deliberately its own test. Trying to resolve the same promise that was
    // already rejected above does nothing — a promise settles once — so the
    // stale-success path needs a promise of its own to be exercised at all.
    const settles: { resolve: (v: string[]) => void }[] = [];
    const load = vi.fn(
      () =>
        new Promise<string[]>((resolve) => {
          settles.push({ resolve });
        }),
    );
    const { result } = renderSection('releases', load);
    await waitFor(() => expect(settles).toHaveLength(1));

    act(() => {
      result.current.reload();
    });
    await waitFor(() => expect(settles).toHaveLength(2));

    await act(async () => {
      settles[0].resolve(['stale']);
    });
    expect(result.current.items).toEqual([]);
    expect(result.current.status).toBe('loading');

    await act(async () => {
      settles[1].resolve(['fresh']);
    });
    await waitFor(() => expect(result.current.items).toEqual(['fresh']));
  });

  it('an aborted load settles nothing and does not mark the section loaded', async () => {
    let reject: (error: Error) => void = () => {};
    const { result, unmount } = renderSection('releases', async () => {
      return new Promise<string[]>((_resolve, r) => {
        reject = r;
      });
    });
    await waitFor(() => expect(result.current.status).toBe('loading'));
    unmount();
    // Leaving mid-fetch is not a failure — and it must not consume the
    // one-load-per-session guard either.
    act(() => {
      reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    });
    expect(hasLoadedBeatportSection('test-section')).toBe(false);
  });
});
