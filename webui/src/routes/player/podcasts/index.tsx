import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import type { RailCard } from '../-lib/rails';

import { podcastSubsQuery } from '../-player.api';
import { CardGrid, Empty, PageTitle, styles as s } from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/podcasts/')({
  component: PodcastsBrowse,
});

function PodcastsBrowse() {
  const sections = useSections();
  const subs = useQuery({ ...podcastSubsQuery(), enabled: sections.podcasts });

  if (!sections.podcasts) return <NotPartOfProfile what="Podcasts" />;

  const cards: RailCard[] = (subs.data ?? []).map((p) => ({
    key: `p${p.id}`,
    title: p.title,
    subtitle: p.author ?? undefined,
    art: p.artwork_url,
    href: `/player/podcasts/${p.id}`,
    shape: 'square',
  }));

  return (
    <>
      <PageTitle>Podcasts</PageTitle>
      {subs.isLoading ? <div className={s.skeleton} style={{ height: '40vh' }} /> : null}
      {subs.data && !cards.length ? (
        <Empty title="No podcasts yet">
          Follow a podcast in SoulSync and its episodes show up here.
        </Empty>
      ) : null}
      {cards.length ? <CardGrid cards={cards} shape="square" /> : null}
    </>
  );
}
