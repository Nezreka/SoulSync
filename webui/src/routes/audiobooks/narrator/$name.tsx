import { createFileRoute, useParams } from '@tanstack/react-router';

import { AudiobookPersonPage } from '../-ui/audiobook-person-page';

/** Narrators are only ever indexed by name — Audible gives them no ASIN at all. */
export const Route = createFileRoute('/audiobooks/narrator/$name')({
  component: NarratorRouteComponent,
});

function NarratorRouteComponent() {
  const { name } = useParams({ from: '/audiobooks/narrator/$name' });
  return <AudiobookPersonPage name={name} role="narrator" />;
}
