import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router';
import { useState } from 'react';

import { apiClient } from '@/app/api-client';

import type { RailCard } from '../-lib/rails';
import type { LibAlbum, LibArtist, LibSong, Pagination } from '../-player.types';

import { albumArtwork, libraryThumb, remoteImage } from '../-lib/images';
import { songItems } from '../-lib/play-items';
import {
  discoverPlaylistsQuery,
  musicChannelsQuery,
  playlistsQuery,
} from '../-player.api';
import {
  CardGrid,
  CardRail,
  Empty,
  PageTitle,
  PlayButton,
  Tabs,
  useViewMode,
  ViewToggle,
  styles as s,
} from '../-ui/kit';
import { DiscoverCard } from '../-ui/music/discover-card';
import m from '../-ui/music/music.module.css';
import { PlaylistCard } from '../-ui/music/playlist-card';
import { TrackList } from '../-ui/music/track-list';
import { NotPartOfProfile } from '../-ui/not-part';
import { getEngine, playItems } from '../-ui/playback/use-player';
import { useSections } from '../-ui/sections';

type MusicTab = 'foryou' | 'artists' | 'albums' | 'songs' | 'playlists';

export const Route = createFileRoute('/player/music/')({
  validateSearch: (search: Record<string, unknown>): { tab?: MusicTab } => {
    const tab = search.tab;
    return tab === 'foryou' ||
      tab === 'albums' ||
      tab === 'songs' ||
      tab === 'artists' ||
      tab === 'playlists'
      ? { tab }
      : {};
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
  const [view, setView] = useViewMode('music');
  const { tab = 'foryou' } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/music/' });
  const [albumSort, setAlbumSort] = useState<'added' | 'title' | 'year'>('added');

  const needArtists = sections.music && (tab === 'artists' || tab === 'foryou');
  const needAlbums = sections.music && (tab === 'albums' || tab === 'foryou');
  const needSongs = sections.music && (tab === 'songs' || tab === 'foryou');

  const artists = usePaged<LibArtist>('artists', 'name', needArtists);
  const albums = usePaged<LibAlbum>('albums', tab === 'albums' ? albumSort : 'added', needAlbums);
  const songs = usePaged<LibSong>('songs', 'title', needSongs);
  const discover = useQuery({
    ...discoverPlaylistsQuery(),
    enabled: sections.music && tab === 'foryou',
  });
  const musicStations = useQuery({
    ...musicChannelsQuery(),
    enabled: sections.music && tab === 'foryou',
  });
  const playlists = useQuery({
    ...playlistsQuery(),
    enabled: sections.music && (tab === 'foryou' || tab === 'playlists'),
  });

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
    play: { kind: 'album' as const, id: Number(a.id) },
    shape: 'square',
  }));

  const current =
    tab === 'artists' ? artists : tab === 'albums' ? albums : tab === 'songs' ? songs : null;
  const count = current?.data?.total ?? 0;

  const playLibraryShuffle = () => {
    const pool = songs.data?.items ?? [];
    if (!pool.length) return;
    const queue = songItems(pool);
    playItems(queue, Math.floor(Math.random() * queue.length));
    getEngine().toggleShuffle();
  };

  const playStation = (station: {
    id: string;
    name: string;
    tracks: [number, string, string, number, number][];
  }) => {
    if (!station.tracks.length) return;
    const queue = station.tracks.slice(0, 120).map(([tid, title, artist, seconds, albumId]) => ({
      type: 'track' as const,
      id: String(tid),
      title,
      subtitle: artist,
      artwork: albumId ? albumArtwork(albumId) : null,
      href: albumId ? `/player/music/album/${albumId}` : undefined,
      duration: seconds || null,
      parentId: albumId ? String(albumId) : undefined,
    }));
    playItems(queue, Math.floor(Math.random() * queue.length));
    getEngine().toggleShuffle();
  };

  const discoverList = (discover.data ?? []).flatMap((sec) => sec.playlists);
  const allPlaylists = [
    ...(playlists.data?.yours ?? []),
    ...(playlists.data?.mirrored ?? []),
  ];

  return (
    <>
      <PageTitle>Music</PageTitle>
      <div className={s.toolbar}>
        <Tabs
          label="Browse music"
          value={tab}
          options={[
            { value: 'foryou', label: 'For you' },
            { value: 'artists', label: 'Artists' },
            { value: 'albums', label: 'Albums' },
            { value: 'songs', label: 'Songs' },
            { value: 'playlists', label: 'Playlists' },
          ]}
          onChange={(next) => void navigate({ search: { tab: next }, replace: true })}
        />
        <div className={s.fieldRow}>
          {tab === 'albums' ? (
            <div style={{ display: 'flex', gap: 6 }}>
              {(
                [
                  ['added', 'Recent'],
                  ['title', 'A–Z'],
                  ['year', 'Year'],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={s.filterPill}
                  aria-pressed={albumSort === key}
                  onClick={() => setAlbumSort(key)}
                  data-nav
                >
                  {label}
                </button>
              ))}
            </div>
          ) : null}
          {count ? (
            <span className={s.notYet}>
              {count.toLocaleString()} {tab}
            </span>
          ) : null}
          {tab === 'artists' || tab === 'albums' ? (
            <ViewToggle value={view} onChange={setView} />
          ) : null}
        </div>
      </div>

      {tab === 'foryou' ? (
        <>
          {/* Quick Mixes & Radio Stations (Plexamp-grade 1-click listening) */}
          <section className={s.rail} aria-label="Stations and mixes">
            <div className={s.railHead}>
              <h2 className={s.sectionTitle}>Stations &amp; Instant Mixes</h2>
            </div>
            <div className={m.radioStrip}>
              <button
                type="button"
                className={m.radioCard}
                data-tone="accent"
                onClick={playLibraryShuffle}
                data-nav
              >
                <span className={m.radioKicker}>Instant Radio</span>
                <div>
                  <div className={m.radioName}>Library Shuffle</div>
                  <div className={m.radioMeta}>
                    {songs.data?.total
                      ? `${songs.data.total.toLocaleString()} songs`
                      : 'Shuffle your music'}
                  </div>
                </div>
                <span className={m.radioOrb} aria-hidden="true">
                  ▶
                </span>
              </button>
              {(musicStations.data ?? []).slice(0, 7).map((st) => (
                <button
                  key={st.id}
                  type="button"
                  className={m.radioCard}
                  data-tone={st.kind === 'vibe' ? 'vibe' : undefined}
                  onClick={() => playStation(st)}
                  data-nav
                >
                  <span className={m.radioKicker}>
                    {st.kind === 'vibe' ? 'Mood Radio' : 'Genre Radio'}
                  </span>
                  <div>
                    <div className={m.radioName}>{st.name}</div>
                    <div className={m.radioMeta}>{st.count.toLocaleString()} songs</div>
                  </div>
                  <span className={m.radioOrb} aria-hidden="true">
                    ▶
                  </span>
                </button>
              ))}
            </div>
          </section>

          {discoverList.length > 0 ? (
            <section className={s.rail} aria-label="Made for you">
              <div className={s.railHead}>
                <h2 className={s.sectionTitle}>Made for you</h2>
              </div>
              <div className={s.cards}>
                {discoverList.slice(0, 12).map((pl) => (
                  <DiscoverCard key={pl.key} card={pl} />
                ))}
              </div>
            </section>
          ) : null}

          <CardRail
            title="Recently added albums"
            cards={albumCards.slice(0, 20)}
            more={{ to: '/player/music?tab=albums', label: 'All albums' }}
          />

          <CardRail
            title="Artists in your library"
            cards={artistCards.slice(0, 20)}
            round
            more={{ to: '/player/music?tab=artists', label: 'All artists' }}
          />

          {allPlaylists.length > 0 ? (
            <section className={s.rail} aria-label="Your playlists">
              <div className={s.railHead}>
                <h2 className={s.sectionTitle}>Your playlists</h2>
                <Link to="/player/music/playlists" className={s.railMore} data-nav>
                  All playlists
                </Link>
              </div>
              <div className={s.cards}>
                {allPlaylists.slice(0, 12).map((pl) => (
                  <PlaylistCard key={pl.id} playlist={pl} />
                ))}
              </div>
            </section>
          ) : null}
        </>
      ) : null}

      {current?.isLoading ? <div className={s.skeleton} style={{ height: '40vh' }} /> : null}
      {current?.isError ? (
        <Empty title="Could not load your music">Try again in a moment.</Empty>
      ) : null}

      {tab === 'artists' && artists.data ? (
        artistCards.length ? (
          <CardGrid cards={artistCards} shape="square" round view={view} />
        ) : (
          <Empty title="No artists yet">Artists you add in SoulSync show up here.</Empty>
        )
      ) : null}

      {tab === 'albums' && albums.data ? (
        albumCards.length ? (
          <CardGrid cards={albumCards} shape="square" view={view} />
        ) : (
          <Empty title="No albums yet">Albums you add in SoulSync show up here.</Empty>
        )
      ) : null}

      {tab === 'songs' && songs.data ? (
        songs.data.items.length ? (
          <>
            <div className={s.heroActions} style={{ marginTop: 8 }}>
              <PlayButton
                label="Play all"
                onPlay={() => playItems(songItems(songs.data!.items))}
              />
              <PlayButton
                label="Shuffle"
                primary={false}
                onPlay={playLibraryShuffle}
              />
            </div>
            <div className={m.albumTrackCard}>
              <TrackList
                label="Songs"
                showArt
                showAlbum
                onPlay={(index) => playItems(songItems(songs.data!.items), index)}
                rows={songs.data.items.map((song, index) => ({
                  key: String(song.id),
                  title: song.title,
                  number: index + 1,
                  trackId: Number(song.id),
                  artist: song.artist_name ?? undefined,
                  album: song.album_title ?? undefined,
                  albumHref: song.album_id ? `/player/music/album/${song.album_id}` : undefined,
                  art: song.album_id
                    ? albumArtwork(song.album_id)
                    : libraryThumb(song.album_thumb_url),
                  duration: song.duration ? song.duration / 1000 : null,
                  song: {
                    title: song.title,
                    artist: song.artist_name ?? '',
                    album: song.album_title ?? undefined,
                    duration_ms: song.duration ?? null,
                    lib2_track_id: Number(song.id),
                  },
                }))}
              />
            </div>
          </>
        ) : (
          <Empty title="No songs yet">Songs you add in SoulSync show up here.</Empty>
        )
      ) : null}

      {tab === 'playlists' ? (
        playlists.isLoading ? (
          <div className={s.skeleton} style={{ height: '36vh' }} />
        ) : allPlaylists.length ? (
          <div className={s.grid} style={{ marginTop: 12 }}>
            {allPlaylists.map((pl) => (
              <PlaylistCard key={pl.id} playlist={pl} />
            ))}
          </div>
        ) : (
          <Empty title="No playlists yet">
            Playlists you create or mirror in SoulSync show up here.
          </Empty>
        )
      ) : null}

      {current ? <MoreButton query={current} /> : null}
    </>
  );
}
