import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { ReviewPage } from './-ui/review-page';

export const Route = createFileRoute('/review')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'tools');
  },
  component: ReviewPage,
});
