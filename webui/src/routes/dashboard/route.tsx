import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { DashboardPage } from './-ui/dashboard-page';

/**
 * No loader — every card hydrates itself on mount (the socket seam plus its
 * own fallback fetch), exactly as the vanilla loadDashboardData fired its
 * loads in parallel and rendered around whatever hadn't answered yet.
 */
export const Route = createFileRoute('/dashboard')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'dashboard');
  },
  component: DashboardPage,
});
