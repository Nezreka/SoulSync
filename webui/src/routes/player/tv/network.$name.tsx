import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import { videoCatalogQuery } from '../-player.api';
import { MakerPage } from '../-ui/maker';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/tv/network/$name')({
  component: NetworkPage,
});

function NetworkPage() {
  const sections = useSections();
  const { name } = Route.useParams();
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.tv });
  const all = useMemo(() => catalog.data?.shows ?? [], [catalog.data]);
  const mine = useMemo(
    () => all.filter((x) => (x.network ?? '').toLowerCase() === name.toLowerCase()),
    [all, name],
  );
  if (!sections.tv) return <NotPartOfProfile what="TV shows" />;
  return <MakerPage kind="network" name={name} mine={mine} all={all} />;
}
