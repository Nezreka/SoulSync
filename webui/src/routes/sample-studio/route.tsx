import { createFileRoute } from '@tanstack/react-router';

import { LegacyRouteController } from '@/platform/shell/route-controllers';
import { guardPageAccess } from '@/platform/shell/route-guard';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

import { SampleStudioPage } from './-ui/sample-studio-page';

/**
 * Sample Studio — the library-powered sampler page (Phase 2: page shell +
 * waveform editor; saving/stems land in later phases).
 *
 * Follows the library route's handover pattern: the route exists before the
 * vanilla shell hands the page over, so a legacy shell keeps serving the old
 * page until the manifest flips to 'react'.
 */
function isReactOwned(): boolean {
  return getShellRouteByPageId('sample-studio')?.kind === 'react';
}

export const Route = createFileRoute('/sample-studio')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'sample-studio');
  },
  component: SampleStudioRouteComponent,
});

function SampleStudioRouteComponent() {
  if (!isReactOwned()) {
    return <LegacyRouteController pathname="/sample-studio" />;
  }
  return <SampleStudioPage />;
}
