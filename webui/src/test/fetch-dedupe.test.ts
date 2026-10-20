import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const SOURCE = readFileSync(resolve(process.cwd(), 'static/fetch-dedupe.js'), 'utf8');
const ORIGIN = 'http://soulsync.test';

function install(raw: typeof fetch): typeof fetch {
  const window = { fetch: raw, location: { origin: ORIGIN, href: `${ORIGIN}/dashboard` } };
  runInNewContext(SOURCE, { window, URL, Date });
  return window.fetch;
}

describe('API GET dedupe', () => {
  it('shares a read but gives each consumer an independently readable body', async () => {
    const raw = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ version: 1 }));
    const fetch = install(raw);
    const responses = await Promise.all([fetch('/api/automations'), fetch('/api/automations')]);
    expect(await Promise.all(responses.map((r) => r.json()))).toEqual([
      { version: 1 },
      { version: 1 },
    ]);
    expect(raw).toHaveBeenCalledOnce();
  });

  it.each(['POST', 'PUT', 'PATCH', 'DELETE'])(
    'invalidates reads after a successful %s',
    async (method) => {
      let version = 0;
      const raw = vi.fn<typeof fetch>(async (_input, init) => {
        if (init?.method === method) version++;
        return Response.json({ version });
      });
      const fetch = install(raw);
      await fetch('/api/automations');
      await fetch('/api/mirrored-playlists');
      await fetch('/api/automations/42', { method });
      expect(await (await fetch('/api/automations')).json()).toEqual({ version: 1 });
      expect(await (await fetch('/api/mirrored-playlists')).json()).toEqual({ version: 1 });
      expect(raw).toHaveBeenCalledTimes(5);
    },
  );

  it.each([
    new Request(`${ORIGIN}/api/automations/42`, { method: 'PATCH' }),
    new URL(`${ORIGIN}/api/automations/42`),
  ])('recognizes standard Request and URL mutation inputs', async (input) => {
    const raw = vi.fn<typeof fetch>().mockImplementation(async () => Response.json({}));
    const fetch = install(raw);
    await fetch('/api/automations');
    await fetch(input, input instanceof Request ? undefined : { method: 'PATCH' });
    await fetch('/api/automations');
    expect(raw).toHaveBeenCalledTimes(3);
  });

  it.each([403, 500])('keeps cached reads when a write fails with HTTP %s', async (status) => {
    const raw = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ version: 1 }))
      .mockResolvedValueOnce(Response.json({ error: 'failed' }, { status }));
    const fetch = install(raw);
    await fetch('/api/automations');
    await fetch('/api/automations/42', { method: 'DELETE' });
    expect(await (await fetch('/api/automations')).json()).toEqual({ version: 1 });
    expect(raw).toHaveBeenCalledTimes(2);
  });

  it('does not invalidate for unrelated writes or turn failed writes into successes', async () => {
    const raw = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ version: 1 }))
      .mockResolvedValueOnce(Response.json({}))
      .mockResolvedValueOnce(Response.json({}))
      .mockRejectedValueOnce(new Error('offline'));
    const fetch = install(raw);
    await fetch('/api/automations');
    await fetch('https://elsewhere.test/api/automations', { method: 'POST' });
    await fetch('/not-an-api', { method: 'POST' });
    await expect(fetch('/api/automations', { method: 'POST' })).rejects.toThrow('offline');
    expect(await (await fetch('/api/automations')).json()).toEqual({ version: 1 });
    expect(raw).toHaveBeenCalledTimes(4);
  });

  it.each([false, true])(
    'an old failing read cannot evict a post-write read: network failure %s',
    async (networkFailure) => {
      let rejectOld!: (error: Error) => void;
      let resolveOld!: (response: Response) => void;
      const old = new Promise<Response>((resolve, reject) => {
        resolveOld = resolve;
        rejectOld = reject;
      });
      const raw = vi
        .fn<typeof fetch>()
        .mockReturnValueOnce(old)
        .mockResolvedValueOnce(Response.json({ success: true }))
        .mockResolvedValueOnce(Response.json({ version: 2 }));
      const fetch = install(raw);
      const pending = fetch('/api/automations');
      await fetch('/api/automations', { method: 'POST' });
      await fetch('/api/automations');
      if (networkFailure) {
        rejectOld(new Error('offline'));
        await expect(pending).rejects.toThrow('offline');
      } else {
        resolveOld(new Response('', { status: 500 }));
        await pending;
      }
      expect(await (await fetch('/api/automations')).json()).toEqual({ version: 2 });
      expect(raw).toHaveBeenCalledTimes(3);
    },
  );
});
