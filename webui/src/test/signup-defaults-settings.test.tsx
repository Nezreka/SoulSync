/**
 * Settings > Security > New people start as (#2166). Sign-ups (Plex, OIDC)
 * follow the sign-up template under People > Templates; the old
 * security.signup_* settings and the two "new profiles can download"
 * switches moved there once (core/permission_store.move_signup_settings).
 * The section only names the template and opens People > Templates; it
 * never writes a sign-up setting of its own any more.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';
import { SecuritySection } from '@/features/settings/tabs/system-security';

const opened: string[] = [];
vi.mock('@/features/settings/tabs/people/people-tab', () => ({
  openPeopleSettings: (pill: string) => opened.push(pill),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  opened.length = 0;
});

const el = (id: string) => document.getElementById(id) as HTMLElement;

function page(security: Record<string, unknown> | null, templates: unknown = null) {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve(url === '/api/permissions/templates' && templates ? templates : {}),
      } as Response),
    ),
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

describe('new people start as', () => {
  it('names the sign-up template once it loads', async () => {
    page(
      {},
      {
        success: true,
        signup_template_id: 9,
        templates: [
          { id: 4, name: 'Guest' },
          { id: 9, name: 'New sign-ups' },
        ],
      },
    );
    await waitFor(() =>
      expect(el('security-signup-template-open').textContent).toBe(
        'New sign-ups: change in People',
      ),
    );
  });

  it('opens People > Templates', () => {
    page({});
    fireEvent.click(el('security-signup-template-open'));
    expect(opened).toEqual(['templates']);
  });

  it('the old sign-up fields are gone, so nothing writes them', () => {
    page({ signup_sides: 'both', signup_request_limit: 3, plex_signin: true });
    for (const id of [
      'security-signup-sides',
      'security-signup-request-limit',
      'security-signup-request-limit-days',
      'security-plex-signin-can-download',
      'security-oidc-signin-can-download',
    ])
      expect(document.getElementById(id), id).toBeNull();
  });
});
