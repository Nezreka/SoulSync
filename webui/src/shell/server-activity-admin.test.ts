import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

/**
 * #2155: server activity is the admin's. A regular profile saw the floating
 * button and the drawer, and the ambient badge tick polled
 * /api/server-activity from every page for everyone. Now nothing runs until
 * the profile is known, a non-admin never gets the button or a poll, and a
 * switch to an admin starts it (and back again stops it).
 */

type Ctx = { profileId: number; isAdmin: boolean } | null;
let ctx: Ctx = null;
const calls: string[] = [];

function btn(): HTMLElement {
  return document.getElementById('activity-float-btn') as HTMLElement;
}
function changed(next: Ctx) {
  ctx = next;
  window.dispatchEvent(new CustomEvent('ss:webui-profile-context-changed'));
}
const flush = async () => {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
};

beforeAll(async () => {
  vi.useFakeTimers();
  document.body.innerHTML =
    '<button id="activity-float-btn" class="activity-float-btn" style="display:none">' +
    '<span id="activity-float-badge" style="display:none">0</span></button>';
  (window as unknown as { getCurrentProfileContext: () => Ctx }).getCurrentProfileContext = () =>
    ctx;
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      calls.push(String(url));
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, summary: { streams: 2 }, sessions: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }),
  );
  await import('./server-activity');
});

afterAll(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('server activity only runs for an admin (#2155)', () => {
  it('does nothing before the profile is known', async () => {
    await flush();
    vi.advanceTimersByTime(60000);
    await flush();
    expect(calls).toEqual([]);
    expect(btn().style.display).toBe('none');
  });

  it('never polls or shows the button for a non-admin', async () => {
    changed({ profileId: 3, isAdmin: false });
    await flush();
    vi.advanceTimersByTime(60000);
    await flush();
    expect(calls).toEqual([]);
    expect(btn().style.display).toBe('none');
  });

  it('will not open the drawer for a non-admin', async () => {
    window.ServerActivity!.toggle();
    await flush();
    expect(document.querySelector('.sact-drawer')).toBeNull();
    expect(calls).toEqual([]);
  });

  it('starts for an admin', async () => {
    changed({ profileId: 1, isAdmin: true });
    await flush();
    await vi.waitFor(() => expect(btn().style.display).toBe(''));
    expect(calls).toContain('/api/server-activity');
    const before = calls.length;
    vi.advanceTimersByTime(20000);
    await flush();
    expect(calls.length).toBeGreaterThan(before);
  });

  it('stops and hides again when a non-admin takes over', async () => {
    changed({ profileId: 4, isAdmin: false });
    await flush();
    expect(btn().style.display).toBe('none');
    const before = calls.length;
    vi.advanceTimersByTime(60000);
    await flush();
    expect(calls.length).toBe(before);
  });
});
