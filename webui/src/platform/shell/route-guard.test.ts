import { isRedirect } from '@tanstack/react-router';
import { describe, expect, it, vi } from 'vitest';

import { createShellBridge } from '@/test/shell-bridge';

import { guardPageAccess } from './route-guard';

describe('guardPageAccess', () => {
  it('lets an allowed page through untouched', () => {
    expect(() => guardPageAccess(createShellBridge(), 'automations')).not.toThrow();
  });

  it('redirects a denied page to the profile home', () => {
    const bridge = createShellBridge({
      isPageAllowed: vi.fn((page: string) => page !== 'automations'),
    });
    let thrown: unknown;
    try {
      guardPageAccess(bridge, 'automations');
    } catch (e) {
      thrown = e;
    }
    expect(isRedirect(thrown)).toBe(true);
  });

  it('NEVER redirects a denied page to itself — the infinite-loop pin', () => {
    // A deny-everything bridge falls back to Help. The guard must render that
    // denied fallback instead of redirecting it to itself forever.
    const bridge = createShellBridge({ isPageAllowed: vi.fn(() => false) });
    expect(() => guardPageAccess(bridge, 'help')).not.toThrow();
  });
});
