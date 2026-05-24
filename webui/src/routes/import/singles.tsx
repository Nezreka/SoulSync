import { createFileRoute } from '@tanstack/react-router';

import { SinglesImportTab } from './-ui/singles-import-tab';

export const Route = createFileRoute('/import/singles')({
  component: SinglesImportTab,
});
