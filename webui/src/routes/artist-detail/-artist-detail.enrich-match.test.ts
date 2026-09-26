import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { EnhancedData } from './-artist-detail.enhanced';

import {
  _resetEnrichmentLock,
  applyManualMatchRequest,
  clearMatchRequest,
  foldUpdatedData,
  MATCH_SERVICE_LABELS,
  matchServiceLabel,
  runEnrichmentRequest,
  searchServiceRequest,
} from './-artist-detail.enrich-match';

/**
 * Enrichment + manual-match request layer. Pins the vanilla's toast sequence,
 * the one-at-a-time lock, and — the bug this port fixes — that clear-match's
 * updated_data comes back through the same channel a successful match uses
 * (the vanilla handed it to renderEnhancedArtistView, defined nowhere).
 */

function stubFetch(body: unknown, status = 200) {
  const spy = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(body), { status }),
  );
  vi.stubGlobal('fetch', spy);
  return spy;
}

const toasts: [string, string][] = [];
beforeEach(() => {
  toasts.length = 0;
  window.showToast = ((msg: string, tone: string) => toasts.push([msg, tone])) as never;
  _resetEnrichmentLock();
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
});

describe('runEnrichmentRequest', () => {
  const PARAMS = {
    entityType: 'album' as const,
    entityId: 7,
    service: 'spotify',
    name: 'SAW 85-92',
    artistName: 'Aphex Twin',
    artistId: 42,
  };

  it('announces, then summarizes per-service successes and failures', async () => {
    stubFetch({
      success: true,
      results: { spotify: { success: true }, deezer: { success: false, error: 'no id' } },
    });
    const outcome = await runEnrichmentRequest(PARAMS);
    expect(toasts).toEqual([
      ['Enriching album from spotify...', 'info'],
      ['Enriched from: spotify', 'success'],
      ['Failed: deezer: no id', 'error'],
    ]);
    expect(outcome.updatedData).toBeNull();
  });

  it('passes updated_data through only when the payload itself succeeded', async () => {
    stubFetch({ success: true, results: {}, updated_data: { success: true, albums: [] } });
    expect((await runEnrichmentRequest(PARAMS)).updatedData).toEqual({ success: true, albums: [] });
    stubFetch({ success: true, results: {}, updated_data: { success: false } });
    expect((await runEnrichmentRequest(PARAMS)).updatedData).toBeNull();
  });

  it('refuses to overlap: the lock rejects a second call while one runs', async () => {
    let release: (r: Response) => void = () => {};
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>((resolve) => (release = resolve))),
    );
    const first = runEnrichmentRequest(PARAMS);
    const second = await runEnrichmentRequest(PARAMS);
    expect(second.updatedData).toBeNull();
    expect(toasts).toContainEqual(['An enrichment is already in progress', 'error']);
    release(new Response(JSON.stringify({ success: true, results: {} })));
    await first;
    // The lock releases in finally — a third call proceeds.
    stubFetch({ success: true, results: {} });
    await runEnrichmentRequest(PARAMS);
    expect(toasts.filter(([m]) => m.startsWith('Enriching'))).toHaveLength(2);
  });

  it('maps 429 to the busy toast and errors to the error toast, never throwing', async () => {
    stubFetch({ error: 'busy' }, 429);
    expect((await runEnrichmentRequest(PARAMS)).updatedData).toBeNull();
    expect(toasts.at(-1)).toEqual(['busy', 'error']);
    stubFetch({ success: false, error: 'boom' });
    expect((await runEnrichmentRequest(PARAMS)).updatedData).toBeNull();
    expect(toasts.at(-1)).toEqual(['Enrichment error: boom', 'error']);
  });
});

describe('manual match requests', () => {
  it('search posts the trimmed query and unwraps results', async () => {
    const spy = stubFetch({ success: true, results: [{ id: 'x' }] });
    const results = await searchServiceRequest('deezer', 'album', '  hi  ');
    expect(JSON.parse(String(spy.mock.calls[0]?.[1]?.body))).toEqual({
      service: 'deezer',
      entity_type: 'album',
      query: 'hi',
    });
    expect(results).toEqual([{ id: 'x' }]);
  });

  it('apply and clear both surface updated_data the same way', async () => {
    stubFetch({ success: true, updated_data: { success: true, marker: 1 } });
    const applied = await applyManualMatchRequest({
      entityType: 'album',
      entityId: 7,
      service: 'spotify',
      serviceId: 'abc',
      artistId: 42,
    });
    expect(applied.updatedData).toMatchObject({ marker: 1 });

    const spy = stubFetch({ success: true, updated_data: { success: true, marker: 2 } });
    const cleared = await clearMatchRequest({
      entityType: 'album',
      entityId: 7,
      service: 'spotify',
      artistId: 42,
    });
    expect(String(spy.mock.calls[0]?.[0])).toBe('/api/library/clear-match');
    expect(cleared.updatedData).toMatchObject({ marker: 2 });
  });

  it('labels cover the 11 matchable services and fall back to the raw id', () => {
    expect(Object.keys(MATCH_SERVICE_LABELS)).toHaveLength(11);
    expect(matchServiceLabel('musicbrainz')).toBe('MusicBrainz');
    expect(matchServiceLabel('mystery')).toBe('mystery');
  });
});

describe('foldUpdatedData', () => {
  it('replaces the loaded payload in place and returns the asked-for album', () => {
    const current = {
      artist: { id: 1, name: 'Old' },
      albums: [{ id: 7, title: 'Old Title' }],
    } as unknown as EnhancedData;
    const updated = {
      artist: { id: 1, name: 'New' },
      albums: [{ id: 7, title: 'New Title' }],
    } as unknown as EnhancedData;
    const fresh = foldUpdatedData(current, updated, 7);
    expect(fresh).toMatchObject({ title: 'New Title' });
    expect(current.artist).toBe(updated.artist);
    expect(current.albums).toBe(updated.albums);
    // No album asked for → payload still folds, nothing returned.
    expect(foldUpdatedData(current, updated)).toBeNull();
  });
});
