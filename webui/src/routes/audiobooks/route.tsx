import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { AudiobookProvider } from './-ui/audiobook-context';
import { AudiobooksLayout } from './-ui/audiobooks-layout';

export const Route = createFileRoute('/audiobooks')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'audiobooks');
  },
  component: AudiobooksRouteComponent,
});

function AudiobooksRouteComponent() {
  return (
    <AudiobookProvider>
      <AudiobooksLayout />
    </AudiobookProvider>
  );
}
