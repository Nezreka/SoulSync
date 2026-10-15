/**
 * Regression: the history modal's loader sequences concurrent requests. A
 * newer tab/search/page request supersedes an older in-flight one — the
 * older response is discarded when it lands, so it can never clobber the
 * list with stale data, and the newer request is never silently dropped
 * (the previous boolean in-flight guard dropped it, leaving the UI on the
 * new tab while showing the old tab's data).
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { VdlHistoryPage } from '../-vdl.api';
import type { VdlHistoryItem } from '../-vdl.types';

import { VdlHistoryModal } from './vdl-history';

const fetchVideoHistory = vi.fn();

vi.mock('../-vdl.api', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../-vdl.api')>();
  return { ...orig, fetchVideoHistory: (...args: unknown[]) => fetchVideoHistory(...args) };
});

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function page(items: VdlHistoryItem[]): VdlHistoryPage {
  return {
    items,
    counts: { movie: items.length, show: 0, youtube: 0, total: items.length },
    totalPages: 1,
  };
}

describe('history load concurrency', () => {
  // The modal also schedules a 250ms search-debounce load on open; the
  // default implementation below returns a real page so those stray refires
  // can never crash or clobber the assertions with undefined.
  beforeEach(() => {
    fetchVideoHistory.mockReset();
  });

  it('a stale in-flight response is discarded when a newer tab request wins the race', async () => {
    const allPage = page([{ id: 'a1', kind: 'movie', title: 'All Tab Result' }]);
    const moviesPage = page([{ id: 'm1', kind: 'movie', title: 'Movies Tab Result' }]);
    const first = deferred<VdlHistoryPage>();
    fetchVideoHistory.mockImplementationOnce(() => first.promise);
    // stray debounce refires (and the tab-switch load below) resolve fast
    fetchVideoHistory.mockImplementation(() => Promise.resolve(moviesPage));
    render(<VdlHistoryModal open onClose={() => {}} onCountChange={() => {}} />);

    // the open effect fires load #1 (slow — still in flight)
    await waitFor(() => expect(fetchVideoHistory).toHaveBeenCalledTimes(1));

    // a tab switch while load #1 is in flight fires load #2 — newer
    // requests are never silently dropped
    fireEvent.click(screen.getByRole('tab', { name: /Movies/ }));
    await waitFor(() => expect(fetchVideoHistory).toHaveBeenCalledTimes(2));

    // load #2 resolves first: the list shows the Movies tab data
    expect(await screen.findByText('Movies Tab Result')).not.toBeNull();

    // the slow stale load #1 resolves last — its data must be discarded,
    // never clobbering the list the user is looking at
    await act(async () => {
      first.resolve(allPage);
    });
    expect(screen.queryByText('All Tab Result')).toBeNull();
    expect(screen.getByText('Movies Tab Result')).not.toBeNull();
  });

  it('loads again once the in-flight request settles', async () => {
    const firstPage = page([{ id: 'a1', kind: 'movie', title: 'First' }]);
    const secondPage = page([{ id: 'b2', kind: 'show', title: 'Second' }]);
    fetchVideoHistory.mockImplementation(() => Promise.resolve(firstPage));
    render(<VdlHistoryModal open onClose={() => {}} onCountChange={() => {}} />);
    expect(await screen.findByText('First')).not.toBeNull();

    // stray debounce refires from here on return the already-loaded content
    fetchVideoHistory.mockImplementation(() => Promise.resolve(secondPage));
    fireEvent.click(screen.getByRole('tab', { name: /^TV/ }));
    expect(await screen.findByText('Second')).not.toBeNull();
  });
});
