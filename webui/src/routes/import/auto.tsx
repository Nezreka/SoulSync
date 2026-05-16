import { useNavigate } from '@tanstack/react-router';
import { createFileRoute } from '@tanstack/react-router';

import type { ImportAutoFilter } from './-import.types';

import { importAutoSearchSchema } from './-import.types';
import { AutoImportPanel } from './-ui/auto-import-tab';

export const Route = createFileRoute('/import/auto')({
  validateSearch: importAutoSearchSchema,
  component: AutoImportRoute,
});

function AutoImportRoute() {
  const navigate = useNavigate({ from: Route.fullPath });
  const { autoFilter } = Route.useSearch();

  const setAutoFilter = (nextFilter: ImportAutoFilter) => {
    void navigate({
      to: Route.fullPath,
      search: (prev) => ({ ...prev, autoFilter: nextFilter }),
      replace: true,
    });
  };

  return <AutoImportPanel autoFilter={autoFilter} onFilterChange={setAutoFilter} />;
}
