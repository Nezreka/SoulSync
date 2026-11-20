/**
 * System > Security (settings-bic phase 6, on the parts): the cards and the
 * hero, the PIN, password and recovery flows against their endpoints, the
 * locks that keep anyone from shutting themselves out, the OIDC block, and
 * every security.* key it saves.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { badOrigins, pinError, SecurityView } from './system-security';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let calls: Array<{ url: string; method: string; body?: unknown }>;
let admin: Record<string, unknown>;

beforeEach(() => {
  const sink: SettingsPayload[] = [];
  posted = sink;
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  calls = [];
  admin = { is_admin: true, has_pin: false, has_password: false, has_recovery: false };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
      calls.push({ url, method, body });
      let data: unknown = { success: true };
      if (url === '/api/profiles') data = { profiles: [{ id: 2, is_admin: false }, admin] };
      else if (url === '/api/auth/oidc/available')
        data = { callback_url: 'http://example.test/api/auth/oidc/callback' };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
  shell.initOidcAdminStatus = vi.fn();
  shell.refreshOidcOnlySettingsStatus = vi.fn(() => Promise.resolve());
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  vi.useRealTimers();
  for (const k of ['showToast', 'initOidcAdminStatus', 'refreshOidcOnlySettingsStatus'])
    delete shell[k];
});

const section = () => render(<SecurityView store={store} />);
const el = (id: string) => document.getElementById(id) as HTMLInputElement;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}
async function loaded(settings: SettingsPayload = { security: {} }) {
  act(() => store.hydrate(settings));
  await waitFor(() => expect(calls.some((c) => c.url === '/api/profiles')).toBe(true));
  await act(() => Promise.resolve());
}

describe('the cards', () => {
  it('three cards, every control in its own, named without a colon, no emoji or step numbers', () => {
    const { container } = section();
    const cards = [...container.querySelectorAll<HTMLElement>('[data-card]')];
    expect(cards.map((c) => c.dataset.card)).toEqual([
      'Lock with a PIN',
      'Sign-in',
      'Reverse proxy',
    ]);
    const home: Record<string, string[]> = {
      'Lock with a PIN': ['security-require-pin', 'security-new-pin', 'security-confirm-pin'],
      'Sign-in': [
        'security-login-password',
        'security-recovery-question',
        'security-require-login',
        'security-plex-signin',
        'security-plex-signin-auto-create',
        'security-oidc-signin',
        'security-oidc-issuer',
        'security-oidc-client-secret',
        'security-oidc-scopes',
        'security-oidc-only',
        'security-signup-template-open',
      ],
      'Reverse proxy': [
        'security-trust-proxy',
        'security-auth-proxy-header',
        'security-cors-origins',
      ],
    };
    for (const card of cards)
      for (const id of home[card.dataset.card!]) expect(card.contains(el(id)), id).toBe(true);
    for (const label of container.querySelectorAll('label, h4'))
      expect(label.textContent ?? '').not.toMatch(/:\s*$|—|Step \d|🔑|👤|🌐/);
    expect(container.querySelector('.form-group, .security-subgroup')).toBeNull();
    // the rare OIDC fields wait behind More options
    expect(el('security-oidc-scopes').closest('[data-more-body]')).not.toBeNull();
    expect(el('security-oidc-issuer').closest('[data-more-body]')).toBeNull();
    // the shell draws its two boxes once they are on the page
    expect(shell.initOidcAdminStatus).toHaveBeenCalledOnce();
    expect(shell.refreshOidcOnlySettingsStatus).toHaveBeenCalledOnce();
  });

  it('the hero says who can get in', async () => {
    const { container } = section();
    await loaded({ security: {} });
    await waitFor(() =>
      expect(container.textContent).toContain(
        'Anyone who can reach SoulSync can use it: there is no PIN and no sign-in.',
      ),
    );
  });
});

describe('the rules', () => {
  it('checks a PIN and the WebSocket origins as the page did', () => {
    expect(pinError('12a4')).toBe('Use digits only');
    expect(pinError('123')).toBe('Use at least 4 digits');
    expect(pinError('1'.repeat(21))).toBe('Use 20 digits at most');
    expect(pinError('1234')).toBe('');
    expect(
      badOrigins('https://a.example\n*, http://b.example:8888, a.example/, https://c/x'),
    ).toEqual(['a.example/', 'https://c/x']);
  });
});

describe('the PIN', () => {
  it('with none set: the lock is off and locked, and asking for it opens the setup', async () => {
    section();
    await loaded({ security: { require_pin_on_launch: true } });
    expect(el('security-require-pin').checked).toBe(false);
    expect(el('security-require-pin').disabled).toBe(true);
    expect(el('security-pin-setup').hidden).toBe(false);
    expect(el('security-change-pin-section').hidden).toBe(true);
  });

  it('a PIN saves through its endpoint, then the lock can be turned on and saves at once', async () => {
    section();
    await loaded();
    fireEvent.change(el('security-new-pin'), { target: { value: '1234' } });
    fireEvent.change(el('security-confirm-pin'), { target: { value: '1235' } });
    fireEvent.click(el('security-save-pin-btn'));
    expect(el('security-pin-msg').textContent).toBe('PINs do not match');
    fireEvent.change(el('security-confirm-pin'), { target: { value: '1234' } });
    fireEvent.click(el('security-save-pin-btn'));
    await waitFor(() =>
      expect(el('security-pin-msg').textContent).toBe(
        'PIN saved! You can now enable the lock screen.',
      ),
    );
    expect(calls.find((c) => c.url === '/api/profiles/1/set-pin')?.body).toEqual({ pin: '1234' });
    expect(el('security-pin-setup').hidden).toBe(true);
    expect(el('security-change-pin-section').hidden).toBe(false);
    expect(el('security-new-pin').value).toBe('');
    expect(el('security-require-pin').disabled).toBe(false);
    fireEvent.click(el('security-require-pin'));
    await waitFor(() => expect(saved().security).toEqual({ require_pin_on_launch: true }));
  });

  it('while a PIN is being changed, the lock waits for it', async () => {
    admin.has_pin = true;
    section();
    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Change PIN' }));
    expect(el('security-pin-setup').hidden).toBe(false);
    fireEvent.click(el('security-require-pin'));
    expect(el('security-require-pin').checked).toBe(false);
    expect(posted).toEqual([]);
  });
});

describe('user accounts', () => {
  it('Require login stays locked until the admin has a password', async () => {
    section();
    await loaded({ security: { require_login: true } });
    expect(el('security-require-login').disabled).toBe(true);
    expect(el('security-require-login').checked).toBe(false);
    expect(el('security-login-toggle-wrap').className).toMatch(/rowOff/);
    expect(el('security-require-login-help').textContent).toBe(
      'Set the admin password above first, then you can turn this on.',
    );
    fireEvent.change(el('security-login-password'), { target: { value: 'abc' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }));
    expect(el('security-login-password-msg').textContent).toBe(
      'Password must be at least 6 characters',
    );
    fireEvent.change(el('security-login-password'), { target: { value: 'secret1' } });
    fireEvent.change(el('security-login-password-confirm'), { target: { value: 'secret1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }));
    await waitFor(() => expect(el('security-require-login').disabled).toBe(false));
    expect(calls.find((c) => c.url === '/api/profiles/1/set-password')?.body).toEqual({
      password: 'secret1',
    });
    expect(el('security-login-password-status').hidden).toBe(false);
    expect(el('security-login-password').placeholder).toBe('New password');
    expect(el('security-login-toggle-wrap').className).not.toMatch(/rowOff/);
    // the saved setting shows again now the lock is open
    expect(el('security-require-login').checked).toBe(true);
  });

  it('the saved recovery question shows, a custom one in its box; a new one saves', async () => {
    admin.has_recovery = true;
    admin.recovery_question = 'Who was my first boss?';
    section();
    await loaded();
    await waitFor(() => expect(el('security-recovery-question').value).toBe('__custom__'));
    expect(el('security-recovery-custom').value).toBe('Who was my first boss?');
    expect(el('security-recovery-custom').parentElement!.parentElement!.hidden).toBe(false);
    expect(el('security-recovery-status').textContent).toBe(
      'Saved: “Who was my first boss?”. Answer a new one below to change it.',
    );
    fireEvent.change(el('security-recovery-question'), {
      target: { value: 'What city were you born in?' },
    });
    expect(el('security-recovery-custom').parentElement!.parentElement!.hidden).toBe(true);
    const save = screen.getByRole('button', { name: 'Save recovery question' });
    fireEvent.click(save);
    expect(el('security-recovery-msg').textContent).toBe('Enter an answer');
    fireEvent.change(el('security-recovery-answer'), { target: { value: 'Leeds' } });
    fireEvent.click(save);
    await waitFor(() =>
      expect(el('security-recovery-status').textContent).toBe(
        'Saved: “What city were you born in?”. Answer a new one below to change it.',
      ),
    );
    expect(calls.find((c) => c.url === '/api/profiles/1/set-recovery')?.body).toEqual({
      question: 'What city were you born in?',
      answer: 'Leeds',
    });
  });
});

describe('OIDC and the rest', () => {
  it('the provider block follows the switch; the callback is the one the server sends', async () => {
    section();
    expect(el('security-oidc-provider').hidden).toBe(false);
    await loaded({ security: { oidc_client_secret: '__redacted_unchanged__' } });
    expect(el('security-oidc-provider').hidden).toBe(true);
    await waitFor(() =>
      expect(el('security-oidc-callback-url').textContent).toBe(
        'http://example.test/api/auth/oidc/callback',
      ),
    );
    // jsdom's page is http too: no warning
    expect(el('security-oidc-callback-warn').hidden).toBe(true);
    fireEvent.click(el('security-oidc-signin'));
    expect(el('security-oidc-provider').hidden).toBe(false);
    // the shell owns the admin box's contents
    expect(el('security-oidc-admin').querySelector('button')!.textContent).toBe(
      'Connect my admin account',
    );
    // what the shell draws there stays through React's next renders
    el('security-oidc-admin').innerHTML = '<span>drawn by the shell</span>';
    el('security-oidc-only-status').innerHTML = '<p>status</p>';
    fireEvent.click(el('security-oidc-signin'));
    act(() => store.set('security', 'plex_signin', true));
    expect(el('security-oidc-admin').textContent).toBe('drawn by the shell');
    expect(el('security-oidc-only-status').textContent).toBe('status');
  });

  it('each field saves its security key, text trimmed and the secret as typed', async () => {
    section();
    await loaded({
      security: { oidc_signin: true, oidc_client_secret: '__redacted_unchanged__' },
    });
    fireEvent.change(el('security-oidc-issuer'), { target: { value: ' https://id.example ' } });
    fireEvent.change(el('security-oidc-client-secret'), { target: { value: ' s3 ' } });
    fireEvent.click(el('security-oidc-signin-auto-create'));
    fireEvent.click(el('security-plex-signin'));
    fireEvent.click(el('security-trust-proxy'));
    fireEvent.change(el('security-auth-proxy-header'), { target: { value: ' Remote-User ' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().security).toEqual({
      oidc_issuer: 'https://id.example',
      oidc_client_secret: ' s3 ',
      oidc_signin_auto_create: false,
      plex_signin: true,
      trust_reverse_proxy: true,
      auth_proxy_header: 'Remote-User',
    });
  });

  it('a malformed WebSocket origin is saved, with the warning once the typing stops', async () => {
    section();
    await loaded();
    vi.useFakeTimers();
    fireEvent.change(el('security-cors-origins'), {
      target: { value: 'https://ok.example\nbad.example/ ' },
    });
    expect(shell.showToast).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(shell.showToast).toHaveBeenCalledWith(
      expect.stringMatching(/^Allowed Origins: 1 entry looks malformed/),
      'warning',
    );
    vi.useRealTimers();
    await act(() => store.flush());
    expect(saved().security).toEqual({ cors_origins: 'https://ok.example\nbad.example/' });
  });
});
