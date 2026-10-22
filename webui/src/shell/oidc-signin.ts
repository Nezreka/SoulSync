/**
 * "sign in with oidc" on the login screen. the button only shows when the
 * admin turned it on and filled in a provider, and it carries the label they
 * chose ("Sign in with Pocket ID"). a click is a plain page navigation: the
 * server sends the browser to the provider and the provider sends it back,
 * so there is no popup and nothing to poll. when it comes back without
 * signing in, the server hands over why once, and it shows here.
 *
 * a connect from my account comes back with ?oidc=connect; my account opens
 * again so its outcome is seen.
 *
 * the admin never gets my account, so their own connection lives in settings
 * > security instead, as the last row of the oidc block: status, the
 * provider's icon and disconnect when connected, else the connect button.
 */

import { OIDC_CONNECT_URL, openMyAccountsModal } from './my-accounts';

const WRAP_ID = 'login-oidc';
const BUTTON_ID = 'login-oidc-button';
const ERROR_ID = 'login-error';
export const OIDC_START_URL = '/api/auth/oidc/start';

function showError(message: string): void {
  const el = document.getElementById(ERROR_ID);
  if (!el) return;
  el.textContent = message;
  el.style.display = message ? 'block' : 'none';
}

/** a connect just came back: drop the marker from the address, reopen my account */
export function resumeOidcConnect(
  open: () => void = openMyAccountsModal,
  loc: Location = window.location,
  hist: History = window.history,
): boolean {
  const params = new URLSearchParams(loc.search);
  if (params.get('oidc') !== 'connect') return false;
  params.delete('oidc');
  const rest = params.toString();
  hist.replaceState(hist.state, '', loc.pathname + (rest ? `?${rest}` : '') + loc.hash);
  open();
  return true;
}

/** show the button when the admin turned oidc sign-in on, and wire it once */
export async function initOidcSignIn(
  go: (url: string) => void = (url) => window.location.assign(url),
): Promise<void> {
  resumeOidcConnect();
  const wrap = document.getElementById(WRAP_ID);
  const button = document.getElementById(BUTTON_ID) as HTMLButtonElement | null;
  if (!wrap || !button || button.dataset.wired === '1') return;
  button.dataset.wired = '1';
  try {
    const resp = await fetch('/api/auth/oidc/available', {
      headers: { Accept: 'application/json' },
    });
    const data = resp.ok
      ? ((await resp.json()) as { enabled?: boolean; label?: string; error?: string | null })
      : {};
    wrap.hidden = data.enabled !== true;
    if (typeof data.label === 'string' && data.label) button.textContent = data.label;
    if (typeof data.error === 'string' && data.error) showError(data.error);
  } catch {
    wrap.hidden = true;
  }
  button.addEventListener('click', () => {
    button.disabled = true;
    button.textContent = 'Redirecting...';
    go(OIDC_START_URL);
  });
}

// -- the admin's own connection, in settings > security --

const ADMIN_ID = 'security-oidc-admin';

interface AdminOidcStatus {
  enabled?: boolean;
  label?: string;
  icon?: string;
  connected?: boolean;
}

const KEY_SVG =
  '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="15" r="4"/><path d="M10.8 12.2 20 3M16 7l3 3M14 9l2 2"/></svg>';

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className: string,
  text = '',
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

/** the provider's icon when one is set (https only, built as an element so
 * the url is never parsed as markup), else a plain key */
function adminLogo(icon: string): HTMLElement {
  const box = el('span', 'security-oidc-admin-logo');
  if (icon.startsWith('https://')) {
    const img = el('img', '');
    img.src = icon;
    img.alt = '';
    img.addEventListener('error', () => {
      img.remove();
      box.innerHTML = KEY_SVG;
    });
    box.appendChild(img);
  } else {
    box.innerHTML = KEY_SVG;
  }
  return box;
}

async function readAdminStatus(): Promise<AdminOidcStatus> {
  try {
    const resp = await fetch('/api/profiles/me/oidc', { headers: { Accept: 'application/json' } });
    return resp.ok ? ((await resp.json()) as AdminOidcStatus) : {};
  } catch {
    return {};
  }
}

/** draw the admin row from the server's answer */
export async function renderOidcAdmin(
  box: HTMLElement,
  go: (url: string) => void = (url) => window.location.assign(url),
): Promise<void> {
  const st = await readAdminStatus();
  const label = st.label || 'Sign in with OIDC';
  box.replaceChildren();
  if (st.connected) {
    box.append(
      adminLogo(st.icon || ''),
      el(
        'span',
        'security-oidc-admin-text',
        `Your admin account is connected. You can use "${label}" on the sign-in screen.`,
      ),
    );
    const off = el('button', 'auth-button', 'Disconnect');
    off.type = 'button';
    off.addEventListener('click', async () => {
      off.disabled = true;
      let error = '';
      try {
        const resp = await fetch('/api/profiles/me/oidc', { method: 'DELETE' });
        const body = (await resp.json().catch(() => ({}))) as { success?: boolean; error?: string };
        if (!resp.ok || body.success === false) error = body.error || "Couldn't disconnect";
      } catch {
        error = "Couldn't disconnect";
      }
      if (error) {
        off.disabled = false;
        box.querySelector('.security-oidc-admin-note')?.remove();
        box.append(el('span', 'security-oidc-admin-note', error));
        return;
      }
      await renderOidcAdmin(box, go);
    });
    box.append(off);
    return;
  }
  box.append(
    adminLogo(st.icon || ''),
    el(
      'span',
      'security-oidc-admin-text',
      'Connect your own account at the provider to sign in as the admin with it.',
    ),
  );
  const on = el('button', 'auth-button', 'Connect my admin account');
  on.type = 'button';
  on.addEventListener('click', async () => {
    // the settings on the page may not be saved yet: going to the provider
    // with oidc still off would only land on an error
    let enabled = false;
    try {
      const resp = await fetch('/api/auth/oidc/available', {
        headers: { Accept: 'application/json' },
      });
      enabled = resp.ok && ((await resp.json()) as { enabled?: boolean }).enabled === true;
    } catch {
      enabled = false;
    }
    if (enabled) {
      go(OIDC_CONNECT_URL);
      return;
    }
    box.querySelector('.security-oidc-admin-note')?.remove();
    box.append(
      el(
        'span',
        'security-oidc-admin-note',
        'Turn on Sign in with OIDC, fill in the provider and save first.',
      ),
    );
  });
  box.append(on);
}

// init.js fires this once it knows who is signed in, and on every switch
const PROFILE_CONTEXT_CHANGED_EVENT = 'ss:webui-profile-context-changed';

/** wire the admin row, if this page has one. it asks the server nothing
 * until the shell knows an admin is signed in: the login screen and member
 * sessions never make the call (no 401 per page load) */
export function initOidcAdminStatus(
  go: (url: string) => void = (url) => window.location.assign(url),
  isAdmin: () => boolean = () => window.getCurrentProfileContext?.()?.isAdmin === true,
): void {
  const box = document.getElementById(ADMIN_ID);
  if (!box || box.dataset.wired === '1') return;
  box.dataset.wired = '1';
  const tryRender = () => {
    if (!isAdmin() || box.dataset.drawn === '1') return;
    box.dataset.drawn = '1';
    window.removeEventListener(PROFILE_CONTEXT_CHANGED_EVENT, tryRender);
    void renderOidcAdmin(box, go);
  };
  window.addEventListener(PROFILE_CONTEXT_CHANGED_EVENT, tryRender);
  tryRender();
}
