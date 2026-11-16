/**
 * Settings > Security > New sign-ups: the side a profile made by sign-in
 * starts on and its request limit. The server reads security.signup_sides,
 * security.signup_request_limit and security.signup_request_limit_days
 * (core/security/signup_defaults.py) and clamps them itself; these pin the
 * form fields to those keys in both directions. The section is React
 * (SETTINGS_PORT.md step 9c): this runs it.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';
import { SecuritySection } from '@/features/settings/tabs/system-security';

afterEach(() => vi.unstubAllGlobals());

const el = (id: string) => document.getElementById(id) as HTMLInputElement;

function page(security: Record<string, unknown> | null) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response)),
  );
  const posted: Array<Record<string, Record<string, unknown>>> = [];
  const store = createSettingsStore({
    post: (body) => {
      posted.push(body as Record<string, Record<string, unknown>>);
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  render(<SecuritySection store={store} part="security" opened={0} active={false} />);
  if (security) act(() => store.hydrate({ security }));
  return { store, posted };
}

describe('new sign-up settings', () => {
  it('the form offers the three sides the server accepts, music first', () => {
    page(null);
    expect([...el('security-signup-sides').querySelectorAll('option')].map((o) => o.value)).toEqual(
      ['music', 'video', 'both'],
    );
  });

  it('the side and the request limit are separate settings rows', () => {
    // a settings row is one label + one control on a line (#settings-page
    // .form-group is a flex row); two settings in one row put the second
    // label beside the first one's help text on a wide screen
    page(null);
    const side = document.getElementById('security-signup-defaults')!;
    expect(side.querySelector('#security-signup-request-limit')).toBeNull();
    const limit = document.getElementById('security-signup-request-limit-row')!;
    expect(limit.querySelector('label')!.getAttribute('for')).toBe('security-signup-request-limit');
  });

  it('the limit fields carry the profile editor bounds', () => {
    page(null);
    expect([
      el('security-signup-request-limit').min,
      el('security-signup-request-limit').max,
    ]).toEqual(['0', '1000']);
    expect([
      el('security-signup-request-limit-days').min,
      el('security-signup-request-limit-days').max,
    ]).toEqual(['1', '365']);
  });

  it('save sends each field under its server key', async () => {
    const { store, posted } = page({});
    fireEvent.change(el('security-signup-sides'), { target: { value: 'video' } });
    fireEvent.change(el('security-signup-request-limit'), { target: { value: '5' } });
    fireEvent.change(el('security-signup-request-limit-days'), { target: { value: 'x' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(Object.assign({}, ...posted.map((b) => b.security))).toEqual({
      signup_sides: 'video',
      signup_request_limit: 5,
      signup_request_limit_days: 7,
    });
  });

  it('load fills each field from its server key, with the defaults when unset', () => {
    page({});
    expect(el('security-signup-sides').value).toBe('music');
    expect(el('security-signup-request-limit').value).toBe('0');
    expect(el('security-signup-request-limit-days').value).toBe('7');
    act(() => undefined);
  });

  it('a saved side and limit show', () => {
    page({ signup_sides: 'both', signup_request_limit: 3, signup_request_limit_days: 14 });
    expect(el('security-signup-sides').value).toBe('both');
    expect(el('security-signup-request-limit').value).toBe('3');
    expect(el('security-signup-request-limit-days').value).toBe('14');
  });
});
