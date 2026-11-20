import { act, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SoulseekLoginBanner, soulseekLoginNotice } from './soulseek-login';

afterEach(() => vi.unstubAllGlobals());

describe('soulseekLoginNotice', () => {
  it('says nothing while logged in or unknown', () => {
    expect(soulseekLoginNotice(null)).toBeNull();
    expect(soulseekLoginNotice({ soulseek: { connected: true } })).toBeNull();
    expect(soulseekLoginNotice({ soulseek: { logged_out: false } })).toBeNull();
  });

  it('says how long ago and what the downloads do', () => {
    const now = 1_700_000_600_000;
    const notice = soulseekLoginNotice(
      { soulseek: { logged_out: true, logged_out_since: 1_700_000_000, login_wait_minutes: 30 } },
      now,
    )!;
    expect(notice.title).toBe('Soulseek logged out (10 min ago)');
    expect(notice.detail).toMatch(/without using up their retries/);
    expect(notice.detail).toMatch(/After 30 min they move on to your next source/);
  });

  it('a zero wait moves straight on', () => {
    const notice = soulseekLoginNotice({ soulseek: { logged_out: true, login_wait_minutes: 0 } })!;
    expect(notice.detail).toMatch(/straight on/);
  });
});

describe('SoulseekLoginBanner', () => {
  it('shows from /status and follows the status push', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ soulseek: { logged_out: true } }),
        } as Response),
      ),
    );
    render(<SoulseekLoginBanner />);
    await act(async () => {});
    expect(document.getElementById('adl-soulseek-login-banner')?.textContent).toMatch(
      /Soulseek logged out/,
    );
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:service-status', { detail: { soulseek: { logged_out: false } } }),
      );
    });
    expect(document.getElementById('adl-soulseek-login-banner')).toBeNull();
  });
});
