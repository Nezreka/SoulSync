import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import type { RailCard } from '../-lib/rails';
import type { LibRelease } from '../-player.types';

import { plainText } from '../-lib/format';
import { ownedReleases } from '../-lib/music';
import { artistQuery } from '../-player.api';
import { CardRail, Empty, Hero, PlayButton, styles as s } from '../-ui/kit';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/music/artist/$artistId')({
  component: ArtistPage,
});

function releaseCards(releases: LibRelease[]): RailCard[] {
  return releases.map((r) => ({
    key: `rel${r.id}`,
    title: r.title,
    subtitle: r.year ? String(r.year) : undefined,
    art: r.image_url ?? r.remote_image_url,
    href: `/player/music/album/${r.id}`,
    shape: 'square',
  }));
}

function ArtistPage() {
  const sections = useSections();
  const id = Number(Route.useParams().artistId);
  const artist = useQuery({ ...artistQuery(id), enabled: sections.music && Number.isFinite(id) });

  if (!sections.music) return <NotPartOfProfile what="Music" single />;
  if (artist.isLoading)
    return <div className={s.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  const a = artist.data;
  if (!a) return <NotFound what="Artist" />;

  const albums = ownedReleases(a.albums);
  const eps = ownedReleases(a.eps);
  const singles = ownedReleases(a.singles);
  const appears = ownedReleases(a.appears_on);
  const about = plainText(a.about?.bio) || plainText(a.summary);
  const image = a.image_url ?? a.remote_image_url;
  // no photo of the artist: their newest cover sets the scene instead
  const scene =
    image ??
    [...albums, ...eps, ...singles].map((r) => r.image_url ?? r.remote_image_url).find(Boolean);

  return (
    <>
      <Hero
        backdrop={scene}
        poster={image}
        posterShape="square"
        posterRound
        kicker="Artist"
        title={a.name}
        meta={(a.genres ?? []).slice(0, 4).map((g) => (
          <span key={g}>{g}</span>
        ))}
        overview={about}
        actions={
          <>
            <PlayButton />
            <PlayButton label="Shuffle" primary={false} />
          </>
        }
      />
      <CardRail title="Albums" cards={releaseCards(albums)} />
      <CardRail title="EPs" cards={releaseCards(eps)} />
      <CardRail title="Singles" cards={releaseCards(singles)} />
      <CardRail title="Appears on" cards={releaseCards(appears)} />
      {!albums.length && !eps.length && !singles.length && !appears.length ? (
        <div className={s.rail}>
          <Empty title="Nothing by this artist on disk yet">
            Releases show up here once at least one of their tracks is in your library.
          </Empty>
        </div>
      ) : null}
    </>
  );
}
