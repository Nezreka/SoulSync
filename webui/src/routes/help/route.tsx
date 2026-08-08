import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { registerDocsNavigationBridge } from './-help.navigate';
import { HelpPage } from './-ui/help-page';

// The route module is imported eagerly by the generated route tree, so the
// app-wide `window.navigateToDocsSection` contract (toast "Learn more →"
// links, notification panel, helper-mode deep links — all still vanilla)
// exists as soon as the React bundle boots, the same timing the legacy
// docs.js script tag gave those callers.
registerDocsNavigationBridge();

/**
 * No loader: the document is fully static (DOCS_SECTIONS ships in the
 * bundle), and the only network call on this page — Copy Debug Info — is
 * click-driven. The legacy page rendered everything synchronously from its
 * init; warming anything here would gate a static document on nothing.
 */
export const Route = createFileRoute('/help')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'help');
  },
  component: HelpPage,
});
