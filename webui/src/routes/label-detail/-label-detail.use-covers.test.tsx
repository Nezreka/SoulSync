import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useLabelCovers } from './-label-detail.use-covers';

/** Records every element handed to an IntersectionObserver. */
function stubObserver() {
  const observed: Element[] = [];
  const disconnects: number[] = [];
  class FakeObserver {
    constructor(
      public cb: IntersectionObserverCallback,
      public options?: IntersectionObserverInit,
    ) {
      created.push(this);
    }
    observe(element: Element) {
      observed.push(element);
    }
    unobserve() {}
    disconnect() {
      disconnects.push(1);
    }
  }
  const created: FakeObserver[] = [];
  vi.stubGlobal('IntersectionObserver', FakeObserver as unknown as typeof IntersectionObserver);
  return { observed, created, disconnects };
}

afterEach(() => vi.unstubAllGlobals());

describe('useLabelCovers', () => {
  it('observes a card instead of requesting it immediately', async () => {
    // The regression this guards: the observer used to be created in an EFFECT,
    // and effects run child-first — so every card asked before it existed and
    // fell through to the immediate path. The concurrency cap still held, but
    // visible-first ordering (the whole reason for the observer) was lost.
    const { observed, created } = stubObserver();
    const { result } = renderHook(() => useLabelCovers('mb-1'));

    const element = document.createElement('div');
    result.current.observe('k', '/cover', element);

    expect(created).toHaveLength(1);
    expect(observed).toEqual([element]);
  });

  it('uses the vanilla 150px margin so a cover starts just before you reach it', () => {
    const { created } = stubObserver();
    const { result } = renderHook(() => useLabelCovers('mb-1'));
    result.current.observe('k', '/cover', document.createElement('div'));
    expect(created[0].options?.rootMargin).toBe('150px');
  });

  it('reuses ONE observer for the whole grid', () => {
    const { created } = stubObserver();
    const { result } = renderHook(() => useLabelCovers('mb-1'));
    for (let i = 0; i < 5; i += 1) {
      result.current.observe(`k${i}`, `/c${i}`, document.createElement('div'));
    }
    expect(created).toHaveLength(1);
  });

  it('requests immediately when the browser has no IntersectionObserver', () => {
    // jsdom and older browsers: asking late is better than never.
    vi.stubGlobal('IntersectionObserver', undefined);
    const { result } = renderHook(() => useLabelCovers('mb-1'));
    // No throw, and a usable no-op cleanup.
    const cleanup = result.current.observe('k', '/cover', document.createElement('div'));
    expect(typeof cleanup).toBe('function');
  });

  it('drops resolved art when the label changes', () => {
    stubObserver();
    const { result, rerender } = renderHook(({ id }) => useLabelCovers(id), {
      initialProps: { id: 'mb-1' },
    });
    expect(result.current.resolved).toEqual({});
    rerender({ id: 'mb-2' });
    // A url resolved for the previous label must not paint under a new card
    // that happens to share a key.
    expect(result.current.resolved).toEqual({});
  });

  it('disconnects on unmount', () => {
    const { disconnects } = stubObserver();
    const { result, unmount } = renderHook(() => useLabelCovers('mb-1'));
    result.current.observe('k', '/cover', document.createElement('div'));
    unmount();
    expect(disconnects).toHaveLength(1);
  });
});
