import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import { videoCatalogQuery } from '../-player.api';
import { MakerPage } from '../-ui/maker';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/movies/studio/$name')({
  component: StudioPage,
});

function StudioPage() {
  const sections = useSections();
  const { name } = Route.useParams();
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.movies });
  const all = useMemo(() => catalog.data?.movies ?? [], [catalog.data]);
  const mine = useMemo(
    () => all.filter((m) => (m.studio ?? '').toLowerCase() === name.toLowerCase()),
    [all, name],
  );
  if (!sections.movies) return <NotPartOfProfile what="Movies" />;
  return <MakerPage kind="studio" name={name} mine={mine} all={all} />;
}
