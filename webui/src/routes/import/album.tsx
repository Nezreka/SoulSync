import { createFileRoute, redirect } from '@tanstack/react-router';

// the old tab. bookmarks and the guided tour still point here; the inbox
// replaced all three tabs with one list.
export const Route = createFileRoute('/import/album')({
  beforeLoad: () => {
    throw redirect({ to: '/import', replace: true });
  },
});
