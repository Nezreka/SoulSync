import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { initPlexSignIn, runPlexSignIn } from './plex-signin';

/** the login screen's plex button: shown when on, popup, poll, reload */
function dom() {
  document.body.innerHTML = `
    <div id="login-plex" class="login-plex" hidden>
      <button id="login-plex-button" type="button">Sign in with Plex</button>
    </div>
    <p id="login-error" style="display:none"></p>`;
}

function fakePopup() {
  return { closed: false, close: vi.fn(), location: { href: '' } } as unknown as Window & {
    close: ReturnType<typeof vi.fn>;
  };
}

beforeEach(() => {
  dom();
  vi.useFakeTimers({ toFake: ['setTimeout', 'Date'] });
});
afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
  document.body.innerHTML = '';
});

describe('the plex button', () => {
  it('shows only when the admin turned plex sign-in on', async () => {
    server.use(
      http.get('*/api/auth/plex/available', () =>
        HttpResponse.json({ success: true, enabled: false }),
      ),
    );
    await initPlexSignIn();
    expect(document.getElementById('login-plex')!.hidden).toBe(true);

    dom();
    server.use(
      http.get('*/api/auth/plex/available', () =>
        HttpResponse.json({ success: true, enabled: true }),
      ),
    );
    await initPlexSignIn();
    expect(document.getElementById('login-plex')!.hidden).toBe(false);
  });
});

describe('runPlexSignIn', () => {
  it('opens plex in the popup, waits for approval, then reloads into the app', async () => {
    let checks = 0;
    server.use(
      http.post('*/api/auth/plex/start', () =>
        HttpResponse.json({ success: true, url: 'https://app.plex.tv/auth#?x' }),
      ),
      http.post('*/api/auth/plex/check', () => {
        checks++;
        return HttpResponse.json(
          checks < 2 ? { success: true, pending: true } : { success: true, pending: false },
        );
      }),
    );
    const popup = fakePopup();
    const reload = vi.fn();
    const done = runPlexSignIn(() => popup, reload);
    await vi.advanceTimersByTimeAsync(5000);
    expect(await done).toBe(true);
    expect(popup.location.href).toBe('https://app.plex.tv/auth#?x');
    expect(popup.close).toHaveBeenCalled();
    expect(reload).toHaveBeenCalledOnce();
  });

  it("shows plex's refusal and never reloads", async () => {
    server.use(
      http.post('*/api/auth/plex/start', () =>
        HttpResponse.json({ success: true, url: 'https://app.plex.tv/auth#?x' }),
      ),
      http.post('*/api/auth/plex/check', () =>
        HttpResponse.json(
          { success: false, error: "That Plex account doesn't have access to this server" },
          { status: 403 },
        ),
      ),
    );
    const reload = vi.fn();
    const done = runPlexSignIn(() => fakePopup(), reload);
    await vi.advanceTimersByTimeAsync(3000);
    expect(await done).toBe(false);
    expect(document.getElementById('login-error')!.textContent).toContain("doesn't have access");
    expect(reload).not.toHaveBeenCalled();
  });

  it('closing plex without approving cancels', async () => {
    server.use(
      http.post('*/api/auth/plex/start', () =>
        HttpResponse.json({ success: true, url: 'https://app.plex.tv/auth#?x' }),
      ),
      http.post('*/api/auth/plex/check', () => HttpResponse.json({ success: true, pending: true })),
    );
    const popup = fakePopup();
    const done = runPlexSignIn(() => popup, vi.fn());
    (popup as unknown as { closed: boolean }).closed = true;
    await vi.advanceTimersByTimeAsync(3000);
    expect(await done).toBe(false);
    expect(document.getElementById('login-error')!.textContent).toBe('Plex sign-in was cancelled.');
  });

  it('a failed start closes the popup and says so', async () => {
    server.use(
      http.post('*/api/auth/plex/start', () =>
        HttpResponse.json({ success: false, error: "Couldn't reach Plex." }, { status: 502 }),
      ),
    );
    const popup = fakePopup();
    expect(await runPlexSignIn(() => popup, vi.fn())).toBe(false);
    expect(popup.close).toHaveBeenCalled();
    expect(document.getElementById('login-error')!.textContent).toBe("Couldn't reach Plex.");
  });
});
