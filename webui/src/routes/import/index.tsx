import { createFileRoute, useNavigate } from '@tanstack/react-router';

import type { ImportInboxFilter } from './-import.types';

import { importInboxSearchSchema } from './-import.types';
import { Inbox } from './-ui/inbox';

export const Route = createFileRoute('/import/')({
  validateSearch: importInboxSearchSchema,
  component: InboxRoute,
});

function InboxRoute() {
  const navigate = useNavigate({ from: Route.fullPath });
  const { filter } = Route.useSearch();
  const setFilter = (next: ImportInboxFilter) => {
    void navigate({
      to: '/import',
      search: (prev) => ({ ...prev, filter: next }),
      replace: true,
    });
  };
  return <Inbox filter={filter} onFilterChange={setFilter} />;
}
