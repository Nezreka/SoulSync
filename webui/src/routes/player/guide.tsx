import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute, useNavigate, useRouteContext } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import { appURL } from '@/platform/url-base';

import type { ChannelFilter } from './-lib/channels';

import {
  autoChannels,
  CHANNEL_GROUPS,
  matchesFilter,
  numberChannels,
  playlistChannel,
  ruleChannel,
  serverMusicChannel,
  type Channel,
  type ChannelRule,
} from './-lib/channels';
import {
  dayLabel,
  GUIDE_WINDOW_HOURS,
  guideWindow,
  loadRules,
  PX_PER_MINUTE,
  saveRules,
} from './-lib/guide';
import { hashSeed, programsInWindow, type GuideProgram } from './-lib/schedule';
import { genresOf } from './-lib/video';
import {
  favoritesQuery,
  musicChannelsQuery,
  playlistTracksQuery,
  saveFavorites,
  songsQuery,
  userPlaylistsQuery,
  videoCatalogQuery,
} from './-player.api';
import { ChannelForm } from './-ui/guide-form';
import { GuideDetail, GuideGrid, OnNow, type Picked } from './-ui/guide-grid';
import { Empty, PageTitle, Tabs, styles as s } from './-ui/kit';
import { useSections } from './-ui/sections';

type GuideView = 'grid' | 'now';

export const Route = createFileRoute('/player/guide')({
  validateSearch: (
    search: Record<string, unknown>,
  ): { view?: GuideView; filter?: ChannelFilter } => {
    const out: { view?: GuideView; filter?: ChannelFilter } = {};
    if (search.view === 'grid' || search.view === 'now') out.view = search.view;
    if (['all', 'favorites', 'video', 'music', 'kids'].includes(String(search.filter)))
      out.filter = search.filter as ChannelFilter;
    return out;
  },
  component: GuidePage,
});

const PLAYLIST_CHANNELS = 8;
const MUSIC_POOL = 300;
/** how far the guide browses back and ahead, in hours */
const BROWSE_HOURS = 2;
const BROWSE_LIMIT = 24;

function useNow(everyMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(id);
  }, [everyMs]);
  return now;
}

/** the music channel's pool: one page of the library, a different page each day */
function useMusicPool(enabled: boolean) {
  const probe = useQuery({ ...songsQuery({ limit: 1 }), enabled });
  const total = probe.data?.pagination?.total_count ?? 0;
  const pages = Math.max(1, Math.ceil(total / MUSIC_POOL));
  const page = (hashSeed(`music|${new Date().toDateString()}`) % pages) + 1;
  const songs = useQuery({
    ...songsQuery({ limit: MUSIC_POOL, page }),
    enabled: enabled && total > 0,
  });
  return useMemo(
    () =>
      (songs.data?.songs ?? []).map((song) => ({
        id: song.id,
        title: song.title,
        artist: song.artist_name,
        seconds: song.duration ? song.duration / 1000 : null,
        href: song.album_id ? `/player/music/album/${song.album_id}` : undefined,
        art: song.album_id
          ? appURL(`/api/library/v2/artwork/album/${song.album_id}?size=thumb`)
          : null,
      })),
    [songs.data],
  );
}

function useFavorites() {
  const queryClient = useQueryClient();
  const favorites = useQuery(favoritesQuery());
  const save = useMutation({
    mutationFn: saveFavorites,
    onMutate: async (next) => {
      await queryClient.cancelQueries({ queryKey: favoritesQuery().queryKey });
      const before = queryClient.getQueryData(favoritesQuery().queryKey);
      queryClient.setQueryData(favoritesQuery().queryKey, next);
      return { before };
    },
    onError: (_e, _next, ctx) => {
      if (ctx?.before) queryClient.setQueryData(favoritesQuery().queryKey, ctx.before);
    },
  });
  const list = favorites.data ?? [];
  const set = useMemo(() => new Set(list), [list]);
  const toggle = (channel: Channel) =>
    save.mutate(
      set.has(channel.id) ? list.filter((id) => id !== channel.id) : [...list, channel.id],
    );
  return { list, set, toggle };
}

function GuidePage() {
  const sections = useSections();
  const { shell } = useRouteContext({ from: '/player' });
  const profileId = shell.profile?.profileId ?? 0;
  const { view = 'grid', filter = 'all' } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/guide' });
  const now = useNow();
  const [offset, setOffset] = useState(0);
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.video });
  const playlists = useQuery({ ...userPlaylistsQuery(), enabled: sections.music });
  const musicChannels = useQuery({ ...musicChannelsQuery(), enabled: sections.music });
  const firstPlaylists = useMemo(
    () =>
      (playlists.data ?? []).filter((p) => (p.track_count ?? 0) > 2).slice(0, PLAYLIST_CHANNELS),
    [playlists.data],
  );
  const playlistTracks = useQueries({
    queries: firstPlaylists.map((p) => ({ ...playlistTracksQuery(p.id), enabled: sections.music })),
  });
  const musicPool = useMusicPool(sections.music);
  const favorites = useFavorites();
  const [rules, setRules] = useState<ChannelRule[]>(() => loadRules(profileId));
  const [making, setMaking] = useState(false);
  const [picked, setPicked] = useState<Picked | null>(null);

  // the query results change identity every render; when their data lands is what matters
  const playlistKey = playlistTracks.map((q) => q.dataUpdatedAt).join(',');
  const playlistData = useRef(playlistTracks);
  playlistData.current = playlistTracks;

  const channels = useMemo(() => {
    const out: Channel[] = [];
    const cat = catalog.data ?? {
      movies: [],
      shows: [],
      youtube: [],
      recent_episodes: [],
      kinds: { movies: false, shows: false, youtube: false },
    };
    out.push(...autoChannels(cat, musicPool));
    firstPlaylists.forEach((p, i) => {
      const tracks = playlistData.current[i]?.data ?? [];
      const channel = playlistChannel({
        id: p.id,
        name: p.name,
        tracks: tracks.map((t) => ({
          id: t.id,
          title: t.track_name,
          artist: t.artist_name,
          seconds: t.duration_ms ? t.duration_ms / 1000 : null,
        })),
      });
      if (channel) out.push(channel);
    });
    for (const m of musicChannels.data ?? []) {
      const channel = serverMusicChannel(m);
      if (channel) out.push(channel);
    }
    for (const rule of rules) {
      const channel = ruleChannel(rule, cat);
      if (channel) out.push(channel);
    }
    return numberChannels(out);
  }, [catalog.data, musicPool, playlistKey, rules, firstPlaylists, musicChannels.data]);

  const { from, to } = guideWindow(now, offset);
  // a schedule only changes when the window or the half hour does
  const halfHour = Math.floor(now / 1_800_000);
  const schedules = useMemo(() => {
    const anchor = halfHour * 1_800_000;
    const map = new Map<string, GuideProgram[]>();
    for (const channel of channels)
      map.set(
        channel.id,
        programsInWindow(
          channel.id,
          channel.pool,
          Math.min(from, anchor),
          Math.max(to, anchor + 4 * 3_600_000),
          { ordered: channel.ordered },
        ),
      );
    return map;
  }, [channels, from, to, halfHour]);

  const shown = channels.filter((c) => matchesFilter(c, filter, favorites.set));
  const groups = [
    ...(filter === 'all' && favorites.list.length
      ? [
          {
            kind: 'favorites',
            label: 'Favorites',
            channels: favorites.list
              .map((id) => channels.find((c) => c.id === id))
              .filter((c): c is Channel => !!c),
          },
        ]
      : []),
    ...CHANNEL_GROUPS.map((group) => ({
      kind: group.kind as string,
      label: group.label,
      channels: shown.filter((c) => c.kind === group.kind),
    })),
  ].filter((group) => group.channels.length);

  const loading = catalog.isLoading || playlists.isLoading || musicChannels.isLoading;
  const genres = genresOf([...(catalog.data?.movies ?? []), ...(catalog.data?.shows ?? [])]);
  const setSearch = (next: { view?: GuideView; filter?: ChannelFilter }) =>
    void navigate({ search: (prev) => ({ ...prev, ...next }), replace: true });

  return (
    <>
      <div className={s.toolbar}>
        <PageTitle>Guide</PageTitle>
        <div className={s.fieldRow}>
          <Tabs
            label="View"
            value={view}
            options={[
              { value: 'grid', label: 'Grid' },
              { value: 'now', label: 'On now' },
            ]}
            onChange={(next) => setSearch({ view: next })}
          />
          <button type="button" className={s.btn} onClick={() => setMaking((v) => !v)} data-nav>
            {making ? 'Close' : 'Make a channel'}
          </button>
        </div>
      </div>

      <div className={s.guideBar}>
        <div className={s.chips} role="tablist" aria-label="Channels">
          {(
            [
              ['all', 'All'],
              ['favorites', `Favorites${favorites.list.length ? ` ${favorites.list.length}` : ''}`],
              ['video', 'Movies & TV'],
              ['music', 'Music'],
              ['kids', 'Kids'],
            ] as [ChannelFilter, string][]
          ).map(([value, text]) => (
            <button
              key={value}
              type="button"
              role="tab"
              className={s.filterChip}
              aria-selected={filter === value}
              onClick={() => setSearch({ filter: value })}
              data-nav
            >
              {text}
            </button>
          ))}
        </div>
        {view === 'grid' ? (
          <div className={s.timeNav}>
            <button
              type="button"
              className={s.btn}
              disabled={offset <= -BROWSE_LIMIT}
              onClick={() => setOffset((o) => o - BROWSE_HOURS)}
              data-nav
            >
              ‹ Earlier
            </button>
            <button
              type="button"
              className={s.btn}
              aria-pressed={offset === 0}
              onClick={() => setOffset(0)}
              data-nav
            >
              Now
            </button>
            <button
              type="button"
              className={s.btn}
              disabled={offset >= BROWSE_LIMIT}
              onClick={() => setOffset((o) => o + BROWSE_HOURS)}
              data-nav
            >
              Later ›
            </button>
            <span className={s.notYet}>{dayLabel(from, now)}</span>
          </div>
        ) : null}
      </div>

      {making ? (
        <ChannelForm
          genres={genres}
          onCancel={() => setMaking(false)}
          onSave={(rule) => {
            const next = [...rules, rule];
            setRules(next);
            saveRules(profileId, next);
            setMaking(false);
          }}
        />
      ) : null}

      {picked ? (
        <GuideDetail
          picked={picked}
          schedules={schedules}
          now={now}
          favorite={favorites.set.has(picked.channel.id)}
          onFavorite={favorites.toggle}
          onClose={() => setPicked(null)}
          onRemoveRule={
            picked.channel.kind === 'rule'
              ? () => {
                  const next = rules.filter((r) => `rule-${r.id}` !== picked.channel.id);
                  setRules(next);
                  saveRules(profileId, next);
                  setPicked(null);
                }
              : undefined
          }
        />
      ) : null}

      {loading ? <div className={s.skeleton} style={{ height: '50vh' }} /> : null}

      {!loading && !groups.length ? (
        <Empty title={filter === 'favorites' ? 'No favorite channels yet' : 'No channels yet'}>
          {filter === 'favorites'
            ? 'Star a channel and it lands here, and at the top of the guide, on every screen you use.'
            : 'Channels are made from what is in your library: a genre, a network, a decade, a mood. Once a few things with known runtimes are on disk, they appear here.'}
        </Empty>
      ) : null}

      {groups.length && view === 'grid' ? (
        <GuideGrid
          groups={groups}
          schedules={schedules}
          from={from}
          to={to}
          now={now}
          pxPerMinute={PX_PER_MINUTE}
          favorites={favorites.set}
          onFavorite={favorites.toggle}
          onPick={setPicked}
          label={`Guide, ${GUIDE_WINDOW_HOURS} hours from ${new Date(from).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`}
        />
      ) : null}

      {groups.length && view === 'now' ? (
        <OnNow
          channels={[...new Map(groups.flatMap((g) => g.channels).map((c) => [c.id, c])).values()]}
          schedules={schedules}
          now={now}
          favorites={favorites.set}
          onFavorite={favorites.toggle}
          onPick={setPicked}
        />
      ) : null}
    </>
  );
}
