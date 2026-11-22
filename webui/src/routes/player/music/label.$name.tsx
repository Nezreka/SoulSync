import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import { appURL } from '@/platform/url-base';

import type { RailCard } from '../-lib/rails';

import { plural } from '../-lib/format';
import { recentAlbumCards } from '../-lib/rails';
import { albumsQuery } from '../-player.api';
import { CollectionPage } from '../-ui/collection';
import { CardRail } from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/music/label/$name')({
  component: MusicLabelPage,
});

function MusicLabelPage() {
  const sections = useSections();
  const { name } = Route.useParams();
  const albums = useQuery({
    ...albumsQuery({ label: name, limit: 300, sort: 'year_desc' }),
    enabled: sections.music,
  });
  if (!sections.music) return <NotPartOfProfile what="Music" single />;
  const rows = albums.data?.albums ?? [];
  const total = albums.data?.pagination?.total_count ?? rows.length;
  // the artists come from the releases, most releases first
  const counts = new Map<number, { name: string; n: number }>();
  for (const a of rows) {
    if (!a.artist_id) continue;
    const entry = counts.get(a.artist_id) ?? { name: a.artist_name ?? '', n: 0 };
    entry.n += 1;
    counts.set(a.artist_id, entry);
  }
  const artists: RailCard[] = [...counts.entries()]
    .sort((a, b) => b[1].n - a[1].n)
    .slice(0, 30)
    .map(([id, e]) => ({
      key: `ar${id}`,
      title: e.name,
      subtitle: plural(e.n, 'release'),
      art: appURL(`/api/library/v2/artwork/artist/${id}?size=thumb`),
      href: `/player/music/artist/${id}`,
      shape: 'square',
    }));
  const lead = rows.find((a) => a.image_url || a.remote_image_url);
  return (
    <CollectionPage
      kicker="Label"
      title={name}
      meta={
        <>
          <span>{plural(total, 'release')}</span>
          {counts.size ? <span>{plural(counts.size, 'artist')}</span> : null}
        </>
      }
      backdrop={lead ? (lead.image_url ?? lead.remote_image_url) : null}
      above={<CardRail title="Artists" cards={artists} round />}
      cards={recentAlbumCards(rows, rows.length)}
      shape="square"
      loading={albums.isLoading}
      empty={`Nothing in your library is filed under ${name}.`}
    />
  );
}
