/**
 * AcoustID account linking on the AcoustID card (#2318): from 1 January 2027
 * a lookup needs the person's own AcoustID key. Link your AcoustID account
 * asks the server for a link, shows the code with a copy button and checks
 * until AcoustID says approved, declined or expired.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsStore } from '../settings-store';
import { acoustidReturnUrl, acoustidStatusWords, type AcoustidAccount } from './connections-extras';
import { ConnectionsTab } from './connections-tab';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let calls: Array<{ url: string; method: string; body?: string }>;
let answers: Record<string, unknown[]>;

const base: AcoustidAccount = {
  has_user_key: false,
  source: '',
  state: '',
  code: '',
  url: '',
  expires: '',
  has_app_key: true,
  builtin_app_key: true,
  required_from: '2027-01-01',
  required_now: false,
  needs_link: false,
  message: '',
};
const ok = (over: Partial<AcoustidAccount>) => ({ success: true, ...base, ...over });
const pending = ok({
  has_user_key: true,
  source: 'linked',
  state: 'pending',
  code: 'WT14-9E3S',
  url: 'https://acoustid.org/link?code=WT14-9E3S',
  expires: '2999-01-01T00:00:00+00:00',
});

beforeEach(() => {
  store = createSettingsStore({ post: () => Promise.resolve({ success: true }), delay: 0 });
  calls = [];
  answers = {};
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method || 'GET', body: init?.body as string | undefined });
      const queue = answers[url];
      const data = queue && queue.length > 1 ? queue.shift() : (queue?.[0] ?? { success: true });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  vi.stubGlobal('open', vi.fn());
  shell.showToast = vi.fn();
  shell.saveSettings = vi.fn(() => Promise.resolve());
});

afterEach(async () => {
  await act(() => store.flush());
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete shell.showToast;
  delete shell.saveSettings;
});

function card() {
  const slot = document.createElement('div');
  document.body.append(slot);
  render(<ConnectionsTab store={store} part="api-music" opened={0} active={false} />, {
    container: slot,
  });
  act(() => store.hydrate({ acoustid: { enabled: true } }));
}
const el = (id: string) => document.getElementById(id) as HTMLInputElement;

describe('the AcoustID card links an account', () => {
  it('Link account asks for a link, shows the code to copy, and turns Linked once approved', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    answers['/api/acoustid/account?check=1'] = [
      ok({}),
      ok({ has_user_key: true, source: 'linked', state: 'approved' }),
    ];
    answers['/api/acoustid/link'] = [pending];
    card();
    await waitFor(() => expect(el('acoustid-link-status').textContent).toBe('Not linked'));
    const button = [...document.querySelectorAll('#acoustid-link-row button')].find(
      (b) => b.textContent === 'Link account',
    )!;
    fireEvent.click(button);
    await waitFor(() => expect(el('acoustid-link-code')?.value).toBe('WT14-9E3S'));
    const post = calls.find((c) => c.url === '/api/acoustid/link')!;
    expect(post.method).toBe('POST');
    expect(JSON.parse(post.body!)).toEqual({ return_url: acoustidReturnUrl() });
    expect(acoustidReturnUrl()).toContain('/settings?tab=connections&view=services&open=acoustid');
    expect(shell.saveSettings).toHaveBeenCalled();
    expect(window.open).toHaveBeenCalledWith(pending.url, '_blank', 'noopener');
    expect(document.querySelector('#acoustid-link-pending [data-copy-btn]')).not.toBeNull();
    expect(el('acoustid-link-status').textContent).toContain('Waiting');
    await act(() => vi.advanceTimersByTimeAsync(4500));
    await waitFor(() => expect(el('acoustid-link-status').textContent).toBe('Linked'));
    expect(el('acoustid-link-code')).toBeNull();
    // it stops checking once AcoustID has answered
    const checks = calls.filter((c) => c.url === '/api/acoustid/account?check=1').length;
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(calls.filter((c) => c.url === '/api/acoustid/account?check=1').length).toBe(checks);
  });

  it('after the cutoff with no key, it says lookups need the account linked', async () => {
    answers['/api/acoustid/account?check=1'] = [
      ok({
        needs_link: true,
        required_now: true,
        message: 'AcoustID needs your account linked: Settings',
      }),
    ];
    card();
    await waitFor(() =>
      expect(el('acoustid-needs-link').textContent).toContain('AcoustID needs your account linked'),
    );
    expect(el('acoustid-link-status').textContent).toBe('Not linked: lookups stop');
  });

  it('the user key field saves acoustid.user_key, and the app key hides with a built-in one', async () => {
    answers['/api/acoustid/account?check=1'] = [ok({})];
    card();
    await waitFor(() => expect(el('acoustid-link-status').textContent).toBe('Not linked'));
    expect(el('acoustid-api-key')).toBeNull();
    fireEvent.change(el('acoustid-user-key'), { target: { value: 'UK' } });
    expect(store.getState().settings.acoustid).toMatchObject({ user_key: 'UK' });
  });

  it('words for each link state', () => {
    expect(acoustidStatusWords(ok({ state: 'declined' }))[0]).toContain('Declined');
    expect(acoustidStatusWords(ok({ state: 'expired' }))[0]).toContain('ran out');
    expect(acoustidStatusWords(ok({ has_user_key: true, source: 'pasted' }))[0]).toBe(
      'Your user key is saved',
    );
  });
});
