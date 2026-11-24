import { infiniteQueryOptions, queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type { HomeLayout, LayoutCatalog } from './-lib/home-layout';
import type { ServerResume } from './-lib/rails';
import type {
  CatalogBook,
  Extras,
  ExtraTitle,
  LibAlbum,
  LibAlbumDetail,
  LibArtist,
  LibArtistDetail,
  LibSong,
  MovieDetail,
  MovieLibraryPayload,
  OwnedBook,
  Pagination,
  PlayerProfile,
  Person,
  PodcastShow,
  PodcastSub,
  ProgressMeta,
  ProgressRow,
  ShowDetail,
  ShowLibraryPayload,
  VideoCatalog,
  YtChannel,
  YtVideo,
} from './-player.types';

import { decodeCatalog, type CatalogWire } from './-lib/catalog';

const MINUTE = 60_000;

// ── progress (the player's own store) ──────────────────────────────────

export const continueQuery = (itemTypes: string[] = []) =>
  queryOptions({
    queryKey: ['player', 'continue', itemTypes.join(',')],
    queryFn: async () => {
      const body = await readJson<{ items?: ProgressRow[] }>(
        apiClient.get('player/continue', {
          searchParams: itemTypes.length
            ? { item_type: itemTypes.join(','), limit: 30 }
            : { limit: 30 },
        }),
      );
      return body.items ?? [];
    },
    staleTime: 30_000,
  });

export async function removeProgress(itemType: string, itemId: string): Promise<void> {
  await apiClient.delete('player/progress', {
    searchParams: { item_type: itemType, item_id: itemId },
  });
}

export async function finishProgress(
  itemType: string,
  itemId: string,
  meta?: ProgressMeta | null,
): Promise<ProgressRow | null> {
  const body = await apiClient
    .post('player/progress/finish', { json: { item_type: itemType, item_id: itemId, meta } })
    .json<{ progress?: ProgressRow }>();
  return body.progress ?? null;
}

export const progressLookupQuery = (itemType: string, itemIds: string[]) =>
  queryOptions({
    queryKey: ['player', 'progress', itemType, itemIds.join(',')],
    queryFn: async () => {
      if (!itemIds.length) return {} as Record<string, ProgressRow>;
      const body = await apiClient
        .post('player/progress/lookup', {
          json: { item_type: itemType, item_ids: itemIds.slice(0, 500) },
        })
        .json<{ progress?: Record<string, ProgressRow> }>();
      return body.progress ?? {};
    },
    staleTime: 30_000,
  });

export const videoCatalogQuery = () =>
  queryOptions({
    queryKey: ['player', 'video-catalog'],
    queryFn: async (): Promise<VideoCatalog> =>
      decodeCatalog(await apiClient.get('player/video-catalog').json<CatalogWire>()),
    staleTime: 30_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });

// ── music (library v2) ─────────────────────────────────────────────────

export const artistsQuery = (opts: {
  page?: number;
  limit?: number;
  sort?: string;
  search?: string;
}) =>
  queryOptions({
    queryKey: ['player', 'music', 'artists', opts],
    queryFn: () =>
      apiClient
        .get('library/v2/artists', {
          searchParams: {
            page: opts.page ?? 1,
            limit: opts.limit ?? 120,
            sort: opts.sort ?? 'name',
            ...(opts.search ? { search: opts.search } : {}),
          },
        })
        .json<{ artists?: LibArtist[]; pagination?: Pagination }>(),
    staleTime: 2 * MINUTE,
  });

export const albumsQuery = (opts: {
  page?: number;
  limit?: number;
  sort?: string;
  search?: string;
  genre?: string;
  label?: string;
}) =>
  queryOptions({
    queryKey: ['player', 'music', 'albums', opts],
    queryFn: () =>
      apiClient
        .get('library/v2/albums', {
          searchParams: {
            page: opts.page ?? 1,
            limit: opts.limit ?? 120,
            sort: opts.sort ?? 'added',
            ...(opts.search ? { search: opts.search } : {}),
            ...(opts.genre ? { genre: opts.genre } : {}),
            ...(opts.label ? { label: opts.label } : {}),
          },
        })
        .json<{ albums?: LibAlbum[]; pagination?: Pagination }>(),
    staleTime: 2 * MINUTE,
  });

export const songsQuery = (opts: {
  page?: number;
  limit?: number;
  sort?: string;
  search?: string;
}) =>
  queryOptions({
    queryKey: ['player', 'music', 'songs', opts],
    queryFn: () =>
      apiClient
        .get('library/v2/songs', {
          searchParams: {
            page: opts.page ?? 1,
            limit: opts.limit ?? 100,
            sort: opts.sort ?? 'title',
            ...(opts.search ? { search: opts.search } : {}),
          },
        })
        .json<{ songs?: LibSong[]; pagination?: Pagination }>(),
    staleTime: 5 * MINUTE,
  });

export const artistQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'music', 'artist', id],
    queryFn: async () =>
      (await apiClient.get(`library/v2/artists/${id}`).json<{ artist?: LibArtistDetail }>())
        .artist ?? null,
    staleTime: 2 * MINUTE,
  });

export const albumQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'music', 'album', id],
    queryFn: async () =>
      (await apiClient.get(`library/v2/albums/${id}`).json<{ album?: LibAlbumDetail }>()).album ??
      null,
    staleTime: 2 * MINUTE,
  });

// ── playlists (music): yours and mirrored ─────────────────────────────

export interface PlaylistSummary {
  id: number;
  name: string;
  description: string | null;
  /** yours: you made it (editable); mirrored: from spotify, deezer and the rest */
  kind: 'yours' | 'mirrored';
  liked: boolean;
  source: string | null;
  owner: string | null;
  track_count: number;
  image_url: string | null;
  cover_tiles: string[];
  updated_at: string | null;
}

export interface PlaylistSong {
  /** 1-based, in order: what remove and reorder take */
  position: number;
  title: string;
  artist: string;
  album: string;
  album_id: number | null;
  artist_id: number | null;
  duration_ms: number;
  image_url: string | null;
  added_at: string | null;
  /** the library track it plays; null: not in your library, or hidden from this profile */
  track_id: number | null;
  /** why it can't play: hidden as explicit for this profile, or not in the library */
  unplayable?: 'explicit' | 'missing' | null;
}

export interface PlaylistDetail extends PlaylistSummary {
  songs: PlaylistSong[];
  /** this profile hides explicit music */
  hides_explicit?: boolean;
  duration_ms: number;
  playable_count: number;
}

export const playlistsQuery = () =>
  queryOptions({
    queryKey: ['player', 'playlists'],
    queryFn: async () => {
      const body = await apiClient
        .get('player/playlists')
        .json<{ yours?: PlaylistSummary[]; mirrored?: PlaylistSummary[] }>();
      return { yours: body.yours ?? [], mirrored: body.mirrored ?? [] };
    },
    staleTime: 2 * MINUTE,
  });

export const playlistQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'playlist', id],
    queryFn: async () =>
      (await apiClient.get(`player/playlists/${id}`).json<{ playlist: PlaylistDetail }>()).playlist,
    staleTime: MINUTE,
  });

// ── discovery playlists: soulsync's own (flow, daily mixes, fresh tape…) ──

export type DiscoverSection = 'made' | 'fresh' | 'picks' | 'listenbrainz';

export interface DiscoverCard {
  /** stable, url-safe: "flow", "daily-2", "lb-12" */
  key: string;
  name: string;
  blurb: string;
  section: DiscoverSection;
  /** every song is in the library */
  owned: boolean;
  track_count: number | null;
  cover_tiles: string[];
  updated_at: string | null;
}

export interface DiscoverSong extends Omit<PlaylistSong, 'added_at' | 'artist_id'> {
  artist_id: number | null;
  /** already on this profile's wishlist */
  wanted: boolean;
}

export interface DiscoverDetail extends DiscoverCard {
  songs: DiscoverSong[];
  playable_count: number;
  hides_explicit: boolean;
}

export const discoverPlaylistsQuery = () =>
  queryOptions({
    queryKey: ['player', 'discover-playlists'],
    queryFn: async () => {
      const res = await apiClient.get('player/discover-playlists', { throwHttpErrors: false });
      // discover turned off for this profile: none, quietly
      if (res.status === 403) return [];
      if (!res.ok) throw new Error(`discover playlists: ${res.status}`);
      const body = await res.json<{
        sections?: { id: DiscoverSection; label: string; playlists: DiscoverCard[] }[];
      }>();
      return body.sections ?? [];
    },
    staleTime: 5 * MINUTE,
  });

export const discoverPlaylistQuery = (key: string) =>
  queryOptions({
    queryKey: ['player', 'discover-playlist', key],
    queryFn: async () =>
      (
        await apiClient
          .get(`player/discover-playlists/${encodeURIComponent(key)}`)
          .json<{ playlist: DiscoverDetail }>()
      ).playlist,
    staleTime: MINUTE,
  });

/** the songs you don't have, onto your wishlist: soulsync gets them, then they play */
export async function getDiscoverSongs(key: string, positions?: number[]) {
  return apiClient
    .post(`player/discover-playlists/${encodeURIComponent(key)}/get`, {
      json: positions ? { positions } : {},
    })
    .json<{ added: number; already: number; skipped: number }>();
}

/** a song as the playlist routes take it: by name, with its library track */
export interface SongRef {
  title: string;
  artist: string;
  album?: string | null;
  duration_ms?: number | null;
  lib2_track_id?: number | null;
}

function songBody(songs: SongRef[]) {
  return songs.map((x) => ({
    title: x.title,
    artist: x.artist,
    album: x.album ?? undefined,
    duration_ms: x.duration_ms ?? undefined,
    ...(x.lib2_track_id ? { lib2_track_id: x.lib2_track_id } : {}),
  }));
}

/** your playlists: every edit the manager's playlists page has */
export const playlistEdits = {
  create: async (name: string, songs: SongRef[] = []) =>
    apiClient
      .post('user-playlists', {
        json: { name, ...(songs.length ? { tracks: songBody(songs) } : {}) },
      })
      .json<{ id: number; added?: number }>(),
  add: async (id: number, songs: SongRef[], allowDuplicates = false) =>
    apiClient
      .post(`user-playlists/${id}/tracks`, {
        json: { tracks: songBody(songs), allow_duplicates: allowDuplicates },
      })
      .json<{ added: number; duplicates: unknown[]; track_count: number }>(),
  /** untick in the picker: these songs, wherever they are in it */
  removeSongs: async (id: number, songs: SongRef[]) =>
    apiClient
      .post(`user-playlists/${id}/tracks/remove`, { json: { tracks: songBody(songs) } })
      .json(),
  removeAt: async (id: number, position: number) =>
    apiClient.delete(`user-playlists/${id}/tracks/${position}`).json(),
  reorder: async (id: number, order: number[]) =>
    apiClient.put(`user-playlists/${id}/order`, { json: { order } }).json(),
  rename: async (id: number, name: string) =>
    apiClient.patch(`mirrored-playlists/${id}/custom-name`, { json: { custom_name: name } }).json(),
  describe: async (id: number, description: string) =>
    apiClient.patch(`user-playlists/${id}`, { json: { description } }).json(),
  duplicate: async (id: number, name?: string) =>
    apiClient
      .post(`user-playlists/${id}/duplicate`, { json: name ? { name } : {} })
      .json<{ id: number }>(),
  remove: async (id: number) => apiClient.delete(`mirrored-playlists/${id}`).json(),
  /** a mirrored playlist made yours: an editable copy, the mirror keeps syncing */
  copyMirror: async (id: number, name?: string) =>
    apiClient
      .post(`user-playlists/from-mirror/${id}`, { json: name ? { name } : {} })
      .json<{ id: number }>(),
  /** which of your playlists already hold these songs */
  membership: async (songs: SongRef[]) =>
    apiClient
      .post('user-playlists/membership', { json: { tracks: songBody(songs) } })
      .json<{ total: number; playlists: Record<string, number> }>(),
  like: async (songs: SongRef[]) =>
    apiClient.post('user-playlists/liked', { json: { tracks: songBody(songs) } }).json(),
  unlike: async (songs: SongRef[]) =>
    apiClient.post('user-playlists/liked/remove', { json: { tracks: songBody(songs) } }).json(),
};

// ── player settings (per profile) and watching now ────────────────────

export type PlayerAccentColor = 'emerald' | 'violet' | 'ocean' | 'amber' | 'rose' | 'crimson';

export type CommercialEra = 'auto' | '70s' | '80s' | '90s' | '2000s' | 'kids' | 'mix';
export type CommercialMode = 'short' | 'cable';

export interface PlayerSettings {
  share_activity: boolean;
  autoplay_next: boolean;
  still_watching_after: number;
  show_movie_ratings: boolean;
  show_tv_episode_count: boolean;
  show_year_on_cards: boolean;
  accent_color?: PlayerAccentColor;
  commercials_enabled?: boolean;
  commercials_era?: CommercialEra;
  commercials_mode?: CommercialMode;
}

export interface CommercialClip {
  id: string;
  title: string;
  era: string;
  duration_seconds: number;
  source: string;
  stream_path: string;
}

export interface CommercialsStatus {
  ready: boolean;
  total_count: number;
  cached_count: number;
  local_count: number;
  cache_mb: number;
  warming: boolean;
  eras: Record<string, number>;
  error?: string | null;
}

export const commercialsStatusQuery = () =>
  queryOptions({
    queryKey: ['player', 'commercials-status'],
    queryFn: () => apiClient.get('player/commercials/status').json<CommercialsStatus>(),
    staleTime: 15_000,
  });

export async function warmCommercials(era: CommercialEra = 'mix'): Promise<CommercialsStatus> {
  return apiClient.post('player/commercials/warm', { json: { era } }).json<CommercialsStatus>();
}

export async function fetchPlayerSettings(): Promise<PlayerSettings | null> {
  try {
    const res = await apiClient
      .get('player/settings')
      .json<{ settings?: PlayerSettings }>();
    return res.settings ?? null;
  } catch {
    return null;
  }
}

export async function fetchCommercialBreak(opts: {
  era?: CommercialEra;
  mode?: CommercialMode;
  channelId?: string;
  channelName?: string;
}): Promise<CommercialClip[]> {
  try {
    const res = await apiClient
      .get('player/commercials/break', {
        searchParams: {
          ...(opts.era ? { era: opts.era } : {}),
          ...(opts.mode ? { mode: opts.mode } : {}),
          ...(opts.channelId ? { channel_id: opts.channelId } : {}),
          ...(opts.channelName ? { channel_name: opts.channelName } : {}),
        },
        timeout: 2500,
      })
      .json<{ clips?: CommercialClip[] }>();
    return res.clips ?? [];
  } catch {
    return [];
  }
}

export const playerSettingsQuery = () =>
  queryOptions({
    queryKey: ['player', 'settings'],
    queryFn: async () =>
      apiClient
        .get('player/settings')
        .json<{ settings: PlayerSettings; about: Record<keyof PlayerSettings, string> }>(),
    staleTime: 5 * MINUTE,
  });

export async function savePlayerSettings(changes: Partial<PlayerSettings>) {
  return (
    await apiClient.put('player/settings', { json: changes }).json<{ settings: PlayerSettings }>()
  ).settings;
}

export interface WatchingSession {
  profile_id: number;
  profile: { name: string; avatar_color?: string | null; avatar_url?: string | null };
  item_type: string;
  item_id: string;
  position: number;
  duration: number | null;
  playing: boolean;
  live: boolean;
  channel: { id: string; name: string; number: number | null } | null;
  meta: ProgressMeta;
  started: number;
  seen: number;
  /** this profile's own screen */
  you?: boolean;
}

export const watchingNowQuery = () =>
  queryOptions({
    queryKey: ['player', 'watching-now'],
    queryFn: async () =>
      apiClient.get('player/presence').json<{ sessions: WatchingSession[]; sharing: boolean }>(),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });

export interface TogetherPlace {
  item_type: string;
  item_id: string;
  position: number;
  duration: number | null;
  finished: boolean;
  meta: ProgressMeta;
  updated: number;
}

export interface TogetherTitle {
  key: string;
  mine: TogetherPlace;
  others: (TogetherPlace & { profile_id: number; profile: WatchingSession['profile'] })[];
}

export const togetherQuery = () =>
  queryOptions({
    queryKey: ['player', 'together'],
    queryFn: async () =>
      (await apiClient.get('player/presence/together').json<{ titles: TogetherTitle[] }>()).titles,
    staleTime: MINUTE,
  });

// ── watch parties (watch together + theater waiting rooms) ────────────

export interface WatchPartyLobbyTrack {
  track_id: string;
  title: string;
  artist: string;
  album_id: number | null;
  artwork: string | null;
  duration_ms: number | null;
  started_at: number;
  live_position?: number;
}

export interface WatchPartyMember {
  profile_id: number;
  profile: WatchingSession['profile'];
  status: 'rsvp' | 'joined';
  ready: boolean;
  joined_at: number;
  seen_at: number;
  is_host: boolean;
  you: boolean;
}

export interface WatchPartyMessage {
  id: number;
  profile_id: number;
  profile: WatchingSession['profile'];
  kind: 'chat' | 'reaction' | 'system';
  body: string;
  created_at: number;
  you: boolean;
}

export interface WatchPartyPeeBreak {
  profile_id: number;
  profile?: WatchingSession['profile'];
  started_at: number;
  ends_at: number;
}

export interface WatchParty {
  id: string;
  host_profile_id: number;
  host_profile: WatchingSession['profile'];
  is_host: boolean;
  is_member: boolean;
  can_control: boolean;
  majority_ready?: boolean;
  title: string;
  item_type: 'movie' | 'episode' | 'youtube';
  item_id: string;
  meta: ProgressMeta;
  pee_break?: WatchPartyPeeBreak | null;
  resume_at?: number | null;
  pee_cooldown_remaining?: number;
  show_id: number | null;
  status: 'scheduled' | 'lobby' | 'playing' | 'paused' | 'ended';
  scheduled_for: number | null;
  control_mode: 'host' | 'everyone';
  position: number;
  position_at: number;
  live_position: number;
  server_now: number;
  lobby_track: WatchPartyLobbyTrack | null;
  members: WatchPartyMember[];
  member_count: number;
  ready_count: number;
  messages: WatchPartyMessage[];
  created_at: number;
  updated_at: number;
}

export const watchPartiesQuery = () =>
  queryOptions({
    queryKey: ['player', 'parties'],
    queryFn: async () =>
      (await apiClient.get('player/parties').json<{ parties: WatchParty[] }>()).parties,
    staleTime: 4_000,
    refetchInterval: 6_000,
  });

export const watchPartyQuery = (partyId: string) =>
  queryOptions({
    queryKey: ['player', 'party', partyId],
    queryFn: async () =>
      (
        await apiClient
          .get(`player/parties/${encodeURIComponent(partyId)}`)
          .json<{ party: WatchParty }>()
      ).party,
    staleTime: 1_500,
    refetchInterval: 2_500,
  });

export const watchPartyApi = {
  get: async (partyId: string): Promise<WatchParty> =>
    (
      await apiClient
        .get(`player/parties/${encodeURIComponent(partyId)}`)
        .json<{ party: WatchParty }>()
    ).party,
  create: async (payload: {
    title?: string;
    item_type: 'movie' | 'episode' | 'youtube';
    item_id: string;
    meta?: ProgressMeta;
    show_id?: number | null;
    scheduled_for?: number | null;
    control_mode?: 'host' | 'everyone';
    lobby_music?: boolean;
  }): Promise<WatchParty> =>
    (await apiClient.post('player/parties', { json: payload }).json<{ party: WatchParty }>()).party,
  join: async (partyId: string, opts?: { ready?: boolean; rsvp?: boolean }): Promise<WatchParty> =>
    (
      await apiClient
        .post(`player/parties/${encodeURIComponent(partyId)}/join`, { json: opts ?? {} })
        .json<{ party: WatchParty }>()
    ).party,
  leave: async (partyId: string): Promise<void> => {
    await apiClient.post(`player/parties/${encodeURIComponent(partyId)}/leave`).json();
  },
  state: async (
    partyId: string,
    payload: {
      action?: 'start' | 'play' | 'resume' | 'pause' | 'pee_break' | 'seek' | 'lobby' | 'end';
      position?: number;
      shuffle_lobby_music?: boolean;
      control_mode?: 'host' | 'everyone';
    },
  ): Promise<WatchParty> =>
    (
      await apiClient
        .post(`player/parties/${encodeURIComponent(partyId)}/state`, { json: payload })
        .json<{ party: WatchParty }>()
    ).party,
  chat: async (
    partyId: string,
    kind: 'chat' | 'reaction',
    body: string,
  ): Promise<WatchPartyMessage> =>
    (
      await apiClient
        .post(`player/parties/${encodeURIComponent(partyId)}/chat`, { json: { kind, body } })
        .json<{ message: WatchPartyMessage }>()
    ).message,
  remove: async (partyId: string): Promise<void> => {
    await apiClient.delete(`player/parties/${encodeURIComponent(partyId)}`).json();
  },
};

// ── notes: comments on a title, marks on a moment ─────────────────────

export interface Note {
  id: number;
  profile_id: number;
  profile: WatchingSession['profile'];
  item_type: string;
  item_id: string;
  /** a mark's moment, in seconds; null: a comment */
  at: number | null;
  body: string;
  shared: boolean;
  mine: boolean;
  created_at: number;
  edited_at: number | null;
}

export const notesQuery = (itemType: string, itemId: string) =>
  queryOptions({
    queryKey: ['player', 'notes', itemType, itemId],
    queryFn: async () =>
      (
        await apiClient
          .get(`player/notes/${itemType}/${encodeURIComponent(itemId)}`)
          .json<{ notes: Note[] }>()
      ).notes,
    staleTime: 30_000,
  });

export const noteEdits = {
  add: async (
    itemType: string,
    itemId: string,
    note: { body?: string; at?: number; shared?: boolean },
  ) =>
    (
      await apiClient
        .post(`player/notes/${itemType}/${encodeURIComponent(itemId)}`, { json: note })
        .json<{ note: Note }>()
    ).note,
  edit: async (id: number, changes: { body?: string; shared?: boolean }) =>
    (await apiClient.patch(`player/note/${id}`, { json: changes }).json<{ note: Note }>()).note,
  remove: async (id: number) => apiClient.delete(`player/note/${id}`).json(),
};

// ── an artist's most played songs ─────────────────────────────────────

export interface TopTrack {
  track_id: number;
  title: string;
  artist: string;
  duration_ms: number;
  album_id: number;
  album: string;
  plays: number;
  explicit: boolean;
}

export const topTracksQuery = (artistId: number) =>
  queryOptions({
    queryKey: ['player', 'music', 'artist-top', artistId],
    queryFn: async () =>
      (
        await apiClient
          .get(`player/music/artist/${artistId}/top`, { searchParams: { limit: 10 } })
          .json<{ tracks: TopTrack[] }>()
      ).tracks,
    staleTime: 5 * MINUTE,
  });

// ── the guide's made channels and this profile's lineup ───────────────

export const guideChannelsQuery = () =>
  queryOptions({
    queryKey: ['player', 'guide', 'channels'],
    queryFn: async () =>
      apiClient.get('player/guide/channels').json<{
        server: import('./-lib/channels').SavedChannel[];
        mine: import('./-lib/channels').SavedChannel[];
        can_edit_server: boolean;
      }>(),
    staleTime: 5 * MINUTE,
  });

export const lineupQuery = () =>
  queryOptions({
    queryKey: ['player', 'guide', 'lineup'],
    queryFn: async () =>
      (
        await apiClient
          .get('player/guide/lineup')
          .json<{ entries: import('./-lib/channels').LineupEntry[] }>()
      ).entries,
    staleTime: 5 * MINUTE,
  });

export const guideEdits = {
  add: async (scope: 'server' | 'mine', spec: import('./-lib/channels').ChannelSpec) =>
    (
      await apiClient
        .post('player/guide/channels', { json: { scope, spec } })
        .json<{ channel: import('./-lib/channels').SavedChannel }>()
    ).channel,
  edit: async (id: string, spec: import('./-lib/channels').ChannelSpec) =>
    (
      await apiClient
        .put(`player/guide/channels/${id}`, { json: { spec } })
        .json<{ channel: import('./-lib/channels').SavedChannel }>()
    ).channel,
  remove: async (id: string) => apiClient.delete(`player/guide/channels/${id}`).json(),
  saveLineup: async (entries: import('./-lib/channels').LineupEntry[]) =>
    (
      await apiClient
        .put('player/guide/lineup', { json: { entries } })
        .json<{ entries: import('./-lib/channels').LineupEntry[] }>()
    ).entries,
};

// ── what the guide knows beyond its catalog ───────────────────────────

export const guideWatchedQuery = () =>
  queryOptions({
    queryKey: ['player', 'guide', 'watched'],
    queryFn: async () =>
      apiClient.get('player/guide/watched').json<{ movies: number[]; episodes: number[] }>(),
    staleTime: 10 * MINUTE,
  });

export interface GuidePerson {
  id: number;
  name: string;
  photo_url: string | null;
  titles: number;
  job: string | null;
}

export async function findPeople(q: string): Promise<GuidePerson[]> {
  return (
    await apiClient
      .get('player/guide/people', { searchParams: { q } })
      .json<{ people: GuidePerson[] }>()
  ).people;
}

export const personTitlesQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'guide', 'person', id],
    queryFn: async () =>
      apiClient
        .get(`player/guide/people/${id}`)
        .json<{ name: string; movies: number[]; shows: number[] }>(),
    staleTime: 30 * MINUTE,
  });

export interface Reminder {
  channel_id: string;
  starts_at: number;
  program_key: string;
  title: string;
  subtitle: string | null;
  channel_name: string | null;
}

export const remindersQuery = () =>
  queryOptions({
    queryKey: ['player', 'guide', 'reminders'],
    queryFn: async () =>
      (await apiClient.get('player/guide/reminders').json<{ reminders: Reminder[] }>()).reminders,
    staleTime: MINUTE,
  });

export const reminderEdits = {
  add: async (
    r: Omit<Reminder, 'subtitle' | 'channel_name'> & { subtitle?: string; channel_name?: string },
  ) => apiClient.post('player/guide/reminders', { json: r }).json(),
  remove: async (channelId: string, startsAt: number) =>
    apiClient
      .delete('player/guide/reminders', {
        searchParams: { channel_id: channelId, starts_at: String(startsAt) },
      })
      .json(),
};

// ── lyrics ─────────────────────────────────────────────────────────────

export interface Lyrics {
  success: boolean;
  synced: string | null;
  plain: string | null;
  /** sidecar: a .lrc beside the file; embedded: the file's tags; lrclib: lrclib.net */
  source: 'sidecar' | 'embedded' | 'lrclib' | null;
  error?: string;
  retryable?: boolean;
}

export const lyricsQuery = (trackId: string) =>
  queryOptions({
    queryKey: ['player', 'lyrics', trackId],
    queryFn: async (): Promise<Lyrics> => {
      const res = await apiClient.get(`player/lyrics/track/${trackId}`, {
        throwHttpErrors: false,
        timeout: 45_000,
      });
      return (await res.json()) as Lyrics;
    },
    staleTime: Number.POSITIVE_INFINITY,
    // a busy lrclib says so: try again later, not on every render
    retry: (n, e) => n < 1 && !!e,
  });

// ── video ──────────────────────────────────────────────────────────────

export const movieDetailQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'movie', id],
    queryFn: () => apiClient.get(`video/detail/movie/${id}`).json<MovieDetail>(),
    staleTime: 2 * MINUTE,
  });

export const movieFilesQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'movie-files', id],
    queryFn: () => apiClient.get(`video/detail/movie/${id}/library`).json<MovieLibraryPayload>(),
    staleTime: 2 * MINUTE,
  });

export const showDetailQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'show', id],
    queryFn: () => apiClient.get(`video/detail/show/${id}`).json<ShowDetail>(),
    staleTime: 2 * MINUTE,
  });

/**
 * every season's files for a show, in one request. no ?season: naming one
 * makes the server check each of that season's files on disk (the
 * manager's library tab wants that), which on a sleeping drive takes
 * longer than a page should wait. the player only needs what's on record.
 */
export const showFilesQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'show-files', id],
    queryFn: () =>
      apiClient
        .get(`video/detail/show/${id}/library`, { timeout: 60_000 })
        .json<ShowLibraryPayload>(),
    staleTime: 2 * MINUTE,
  });

export interface FollowedChannel {
  id: string;
  title: string;
  poster_url?: string | null;
  followed?: boolean;
  video_count?: number;
  owned_count?: number;
}

export const followedChannelsQuery = () =>
  queryOptions({
    queryKey: ['player', 'yt-channels'],
    queryFn: async () =>
      (
        await apiClient
          .get('video/library', { searchParams: { kind: 'channels', limit: 200 } })
          .json<{ items?: FollowedChannel[] }>()
      ).items ?? [],
    staleTime: 5 * MINUTE,
  });

// ── podcasts + audiobooks ──────────────────────────────────────────────

export const podcastSubsQuery = () =>
  queryOptions({
    queryKey: ['player', 'podcasts'],
    queryFn: async () =>
      (await apiClient.get('podcasts/watchlist').json<{ podcasts?: PodcastSub[] }>()).podcasts ??
      [],
    staleTime: 5 * MINUTE,
  });

export const podcastShowQuery = (feedUrl: string) =>
  queryOptions({
    queryKey: ['player', 'podcast', feedUrl],
    queryFn: async () =>
      (
        await readJson<{ show?: PodcastShow }>(
          apiClient.get('podcasts/show', { searchParams: { url: feedUrl } }),
        )
      ).show ?? null,
    staleTime: 10 * MINUTE,
  });

export const booksQuery = () =>
  queryOptions({
    queryKey: ['player', 'books'],
    queryFn: async () =>
      (await apiClient.get('audiobooks/library').json<{ books?: OwnedBook[] }>()).books ?? [],
    staleTime: 5 * MINUTE,
  });

export const catalogBookQuery = (asin: string) =>
  queryOptions({
    queryKey: ['player', 'book', asin],
    queryFn: async () =>
      (
        await apiClient
          .get(`audiobooks/book/${encodeURIComponent(asin)}`)
          .json<{ book?: CatalogBook }>()
      ).book ?? null,
    staleTime: 30 * MINUTE,
    retry: false,
  });

// ── profiles ───────────────────────────────────────────────────────────

export const profilesQuery = () =>
  queryOptions({
    queryKey: ['player', 'profiles'],
    queryFn: async () =>
      (await apiClient.get('profiles').json<{ profiles?: PlayerProfile[] }>()).profiles ?? [],
    staleTime: MINUTE,
  });

export type SelectResult =
  | { ok: true }
  | { ok: false; needs: 'pin' | 'password'; error?: string }
  | { ok: false; needs?: undefined; error: string };

/**
 * the same call the manager's picker makes (init.js selectProfile). the
 * caller reloads afterwards, as the manager does, so everything fetched for
 * the old profile is fetched again for the new one.
 */
export async function selectProfile(
  profileId: number,
  secret?: { pin?: string; password?: string },
): Promise<SelectResult> {
  const res = await apiClient.post('profiles/select', {
    json: {
      profile_id: profileId,
      ...(secret?.pin ? { pin: secret.pin } : {}),
      ...(secret?.password ? { password: secret.password } : {}),
    },
    throwHttpErrors: false,
  });
  let body: {
    success?: boolean;
    error?: string;
    pin_required?: boolean;
    password_required?: boolean;
  } = {};
  try {
    body = await res.json();
  } catch {
    body = {};
  }
  if (res.ok && body.success) {
    try {
      sessionStorage.setItem('ss-profile-picked-at', String(Date.now()));
    } catch {
      /* private mode */
    }
    return { ok: true };
  }
  if (body.password_required || body.error === 'password_required')
    return {
      ok: false,
      needs: 'password',
      error: secret?.password ? 'That password is not right' : undefined,
    };
  if (body.pin_required || body.error === 'pin_required')
    return { ok: false, needs: 'pin', error: secret?.pin ? 'That PIN is not right' : undefined };
  if (res.status === 429) return { ok: false, error: 'Too many tries. Wait a moment.' };
  return { ok: false, error: body.error || 'Could not switch profile' };
}

// ── youtube ────────────────────────────────────────────────────────────

export const youtubeHomeQuery = () =>
  queryOptions({
    queryKey: ['player', 'youtube'],
    queryFn: async () => {
      const body = await apiClient
        .get('player/youtube')
        .json<{ channels?: YtChannel[]; latest?: YtVideo[]; downloads?: YtVideo[] }>();
      return {
        channels: body.channels ?? [],
        latest: body.latest ?? [],
        downloads: body.downloads ?? [],
      };
    },
    staleTime: 2 * MINUTE,
  });

export const youtubeChannelQuery = (channelId: string) =>
  queryOptions({
    queryKey: ['player', 'youtube', 'channel', channelId],
    queryFn: () =>
      readJson<{ channel: YtChannel; videos: YtVideo[] }>(
        apiClient.get(`player/youtube/channel/${encodeURIComponent(channelId)}`),
      ),
    staleTime: 2 * MINUTE,
    retry: false,
  });

// ── trailers (the video side's own extras + its billboard setting) ─────

export interface Trailer {
  key: string;
  name?: string | null;
}

/** the title's tmdb extras: trailer, videos, gallery, keywords, collection, picks */
export const extrasQuery = (kind: 'movie' | 'show', id: number) =>
  queryOptions({
    queryKey: ['player', 'extras', kind, id],
    queryFn: () => apiClient.get(`video/detail/${kind}/${id}/extras`).json<Extras>(),
    staleTime: 30 * MINUTE,
    retry: false,
  });

export const videoPrefsQuery = () =>
  queryOptions({
    queryKey: ['player', 'video-prefs'],
    queryFn: () => apiClient.get('video/prefs').json<{ billboard_autoplay?: boolean }>(),
    staleTime: 30 * MINUTE,
    retry: false,
  });

export interface EpisodeExtra {
  guest_stars?: {
    name: string;
    character?: string | null;
    tmdb_id?: number | null;
    photo?: string | null;
  }[];
  crew?: {
    name: string;
    job: string;
    tmdb_id?: number | null;
    photo?: string | null;
  }[];
  still_url?: string | null;
  rating?: number | null;
  overview?: string | null;
  runtime_minutes?: number | null;
  air_date?: string | null;
}

export const episodeExtraQuery = (tmdbId: number, season: number, episode: number) =>
  queryOptions({
    queryKey: ['player', 'episode-extra', tmdbId, season, episode],
    queryFn: () =>
      readJson<EpisodeExtra>(apiClient.get(`video/episode/${tmdbId}/${season}/${episode}`)),
    staleTime: 30 * MINUTE,
    retry: false,
  });

export type VideoIssueEntityType = 'movie' | 'show' | 'episode';

export async function submitVideoIssue(body: {
  entity_type: VideoIssueEntityType;
  entity_id: number;
  category: string;
  title: string;
  description?: string;
}): Promise<{ success: boolean; issue?: { id: number }; error?: string }> {
  const res = await apiClient.post('video/issues', {
    json: body,
    throwHttpErrors: false,
  });
  const data = (await res.json().catch(() => ({}))) as {
    success?: boolean;
    issue?: { id: number };
    error?: string;
  };
  if (!res.ok || data.success === false) {
    throw new Error(data.error || `Failed to submit issue (${res.status})`);
  }
  return { success: true, issue: data.issue };
}

export const personQuery = (tmdbId: number) =>
  queryOptions({
    queryKey: ['player', 'person', tmdbId],
    queryFn: () => readJson<Person>(apiClient.get(`video/person/${tmdbId}`)),
    staleTime: 30 * MINUTE,
    retry: false,
  });

/** login mode decides whether the menu offers sign out (the manager's rule) */
export const currentProfileQuery = () =>
  queryOptions({
    queryKey: ['player', 'current-profile'],
    queryFn: () => apiClient.get('profiles/current').json<{ login_mode?: boolean }>(),
    staleTime: 10 * MINUTE,
  });

// ── guide ──────────────────────────────────────────────────────────────

export const musicChannelsQuery = () =>
  queryOptions({
    queryKey: ['player', 'music-channels'],
    queryFn: async () =>
      (
        await apiClient
          .get('player/music-channels')
          .json<{ channels?: import('./-lib/channels').ServerMusicChannel[] }>()
      ).channels ?? [],
    staleTime: 5 * MINUTE,
    retry: false,
  });

export const favoritesQuery = () =>
  queryOptions({
    queryKey: ['player', 'guide-favorites'],
    queryFn: async () =>
      (await apiClient.get('player/guide/favorites').json<{ channels?: string[] }>()).channels ??
      [],
    staleTime: 5 * MINUTE,
  });

export async function saveFavorites(channels: string[]): Promise<string[]> {
  return (
    (
      await readJson<{ channels?: string[] }>(
        apiClient.put('player/guide/favorites', { json: { channels } }),
      )
    ).channels ?? []
  );
}

export const keywordQuery = (name: string) =>
  queryOptions({
    queryKey: ['player', 'keyword', name],
    queryFn: () =>
      readJson<{ found?: boolean; movies?: ExtraTitle[]; shows?: ExtraTitle[] }>(
        apiClient.get('player/keyword', { searchParams: { name } }),
      ),
    staleTime: 30 * MINUTE,
    retry: false,
  });

// ── titles you don't own, and asking for them ──────────────────────────

export interface TmdbTitle extends MovieDetail {
  redirect?: { source: string; kind: 'movie' | 'show'; id: number };
  poster_url?: string | null;
  backdrop_url?: string | null;
  season_count?: number | null;
  network?: string | null;
  trailer?: { key: string; name?: string | null } | null;
  recommendations?: ExtraTitle[];
}

/** the video side's tmdb detail (redirects to the library title when owned) */
export const tmdbTitleQuery = (kind: 'movie' | 'show', tmdbId: number) =>
  queryOptions({
    queryKey: ['player', 'tmdb', kind, tmdbId],
    queryFn: () => readJson<TmdbTitle>(apiClient.get(`video/tmdb/${kind}/${tmdbId}`)),
    staleTime: 30 * MINUTE,
    retry: false,
  });

export type RequestMode = 'download' | 'ask' | 'none';

/** what this profile may do per kind: get it, ask for it, or neither */
export const requestOptionsQuery = () =>
  queryOptions({
    queryKey: ['player', 'request-options'],
    queryFn: async () =>
      (
        await apiClient
          .get('video/requests/options')
          .json<{ modes?: { movie?: RequestMode; show?: RequestMode; youtube?: RequestMode } }>()
      ).modes ?? {},
    staleTime: 5 * MINUTE,
  });

export type RequestResult =
  | { state: 'got' }
  | { state: 'asked'; already: boolean }
  | { state: 'on-the-way' }
  | { state: 'owned' }
  | { state: 'error'; message: string };

/** file it through the video side's own request route: its rules, its quotas */
export async function requestTitle(body: {
  kind: 'movie' | 'show' | 'episode';
  tmdb_id: number;
  title: string;
  season?: number;
  episode?: number;
  year?: number | string | null;
  poster_url?: string | null;
  /** a profile that may download (admins always) gets it now: straight to the wishlist */
  get?: boolean;
}): Promise<RequestResult> {
  const res = await apiClient.post('video/requests', { json: body, throwHttpErrors: false });
  let data: {
    success?: boolean;
    got?: boolean;
    asked?: boolean;
    already?: boolean;
    on_the_way?: boolean;
    error?: string;
    message?: string;
  } = {};
  try {
    data = await res.json();
  } catch {
    data = {};
  }
  if (data.success && data.got) return { state: 'got' };
  if (data.success) return { state: 'asked', already: !!data.already };
  if (data.on_the_way) return { state: 'on-the-way' };
  if ((data.error ?? '').toLowerCase().includes('already in the library'))
    return { state: 'owned' };
  return { state: 'error', message: data.message || data.error || 'Could not ask for it' };
}

export type MusicTrackRequestResult =
  | { state: 'wishlist' }
  | { state: 'requested' }
  | { state: 'owned' }
  | { state: 'error'; message: string };

/** Put a missing album track onto this profile's wishlist (or pending music request queue). */
export async function requestMusicTrack(body: {
  title: string;
  artist: string;
  album?: string | null;
  album_id?: number | null;
  artist_id?: number | null;
  track_id?: number | string | null;
  track_number?: number | null;
  disc_number?: number | null;
  duration_ms?: number | null;
  year?: number | null;
  image_url?: string | null;
}): Promise<MusicTrackRequestResult> {
  const res = await apiClient.post('player/music/request-track', {
    json: body,
    throwHttpErrors: false,
  });
  let data: {
    success?: boolean;
    skipped?: boolean;
    mode?: 'download' | 'ask';
    error?: string;
    message?: string;
  } = {};
  try {
    data = await res.json();
  } catch {
    data = {};
  }
  if (data.success && data.skipped) return { state: 'owned' };
  if (data.success) return { state: data.mode === 'ask' ? 'requested' : 'wishlist' };
  return { state: 'error', message: data.message || data.error || 'Could not request track' };
}

/** the media server's own resume list, as the video side's dashboard shows it */
export const serverResumeQuery = () =>
  queryOptions({
    queryKey: ['player', 'continue', 'server'],
    queryFn: async () =>
      (await apiClient.get('video/dashboard/continue-watching').json<{ items?: ServerResume[] }>())
        .items ?? [],
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });

// ── discover: the video side's tmdb lists, owned titles stamped ────────

export const discoverListQuery = (key: string, pages = 2) =>
  queryOptions({
    queryKey: ['player', 'discover', key, pages],
    queryFn: async () =>
      (
        await apiClient
          .get('video/discover/list', { searchParams: { key, pages } })
          .json<{ items?: ExtraTitle[] }>()
      ).items ?? [],
    staleTime: 30 * MINUTE,
    retry: false,
  });

/** one wall blended from tmdb's picks for titles you own (more votes rank higher) */
export const discoverForYouQuery = () =>
  queryOptions({
    queryKey: ['player', 'discover', 'foryou'],
    queryFn: async () =>
      (await apiClient.get('video/discover/foryou').json<{ items?: ExtraTitle[] }>()).items ?? [],
    staleTime: 30 * MINUTE,
    retry: false,
  });

/** "more like <a title you own>" rails */
export const discoverMoreLikeQuery = () =>
  queryOptions({
    queryKey: ['player', 'discover', 'morelike'],
    queryFn: async () =>
      (
        await apiClient
          .get('video/discover/morelike')
          .json<{ rails?: { title: string; items: ExtraTitle[] }[] }>()
      ).rails ?? [],
    staleTime: 30 * MINUTE,
    retry: false,
  });

export interface StudioCollection {
  id: number;
  name: string;
  media_type: 'movie' | 'show';
  summary?: string | null;
  poster?: string | null;
  member_posters?: string[];
  pinned?: boolean;
  /** library ids of the members this profile can see */
  members: number[];
  /** list members not in the library */
  missing?: number;
}

/** the collections built in collection studio, with what you can see of each */
export const studioCollectionsQuery = () =>
  queryOptions({
    queryKey: ['player', 'collections'],
    queryFn: async () =>
      (
        await apiClient
          .get('player/collections', { timeout: 60_000 })
          .json<{ collections?: StudioCollection[] }>()
      ).collections ?? [],
    staleTime: 10 * MINUTE,
  });

export type MakerSort = 'popular' | 'newest' | 'rated';

export interface MakerPage {
  found?: boolean;
  card?: {
    name?: string | null;
    logo?: string | null;
    description?: string | null;
    headquarters?: string | null;
    origin_country?: string | null;
  } | null;
  titles: ExtraTitle[];
  page?: number;
  has_more?: boolean;
}

/** everything a studio or a network made, a page at a time */
export const makerQuery = (kind: 'studio' | 'network', name: string, sort: MakerSort) =>
  infiniteQueryOptions({
    queryKey: ['player', 'maker', kind, name, sort],
    queryFn: async ({ pageParam }) =>
      await apiClient
        .get('player/maker', { searchParams: { kind, name, sort, page: pageParam } })
        .json<MakerPage>(),
    initialPageParam: 1,
    getNextPageParam: (last, all) => (last.has_more ? all.length + 1 : undefined),
    staleTime: 30 * MINUTE,
    retry: false,
  });

export interface TmdbSearchHit {
  kind: 'movie' | 'show' | 'person';
  tmdb_id: number;
  title: string;
  year?: string | number | null;
  poster?: string | null;
  library_id?: number | null;
  known_for?: string | null;
  department?: string | null;
}

/** tmdb's multi-search, as the video side runs it (owned ones stamped, kids capped) */
export const tmdbSearchQuery = (q: string) =>
  queryOptions({
    queryKey: ['player', 'tmdb-search', q],
    queryFn: async () =>
      (
        await apiClient
          .get('video/search', { searchParams: { q } })
          .json<{ results?: TmdbSearchHit[] }>()
      ).results ?? [],
    staleTime: 5 * MINUTE,
    retry: false,
  });

/** tmdb title logos for a few titles ("movie:603") */
export const logosQuery = (items: string[]) =>
  queryOptions({
    queryKey: ['player', 'logos', items],
    queryFn: async () =>
      (
        await apiClient
          .get('player/logos', { searchParams: { items: items.join(',') } })
          .json<{ logos?: Record<string, string | null> }>()
      ).logos ?? {},
    staleTime: 60 * MINUTE,
    enabled: items.length > 0,
  });

// ── home layout, per profile and per screen ────────────────────────────

export const homeLayoutQuery = (device: string) =>
  queryOptions({
    queryKey: ['player', 'home-layout', device],
    queryFn: async () =>
      await apiClient.get('player/home-layout', { searchParams: { device } }).json<{
        layout?: HomeLayout;
        scope?: 'device' | 'all' | null;
        catalog?: LayoutCatalog;
      }>(),
    staleTime: 5 * MINUTE,
  });

/** save for this screen, or for every screen of the profile ('*') */
export async function saveHomeLayout(layout: unknown, device: string, fromDevice?: string) {
  await apiClient.put('player/home-layout', {
    json: { layout, device, ...(fromDevice ? { from_device: fromDevice } : {}) },
  });
}

/** this screen follows the shared layout again; without a device, all go to the default */
export async function resetHomeLayout(device?: string) {
  await apiClient.delete('player/home-layout', {
    searchParams: device ? { device } : {},
  });
}

// ── watchlist (per profile, in player.db) ──────────────────────────────

export type WatchlistKind = 'movie' | 'show' | 'youtube';

export interface WatchlistItem {
  item_type: WatchlistKind;
  item_id: string;
  meta?: ProgressMeta | null;
  added_at: number;
}

export const watchlistQuery = () =>
  queryOptions({
    queryKey: ['player', 'watchlist'],
    queryFn: async () =>
      (await apiClient.get('player/watchlist').json<{ items?: WatchlistItem[] }>()).items ?? [],
    staleTime: MINUTE,
  });

export async function addToWatchlist(
  item_type: WatchlistKind,
  item_id: string,
  meta?: ProgressMeta,
) {
  await apiClient.put('player/watchlist', { json: { item_type, item_id, meta } });
}

export async function removeFromWatchlist(item_type: WatchlistKind, item_id: string) {
  await apiClient.delete('player/watchlist', { searchParams: { item_type, item_id } });
}

export interface ArtistQueueRow {
  track_id: number;
  track_title: string;
  duration?: number | null;
  album_id: number;
  album_title?: string | null;
  album_image_url?: string | null;
  artist_name?: string | null;
  file_state: 'active' | 'missing';
}

/** what an artist's play button queues: their tracks on disk, credits included */
export async function artistQueue(artistId: number): Promise<ArtistQueueRow[]> {
  const body = await apiClient
    .get(`library/v2/artists/${artistId}/play-queue`, { searchParams: { limit: 500 } })
    .json<{ files?: ArtistQueueRow[] }>();
  return (body.files ?? []).filter((f) => f.file_state === 'active');
}

/** how an item plays: its length, parts and chapters (an audiobook's, read from its files) */
export const playableQuery = (itemType: string, itemId: string) =>
  queryOptions({
    queryKey: ['player', 'playable', itemType, itemId],
    queryFn: async () =>
      await apiClient
        .get(`player/playable/${itemType}/${encodeURIComponent(itemId)}`)
        .json<{ duration?: number; chapters?: { start: number; title: string }[] }>(),
    staleTime: 10 * MINUTE,
    retry: false,
  });
