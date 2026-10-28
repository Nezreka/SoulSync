/**
 * sign in with oidc only (#1839), the admin's side of it.
 *
 * - while SOULSYNC_OIDC_ONLY_OVERRIDE has the mode off, an amber button
 *   joins the floating row at the bottom right (beside server activity,
 *   the bell and help) on every page, for admins only. a click says what
 *   it means and how to clear it. a member never sees it, and never asks.
 * - settings > security's oidc-only row says what turning it on would do:
 *   who isn't connected yet, whether this session can turn it on, how
 *   many devices that didn't sign in with oidc were used lately.
 *
 * both read /api/security/oidc-only, which is admin only, so nothing is
 * asked until the shell knows an admin is signed in.
 */

const STATUS_URL = '/api/security/oidc-only';
const BUTTON_ID = 'oidc-override-btn';
const PANEL_ID = 'oidc-override-panel';
const SETTINGS_ID = 'security-oidc-only-status';
const TOGGLE_ID = 'security-oidc-only';
const PROFILE_CONTEXT_CHANGED_EVENT = 'ss:webui-profile-context-changed';

export interface OidcOnlyStatus {
  stored?: boolean;
  on?: boolean;
  overridden?: boolean;
  override_env?: string;
  would_sign_out?: number | null;
  recent_days?: number;
  unlinked?: { id: number; name: string }[];
  session_ok?: boolean;
}

const SHIELD_SVG =
  '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><line x1="4" y1="4" x2="20" y2="20"/></svg>';

export async function readOidcOnlyStatus(): Promise<OidcOnlyStatus | null> {
  try {
    const resp = await fetch(STATUS_URL, { headers: { Accept: 'application/json' } });
    return resp.ok ? ((await resp.json()) as OidcOnlyStatus) : null;
  } catch {
    return null;
  }
}

function overrideName(st: OidcOnlyStatus): string {
  return st.override_env || 'SOULSYNC_OIDC_ONLY_OVERRIDE';
}

export function overrideMessage(st: OidcOnlyStatus): string {
  return st.stored
    ? `OIDC-only sign-in is switched off by ${overrideName(st)}, so password sign-in is open. ` +
        'Remove it from the container and restart SoulSync to turn OIDC-only back on.'
    : `${overrideName(st)} is set, but OIDC-only sign-in isn't on. Remove it from the container.`;
}

function closePanel(): void {
  document.getElementById(PANEL_ID)?.remove();
}

function openPanel(button: HTMLElement, st: OidcOnlyStatus): void {
  closePanel();
  const panel = document.createElement('div');
  panel.id = PANEL_ID;
  panel.className = 'notif-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'OIDC-only sign-in is overridden');
  const head = document.createElement('div');
  head.className = 'notif-panel-header';
  const title = document.createElement('span');
  title.className = 'notif-panel-title';
  title.textContent = 'OIDC-only sign-in is overridden';
  head.append(title);
  const text = document.createElement('p');
  text.className = 'oidc-override-text';
  text.textContent = overrideMessage(st);
  panel.append(head, text);
  const rect = button.getBoundingClientRect();
  panel.style.right = `${window.innerWidth - rect.right}px`;
  panel.style.bottom = `${window.innerHeight - rect.top + 8}px`;
  document.body.append(panel);
  requestAnimationFrame(() => panel.classList.add('visible'));
  setTimeout(() => {
    const away = (e: MouseEvent) => {
      if (!panel.contains(e.target as Node) && e.target !== button) {
        closePanel();
        document.removeEventListener('click', away);
      }
    };
    document.addEventListener('click', away);
  }, 0);
}

/** the floating button, made once, beside server activity */
export function showOverrideButton(st: OidcOnlyStatus): HTMLButtonElement {
  let button = document.getElementById(BUTTON_ID) as HTMLButtonElement | null;
  if (button) return button;
  button = document.createElement('button');
  button.id = BUTTON_ID;
  button.type = 'button';
  // the activity button's look and stacking, its own slot and colour
  button.className = 'activity-float-btn oidc-override-btn';
  button.title = 'OIDC-only sign-in is overridden';
  button.setAttribute('aria-label', 'OIDC-only sign-in is overridden');
  button.innerHTML = SHIELD_SVG;
  const b = button;
  b.addEventListener('click', (e) => {
    e.stopPropagation();
    if (document.getElementById(PANEL_ID)) closePanel();
    else openPanel(b, st);
  });
  const anchor = document.getElementById('activity-float-btn');
  if (anchor?.parentNode) anchor.parentNode.insertBefore(b, anchor);
  else document.body.append(b);
  return b;
}

/** settings > security: what turning it on would do, or why it's off */
export function describeForSettings(st: OidcOnlyStatus): string[] {
  const lines: string[] = [];
  if (st.overridden) lines.push(overrideMessage(st));
  if (!st.stored) {
    const unlinked = st.unlinked ?? [];
    if (unlinked.length)
      lines.push(
        `Not connected to OIDC yet: ${unlinked.map((m) => m.name).join(', ')}. ` +
          'Each needs to connect from My Account first.',
      );
    if (st.session_ok === false) lines.push('Sign out and sign back in with OIDC to turn this on.');
    if (typeof st.would_sign_out === 'number' && st.would_sign_out > 0) {
      // a device row can outlive its session (a cleared cookie, sign out
      // everywhere), so this says what the rows show, not that each of them
      // is still signed in
      const n = st.would_sign_out;
      const days = st.recent_days ?? 7;
      lines.push(
        `${n} ${n === 1 ? 'device that signed in without OIDC was' : 'devices that signed in without OIDC were'} ` +
          `used in the last ${days} days. Any still signed in will be signed out.`,
      );
    }
  }
  return lines;
}

export function renderSettingsStatus(box: HTMLElement, st: OidcOnlyStatus | null): void {
  box.replaceChildren();
  const lines = st ? describeForSettings(st) : [];
  for (const line of lines) {
    const p = document.createElement('p');
    p.textContent = line;
    box.append(p);
  }
  box.hidden = lines.length === 0;
  const toggle = document.getElementById(TOGGLE_ID) as HTMLInputElement | null;
  const label = toggle?.parentElement?.querySelector('span');
  if (label && st?.overridden && !label.dataset.base) {
    label.dataset.base = label.textContent || '';
    label.textContent = `${label.dataset.base} (overridden by environment)`;
  }
}

/** wire both, once an admin is signed in */
export function initOidcOnlyAdmin(
  isAdmin: () => boolean = () => window.getCurrentProfileContext?.()?.isAdmin === true,
): void {
  let done = false;
  const tryRun = async () => {
    if (done || !isAdmin()) return;
    done = true;
    window.removeEventListener(PROFILE_CONTEXT_CHANGED_EVENT, tryRun);
    const st = await readOidcOnlyStatus();
    if (!st) return;
    if (st.overridden) showOverrideButton(st);
    const box = document.getElementById(SETTINGS_ID);
    if (box) renderSettingsStatus(box, st);
  };
  window.addEventListener(PROFILE_CONTEXT_CHANGED_EVENT, tryRun);
  void tryRun();
}
