import { beforeEach, describe, expect, it, vi } from 'vitest';

import { bulkWishlistActionChunked, BULK_WISHLIST_MAX_IDS } from './-wishlist.api';

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

describe('bulkWishlistActionChunked', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends one call when the ids fit the server cap', async () => {
    const seen: string[][] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const body = (await (input as Request).json()) as { track_ids: string[] };
        seen.push(body.track_ids);
        return jsonResponse({
          success: true,
          results: body.track_ids.map((id) => ({ id, ok: true, message: 'Queued' })),
        });
      }),
    );

    const response = await bulkWishlistActionChunked('grab', ['a', 'b']);
    expect(seen).toEqual([['a', 'b']]);
    expect(response.results).toHaveLength(2);
  });

  it('splits oversized selections into capped chunks and merges results', async () => {
    const seen: string[][] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const body = (await (input as Request).json()) as { track_ids: string[] };
        seen.push(body.track_ids);
        return jsonResponse({
          success: true,
          results: body.track_ids.map((id) => ({ id, ok: true, message: 'Queued' })),
        });
      }),
    );

    const ids = Array.from({ length: BULK_WISHLIST_MAX_IDS + 50 }, (_, i) => `t${i}`);
    const response = await bulkWishlistActionChunked('retry', ids);

    expect(seen).toHaveLength(2);
    expect(seen[0]).toHaveLength(BULK_WISHLIST_MAX_IDS);
    expect(seen[1]).toHaveLength(50);
    // Merged back into one per-item list, in order.
    expect(response.results?.map((r) => r.id)).toEqual(ids);
  });

  it('reports partial progress when a later chunk fails', async () => {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        calls += 1;
        const body = (await (input as Request).json()) as { track_ids: string[] };
        if (calls === 2) {
          return new Response(JSON.stringify({ error: 'busy' }), { status: 409 });
        }
        return jsonResponse({
          success: true,
          results: body.track_ids.map((id) => ({ id, ok: true, message: 'ok' })),
        });
      }),
    );

    const ids = Array.from({ length: BULK_WISHLIST_MAX_IDS + 10 }, (_, i) => `t${i}`);
    await expect(bulkWishlistActionChunked('grab', ids)).rejects.toThrow(
      `stopped after ${BULK_WISHLIST_MAX_IDS} of ${ids.length} tracks`,
    );
  });
});
