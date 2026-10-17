import { createFileRoute, redirect } from '@tanstack/react-router';

// Library V2 is now simply "Library" and lives at /library. This route exists
// only so the links people already have -- bookmarks, browser history, an open
// tab from before the update -- keep working. The whole query string travels
// along, so a bookmarked album, filter or discovery view lands where it used to.
export const Route = createFileRoute('/library-v2')({
  beforeLoad: ({ location }) => {
    throw redirect({ href: `/library${location.searchStr}`, replace: true });
  },
});
