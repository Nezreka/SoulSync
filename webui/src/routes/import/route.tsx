import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';

import { importStagingFilesQueryOptions } from './-import.api';
import { ImportPage } from './-ui/import-page';

export const Route = createFileRoute('/import')({
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('import')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loader: ({ context }) => {
    // Warm the staging query if possible, but never block the route on a transient fetch
    // failure. The page owns the in-place error state for that case.
    void context.queryClient.prefetchQuery(importStagingFilesQueryOptions());
  },
  component: ImportPage,
});
