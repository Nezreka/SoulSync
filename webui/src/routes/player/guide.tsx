import { useQueries, useQuery } from '@tanstack/react-query';
import { createFileRoute, useRouteContext } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';

import {
  autoChannels,
  CHANNEL_GROUPS,
  playlistChannel,
  ruleChannel,
  type Channel,
  type ChannelRule,
} from './-lib/channels';
import { clock, runtime } from './-lib/format';
import { loadRules, saveRules, GUIDE_WINDOW_HOURS, PX_PER_MINUTE, guideWindow } from './-lib/guide';
import { hashSeed, programsInWindow, type GuideProgram } from './-lib/schedule';
import { genresOf } from './-lib/video';
import {
  playlistTracksQuery,
  songsQuery,
  userPlaylistsQuery,
  videoCatalogQuery,
} from './-player.api';
import { Empty, PageTitle, PlayButton, PlayerLink, styles as s } from './-ui/kit';
import { useSections } from './-ui/sections';

export const Route = createFileRoute('/player/guide')({
  component: GuidePage,
});

const PLAYLIST_CHANNELS = 8;
const MUSIC_POOL = 300;

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
  const today = new Date().toDateString();
  const page = (hashSeed(`music|${today}`) % pages) + 1;
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
      })),
    [songs.data],
  );
}

function ChannelForm({
  genres,
  onSave,
  onCancel,
}: {
  genres: string[];
  onSave: (rule: ChannelRule) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [movies, setMovies] = useState(true);
  const [tv, setTv] = useState(true);
  const [genre, setGenre] = useState('');
  const [decade, setDecade] = useState('');
  const [kids, setKids] = useState(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const kinds = [...(movies ? (['movies'] as const) : []), ...(tv ? (['tv'] as const) : [])];
    if (!name.trim() || !kinds.length) return;
    onSave({
      id: `${Date.now().toString(36)}`,
      name: name.trim().slice(0, 40),
      kinds,
      genre: genre || null,
      decade: decade ? Number(decade) : null,
      kidsOnly: kids,
    });
  };
  return (
    <form className={s.guideDetail} onSubmit={submit} aria-label="Make a channel">
      <div className={s.fieldRow}>
        <label className={s.field}>
          Name
          <input
            className={s.fieldInput}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            data-nav
          />
        </label>
        <label className={s.check}>
          <input
            type="checkbox"
            checked={movies}
            onChange={(e) => setMovies(e.target.checked)}
            data-nav
          />{' '}
          Movies
        </label>
        <label className={s.check}>
          <input type="checkbox" checked={tv} onChange={(e) => setTv(e.target.checked)} data-nav />{' '}
          TV
        </label>
        <label className={s.field}>
          Genre
          <select
            className={s.fieldInput}
            value={genre}
            onChange={(e) => setGenre(e.target.value)}
            data-nav
          >
            <option value="">Any</option>
            {genres.map((g) => (
              <option key={g}>{g}</option>
            ))}
          </select>
        </label>
        <label className={s.field}>
          Decade
          <select
            className={s.fieldInput}
            value={decade}
            onChange={(e) => setDecade(e.target.value)}
            data-nav
          >
            <option value="">Any</option>
            {[1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020].map((d) => (
              <option key={d} value={d}>
                {d}s
              </option>
            ))}
          </select>
        </label>
        <label className={s.check}>
          <input
            type="checkbox"
            checked={kids}
            onChange={(e) => setKids(e.target.checked)}
            data-nav
          />{' '}
          Kids only
        </label>
      </div>
      <div className={s.heroActions}>
        <button type="button" className={s.btn} onClick={onCancel} data-nav>
          Cancel
        </button>
        <button type="submit" className={s.btnPrimary} data-nav>
          Add channel
        </button>
      </div>
      <span className={s.notYet}>Your channels are kept on this device.</span>
    </form>
  );
}

function GuidePage() {
  const sections = useSections();
  const { shell } = useRouteContext({ from: '/player' });
  const profileId = shell.profile?.profileId ?? 0;
  const now = useNow();
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.video });
  const playlists = useQuery({ ...userPlaylistsQuery(), enabled: sections.music });
  const firstPlaylists = useMemo(
    () =>
      (playlists.data ?? []).filter((p) => (p.track_count ?? 0) > 2).slice(0, PLAYLIST_CHANNELS),
    [playlists.data],
  );
  const playlistTracks = useQueries({
    queries: firstPlaylists.map((p) => ({ ...playlistTracksQuery(p.id), enabled: sections.music })),
  });
  const musicPool = useMusicPool(sections.music);
  const [rules, setRules] = useState<ChannelRule[]>(() => loadRules(profileId));
  const [making, setMaking] = useState(false);
  const [picked, setPicked] = useState<{ channel: Channel; program: GuideProgram } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

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
    for (const rule of rules) {
      const channel = ruleChannel(rule, cat);
      if (channel) out.push(channel);
    }
    return out;
  }, [catalog.data, musicPool, playlistKey, rules, firstPlaylists]);

  const { from, to } = guideWindow(now);
  const width = ((to - from) / 60000) * PX_PER_MINUTE;
  const x = (ms: number) => ((ms - from) / 60000) * PX_PER_MINUTE;

  const grouped = useMemo(
    () =>
      CHANNEL_GROUPS.map((group) => ({
        ...group,
        channels: channels.filter((c) => c.kind === group.kind),
      })).filter((group) => group.channels.length),
    [channels],
  );

  const schedules = useMemo(() => {
    const map = new Map<string, GuideProgram[]>();
    for (const channel of channels)
      map.set(
        channel.id,
        programsInWindow(channel.id, channel.pool, from, to, { ordered: channel.ordered }),
      );
    return map;
  }, [channels, from, to]);

  // open the grid at "now" with a little of the past showing
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollLeft = 0;
  }, [from]);

  const loading = catalog.isLoading || playlists.isLoading;
  const timeMarks: number[] = [];
  for (let t = Math.ceil(from / 1800000) * 1800000; t < to; t += 1800000) timeMarks.push(t);
  const genres = genresOf([...(catalog.data?.movies ?? []), ...(catalog.data?.shows ?? [])]);

  return (
    <>
      <div className={s.toolbar}>
        <PageTitle>Guide</PageTitle>
        <button type="button" className={s.btn} onClick={() => setMaking((v) => !v)} data-nav>
          {making ? 'Close' : 'Make a channel'}
        </button>
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
        <div className={s.guideDetail} aria-live="polite">
          <div className={s.rowMain}>
            <span className={s.heroKicker}>
              {picked.channel.name} · {clock(picked.program.start)} – {clock(picked.program.end)}
            </span>
            <span className={s.sectionTitle}>{picked.program.title}</span>
            <span className={s.rowSub}>
              {[picked.program.subtitle, runtime(picked.program.seconds)]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </div>
          <div className={s.heroActions}>
            {picked.program.start <= now && picked.program.end > now ? (
              <PlayButton
                label={`Join live, ${runtime((now - picked.program.start) / 1000) || 'just started'} in`}
              />
            ) : null}
            <PlayButton label="Watch from the start" primary={false} />
            {picked.program.href ? (
              <PlayerLink href={picked.program.href} className={s.btn} data-nav>
                Details
              </PlayerLink>
            ) : null}
            {picked.channel.kind === 'rule' ? (
              <button
                type="button"
                className={s.btn}
                onClick={() => {
                  const next = rules.filter((r) => `rule-${r.id}` !== picked.channel.id);
                  setRules(next);
                  saveRules(profileId, next);
                  setPicked(null);
                }}
                data-nav
              >
                Remove channel
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {loading ? <div className={s.skeleton} style={{ height: '50vh' }} /> : null}

      {!loading && !channels.length ? (
        <Empty title="No channels yet">
          Channels are made from what is in your library: a genre, a network, a decade. Once a few
          things with known runtimes are on disk, they appear here.
        </Empty>
      ) : null}

      {channels.length ? (
        <div
          className={s.guide}
          ref={scrollRef}
          role="grid"
          aria-label={`Guide, next ${GUIDE_WINDOW_HOURS} hours`}
        >
          <div className={s.guideCanvas} style={{ width: `calc(var(--g-chan) + ${width}px)` }}>
            <div className={s.guideTimes} role="row">
              <div className={s.guideCorner} />
              <div className={s.guideLane} style={{ width }}>
                {timeMarks.map((t) => (
                  <div key={t} className={s.guideTime} style={{ left: x(t) }} role="columnheader">
                    {clock(t)}
                  </div>
                ))}
              </div>
            </div>
            {grouped.map((group) => (
              <div key={group.kind} role="rowgroup" aria-label={group.label}>
                <div className={s.guideGroup}>{group.label}</div>
                {group.channels.map((channel) => (
                  <div key={channel.id} className={s.guideRow} role="row">
                    <div className={s.guideChannel} role="rowheader">
                      <span className={s.guideChannelName}>{channel.name}</span>
                      <span className={s.guideChannelBlurb}>{channel.blurb}</span>
                    </div>
                    <div className={s.guideLane} style={{ width }}>
                      {(schedules.get(channel.id) ?? []).map((program) => {
                        const left = Math.max(0, x(program.start));
                        const right = Math.min(width, x(program.end));
                        const live = program.start <= now && program.end > now;
                        return (
                          <button
                            key={`${program.key}-${program.start}`}
                            type="button"
                            role="gridcell"
                            className={s.guideProgram}
                            data-now={live ? 'true' : undefined}
                            style={{ left: left + 2, width: Math.max(8, right - left - 4) }}
                            title={`${program.title}${program.subtitle ? ` · ${program.subtitle}` : ''}\n${clock(program.start)} – ${clock(program.end)}`}
                            onClick={() => setPicked({ channel, program })}
                            data-nav
                          >
                            <span className={s.guideProgramTitle}>{program.title}</span>
                            {right - left > 110 ? (
                              <span className={s.guideProgramSub}>
                                {program.subtitle ? `${program.subtitle} · ` : ''}
                                {clock(program.start)}
                              </span>
                            ) : null}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            ))}
            <div
              className={s.guideNow}
              style={{ left: `calc(var(--g-chan) + ${x(now)}px)` }}
              aria-hidden="true"
            />
          </div>
        </div>
      ) : null}
    </>
  );
}
