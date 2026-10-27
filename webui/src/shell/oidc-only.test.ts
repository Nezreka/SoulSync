import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import {
  describeForSettings,
  initOidcOnlyAdmin,
  overrideMessage,
  renderSettingsStatus,
  showOverrideButton,
} from './oidc-only';

/** the floating row and settings > security's oidc-only row */
function dom() {
  document.body.innerHTML = `
    <button id="activity-float-btn" class="activity-float-btn"></button>
    <button id="notif-bell-btn" class="notif-bell-btn"></button>
    <label class="toggle-label"><input type="checkbox" id="security-oidc-only">
      <span>OIDC only: turn off every other way to sign in</span></label>
    <div id="security-oidc-only-status" hidden></div>`;
}

function status(body: Record<string, unknown>, onCall?: () => void) {
  server.use(
    http.get('*/api/security/oidc-only', () => {
      onCall?.();
      return HttpResponse.json({
        success: true,
        override_env: 'SOULSYNC_OIDC_ONLY_OVERRIDE',
        ...body,
      });
    }),
  );
}

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => dom());
afterEach(() => {
  server.resetHandlers();
  document.body.innerHTML = '';
});

describe('the override button', () => {
  it('joins the floating row beside server activity for an admin', async () => {
    status({ stored: true, on: false, overridden: true });
    initOidcOnlyAdmin(() => true);
    await flush();
    await flush();
    const btn = document.getElementById('oidc-override-btn')!;
    expect(btn).not.toBeNull();
    expect(btn.nextElementSibling?.id).toBe('activity-float-btn');
    expect(btn.classList.contains('oidc-override-btn')).toBe(true);
  });

  it('is not there when nothing is overridden', async () => {
    status({ stored: true, on: true, overridden: false });
    initOidcOnlyAdmin(() => true);
    await flush();
    await flush();
    expect(document.getElementById('oidc-override-btn')).toBeNull();
  });

  it('a member never asks and never sees it', async () => {
    let asked = false;
    status({ stored: true, on: false, overridden: true }, () => {
      asked = true;
    });
    initOidcOnlyAdmin(() => false);
    await flush();
    await flush();
    expect(asked).toBe(false);
    expect(document.getElementById('oidc-override-btn')).toBeNull();
  });

  it('a click says how to clear it, a second click closes it', () => {
    const btn = showOverrideButton({ stored: true, overridden: true });
    btn.click();
    const panel = document.getElementById('oidc-override-panel')!;
    expect(panel.textContent).toContain('SOULSYNC_OIDC_ONLY_OVERRIDE');
    expect(panel.textContent).toContain('restart');
    btn.click();
    expect(document.getElementById('oidc-override-panel')).toBeNull();
  });

  it('is made once', () => {
    showOverrideButton({ stored: true, overridden: true });
    showOverrideButton({ stored: true, overridden: true });
    expect(document.querySelectorAll('#oidc-override-btn')).toHaveLength(1);
  });

  it('says so when the variable is set but the mode is not on', () => {
    expect(overrideMessage({ stored: false, overridden: true })).toContain("isn't on");
  });
});

describe('the settings row', () => {
  it('names who is not connected, the session and the sign-out count', () => {
    const lines = describeForSettings({
      stored: false,
      unlinked: [
        { id: 2, name: 'Z' },
        { id: 3, name: 'Kim' },
      ],
      session_ok: false,
      would_sign_out: 3,
      recent_days: 7,
    });
    expect(lines.join(' ')).toContain('Z, Kim');
    expect(lines.join(' ')).toContain('sign back in with OIDC');
    expect(lines.join(' ')).toContain(
      '3 devices that signed in without OIDC were used in the last 7 days. Any still signed in will be signed out.',
    );
  });

  it('says nothing more once it is on', () => {
    expect(describeForSettings({ stored: true, on: true, unlinked: [], session_ok: true })).toEqual(
      [],
    );
  });

  it('marks the toggle overridden', () => {
    const box = document.getElementById('security-oidc-only-status')!;
    renderSettingsStatus(box, { stored: true, overridden: true });
    expect(box.hidden).toBe(false);
    const label = document.querySelector('#security-oidc-only + span')!;
    expect(label.textContent).toContain('(overridden by environment)');
    renderSettingsStatus(box, { stored: true, overridden: true });
    expect(label.textContent!.match(/overridden by environment/g)).toHaveLength(1);
  });
});
