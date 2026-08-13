import { describe, expect, it, vi } from 'vitest';

import { COVER_CONCURRENCY, CoverLoader, type CoverProbe } from './-label-detail.covers';

/** A probe you can resolve by hand, so concurrency is observable. */
function deferredProbe() {
  const pending: { url: string; settle: (ok: boolean) => void }[] = [];
  const probe: CoverProbe = (url) =>
    new Promise<boolean>((resolve) => {
      pending.push({ url, settle: resolve });
    });
  return { probe, pending };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('CoverLoader', () => {
  it('runs at most two probes at once', async () => {
    // The whole point: the endpoint is rate-limited, and firing sixty at once
    // makes the VISIBLE covers finish last.
    const { probe, pending } = deferredProbe();
    const loader = new CoverLoader(() => {}, probe);

    for (let i = 0; i < 5; i += 1) loader.request(`k${i}`, `/u${i}`);
    expect(pending).toHaveLength(COVER_CONCURRENCY);

    pending[0].settle(true);
    await flush();
    expect(pending).toHaveLength(3);
  });

  it('reports a resolved cover once and caches it', async () => {
    const onResolved = vi.fn();
    const { probe, pending } = deferredProbe();
    const loader = new CoverLoader(onResolved, probe);

    loader.request('k', '/u');
    pending[0].settle(true);
    await flush();

    expect(onResolved).toHaveBeenCalledExactlyOnceWith('k', '/u');
    expect(loader.urlFor('k')).toBe('/u');

    // A re-render re-requests the same key; it must not probe again.
    loader.request('k', '/u');
    expect(pending).toHaveLength(1);
  });

  it('remembers a MISS so a re-render does not retry it forever', async () => {
    // Without this the queue never drains: every filter/sort re-render puts
    // the same dead lookups back in.
    const { probe, pending } = deferredProbe();
    const loader = new CoverLoader(() => {}, probe);

    loader.request('k', '/u');
    pending[0].settle(false);
    await flush();

    loader.request('k', '/u');
    expect(pending).toHaveLength(1);
    expect(loader.urlFor('k')).toBe('');
  });

  it('does not spend both slots on the same cover', async () => {
    // The observer fires repeatedly for one element as it crosses the margin.
    const { probe, pending } = deferredProbe();
    const loader = new CoverLoader(() => {}, probe);

    loader.request('k', '/u');
    loader.request('k', '/u');
    loader.request('k', '/u');
    expect(pending).toHaveLength(1);
  });

  it('ignores a request with no url — there is nothing to look up', () => {
    const { probe, pending } = deferredProbe();
    const loader = new CoverLoader(() => {}, probe);
    loader.request('k', '');
    expect(pending).toHaveLength(0);
  });

  it('drops queued work on reset so a new label gets the slots', async () => {
    const { probe, pending } = deferredProbe();
    const loader = new CoverLoader(() => {}, probe);

    for (let i = 0; i < 5; i += 1) loader.request(`k${i}`, `/u${i}`);
    loader.reset();
    pending[0].settle(true);
    await flush();

    // Only the two already in flight ever ran; the queued three are gone.
    expect(pending).toHaveLength(COVER_CONCURRENCY);
  });

  it('forgets resolved covers on reset, so a new label re-resolves its own', async () => {
    const { probe, pending } = deferredProbe();
    const loader = new CoverLoader(() => {}, probe);

    loader.request('k', '/u');
    pending[0].settle(true);
    await flush();
    expect(loader.urlFor('k')).toBe('/u');

    loader.reset();
    expect(loader.urlFor('k')).toBe('');
  });

  it('goes quiet after dispose', async () => {
    const onResolved = vi.fn();
    const { probe, pending } = deferredProbe();
    const loader = new CoverLoader(onResolved, probe);

    loader.request('k', '/u');
    loader.dispose();
    pending[0].settle(true);
    await flush();

    // The unmounted page must not be told about a late resolution.
    expect(onResolved).not.toHaveBeenCalled();
    loader.request('k2', '/u2');
    expect(pending).toHaveLength(1);
  });

  it('keeps draining after a failure', async () => {
    const { probe, pending } = deferredProbe();
    const loader = new CoverLoader(() => {}, probe);

    for (let i = 0; i < 4; i += 1) loader.request(`k${i}`, `/u${i}`);
    pending[0].settle(false);
    await flush();
    expect(pending).toHaveLength(3);
  });
});
