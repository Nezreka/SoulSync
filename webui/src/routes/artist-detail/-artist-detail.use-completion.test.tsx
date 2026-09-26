import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { emptyStreamCounts } from './-artist-detail.completion';
import { bucketCounts, useCompletionStream } from './-artist-detail.use-completion';

/** A stream that emits the given SSE text, then closes. */
function stubStream(frames: string[], opts: { ok?: boolean } = {}) {
  const aborted = { value: false };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: unknown, init?: RequestInit) => {
      init?.signal?.addEventListener('abort', () => {
        aborted.value = true;
      });
      const encoder = new TextEncoder();
      let i = 0;
      const body = new ReadableStream({
        pull(controller) {
          if (i < frames.length) controller.enqueue(encoder.encode(frames[i++]));
          else controller.close();
        },
      });
      // A non-ok response still gets the FULL body: an empty error body reads
      // as "done" immediately, so dropping the response.ok guard would look
      // identical and the test would prove nothing.
      return new Response(body, { status: opts.ok === false ? 500 : 200 });
    }),
  );
  return aborted;
}

const DISC = {
  albums: [
    { id: 1, title: 'A', owned: null },
    { id: 2, title: 'B', owned: null },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useCompletionStream', () => {
  it('merges each completion event into the discography', async () => {
    stubStream([
      'data: {"type":"completion","id":1,"category":"albums","status":"ok","owned_tracks":5,"expected_tracks":5}\n',
      'data: {"type":"completion","id":2,"category":"albums","status":"missing"}\n',
    ]);
    const { result } = renderHook(() => useCompletionStream('Aphex Twin', DISC, true));

    await waitFor(() => expect(result.current.discography.albums?.[1].owned).toBe(false));
    expect(result.current.discography.albums?.[0].owned).toBe(true);
  });

  it('tallies owned and missing per bucket', async () => {
    stubStream([
      'data: {"type":"completion","id":1,"category":"albums","status":"ok"}\n',
      'data: {"type":"completion","id":2,"category":"albums","status":"missing"}\n',
    ]);
    const { result } = renderHook(() => useCompletionStream('X', DISC, true));

    await waitFor(() => expect(result.current.counts?.total.albums).toBe(2));
    expect(bucketCounts(result.current.counts, 'albums')).toEqual({ owned: 1, missing: 1 });
  });

  it('does not open a stream when disabled', async () => {
    stubStream([]);
    renderHook(() => useCompletionStream('X', DISC, false));
    await new Promise((r) => setTimeout(r, 20));
    expect(fetch).not.toHaveBeenCalled();
  });

  it('does not open a stream without an artist name', async () => {
    stubStream([]);
    renderHook(() => useCompletionStream(undefined, DISC, true));
    await new Promise((r) => setTimeout(r, 20));
    expect(fetch).not.toHaveBeenCalled();
  });

  it('ABORTS on unmount so a stale stream cannot write into the next artist', async () => {
    // The vanilla kept one AbortController and aborted it on navigation;
    // without this two streams race and the slower one wins.
    const aborted = stubStream([
      'data: {"type":"completion","id":1,"category":"albums","status":"ok"}\n',
    ]);
    const { result, unmount } = renderHook(() => useCompletionStream('X', DISC, true));
    await waitFor(() => expect(result.current.discography.albums?.[0].owned).toBe(true));
    unmount();
    await waitFor(() => expect(aborted.value).toBe(true));
  });

  it('ignores a non-ok response even when it carries events', async () => {
    stubStream(['data: {"type":"completion","id":1,"category":"albums","status":"ok"}\n'], {
      ok: false,
    });
    const { result } = renderHook(() => useCompletionStream('X', DISC, true));
    // The stream's state updates land on a timer, so the wait itself has to
    // be inside act() — otherwise React warns they were never wrapped.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(result.current.discography.albums?.[0].owned).toBeNull();
    expect(result.current.streaming).toBe(false);
  });

  it('ignores a frame split across two chunks only once it completes', async () => {
    stubStream(['data: {"type":"completion","id":1,"category":"albums"', ',"status":"ok"}\n']);
    const { result } = renderHook(() => useCompletionStream('X', DISC, true));
    await waitFor(() => expect(result.current.discography.albums?.[0].owned).toBe(true));
  });

  it('ignores non-completion events even when they LOOK mergeable', async () => {
    // A terminal frame with no id merges to a no-op anyway, so it cannot
    // detect a missing type check. This one carries an id and a status.
    stubStream(['data: {"type":"complete","id":1,"category":"albums","status":"ok"}\n']);
    const { result } = renderHook(() => useCompletionStream('X', DISC, true));
    // The stream's state updates land on a timer, so the wait itself has to
    // be inside act() — otherwise React warns they were never wrapped.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(result.current.discography.albums?.[0].owned).toBeNull();
    expect(result.current.counts?.total.albums ?? 0).toBe(0);
  });
});

describe('the terminal complete frame', () => {
  it('marks the stream completed', async () => {
    stubStream([
      'data: {"type":"completion","id":1,"category":"albums","status":"ok"}\n',
      'data: {"type":"complete","processed_count":1}\n',
    ]);
    const { result } = renderHook(() => useCompletionStream('X', DISC, true));
    await waitFor(() => expect(result.current.completed).toBe(true));
  });

  it('leaves a truncated stream NOT completed', async () => {
    // The bars stay on the running tallies rather than recomputing from a
    // discography that was never fully checked.
    stubStream(['data: {"type":"completion","id":1,"category":"albums","status":"ok"}\n']);
    const { result } = renderHook(() => useCompletionStream('X', DISC, true));
    await waitFor(() => expect(result.current.streaming).toBe(false));
    expect(result.current.completed).toBe(false);
  });

  it('resets on a new discography without one stale committed frame', async () => {
    stubStream([
      'data: {"type":"completion","id":1,"category":"albums","status":"ok"}\n',
      'data: {"type":"complete"}\n',
    ]);
    const { result, rerender } = renderHook(({ disc }) => useCompletionStream('X', disc, true), {
      initialProps: { disc: DISC },
    });
    await waitFor(() => expect(result.current.completed).toBe(true));

    const next = { albums: [{ id: 9, title: 'Other', owned: null }] };
    rerender({ disc: next });
    // Synchronous: an effect-based reset would leave the previous artist's
    // merged discography on screen for one frame.
    expect(result.current.discography).toBe(next);
    expect(result.current.counts).toBeNull();
    expect(result.current.completed).toBe(false);
    // Let the remaining in-flight request settle inside act(), so its state
    // update does not land after the test returns (React's act warning).
    await act(async () => {});
  });
});

describe('bucketCounts', () => {
  it('derives missing rather than reading a third counter', () => {
    const counts = emptyStreamCounts();
    counts.total.eps = 5;
    counts.owned.eps = 2;
    expect(bucketCounts(counts, 'eps')).toEqual({ owned: 2, missing: 3 });
  });

  it('is null before the stream starts', () => {
    expect(bucketCounts(null, 'albums')).toBeNull();
  });
});
