import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute, useNavigate, useRouteContext } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import { appURL } from '@/platform/url-base';

import type { ChannelFilter } from './-lib/channels';

import {
  autoChannels,
  CHANNEL_GROUPS,
  musicTypeChannel,
  matchesFilter,
  applyLineup,
  channelMatches,
  numberChannels,
  playlistChannel,
  serverMusicChannel,
  specChannel,
  specFromRule,
  marathonItems,
  type Channel,
  type PeopleTitles,
  type SavedChannel,
} from './-lib/channels';
import {
  dayLabel,
  GUIDE_WINDOW_HOURS,
  guideWindow,
  loadRules,
  PX_PER_MINUTE,
  saveRules,
} from './-lib/guide';
import { albumArtwork } from './-lib/images';
import {
  hashSeed,
  mergeIntoBlocks,
  programsInWindow,
  startOfLocalDay,
  type GuideItem,
  type GuideProgram,
} from './-lib/schedule';
import {
  favoritesQuery,
  guideChannelsQuery,
  guideWatchedQuery,
  personTitlesQuery,
  guideEdits,
  lineupQuery,
  showDetailQuery,
  musicChannelsQuery,
  playlistQuery,
  saveFavorites,
  songsQuery,
  playlistsQuery,
  videoCatalogQuery,
} from './-player.api';
import { ChannelForm } from './-ui/guide-form';
import { GuideDetail, GuideGrid, OnNow, type Picked } from './-ui/guide-grid';
import { GuidePop, GuideStrip } from './-ui/guide-pop';
import { SearchIcon } from './-ui/icons';
import { Empty, PageTitle, Tabs, styles as s } from './-ui/kit';
import { LineupEditor } from './-ui/lineup-editor';
import { setLineup } from './-ui/playback/tuner';
import { useSections } from './-ui/sections';
import { ConfirmSheet } from './-ui/sheet';

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
  const playlists = useQuery({ ...playlistsQuery(), enabled: sections.music });
  const musicChannels = useQuery({ ...musicChannelsQuery(), enabled: sections.music });
  const firstPlaylists = useMemo(
    () =>
      (playlists.data?.yours ?? []).filter((p) => p.track_count > 2).slice(0, PLAYLIST_CHANNELS),
    [playlists.data],
  );
  const playlistTracks = useQueries({
    queries: firstPlaylists.map((p) => ({ ...playlistQuery(p.id), enabled: sections.music })),
  });
  const musicPool = useMusicPool(sections.music);
  const favorites = useFavorites();
  const made = useQuery(guideChannelsQuery());
  const lineupQ = useQuery(lineupQuery());
  const queryClient = useQueryClient();
  const [making, setMaking] = useState<SavedChannel | 'new' | null>(null);
  const [editingLineup, setEditingLineup] = useState(false);
  const [removing, setRemoving] = useState<SavedChannel | null>(null);
  const madeList = useMemo(
    () => [...(made.data?.server ?? []), ...(made.data?.mine ?? [])],
    [made.data],
  );
  const refreshMade = () => queryClient.invalidateQueries({ queryKey: ['player', 'guide'] });

  // channels made before they lived on the server: moved there once, then gone from this browser
  const moved = useRef(false);
  useEffect(() => {
    if (moved.current || !made.data || made.data.mine.length) return;
    const local = loadRules(profileId);
    moved.current = true;
    if (!local.length) return;
    void Promise.all(
      local.map((r) => guideEdits.add('mine', specFromRule(r)).catch(() => null)),
    ).then(() => {
      saveRules(profileId, []);
      void refreshMade();
    });
  }, [made.data, profileId]);

  // a marathon channel airs every episode of its show, in order
  const marathonShows = useMemo(
    () => [...new Set(madeList.map((c) => c.spec.show).filter((x): x is number => !!x))],
    [madeList],
  );
  const marathonDetails = useQueries({
    queries: marathonShows.map((id) => ({ ...showDetailQuery(id), enabled: sections.tv })),
  });
  const marathonKey = marathonDetails.map((q) => q.dataUpdatedAt).join(',');
  const marathons = useMemo(() => {
    const map = new Map<number, { title: string; episodes: GuideItem[] }>();
    marathonShows.forEach((id, i) => {
      const show = marathonDetails[i]?.data;
      if (show) map.set(id, { title: show.title, episodes: marathonItems(show) });
    });
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [marathonShows, marathonKey]);
  // a channel of someone's work: their films and shows
  const personIds = useMemo(
    () => [...new Set(madeList.flatMap((c) => (c.spec.people ?? []).map((p) => p.id)))],
    [madeList],
  );
  const personTitles = useQueries({
    queries: personIds.map((id) => ({ ...personTitlesQuery(id), enabled: sections.video })),
  });
  const personKey = personTitles.map((q) => q.dataUpdatedAt).join(',');
  const peopleFor = useMemo(() => {
    const byId = new Map(personIds.map((id, i) => [id, personTitles[i]?.data]));
    return (spec: SavedChannel['spec']): PeopleTitles | null => {
      const all = (spec.people ?? []).map((p) => byId.get(p.id));
      if (!all.length || all.some((x) => !x)) return null;
      return {
        movies: new Set(all.flatMap((x) => x!.movies)),
        shows: new Set(all.flatMap((x) => x!.shows)),
      };
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personIds, personKey]);
  // added-in-the-last-n-days moves once a day, not every tick
  const today = Math.floor(now / 86_400_000) * 86_400_000;
  const [picked, setPickedOnly] = useState<Picked | null>(null);
  // the program it was picked from: the card pops in beside it
  const [pickedAt, setPickedAt] = useState<HTMLElement | null>(null);
  const setPicked = (next: Picked | null, at?: HTMLElement) => {
    setPickedOnly(next);
    setPickedAt(at ?? null);
  };
  // what the pointer or focus rests on in the grid; settles before the panel follows
  const [preview, setPreview] = useState<Picked | null>(null);
  const previewTimer = useRef<number | undefined>(undefined);
  const hoverPreview = (p: Picked) => {
    window.clearTimeout(previewTimer.current);
    previewTimer.current = window.setTimeout(() => setPreview(p), 140);
  };
  useEffect(() => () => window.clearTimeout(previewTimer.current), []);

  // the query results change identity every render; when their data lands is what matters
  const playlistKey = playlistTracks.map((q) => q.dataUpdatedAt).join(',');
  const playlistData = useRef(playlistTracks);
  playlistData.current = playlistTracks;

  const cat = useMemo(
    () =>
      catalog.data ?? {
        movies: [],
        shows: [],
        youtube: [],
        recent_episodes: [],
        kinds: { movies: false, shows: false, youtube: false },
      },
    [catalog.data],
  );
  // each part rebuilds only when its own data lands
  const videoChannels = useMemo(() => autoChannels(cat), [cat]);
  const musicChannel = useMemo(() => musicTypeChannel(musicPool), [musicPool]);
  const serverMusic = useMemo(
    () => (musicChannels.data ?? []).map(serverMusicChannel).filter((c): c is Channel => !!c),
    [musicChannels.data],
  );
  const allChannels = useMemo(() => {
    const out: Channel[] = [...videoChannels];
    if (musicChannel) out.splice(Math.min(3, out.length), 0, musicChannel);
    firstPlaylists.forEach((p, i) => {
      // only songs this profile can play, by their library track (a row id plays nothing)
      const songs = (playlistData.current[i]?.data?.songs ?? []).filter((t) => t.track_id !== null);
      const channel = playlistChannel({
        id: p.id,
        name: p.name,
        tracks: songs.map((t) => ({
          id: t.track_id!,
          title: t.title,
          artist: t.artist,
          seconds: t.duration_ms ? t.duration_ms / 1000 : null,
          art: t.album_id ? albumArtwork(t.album_id) : null,
        })),
      });
      if (channel) out.push(channel);
    });
    out.push(...serverMusic);
    for (const saved of madeList) {
      const channel = specChannel(
        saved,
        cat,
        today,
        saved.spec.show ? marathons.get(saved.spec.show) : null,
        peopleFor(saved.spec),
      );
      if (channel) out.push(channel);
    }
    return out;
  }, [
    videoChannels,
    musicChannel,
    serverMusic,
    playlistKey,
    madeList,
    marathons,
    peopleFor,
    firstPlaylists,
    cat,
    today,
  ]);

  // this profile's lineup: its order and names; what it hid stays out of the guide
  const lined = useMemo(
    () => applyLineup(allChannels, lineupQ.data ?? []),
    [allChannels, lineupQ.data],
  );
  // what this profile has seen: channels air the unseen first, a marathon starts at your next episode
  const watched = useQuery({ ...guideWatchedQuery(), enabled: sections.video });
  const seen = useMemo(() => {
    const out = new Set<string>();
    for (const id of watched.data?.movies ?? []) out.add(`movie:${id}`);
    for (const id of watched.data?.episodes ?? []) out.add(`episode:${id}`);
    return out;
  }, [watched.data]);
  const seenKey = String(watched.dataUpdatedAt);
  const channels = useMemo(() => {
    const visible = lined.filter((c) => !c.hidden);
    return numberChannels(visible).map((c) => {
      if (c.music) return c;
      return {
        ...c,
        // Shared broadcast schedule across all devices (Desktop, Android, TV):
        // deterministic from (channelId, day, pool) with cable-style show rotation & evening films
        schedule: {
          daypart: true,
          blocks: 1,
          rotateShows: true,
          today: startOfLocalDay(today),
        },
      };
    });
  }, [lined, today]);
  const numbers = useMemo(() => new Map(channels.map((c) => [c.id, c.number])), [channels]);

  // channel up / down follow the guide's numbers, from anywhere in the player
  useEffect(
    () => setLineup([...channels].sort((a, b) => (a.number ?? 0) - (b.number ?? 0))),
    [channels],
  );

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
          { ...channel.schedule, ordered: channel.ordered },
        ),
      );
    return map;
  }, [channels, from, to, halfHour]);

  const [find, setFind] = useState('');
  const shown = channels.filter(
    (c) => matchesFilter(c, filter, favorites.set) && channelMatches(c, find),
  );
  const groups = [
    ...(filter === 'all' && favorites.list.length
      ? [
          {
            kind: 'favorites',
            label: 'Favorites',
            channels: favorites.list
              .map((id) => channels.find((c) => c.id === id))
              .filter((c): c is Channel => !!c && channelMatches(c, find)),
          },
        ]
      : []),
    ...CHANNEL_GROUPS.map((group) => ({
      kind: group.kind as string,
      label: group.label,
      channels: shown.filter((c) => c.kind === group.kind),
    })),
  ].filter((group) => group.channels.length);

  // the guide fits the screen: the panel above and the channels below, the
  // channels scrolling inside. measured from where the stage starts to the
  // bottom of the screen (or the top of the playing bar)
  const stage = useRef<HTMLDivElement>(null);
  const [stageH, setStageH] = useState<string | undefined>(undefined);
  useEffect(() => {
    const measure = () => {
      const el = stage.current;
      if (!el) return;
      const bar = document.querySelector('section[aria-label="Now playing"]');
      const bottom = bar ? bar.getBoundingClientRect().top : window.innerHeight;
      const top = el.getBoundingClientRect().top + (el.closest('main')?.scrollTop ?? 0);
      setStageH(`${Math.max(360, Math.floor(bottom - top - 16))}px`);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(document.body);
    window.addEventListener('resize', measure);
    // the playing bar comes and goes without the page changing size: closing
    // it left its height empty under the guide. look again when it does
    let frame = 0;
    const soon = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    const root = document.querySelector('[data-player-root]') ?? document.body;
    const mo = new MutationObserver((changes) => {
      if (changes.some((c) => c.addedNodes.length || c.removedNodes.length)) soon();
    });
    mo.observe(root, { childList: true, subtree: true });
    return () => {
      ro.disconnect();
      mo.disconnect();
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', measure);
    };
  }, [view, making]);

  // the made channel the panel shows, when it is one: changed or removed from there
  const pickedMade = picked
    ? madeList.find((m) => `made-${m.id}` === picked.channel.id)
    : undefined;

  // the panel above the grid starts on what's on now on the first channel
  const firstChannel = groups[0]?.channels[0];
  const firstOn = useMemo<Picked | null>(() => {
    if (!firstChannel) return null;
    const programs = schedules.get(firstChannel.id) ?? [];
    const program = programs.find((p) => p.start <= now && p.end > now);
    if (!program) return null;
    const block = firstChannel.music
      ? mergeIntoBlocks(programs).find((b) => b.start <= now && b.end > now)
      : undefined;
    return { channel: firstChannel, program, block };
  }, [firstChannel, schedules, now]);

  // the music channels take a library pass the first time; the guide doesn't wait for them
  const loading = catalog.isLoading || playlists.isLoading;
  const tuningMusic = musicChannels.isLoading && musicChannels.fetchStatus === 'fetching';
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
          <button type="button" className={s.btn} onClick={() => setEditingLineup(true)} data-nav>
            Your lineup
          </button>
          <button type="button" className={s.btnPrimary} onClick={() => setMaking('new')} data-nav>
            Make a channel
          </button>
        </div>
      </div>

      <div className={s.guideBar}>
        <label className={s.guideFind}>
          <SearchIcon />
          <input
            type="search"
            placeholder="Find a channel"
            value={find}
            onChange={(e) => setFind(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setFind('');
            }}
            aria-label="Find a channel by name or number"
          />
        </label>
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
          catalog={cat}
          editing={making === 'new' ? null : making}
          canServer={!!made.data?.can_edit_server}
          onCancel={() => setMaking(null)}
          onSaved={() => {
            setMaking(null);
            void refreshMade();
          }}
        />
      ) : null}

      {editingLineup ? (
        <LineupEditor
          channels={lined.map((c) => ({ ...c, number: numbers.get(c.id) }))}
          made={madeList}
          canEditServer={!!made.data?.can_edit_server}
          onChanged={() =>
            void queryClient.invalidateQueries({ queryKey: ['player', 'guide', 'lineup'] })
          }
          onEdit={(c) => {
            setEditingLineup(false);
            setMaking(c);
          }}
          onRemove={(c) => setRemoving(c)}
          onClose={() => setEditingLineup(false)}
        />
      ) : null}

      {removing ? (
        <ConfirmSheet
          title={`Remove ${removing.spec.name}?`}
          confirm="Remove"
          danger
          onClose={() => setRemoving(null)}
          onConfirm={() => {
            void guideEdits.remove(removing.id).then(() => {
              setRemoving(null);
              setPicked(null);
              void refreshMade();
            });
          }}
        >
          {removing.scope === 'server'
            ? 'It goes from the guide for everyone on this server.'
            : 'It goes from your guide. The movies and shows on it stay in your library.'}
        </ConfirmSheet>
      ) : null}

      {/* the panel on top stays put; the channels scroll under it inside the screen */}
      <div
        ref={stage}
        className={s.guideStage}
        style={{ '--stage-h': stageH } as React.CSSProperties}
      >
        {view === 'grid' && (preview ?? firstOn) ? (
          <GuideStrip picked={(preview ?? firstOn)!} now={now} />
        ) : null}

        {picked ? (
          <GuidePop anchor={pickedAt} onClose={() => setPicked(null)}>
            <GuideDetail
              picked={picked}
              schedules={schedules}
              now={now}
              favorite={favorites.set.has(picked.channel.id)}
              onFavorite={favorites.toggle}
              onClose={() => setPicked(null)}
              onRemoveRule={
                pickedMade && (pickedMade.scope === 'mine' || made.data?.can_edit_server)
                  ? () => setRemoving(pickedMade)
                  : undefined
              }
              onEditRule={
                pickedMade && (pickedMade.scope === 'mine' || made.data?.can_edit_server)
                  ? () => setMaking(pickedMade)
                  : undefined
              }
            />
          </GuidePop>
        ) : null}

        {loading && !groups.length ? (
          <div className={s.skeleton} style={{ height: '50vh' }} />
        ) : null}
        {tuningMusic && groups.length ? (
          <div className={s.guideNote} role="status">
            Tuning in the music channels…
          </div>
        ) : null}

        {!loading && !tuningMusic && !groups.length ? (
          <Empty
            title={
              find.trim()
                ? `No channel called “${find.trim()}”`
                : filter === 'favorites'
                  ? 'No favorite channels yet'
                  : 'No channels yet'
            }
          >
            {find.trim()
              ? 'Try part of its name, what it airs (horror, HBO, 1990s) or its number.'
              : filter === 'favorites'
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
            onPreview={hoverPreview}
            label={`Guide, ${GUIDE_WINDOW_HOURS} hours from ${new Date(from).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`}
          />
        ) : null}

        {groups.length && view === 'now' ? (
          <OnNow
            channels={[
              ...new Map(groups.flatMap((g) => g.channels).map((c) => [c.id, c])).values(),
            ]}
            schedules={schedules}
            now={now}
            favorites={favorites.set}
            onFavorite={favorites.toggle}
            onPick={setPicked}
          />
        ) : null}
      </div>
    </>
  );
}
