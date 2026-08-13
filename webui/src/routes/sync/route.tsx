import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { SyncPage } from './-ui/sync-page';

/**
 * No loader. Every panel fetches on mount, and only the tab you open mounts at
 * all — which is what the vanilla's one-shot per-tab load flags amounted to.
 * Warming anything here would gate the whole page on a backend the vanilla was
 * happy to render around.
 *
 * The sequential-sync run deliberately does NOT live in route state: it sits in
 * a module-scoped store (-sync.use-sequential.ts) so navigating away mid-sync
 * does not cancel it, matching the vanilla's engine-owned manager.
 */
export const Route = createFileRoute('/sync')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'sync');
  },
  component: SyncPage,
});
