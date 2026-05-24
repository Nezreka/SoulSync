import { createFileRoute, redirect } from '@tanstack/react-router';

export const Route = createFileRoute('/import/')({
  beforeLoad: () => {
    throw redirect({ to: '/import/album', replace: true });
  },
});
