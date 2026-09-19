import { createFileRoute } from '@tanstack/react-router';

import { Matcher } from './-ui/matcher';

export const Route = createFileRoute('/import/match/$key')({
  component: MatchRoute,
});

function MatchRoute() {
  const { key } = Route.useParams();
  return <Matcher itemKey={key} />;
}
