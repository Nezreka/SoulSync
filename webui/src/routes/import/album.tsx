import { createFileRoute } from '@tanstack/react-router';

import {
  importStagingGroupsQueryOptions,
  importStagingSuggestionsQueryOptions,
} from './-import.api';
import { AlbumImportTab } from './-ui/album-import-tab';

export const Route = createFileRoute('/import/album')({
  loader: async ({ context }) => {
    void context.queryClient.prefetchQuery(importStagingGroupsQueryOptions());
    void context.queryClient.prefetchQuery(importStagingSuggestionsQueryOptions());
  },
  component: AlbumImportTab,
});
