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
    // The test bridge's home page is 'discover'. A deny-everything bridge once
    // sent every gated route home to /discover, whose own gate denied it and
    // redirected home to /discover, forever — the router allocated ~1MB/s and
    // the worker died. Two route files ground for 28 minutes each in CI
    // because of this; the guard must render the denied home page instead.
    const bridge = createShellBridge({ isPageAllowed: vi.fn(() => false) });
    expect(() => guardPageAccess(bridge, 'discover')).not.toThrow();
  });
});
