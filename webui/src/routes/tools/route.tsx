import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { ToolsPage } from './-ui/tools-page';

/**
 * No loader. Every card on this page hydrates itself on mount — the database
 * stats, the active media server, the backup list, the maintenance jobs — which
 * is exactly what the vanilla did from its own DOMContentLoaded handlers. Warming
 * any of it here would gate the whole page on a backend that the vanilla was
 * happy to render around.
 */
export const Route = createFileRoute('/tools')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'tools');
  },
  component: ToolsPage,
});
