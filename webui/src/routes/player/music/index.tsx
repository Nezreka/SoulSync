import { useInfiniteQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';

import { apiClient } from '@/app/api-client';

import type { RailCard } from '../-lib/rails';
import type { LibAlbum, LibArtist, LibSong, Pagination } from '../-player.types';

import { runtime } from '../-lib/format';
import { libraryThumb, remoteImage } from '../-lib/images';
import { CardGrid, Empty, PageTitle, PlayButton, PlayerLink, Tabs, styles as s } from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

type MusicTab = 'artists' | 'albums' | 'songs';

export const Route = createFileRoute('/player/music/')({
  validateSearch: (search: Record<string, unknown>): { tab?: MusicTab } => {
    const tab = search.tab;
    return tab === 'albums' || tab === 'songs' || tab === 'artists' ? { tab } : {};
  },
  component: MusicBrowse,
});

const PAGE = 96;

function usePaged<T>(kind: 'artists' | 'albums' | 'songs', sort: string, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: ['player', 'music', 'browse', kind, sort],
    enabled,
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      apiClient
        .get(`library/v2/${kind}`, { searchParams: { page: pageParam, limit: PAGE, sort } })
        .json<Record<string, unknown> & { pagination?: Pagination }>(),
    getNextPageParam: (last) => (last.pagination?.has_next ? last.pagination.page + 1 : undefined),
    select: (data) => ({
      items: data.pages.flatMap((p) => (p[kind] as T[] | undefined) ?? []),
      total: data.pages[0]?.pagination?.total_count ?? 0,
    }),
    staleTime: 2 * 60_000,
  });
}

function MoreButton({
  query,
}: {
  query: { hasNextPage: boolean; isFetchingNextPage: boolean; fetchNextPage: () => unknown };
}) {
  if (!query.hasNextPage) return null;
  return (
    <div className={s.rail}>
      <button
        type="button"
        className={s.btn}
        onClick={() => void query.fetchNextPage()}
        disabled={query.isFetchingNextPage}
        data-nav
      >
        {query.isFetchingNextPage ? 'Loading…' : 'Show more'}
      </button>
    </div>
  );
}

function MusicBrowse() {
  const sections = useSections();
  const { tab = 'artists' } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/music/' });
  const artists = usePaged<LibArtist>('artists', 'name', sections.music && tab === 'artists');
  const albums = usePaged<LibAlbum>('albums', 'added', sections.music && tab === 'albums');
  const songs = usePaged<LibSong>('songs', 'title', sections.music && tab === 'songs');

  if (!sections.music) return <NotPartOfProfile what="Music" single />;

  const artistCards: RailCard[] = (artists.data?.items ?? []).map((a) => ({
    key: `ar${a.id}`,
    title: a.name,
    subtitle: a.album_count ? `${a.album_count} album${a.album_count === 1 ? '' : 's'}` : undefined,
    art: libraryThumb(a.image_url) ?? remoteImage(a.remote_image_url),
    href: `/player/music/artist/${a.id}`,
    shape: 'square',
  }));
  const albumCards: RailCard[] = (albums.data?.items ?? []).map((a) => ({
    key: `al${a.id}`,
    title: a.title,
    subtitle: [a.artist_name, a.year].filter(Boolean).join(' · '),
    art: libraryThumb(a.image_url) ?? remoteImage(a.remote_image_url),
    href: `/player/music/album/${a.id}`,
    shape: 'square',
  }));

  const current = tab === 'artists' ? artists : tab === 'albums' ? albums : songs;
  const count = current.data?.total ?? 0;

  return (
    <>
      <PageTitle>Music</PageTitle>
      <div className={s.toolbar}>
        <Tabs
          label="Browse music"
          value={tab}
          options={[
            { value: 'artists', label: 'Artists' },
            { value: 'albums', label: 'Albums' },
            { value: 'songs', label: 'Songs' },
          ]}
          onChange={(next) => void navigate({ search: { tab: next }, replace: true })}
        />
        {count ? (
          <span className={s.notYet}>
            {count.toLocaleString()} {tab}
          </span>
        ) : null}
      </div>

      {current.isLoading ? <div className={s.skeleton} style={{ height: '40vh' }} /> : null}
      {current.isError ? (
        <Empty title="Could not load your music">Try again in a moment.</Empty>
      ) : null}

      {tab === 'artists' && artists.data ? (
        artistCards.length ? (
          <CardGrid cards={artistCards} shape="square" round />
        ) : (
          <Empty title="No artists yet">Artists you add in SoulSync show up here.</Empty>
        )
      ) : null}
      {tab === 'albums' && albums.data ? (
        albumCards.length ? (
          <CardGrid cards={albumCards} shape="square" />
        ) : (
          <Empty title="No albums yet">Albums you add in SoulSync show up here.</Empty>
        )
      ) : null}
      {tab === 'songs' && songs.data ? (
        songs.data.items.length ? (
          <>
            <div className={s.heroActions}>
              <PlayButton label="Play all" />
            </div>
            <div className={s.list}>
              {songs.data.items.map((song, index) => (
                <PlayerLink
                  key={String(song.id)}
                  href={song.album_id ? `/player/music/album/${song.album_id}` : '/player/music'}
                  className={s.row}
                  data-nav
                >
                  <span className={s.rowNum}>{index + 1}</span>
                  <span className={s.rowMain}>
                    <span className={s.rowTitle}>{song.title}</span>
                    <span className={s.rowSub}>
                      {[song.artist_name, song.album_title].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  <span className={s.rowEnd}>{runtime((song.duration ?? 0) / 1000)}</span>
                </PlayerLink>
              ))}
            </div>
          </>
        ) : (
          <Empty title="No songs yet">Songs you add in SoulSync show up here.</Empty>
        )
      ) : null}

      <MoreButton query={current} />
    </>
  );
}
