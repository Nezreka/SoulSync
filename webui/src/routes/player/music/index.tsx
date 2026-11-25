import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo, useState, type ReactNode } from 'react';

import { apiClient } from '@/app/api-client';

import type { RailCard } from '../-lib/rails';
import type { LibAlbum, LibArtist, LibSong, Pagination } from '../-player.types';

import { albumArtwork, libraryThumb, remoteImage } from '../-lib/images';
import { songItems } from '../-lib/play-items';
import { dayKey, hashSeed, seededShuffle } from '../-lib/schedule';
import {
  discoverPlaylistsQuery,
  musicChannelsQuery,
  musicListeningQuery,
  playlistsQuery,
} from '../-player.api';
import {
  CardGrid,
  Empty,
  PageTitle,
  PlayButton,
  Rail,
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
import { StyledRow, usePageRows } from '../-ui/page-rows';
import { getEngine, playItems } from '../-ui/playback/use-player';
import { useSections } from '../-ui/sections';

/** the rows the page shows before its layout has loaded: the defaults that are on */
const MUSIC_ROWS = [
  'recently-played',
  'stations',
  'discovery',
  'heavy-rotation',
  'recent-albums',
  'artists',
  'playlists',
] as const;

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
    enabled: sections.music && (tab === 'foryou' || tab === 'playlists'),
  });
  const musicStations = useQuery({
    ...musicChannelsQuery(),
    enabled: sections.music && tab === 'foryou',
  });
  const playlists = useQuery({
    ...playlistsQuery(),
    enabled: sections.music && (tab === 'foryou' || tab === 'playlists'),
  });

  const { rows, editor, customize } = usePageRows({
    page: 'music',
    name: 'Music',
    fallback: MUSIC_ROWS,
  });
  const listensOn = rows.some((r) =>
    ['recently-played', 'heavy-rotation', 'top-albums', 'top-tracks', 'all-time-artists'].includes(
      r.id,
    ),
  );
  const listening = useQuery({
    ...musicListeningQuery(),
    enabled: sections.music && tab === 'foryou' && listensOn,
  });
  // a different handful of artists each day, from the ones loaded
  const dailyArtists = useMemo(
    () => seededShuffle(artists.data?.items ?? [], hashSeed(`music-artists|${dayKey(new Date())}`)),
    [artists.data],
  );

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
  const allPlaylists = [...(playlists.data?.yours ?? []), ...(playlists.data?.mirrored ?? [])];

  // every row of For you, by its layout id (core/player/page_layout.py)
  const albumCard = (
    a: {
      album_id: number;
      title: string;
      artist: string | null;
      image_url: string | null;
    },
    sub?: string,
  ): RailCard => ({
    key: `al${a.album_id}`,
    title: a.title,
    subtitle: sub ?? a.artist ?? undefined,
    art: libraryThumb(a.image_url),
    href: `/player/music/album/${a.album_id}`,
    play: { kind: 'album' as const, id: Number(a.album_id) },
    shape: 'square',
  });
  const plays = (n: number) => `${n.toLocaleString()} play${n === 1 ? '' : 's'}`;
  const artistRow = (list: NonNullable<typeof listening.data>['heavy_rotation']): RailCard[] =>
    list.map((a) => ({
      key: `ar${a.artist_id}`,
      title: a.name,
      subtitle: plays(a.plays),
      art: libraryThumb(a.image_url),
      href: `/player/music/artist/${a.artist_id}`,
      shape: 'square',
    }));
  const heard = listening.data;
  const drawn: ReactNode[] = [];
  if (tab === 'foryou')
    for (const r of rows) {
      const row = (
        title: string,
        cards: RailCard[],
        more?: { to: string; label?: string },
        round = false,
      ) =>
        drawn.push(
          <StyledRow
            key={r.id}
            style={r.style}
            title={title}
            cards={cards}
            more={more}
            round={round}
          />,
        );
      if (r.id === 'recently-played')
        row(
          'Recently played',
          (heard?.recent ?? []).map((a) => albumCard(a)),
        );
      else if (r.id === 'heavy-rotation')
        row(
          'Heavy rotation · last 30 days',
          artistRow(heard?.heavy_rotation ?? []),
          undefined,
          true,
        );
      else if (r.id === 'all-time-artists')
        row('Most played of all time', artistRow(heard?.all_time_artists ?? []), undefined, true);
      else if (r.id === 'top-albums')
        row(
          'Most played albums · last 30 days',
          (heard?.top_albums ?? []).map((a) =>
            albumCard(a, [a.artist, plays(a.plays ?? 0)].filter(Boolean).join(' · ')),
          ),
        );
      else if (r.id === 'top-tracks')
        row(
          'Most played tracks · last 30 days',
          (heard?.top_tracks ?? []).map((t) => ({
            ...albumCard(
              { album_id: t.album_id, title: t.title, artist: t.artist, image_url: t.image_url },
              [t.artist, plays(t.plays)].filter(Boolean).join(' · '),
            ),
            key: `tr${t.track_id}`,
          })),
        );
      else if (r.id === 'recent-albums')
        row('Recently added albums', albumCards.slice(0, 20), {
          to: '/player/music?tab=albums',
          label: 'All albums',
        });
      else if (r.id === 'artists')
        row(
          'Artists in your library',
          dailyArtists.slice(0, 20).map((a) => ({
            key: `ar${a.id}`,
            title: a.name,
            subtitle: a.album_count
              ? `${a.album_count} album${a.album_count === 1 ? '' : 's'}`
              : undefined,
            art: libraryThumb(a.image_url) ?? remoteImage(a.remote_image_url),
            href: `/player/music/artist/${a.id}`,
            shape: 'square' as const,
          })),
          { to: '/player/music?tab=artists', label: 'All artists' },
          true,
        );
      else if (r.id === 'stations')
        drawn.push(
          <div key="stations">
            <Rail title="Stations & Artist Mixes">
              <button
                type="button"
                className={m.mixCard}
                data-tone="accent"
                onClick={playLibraryShuffle}
                data-nav
              >
                <div className={m.mixCover}>
                  <div className={m.mixMosaic}>
                    {(albums.data?.items ?? []).slice(0, 4).map((al) => {
                      const art = libraryThumb(al.image_url) ?? remoteImage(al.remote_image_url);
                      return art ? (
                        <img key={al.id} src={art} alt="" loading="lazy" />
                      ) : (
                        <span key={al.id} />
                      );
                    })}
                  </div>
                  <div className={m.mixVignette} aria-hidden="true" />
                  <span className={m.mixBadge}>Library Radio</span>
                  <span className={m.mixPlayOrb} aria-hidden="true">
                    ▶
                  </span>
                </div>
                <div className={m.mixTitle}>Library Shuffle</div>
                <div className={m.mixSub}>
                  {songs.data?.total
                    ? `${songs.data.total.toLocaleString()} songs · all vibes`
                    : 'Shuffle your whole library'}
                </div>
              </button>
              {(musicStations.data ?? []).slice(0, 12).map((st) => {
                const distinctArtists: string[] = [];
                const seenArtists = new Set<string>();
                const mosaicUrls: string[] = [];
                const seenAlbums = new Set<number>();
                for (const [, , artist, , albumId] of st.tracks) {
                  const aName = artist?.trim();
                  if (
                    aName &&
                    !seenArtists.has(aName.toLowerCase()) &&
                    distinctArtists.length < 3
                  ) {
                    seenArtists.add(aName.toLowerCase());
                    distinctArtists.push(aName);
                  }
                  if (albumId && !seenAlbums.has(albumId) && mosaicUrls.length < 4) {
                    seenAlbums.add(albumId);
                    const artUrl = albumArtwork(albumId);
                    if (artUrl) mosaicUrls.push(artUrl);
                  }
                  if (distinctArtists.length >= 3 && mosaicUrls.length >= 4) break;
                }
                const artistHeadline =
                  distinctArtists.length >= 2
                    ? `${distinctArtists.slice(0, 2).join(', ')} & More`
                    : st.name;
                return (
                  <button
                    key={st.id}
                    type="button"
                    className={m.mixCard}
                    data-tone={st.kind === 'vibe' ? 'vibe' : undefined}
                    onClick={() => playStation(st)}
                    data-nav
                  >
                    <div className={m.mixCover}>
                      {mosaicUrls.length >= 4 ? (
                        <div className={m.mixMosaic}>
                          {mosaicUrls.map((u, idx) => (
                            <img key={idx} src={u} alt="" loading="lazy" />
                          ))}
                        </div>
                      ) : mosaicUrls[0] ? (
                        <img src={mosaicUrls[0]} alt="" className={m.mixSingleImg} loading="lazy" />
                      ) : null}
                      <div className={m.mixVignette} aria-hidden="true" />
                      <span className={m.mixBadge}>
                        {st.kind === 'vibe' ? `${st.name} Vibe` : `${st.name} Mix`}
                      </span>
                      <span className={m.mixPlayOrb} aria-hidden="true">
                        ▶
                      </span>
                    </div>
                    <div className={m.mixTitle}>{artistHeadline}</div>
                    <div className={m.mixSub}>
                      {st.name} · {st.count.toLocaleString()} songs
                    </div>
                  </button>
                );
              })}
            </Rail>
          </div>,
        );
      else if (r.id === 'discovery')
        drawn.push(
          <div key="discovery">
            {discoverList.length > 0 ? (
              <Rail
                title="Made for you · SoulSync Discovery"
                more={{ to: '/player/music?tab=playlists', label: `All ${discoverList.length}` }}
              >
                {discoverList.slice(0, 24).map((pl) => (
                  <DiscoverCard key={pl.key} card={pl} />
                ))}
              </Rail>
            ) : null}
          </div>,
        );
      else if (r.id === 'playlists')
        drawn.push(
          <div key="playlists">
            {allPlaylists.length > 0 ? (
              <Rail
                title="Your playlists"
                more={{ to: '/player/music/playlists', label: 'All playlists' }}
              >
                {allPlaylists.slice(0, 14).map((pl) => (
                  <PlaylistCard key={pl.id} playlist={pl} />
                ))}
              </Rail>
            ) : null}
          </div>,
        );
    }

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
          {drawn}
          {customize}
          {editor}
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
              <PlayButton label="Play all" onPlay={() => playItems(songItems(songs.data!.items))} />
              <PlayButton label="Shuffle" primary={false} onPlay={playLibraryShuffle} />
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
        playlists.isLoading && discover.isLoading ? (
          <div className={s.skeleton} style={{ height: '36vh' }} />
        ) : allPlaylists.length || discoverList.length ? (
          <>
            {(discover.data ?? []).map((sec) => (
              <section key={sec.id} className={s.rail} aria-label={sec.label}>
                <div className={s.railHead}>
                  <h2 className={s.sectionTitle}>{sec.label}</h2>
                  <span className={s.railMore}>{sec.playlists.length} playlists</span>
                </div>
                <div className={s.grid}>
                  {sec.playlists.map((pl) => (
                    <DiscoverCard key={pl.key} card={pl} />
                  ))}
                </div>
              </section>
            ))}
            {allPlaylists.length ? (
              <section className={s.rail} aria-label="Your playlists">
                <div className={s.railHead}>
                  <h2 className={s.sectionTitle}>Your playlists &amp; mirrored</h2>
                  <span className={s.railMore}>{allPlaylists.length} playlists</span>
                </div>
                <div className={s.grid}>
                  {allPlaylists.map((pl) => (
                    <PlaylistCard key={pl.id} playlist={pl} />
                  ))}
                </div>
              </section>
            ) : null}
          </>
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
