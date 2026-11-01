import { createFileRoute } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { resetPersistedBasicSearch } from './-basic.use-controller';
import { resetPersistedSearch } from './-search.use-controller';
import { SearchPage } from './-ui/search-page';

declare global {
  interface Window {
    __soulsyncFreshNav?: string | null;
  }
}

function startFresh() {
  window.__soulsyncFreshNav = null;
  resetPersistedSearch();
  resetPersistedBasicSearch();
}

/**
 * The last search survives leaving the page, so Back lands on it again. The
 * sidebar's Search button means a new search though (#1937): it opens the page
 * empty, and clicking it while already here clears it too.
 */
function SearchRoute() {
  const [entry, setEntry] = useState(() => {
    if (window.__soulsyncFreshNav === 'search') startFresh();
    return 0;
  });

  useEffect(() => {
    const onFreshNav = (event: Event) => {
      if ((event as CustomEvent<{ pageId?: string }>).detail?.pageId !== 'search') return;
      startFresh();
      setEntry((n) => n + 1);
    };
    window.addEventListener('soulsync:fresh-nav', onFreshNav);
    return () => window.removeEventListener('soulsync:fresh-nav', onFreshNav);
  }, []);

  return <SearchPage key={entry} />;
}

/**
 * The Search page.
 *
 * No loader: nothing is worth fetching before the user types. The controller
 * runs its own init on mount (/status then config-status) because the source
 * picker's initial state depends on both, and a search only happens once there
 * is a query.
 */
export const Route = createFileRoute('/search')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'search');
  },
  component: SearchRoute,
});
