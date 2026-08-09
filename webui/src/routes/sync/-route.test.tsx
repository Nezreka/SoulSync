/**
 * The route definition. Small, but not nothing: the guard is what stops a
 * profile without sync access from reaching the page, and it is easy to write
 * a route that renders correctly and guards nothing.
 */

import { describe, expect, it, vi } from 'vitest';

import { SyncPage } from './-ui/sync-page';
import { Route } from './route';

vi.mock('@/platform/shell/route-guard', () => ({
  guardPageAccess: vi.fn(),
}));

import { guardPageAccess } from '@/platform/shell/route-guard';

describe('the sync route', () => {
  it('renders the page component', () => {
    // The PATH is not asserted here: createFileRoute takes it from the file
    // location and the router plugin writes it into routeTree.gen.ts, so it is
    // undefined on the route object itself. The build regenerating that tree
    // with a /sync entry is what proves the binding.
    expect(Route.options.component).toBe(SyncPage);
  });

  it('guards the page id — not some other page id', () => {
    // Passing the wrong id here would guard the wrong permission and let a
    // profile without sync access straight in.
    const bridge = {} as never;
    Route.options.beforeLoad?.({ context: { shell: { bridge } } } as never);
    expect(guardPageAccess).toHaveBeenCalledWith(bridge, 'sync');
  });

  it('declares no loader — every panel fetches on mount', () => {
    // A loader would gate the whole page on a backend the vanilla rendered
    // around, and only the open tab mounts anything at all.
    expect(Route.options.loader).toBeUndefined();
  });
});
