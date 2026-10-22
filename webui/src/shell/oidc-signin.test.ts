import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
  OIDC_START_URL,
  initOidcAdminStatus,
  initOidcSignIn,
  renderOidcAdmin,
  resumeOidcConnect,
} from './oidc-signin';

vi.mock('./my-accounts', () => ({
  openMyAccountsModal: vi.fn(),
  OIDC_CONNECT_URL: '/api/profiles/me/oidc-connect/start',
}));

/** the login screen's oidc button: shown when on, labelled, a plain navigation */
function dom() {
  document.body.innerHTML = `
    <div id="login-oidc" class="login-oidc" hidden>
      <button id="login-oidc-button" type="button">Sign in with OIDC</button>
    </div>
    <p id="login-error" style="display:none"></p>`;
}

function available(body: Record<string, unknown>) {
  server.use(
    http.get('*/api/auth/oidc/available', () => HttpResponse.json({ success: true, ...body })),
  );
}

beforeEach(() => dom());
afterEach(() => {
  server.resetHandlers();
  document.body.innerHTML = '';
});

describe('the oidc button', () => {
  it('stays hidden when the admin has not turned it on', async () => {
    available({ enabled: false, label: 'Sign in with OIDC', error: null });
    await initOidcSignIn(vi.fn());
    expect(document.getElementById('login-oidc')!.hidden).toBe(true);
  });

  it("shows with the admin's label", async () => {
    available({ enabled: true, label: 'Sign in with Pocket ID', error: null });
    await initOidcSignIn(vi.fn());
    expect(document.getElementById('login-oidc')!.hidden).toBe(false);
    expect(document.getElementById('login-oidc-button')!.textContent).toBe(
      'Sign in with Pocket ID',
    );
  });

  it('a click goes to the provider through the server', async () => {
    available({ enabled: true, label: 'Sign in with Pocket ID', error: null });
    const go = vi.fn();
    await initOidcSignIn(go);
    const button = document.getElementById('login-oidc-button') as HTMLButtonElement;
    button.click();
    expect(go).toHaveBeenCalledWith(OIDC_START_URL);
    expect(button.disabled).toBe(true);
  });

  it('shows why the last attempt failed', async () => {
    available({ enabled: true, label: 'Sign in with Pocket ID', error: 'Sign-in was cancelled.' });
    await initOidcSignIn(vi.fn());
    const err = document.getElementById('login-error')!;
    expect(err.textContent).toBe('Sign-in was cancelled.');
    expect(err.style.display).toBe('block');
  });

  it('an unreachable server keeps the button hidden', async () => {
    server.use(http.get('*/api/auth/oidc/available', () => HttpResponse.error()));
    await initOidcSignIn(vi.fn());
    expect(document.getElementById('login-oidc')!.hidden).toBe(true);
  });
});

describe('coming back from a connect', () => {
  it('reopens my account and drops the marker', () => {
    const open = vi.fn();
    const replace = vi.fn();
    const loc = { search: '?oidc=connect&x=1', pathname: '/', hash: '#a' } as Location;
    expect(
      resumeOidcConnect(open, loc, { state: null, replaceState: replace } as unknown as History),
    ).toBe(true);
    expect(open).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledWith(null, '', '/?x=1#a');
  });

  it('does nothing on an ordinary load', () => {
    const open = vi.fn();
    const loc = { search: '', pathname: '/', hash: '' } as Location;
    expect(resumeOidcConnect(open, loc, { replaceState: vi.fn() } as unknown as History)).toBe(
      false,
    );
    expect(open).not.toHaveBeenCalled();
  });
});

describe("the admin's own connection in settings", () => {
  function adminBox() {
    document.body.innerHTML = '<div id="security-oidc-admin"></div>';
    return document.getElementById('security-oidc-admin') as HTMLElement;
  }
  function status(body: Record<string, unknown>) {
    server.use(
      http.get('*/api/profiles/me/oidc', () => HttpResponse.json({ success: true, ...body })),
    );
  }

  it('connected: says so, shows the icon, offers disconnect', async () => {
    const box = adminBox();
    status({
      enabled: true,
      label: 'Sign In With Pocket ID',
      icon: 'https://cdn.example.test/p.png',
      connected: true,
    });
    await renderOidcAdmin(box, vi.fn());
    expect(box.textContent).toContain('Your admin account is connected');
    expect(box.textContent).toContain('Sign In With Pocket ID');
    expect(box.querySelector('img')!.getAttribute('src')).toBe('https://cdn.example.test/p.png');
    expect(box.querySelector('button')!.textContent).toBe('Disconnect');
  });

  it('a url that is not https never becomes an image', async () => {
    const box = adminBox();
    status({ enabled: true, label: 'x', icon: 'javascript:alert(1)', connected: true });
    await renderOidcAdmin(box, vi.fn());
    expect(box.querySelector('img')).toBeNull();
    expect(box.querySelector('svg')).not.toBeNull();
  });

  it('disconnect redraws as not connected', async () => {
    const box = adminBox();
    let connected = true;
    server.use(
      http.get('*/api/profiles/me/oidc', () =>
        HttpResponse.json({ success: true, enabled: true, label: 'x', icon: '', connected }),
      ),
      http.delete('*/api/profiles/me/oidc', () => {
        connected = false;
        return HttpResponse.json({ success: true });
      }),
    );
    await renderOidcAdmin(box, vi.fn());
    (box.querySelector('button') as HTMLButtonElement).click();
    await vi.waitFor(() =>
      expect(box.querySelector('button')!.textContent).toBe('Connect my admin account'),
    );
  });

  it('a refused disconnect shows why and keeps the button', async () => {
    const box = adminBox();
    status({ enabled: true, label: 'x', icon: '', connected: true });
    server.use(
      http.delete('*/api/profiles/me/oidc', () =>
        HttpResponse.json({ success: false, error: 'This is the only way' }, { status: 400 }),
      ),
    );
    await renderOidcAdmin(box, vi.fn());
    (box.querySelector('button') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(box.textContent).toContain('This is the only way'));
    expect(box.querySelector('button')!.textContent).toBe('Disconnect');
  });

  it('connect goes to the provider only once oidc is saved on', async () => {
    const box = adminBox();
    status({ enabled: false, label: 'x', icon: '', connected: false });
    server.use(
      http.get('*/api/auth/oidc/available', () =>
        HttpResponse.json({ success: true, enabled: false }),
      ),
    );
    const go = vi.fn();
    await renderOidcAdmin(box, go);
    (box.querySelector('button') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(box.textContent).toContain('save first'));
    expect(go).not.toHaveBeenCalled();

    server.use(
      http.get('*/api/auth/oidc/available', () =>
        HttpResponse.json({ success: true, enabled: true }),
      ),
    );
    (box.querySelector('button') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(go).toHaveBeenCalledWith('/api/profiles/me/oidc-connect/start'));
  });

  it('asks the server nothing until an admin is known to be signed in', async () => {
    const box = adminBox();
    let calls = 0;
    server.use(
      http.get('*/api/profiles/me/oidc', () => {
        calls++;
        return HttpResponse.json({ success: true, enabled: true, label: 'x', connected: false });
      }),
    );
    let admin = false;
    initOidcAdminStatus(vi.fn(), () => admin);
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toBe(0);
    admin = true;
    window.dispatchEvent(new CustomEvent('ss:webui-profile-context-changed'));
    await vi.waitFor(() => expect(box.querySelector('button')).not.toBeNull());
    expect(calls).toBe(1);
    window.dispatchEvent(new CustomEvent('ss:webui-profile-context-changed'));
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toBe(1);
  });
});
