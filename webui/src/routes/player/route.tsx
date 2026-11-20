import { createFileRoute } from '@tanstack/react-router';

import { PlayerShell } from './-ui/shell';

/**
 * /player: the player app. its own full-screen frame over the manager, which
 * stays mounted and unchanged underneath. each section checks the profile's
 * media access itself, so the route is open to every signed-in profile.
 */
export const Route = createFileRoute('/player')({
  component: PlayerRoute,
});

function PlayerRoute() {
  const { shell } = Route.useRouteContext();
  return <PlayerShell profileId={shell.profile?.profileId ?? null} />;
}
