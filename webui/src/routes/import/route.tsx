import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { importInboxQueryOptions } from './-import.api';
import { ImportPage } from './-ui/import-page';

export const Route = createFileRoute('/import')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'import');
  },
  loader: ({ context }) => {
    // Warm the inbox if possible, but never block the route on a transient fetch
    // failure. The page owns the in-place error state for that case.
    void context.queryClient.prefetchQuery(importInboxQueryOptions());
  },
  component: ImportPage,
});
