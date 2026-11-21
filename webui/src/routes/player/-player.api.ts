import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  CatalogBook,
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
    staleTime: 5 * MINUTE,
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

export interface UserPlaylist {
  id: number;
  name: string;
  track_count?: number;
  image_url?: string | null;
}

export const userPlaylistsQuery = () =>
  queryOptions({
    queryKey: ['player', 'playlists'],
    queryFn: async () =>
      (await apiClient.get('user-playlists').json<{ playlists?: UserPlaylist[] }>()).playlists ??
      [],
    staleTime: 5 * MINUTE,
  });

export interface PlaylistTrack {
  id: number;
  track_name: string;
  artist_name?: string | null;
  duration_ms?: number | null;
}

export const playlistTracksQuery = (id: number) =>
  queryOptions({
    queryKey: ['player', 'playlist', id],
    queryFn: async () =>
      (await apiClient.get(`mirrored-playlists/${id}`).json<{ tracks?: PlaylistTrack[] }>())
        .tracks ?? [],
    staleTime: 5 * MINUTE,
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

export const showFilesQuery = (id: number, season: number) =>
  queryOptions({
    queryKey: ['player', 'show-files', id, season],
    queryFn: () =>
      apiClient
        .get(`video/detail/show/${id}/library`, { searchParams: { season } })
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

export const trailerQuery = (kind: 'movie' | 'show', id: number) =>
  queryOptions({
    queryKey: ['player', 'trailer', kind, id],
    queryFn: async () =>
      (
        await apiClient
          .get(`video/detail/${kind}/${id}/extras`)
          .json<{ trailer?: Trailer | null }>()
      ).trailer ?? null,
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
