/**
 * Regression: the history modal's loader has a concurrency guard (parity
 * with the old modal's `if (state.loading) return;`). While one request is
 * in flight, later loads are dropped — so a slow earlier response can never
 * resolve after a newer one and clobber the list with stale data.
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

  it('drops a second load while the first is in flight, so stale responses cannot clobber the list', async () => {
    const allPage = page([{ id: 'a1', kind: 'movie', title: 'All Tab Result' }]);
    fetchVideoHistory.mockImplementation(() => Promise.resolve(allPage));
    const first = deferred<VdlHistoryPage>();
    fetchVideoHistory.mockImplementationOnce(() => first.promise);
    render(<VdlHistoryModal open onClose={() => {}} onCountChange={() => {}} />);

    // the open effect fires load #1
    await waitFor(() => expect(fetchVideoHistory).toHaveBeenCalledTimes(1));

    // a tab switch while load #1 is still in flight must not start load #2
    fireEvent.click(screen.getByRole('tab', { name: /Movies/ }));
    expect(fetchVideoHistory).toHaveBeenCalledTimes(1);

    // the slow first request resolves — the list shows its data, and no
    // second request was ever in flight to be clobbered by it
    await act(async () => {
      first.resolve(allPage);
    });
    expect(await screen.findByText('All Tab Result')).not.toBeNull();
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
