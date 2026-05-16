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
  loader: async ({ context }) => {
    await context.queryClient.ensureQueryData(importStagingFilesQueryOptions());
  },
  component: ImportPage,
});
