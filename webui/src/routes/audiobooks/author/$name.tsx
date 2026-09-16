import { createFileRoute, useParams } from '@tanstack/react-router';

import { AudiobookPersonPage } from '../-ui/audiobook-person-page';

/**
 * Keyed on the name, not on Audible's author ASIN.
 *
 * The ASIN exists on every product and looks like the right identifier, but the
 * catalogue accepts author_asin as a filter and then ignores it, returning the
 * unfiltered storefront — the same trap series_asin sets. Name search, by
 * contrast, is exact.
 */
export const Route = createFileRoute('/audiobooks/author/$name')({
  component: AuthorRouteComponent,
});

function AuthorRouteComponent() {
  const { name } = useParams({ from: '/audiobooks/author/$name' });
  return <AudiobookPersonPage name={name} role="author" />;
}
