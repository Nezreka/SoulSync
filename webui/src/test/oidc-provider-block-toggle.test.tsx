/**
 * Settings > Security > Sign in with OIDC: the provider's settings (and the
 * admin's connect row inside them) show only while the toggle is on. Hidden,
 * not removed: the fields keep their values and still save. The section is
 * React (SETTINGS_PORT.md step 9c): this runs it.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';
import { SecuritySection } from '@/features/settings/tabs/system-security';

afterEach(() => vi.unstubAllGlobals());

function page(security: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response)),
  );
  const posted: unknown[] = [];
  const store = createSettingsStore({
    post: (body) => {
      posted.push(body);
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  render(<SecuritySection store={store} part="security" opened={0} active={false} />);
  act(() => store.hydrate({ security }));
  return { store, posted, block: () => document.getElementById('security-oidc-provider')! };
}

describe('the oidc provider settings follow their toggle', () => {
  it('hides the provider block while sign in with oidc is off', () => {
    expect(page({ oidc_signin: false }).block().hidden).toBe(true);
  });

  it('shows it while sign in with oidc is on', () => {
    expect(page({ oidc_signin: true }).block().hidden).toBe(false);
  });

  it('hides it when the setting was never saved', () => {
    expect(page({}).block().hidden).toBe(true);
  });

  it('follows the toggle, and a hidden field keeps its value and still saves', async () => {
    const { block, store, posted } = page({ oidc_signin: true, oidc_issuer: 'https://id.x' });
    fireEvent.click(document.getElementById('security-oidc-signin')!);
    expect(block().hidden).toBe(true);
    expect((document.getElementById('security-oidc-issuer') as HTMLInputElement).value).toBe(
      'https://id.x',
    );
    fireEvent.change(document.getElementById('security-oidc-issuer')!, {
      target: { value: 'https://id.y' },
    });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(posted).toContainEqual({
      security: { oidc_signin: false, oidc_issuer: 'https://id.y' },
    });
  });

  it('the block it hides is the one holding the provider fields and the admin row', () => {
    const { block } = page({});
    for (const id of ['security-oidc-issuer', 'security-oidc-client-secret', 'security-oidc-admin'])
      expect(block().querySelector(`#${id}`)).not.toBeNull();
  });
});
