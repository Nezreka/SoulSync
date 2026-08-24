import { queryOptions, type QueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  LibraryV2AlbumDetail,
  LibraryV2ArtistAliasMember,
  LibraryV2ArtistDetail,
  LibraryV2ArtistSettings,
  LibraryV2ArtistSummary,
  LibraryV2FileTags,
  LibraryV2ImportState,
  LibraryV2JobState,
  LibraryV2Pagination,
  LibraryV2ArtCandidate,
  LibraryV2ManualSkip,
  LibraryV2MatchService,
  LibraryV2QualityProfile,
  LibraryV2QueueStatusResponse,
  LibraryV2ReorganizePreview,
  LibraryV2ReorganizeQueueItem,
  LibraryV2ReorganizeQueueSnapshot,
  LibraryV2ReorganizeSource,
  LibraryV2Search,
  LibraryV2Track,
  LibraryV2TrackDownload,
  LibraryV2ArtistTableColumns,
  LibraryV2TrackTableColumns,
  LibraryV2UiPreferences,
  LibraryV2WantedKind,
  LibraryV2WantedRow,
} from './-library-v2.types';

import { bitrateKbps } from './-bitrate';

export const LIBRARY_V2_QUERY_KEY = ['library-v2'] as const;

interface EnabledResponse {
  success: boolean;
  enabled: boolean;
  /** iss29-C10: whether THIS profile may mutate the catalogue at all. */
  can_write?: boolean;
}

export interface LibraryV2Availability {
  enabled: boolean;
  /** False for a non-admin profile: every mutating endpoint answers 403. */
  canWrite: boolean;
}
interface ArtistsResponse {
  success: boolean;
  artists: LibraryV2ArtistSummary[];
  pagination: LibraryV2Pagination;
  error?: string;
}
interface ArtistResponse {
  success: boolean;
  artist?: LibraryV2ArtistDetail;
  error?: string;
}
interface AlbumResponse {
  success: boolean;
  album?: LibraryV2AlbumDetail;
  error?: string;
}
interface TrackResponse {
  success: boolean;
  track?: LibraryV2Track;
  error?: string;
}
interface QualityProfilesResponse {
  success: boolean;
  profiles: LibraryV2QualityProfile[];
  error?: string;
}

export async function fetchLibraryV2Enabled(): Promise<LibraryV2Availability> {
  const payload = await readJson<EnabledResponse>(apiClient.get('library/v2/enabled'));
  return {
    enabled: Boolean(payload.enabled),
    // Missing capability data is not permission to mutate.
    canWrite: payload.can_write === true,
  };
}

export async function fetchLibraryV2Artists(
  search: Pick<LibraryV2Search, 'q' | 'sort' | 'page' | 'monitored'> & { includeSize?: boolean },
): Promise<ArtistsResponse> {
  const params = new URLSearchParams();
  if (search.q) params.set('search', search.q);
  params.set('sort', search.sort);
  params.set('monitored', search.monitored);
  params.set('page', String(search.page));
  // rev25-06/rev25-11: the disk-space roll-up is expensive, so it's opt-in —
  // but via a request parameter the caller sets, not a stored preference the
  // server used to derive it from. That silently zeroed the field for every
  // other consumer of this endpoint and didn't invalidate an already-fetched
  // list when the preference was toggled.
  if (search.includeSize) params.set('include', 'size');
  const payload = await readJson<ArtistsResponse>(
    apiClient.get('library/v2/artists', { searchParams: params }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to load library');
  return payload;
}

interface WantedResponse {
  success: boolean;
  kind: LibraryV2WantedKind;
  tracks: LibraryV2WantedRow[];
  pagination: LibraryV2Pagination;
  error?: string;
}

/** §64 I2: library-wide Missing / Cutoff Unmet lists. */
export async function fetchLibraryV2Wanted(
  search: Pick<LibraryV2Search, 'q' | 'page' | 'wantedKind'>,
): Promise<WantedResponse> {
  const params = new URLSearchParams();
  params.set('kind', search.wantedKind);
  if (search.q) params.set('search', search.q);
  params.set('page', String(search.page));
  const payload = await readJson<WantedResponse>(
    apiClient.get('library/v2/wanted', { searchParams: params }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to load wanted tracks');
  return payload;
}

export function libraryV2WantedQueryOptions(
  search: Pick<LibraryV2Search, 'q' | 'page' | 'wantedKind'>,
) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'wanted', search.wantedKind, search.q, search.page],
    queryFn: () => fetchLibraryV2Wanted(search),
  });
}

export async function setLibraryV2Monitored(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
  monitored: boolean,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/monitor`, { json: { monitored } }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to update monitoring');
}

/** Turn a missing album slot into a real track row (legacy "Add to Library"
 *  prerequisite). Returns the new/existing track id; monitoring is a separate
 *  setLibraryV2Monitored call so it runs through the proven wishlist mirror. */
export async function materializeLibraryV2MissingTrack(
  albumId: number,
  slot: { track_number: number; disc_number?: number; title?: string },
): Promise<{ track_id: number; created: boolean }> {
  const payload = await readJson<{
    success: boolean;
    track_id: number;
    created: boolean;
    error?: string;
  }>(apiClient.post(`library/v2/albums/${albumId}/missing-tracks/materialize`, { json: slot }));
  if (!payload.success) throw new Error(payload.error || 'Failed to add track to library');
  return { track_id: payload.track_id, created: payload.created };
}

export async function fetchLibraryV2QualityProfiles(): Promise<LibraryV2QualityProfile[]> {
  const payload = await readJson<QualityProfilesResponse>(
    apiClient.get('library/v2/quality-profiles'),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to load quality profiles');
  return payload.profiles;
}

export async function setLibraryV2QualityProfile(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
  qualityProfileId: number | null,
  cascade = true,
  monitorExisting = false,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/quality-profile`, {
      json: {
        ...(qualityProfileId === null
          ? { inherit: true }
          : { quality_profile_id: qualityProfileId, inherit: false }),
        cascade,
        // Assigning a profile is a quality decision; monitoring the entity's
        // tracks for upgrades (a wanted-action) is a separate, explicit
        // opt-in (audit P1-15).
        monitor_existing: monitorExisting,
      },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to update quality profile');
}

export async function refreshLibraryV2(entity: 'artists' | 'albums', id: number): Promise<string> {
  /**
   * iss29-C02: a 409 means "this scan is ALREADY running", and the server
   * returns the running job's id in the body so the client can attach to it.
   * Without `throwHttpErrors: false`, ky (created with `retry: 0`) threw before
   * the body was ever read, so a second tab, a remount, or the album page's
   * separate banner instance turned a perfectly healthy job into a red error.
   * Same shape as `startLibraryV2DiscographyRefresh`.
   */
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/refresh`, {
      json: {},
      throwHttpErrors: false,
    }),
  );
  if (!payload.job_id) throw new Error(payload.error || 'Failed to refresh');
  return payload.job_id;
}

export async function fetchLibraryV2Artist(artistId: number): Promise<LibraryV2ArtistDetail> {
  const payload = await readJson<ArtistResponse>(apiClient.get(`library/v2/artists/${artistId}`));
  if (!payload.success || !payload.artist) throw new Error(payload.error || 'Artist not found');
  return payload.artist;
}

export async function fetchLibraryV2Album(
  albumId: number,
  options: { resolve?: boolean } = {},
): Promise<LibraryV2AlbumDetail> {
  const params = new URLSearchParams();
  if (options.resolve) params.set('resolve', '1');
  const payload = await readJson<AlbumResponse>(
    apiClient.get(`library/v2/albums/${albumId}`, { searchParams: params }),
  );
  if (!payload.success || !payload.album) throw new Error(payload.error || 'Album not found');
  return payload.album;
}

/** Provider match chips for an artist (legacy Enhanced-View parity). */
/** ldp-05: `enrichment_coverage` is the per-provider share of this artist's
 *  TRACKS that carry a qualified id — the legacy hero's ring panel. It rides
 *  along with the chips because it answers the same question one level down,
 *  and a separate endpoint would mean a second round trip per header. */
export interface LibraryV2ArtistMatchStatus {
  services: LibraryV2MatchService[];
  enrichmentCoverage: Record<string, number> & { total_tracks?: number };
}

export async function fetchLibraryV2ArtistMatchStatus(
  artistId: number,
): Promise<LibraryV2ArtistMatchStatus> {
  const payload = await readJson<{
    success: boolean;
    services: LibraryV2MatchService[];
    enrichment_coverage?: Record<string, number>;
    error?: string;
  }>(apiClient.get(`library/v2/artists/${artistId}/match-status`));
  if (!payload.success) throw new Error(payload.error || 'Failed to load match status');
  return {
    services: payload.services ?? [],
    enrichmentCoverage: payload.enrichment_coverage ?? {},
  };
}

export interface LibraryV2AlbumMatchBundle {
  album: LibraryV2MatchService[];
  tracks: Record<number, LibraryV2MatchService[]>;
}

/** Album chips + per-track chip map in one batched read. */
export async function fetchLibraryV2AlbumMatchStatus(
  albumId: number,
): Promise<LibraryV2AlbumMatchBundle> {
  const payload = await readJson<{
    success: boolean;
    album: LibraryV2MatchService[];
    tracks: Record<number, LibraryV2MatchService[]>;
    error?: string;
  }>(apiClient.get(`library/v2/albums/${albumId}/match-status`));
  if (!payload.success) throw new Error(payload.error || 'Failed to load match status');
  return { album: payload.album ?? [], tracks: payload.tracks ?? {} };
}

/** §40 — the artist's full alias group (canonical + linked aliases). Works
 *  whether ``artistId`` is itself the canonical row or one of its aliases.
 *  See docs/library-v2.md §24. */
export async function fetchLibraryV2ArtistAliases(
  artistId: number,
): Promise<{ canonicalArtistId: number; aliases: LibraryV2ArtistAliasMember[] }> {
  const payload = await readJson<{
    success: boolean;
    canonical_artist_id?: number;
    aliases?: LibraryV2ArtistAliasMember[];
    error?: string;
  }>(apiClient.get(`library/v2/artists/${artistId}/aliases`));
  if (!payload.success) throw new Error(payload.error || 'Failed to load aliases');
  return {
    canonicalArtistId: payload.canonical_artist_id ?? artistId,
    aliases: payload.aliases ?? [],
  };
}

/** §40: mark ``artistId`` as an alias of ``aliasOfId`` — the same real artist
 *  under a different, unlinked provider identity. Both rows keep their own
 *  albums/tracks (soft link, nothing is reassigned or deleted). */
export async function linkLibraryV2ArtistAlias(artistId: number, aliasOfId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/link-alias`, {
      json: { alias_of: aliasOfId },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Link failed');
}

/** §40: detach ``artistId`` from its canonical artist, if any — it becomes a
 *  standalone entry again (its own albums are untouched either way). */
export async function unlinkLibraryV2ArtistAlias(artistId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.delete(`library/v2/artists/${artistId}/link-alias`),
  );
  if (!payload.success) throw new Error(payload.error || 'Unlink failed');
}

/** Manually match an entity to a provider id, reusing the app-wide legacy
 *  endpoint (keys on the legacy row id carried by the match chip). */
export async function manualMatchLibraryV2Entity(input: {
  entity_type: 'artist' | 'album' | 'track';
  legacy_entity_id?: number | string | null;
  library_v2_entity_id?: number | null;
  service: string;
  service_id: string;
  artist_legacy_id?: number | string;
  watchlist_row_id?: number;
}): Promise<void> {
  const useLegacy = input.legacy_entity_id != null;
  if (!useLegacy && input.library_v2_entity_id == null) {
    throw new Error('No matchable entity id');
  }
  const payload = await readJson<{ success: boolean; error?: string }>(
    useLegacy
      ? apiClient.put('library/manual-match', {
          json: {
            entity_type: input.entity_type,
            entity_id: input.legacy_entity_id,
            service: input.service,
            service_id: input.service_id,
            ...(input.artist_legacy_id ? { artist_id: input.artist_legacy_id } : {}),
            ...(input.watchlist_row_id ? { watchlist_row_id: input.watchlist_row_id } : {}),
          },
        })
      : apiClient.put(
          `library/v2/${input.entity_type}s/${input.library_v2_entity_id}/manual-match`,
          {
            json: {
              service: input.service,
              service_id: input.service_id,
              ...(input.watchlist_row_id ? { watchlist_row_id: input.watchlist_row_id } : {}),
            },
          },
        ),
  );
  if (!payload.success) throw new Error(payload.error || 'Manual match failed');
}

/** Clear a wrong provider identity, optionally keeping the linked Watchlist
 * row in the same transaction as the legacy library row. */
export async function clearLibraryV2EntityMatch(input: {
  entity_type: 'artist' | 'album' | 'track';
  legacy_entity_id?: number | string | null;
  library_v2_entity_id?: number | null;
  service: string;
  watchlist_row_id?: number;
}): Promise<void> {
  const useLegacy = input.legacy_entity_id != null;
  if (!useLegacy && input.library_v2_entity_id == null) {
    throw new Error('No matchable entity id');
  }
  const payload = await readJson<{ success: boolean; error?: string }>(
    useLegacy
      ? apiClient.put('library/clear-match', {
          json: {
            entity_type: input.entity_type,
            entity_id: input.legacy_entity_id,
            service: input.service,
            ...(input.watchlist_row_id ? { watchlist_row_id: input.watchlist_row_id } : {}),
          },
        })
      : apiClient.delete(
          `library/v2/${input.entity_type}s/${input.library_v2_entity_id}/manual-match`,
          {
            json: {
              service: input.service,
              ...(input.watchlist_row_id ? { watchlist_row_id: input.watchlist_row_id } : {}),
            },
          },
        ),
  );
  if (!payload.success) throw new Error(payload.error || 'Clear match failed');
}

export interface LibraryV2MatchSearchResult {
  id: string;
  name: string;
  extra?: string;
  image?: string;
  provider?: string;
  /** §52.5: only Spotify and Deezer artist search actually supply these —
   *  0/undefined means "not provided by this provider", not a real zero. */
  followers?: number;
  popularity?: number;
}

export interface LibraryV2MatchRelease {
  id: string;
  title: string;
  image?: string | null;
  release_date?: string | null;
  album_type?: string | null;
  total_tracks?: number | null;
}

export interface LibraryV2MatchReleasePreview {
  supported: boolean;
  albums: LibraryV2MatchRelease[];
}

/** Search a provider for candidate matches (reuses the app-wide endpoint). */
export async function searchLibraryV2MatchService(input: {
  service: string;
  entity_type: 'artist' | 'album' | 'track';
  query: string;
}): Promise<LibraryV2MatchSearchResult[]> {
  const payload = await readJson<{
    success: boolean;
    results: LibraryV2MatchSearchResult[];
    error?: string;
  }>(apiClient.post('library/search-service', { json: input }));
  if (!payload.success) throw new Error(payload.error || 'Provider search failed');
  return payload.results ?? [];
}

/** Exact-provider album context for disambiguating an artist candidate. */
export async function fetchLibraryV2MatchArtistReleases(input: {
  service: string;
  artist_id: string;
  artist_name: string;
  limit?: number;
}): Promise<LibraryV2MatchReleasePreview> {
  const payload = await readJson<{
    success: boolean;
    supported?: boolean;
    albums?: LibraryV2MatchRelease[];
    error?: string;
  }>(apiClient.post('library/match-artist-releases', { json: input }));
  if (!payload.success) throw new Error(payload.error || 'Release preview failed');
  return { supported: payload.supported !== false, albums: payload.albums ?? [] };
}

interface SourceInfoResponse {
  success: boolean;
  downloads: LibraryV2TrackDownload[];
  manual_skips?: LibraryV2ManualSkip[];
  error?: string;
}

export interface LibraryV2TrackSourceInfo {
  downloads: LibraryV2TrackDownload[];
  manual_skips: LibraryV2ManualSkip[];
}

/** Download provenance + manual check-skip audit for a track (legacy
 *  "Source Info" popover parity, extended with the §18.3 lifecycle log). */
export async function fetchLibraryV2TrackSourceInfo(
  trackId: number,
): Promise<LibraryV2TrackSourceInfo> {
  const payload = await readJson<SourceInfoResponse>(
    apiClient.get(`library/v2/tracks/${trackId}/source-info`),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to load source info');
  return { downloads: payload.downloads ?? [], manual_skips: payload.manual_skips ?? [] };
}

/** Blacklist a download source so the pipeline skips it (reuses the app-wide route). */
export async function blacklistLibraryV2Source(input: {
  track_title: string;
  track_artist?: string;
  blocked_filename: string;
  blocked_username: string;
}): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post('library/blacklist', {
      json: { reason: 'user_rejected', track_artist: '', ...input },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to blacklist source');
}

/** Start the provider discography walk. Returns the background job to poll.
 *
 *  This used to be a plain request that answered with the stats. A prolific
 *  artist is a ~70-release catalogue plus tracklists — measured at 55s on a
 *  real library — so the answer regularly arrived after any client timeout we
 *  could justify, and the browser reported a failure for work the server
 *  completed. A 409 means the same artist is already being walked; its job id
 *  comes back so the caller attaches to that instead of starting a second one.
 */
export async function startLibraryV2DiscographyRefresh(artistId: number): Promise<string> {
  const payload = await readJson<{ success: boolean; error?: string; job_id?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/discography/refresh`, {
      json: {},
      throwHttpErrors: false,
    }),
  );
  if (!payload.job_id) throw new Error(payload.error || 'Discography refresh failed');
  return payload.job_id;
}

// --- ldp-01/ldp-02/ldp-05: provider-artist discovery ------------------------

/** One release card exactly as the shared provider discography endpoint
 *  returns it — the same payload the legacy artist page rendered, reused
 *  verbatim rather than reshaped (issues §28.5). */
export interface ProviderRelease {
  id: string;
  title?: string;
  name?: string;
  album_type?: string;
  release_date?: string | null;
  year?: number | null;
  image_url?: string | null;
  explicit?: boolean;
  owned?: boolean | null;
  musicbrainz_release_id?: string | null;
  track_completion?: {
    owned_tracks?: number;
    total_tracks?: number;
    missing_tracks?: number;
  } | null;
}

export interface ProviderArtistDetail {
  artist: {
    id: string;
    name: string;
    image_url?: string | null;
    genres?: string[];
    lastfm_bio?: string | null;
    lastfm_listeners?: number | null;
    lastfm_playcount?: number | null;
    followers?: number | null;
  };
  discography: { albums?: ProviderRelease[]; eps?: ProviderRelease[]; singles?: ProviderRelease[] };
}

/** The artist page for someone who isn't in the catalogue. Reuses the endpoint
 *  the legacy page already used — nothing new had to be built server-side. */
export async function fetchProviderArtistDetail(input: {
  source: string;
  providerId: string;
  name: string;
}): Promise<ProviderArtistDetail> {
  const payload = await readJson<
    { success: boolean; error?: string } & Partial<ProviderArtistDetail>
  >(
    apiClient.get(`artist-detail/${encodeURIComponent(input.providerId)}`, {
      searchParams: { source: input.source, name: input.name },
      timeout: 60_000, // a cold provider discography walk is not fast
    }),
  );
  if (!payload.success || !payload.artist) {
    throw new Error(payload.error || 'Could not load this artist');
  }
  return { artist: payload.artist, discography: payload.discography ?? {} };
}

/** Read-only: the catalogue id for a provider artist, or `null` when there is
 *  none yet. Opening a search result must not create anything (§28.6 q1). */
export async function resolveLibraryV2DiscoveryArtist(input: {
  source: string;
  providerId: string;
  name: string;
}): Promise<number | null> {
  const payload = await readJson<{ success: boolean; artist_id: number | null; error?: string }>(
    apiClient.get('library/v2/discovery/artist', {
      searchParams: { source: input.source, provider_id: input.providerId, name: input.name },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Artist lookup failed');
  return payload.artist_id;
}

/** The first write: turn the provider artist into a real catalogue row so the
 *  normal Library V2 artist page (and monitoring) can take over. */
export async function materializeLibraryV2DiscoveryArtist(input: {
  source: string;
  providerId: string;
  name: string;
}): Promise<number> {
  const payload = await readJson<{ success: boolean; artist_id: number; error?: string }>(
    apiClient.post('library/v2/discovery/artist', {
      json: { source: input.source, provider_id: input.providerId, name: input.name },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Could not add this artist');
  return payload.artist_id;
}

/** ldp-06: which of these top-track titles the catalogue already has, and
 *  whether they are wanted. The bookmark tick has to survive a reload — it
 *  is a fact about the library, not about this component's lifetime. */
export async function fetchLibraryV2DiscoveryTrackStatus(input: {
  source: string;
  artistName: string;
  artistProviderId?: string | null;
  titles: string[];
}): Promise<Record<string, { track_id: number; monitored: boolean }>> {
  if (!input.titles.length || !input.artistName) return {};
  const params = new URLSearchParams({ artist_name: input.artistName, source: input.source });
  if (input.artistProviderId) params.set('artist_provider_id', input.artistProviderId);
  for (const title of input.titles) params.append('title', title);
  const payload = await readJson<{
    success: boolean;
    statuses?: Record<string, { track_id: number; monitored: boolean }>;
  }>(apiClient.get('library/v2/discovery/track-status', { searchParams: params }));
  return payload.statuses ?? {};
}

/** ldp-06: give a provider Top Track its catalogue rows so the normal
 *  Bookmark (`setLibraryV2Monitored('tracks', …)`) has something to act on. */
export async function materializeLibraryV2DiscoveryTrack(input: {
  source: string;
  artistName: string;
  artistProviderId?: string | null;
  trackTitle: string;
  trackProviderId?: string | null;
  albumTitle?: string | null;
  albumProviderId?: string | null;
}): Promise<number> {
  const payload = await readJson<{ success: boolean; track_id: number; error?: string }>(
    apiClient.post('library/v2/discovery/track', {
      json: {
        source: input.source,
        artist_name: input.artistName,
        artist_provider_id: input.artistProviderId ?? null,
        track_title: input.trackTitle,
        track_provider_id: input.trackProviderId ?? null,
        album_title: input.albumTitle ?? null,
        album_provider_id: input.albumProviderId ?? null,
      },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Could not bookmark this track');
  return payload.track_id;
}

/** "+ Other sources" (#1067): releases that OTHER configured metadata sources
 *  list and the base source does not. Reuses the legacy endpoint unchanged —
 *  it is deliberately conservative (only sources with a verified artist id,
 *  no fuzzy name search) and re-deriving that here would be a second, weaker
 *  copy of the same policy. */
export async function fetchArtistDiscographyGapFill(input: {
  providerId: string;
  artistName: string;
  baseSource?: string | null;
}): Promise<Array<ProviderRelease & { gap_source?: string; _bucket: 'album' | 'ep' | 'single' }>> {
  const params = new URLSearchParams({ artist_name: input.artistName });
  if (input.baseSource) params.set('base_source', input.baseSource);
  const payload = await readJson<{
    success: boolean;
    gaps?: { albums?: ProviderRelease[]; eps?: ProviderRelease[]; singles?: ProviderRelease[] };
  }>(
    apiClient.get(`artist/${encodeURIComponent(input.providerId)}/discography/gap-fill`, {
      searchParams: params,
      timeout: 60_000,
    }),
  );
  if (!payload.success) return [];
  const gaps = payload.gaps ?? {};
  return [
    ...(gaps.albums ?? []).map((r) => ({ ...r, _bucket: 'album' as const })),
    ...(gaps.eps ?? []).map((r) => ({ ...r, _bucket: 'ep' as const })),
    ...(gaps.singles ?? []).map((r) => ({ ...r, _bucket: 'single' as const })),
  ];
}

/** ldp-05: every number the rich header can show. `followers` is the one
 *  that survives an instance without a Last.fm key, which is why it is here
 *  rather than left to the chips. */
export interface ArtistHeroStats {
  listeners: number | null;
  playcount: number | null;
  followers: number | null;
  bio: string | null;
}

export async function fetchArtistHeroStats(input: {
  name: string;
  spotifyId?: string | null;
  deezerId?: string | null;
}): Promise<ArtistHeroStats> {
  const params = new URLSearchParams({ name: input.name });
  if (input.spotifyId) params.set('spotify_id', input.spotifyId);
  if (input.deezerId) params.set('deezer_id', input.deezerId);
  const payload = await readJson<{ success: boolean } & Partial<ArtistHeroStats>>(
    apiClient.get('artist/hero-stats', { searchParams: params }),
  ).catch(() => null);
  return {
    listeners: payload?.listeners ?? null,
    playcount: payload?.playcount ?? null,
    followers: payload?.followers ?? null,
    bio: payload?.bio ?? null,
  };
}

export interface ArtistTopTrack {
  name: string;
  playcount?: number | null;
  /** Only present on provider (Spotify/Deezer) results, which carry enough
   *  metadata for the Bookmark action to materialize a real track. */
  album?: { id?: string; name?: string } | null;
  artists?: Array<{ id?: string; name?: string }> | null;
  id?: string;
}

/** Legacy's exact two-pass ladder (`library.js:1625-1745`): the primary
 *  metadata source's popularity ranking first — it returns full track objects,
 *  so each row can be bookmarked — and Last.fm's display-only playcounts when
 *  the configured source has no popularity API at all. */
export async function fetchArtistTopTracks(input: {
  providerId: string | null;
  name: string;
}): Promise<{
  kind: 'provider' | 'lastfm';
  /** The provider that ACTUALLY answered, plus the id it resolved to. The
   *  endpoint routes by the configured primary source, so asking with a
   *  Spotify artist id can come back with Deezer track objects — writing
   *  those ids into a Spotify slot is exactly what guide §2.5 forbids. */
  source: string;
  resolvedArtistId: string | null;
  tracks: ArtistTopTrack[];
}> {
  if (input.providerId) {
    const payload = await readJson<{
      success: boolean;
      source?: string;
      resolved_artist_id?: string;
      tracks?: ArtistTopTrack[];
    }>(
      apiClient.get(`artist/${encodeURIComponent(input.providerId)}/top-tracks`, {
        searchParams: { limit: 10 },
      }),
    ).catch(() => null);
    if (payload?.success && payload.tracks?.length) {
      return {
        kind: 'provider',
        source: payload.source ?? '',
        resolvedArtistId: payload.resolved_artist_id ?? input.providerId,
        tracks: payload.tracks,
      };
    }
  }
  const fallback = await readJson<{ success: boolean; tracks?: ArtistTopTrack[] }>(
    apiClient.get('artist/0/lastfm-top-tracks', { searchParams: { name: input.name, limit: 10 } }),
  ).catch(() => null);
  return {
    kind: 'lastfm',
    source: 'lastfm',
    resolvedArtistId: null,
    tracks: (fallback?.tracks ?? []).slice(0, 10),
  };
}

export async function bulkMonitorLibraryV2Releases(
  artistId: number,
  scope: 'albums' | 'eps' | 'singles' | 'all' | 'missing',
  monitored: boolean,
  albumIds?: number[],
): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/releases/monitor`, {
      json: { scope, monitored, ...(albumIds ? { album_ids: albumIds } : {}) },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Bulk monitor failed');
  if (!payload.job_id) throw new Error('Bulk monitor did not return a job id');
  return payload.job_id;
}

export async function editLibraryV2Artist(
  artistId: number,
  monitorNewItems: 'all' | 'none' | 'new',
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/edit`, {
      json: { monitor_new_items: monitorNewItems },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Edit failed');
}

/** §52.5/§56.2: on-demand Spotify stats for the current-match identity card —
 *  omitted entirely when the artist has no linked spotify id or the live
 *  lookup failed/was rate-limited, never a fake zero. */
export interface LibraryV2ArtistStats {
  followers: number;
  popularity: number;
  name?: string | null;
  image_url?: string | null;
  genres?: string[];
}

export interface LibraryV2ArtistSettingsResponse {
  settings: LibraryV2ArtistSettings;
  metadata_sources: string[];
  global_metadata_source: string | null;
  artist_stats: LibraryV2ArtistStats | null;
}

export async function fetchLibraryV2ArtistSettings(
  artistId: number,
): Promise<LibraryV2ArtistSettingsResponse> {
  const payload = await readJson<{
    success: boolean;
    error?: string;
    settings?: LibraryV2ArtistSettings;
    metadata_sources?: string[];
    global_metadata_source?: string | null;
    artist_stats?: LibraryV2ArtistStats | null;
  }>(apiClient.get(`library/v2/artists/${artistId}/settings`));
  if (!payload.success || !payload.settings) {
    throw new Error(payload.error || 'Artist settings could not be loaded');
  }
  return {
    settings: payload.settings,
    metadata_sources: payload.metadata_sources ?? [],
    global_metadata_source: payload.global_metadata_source ?? null,
    artist_stats: payload.artist_stats ?? null,
  };
}

export async function updateLibraryV2ArtistSettings(
  artistId: number,
  settings: Pick<
    LibraryV2ArtistSettings,
    | 'monitor_new_items'
    | 'include_albums'
    | 'include_eps'
    | 'include_singles'
    | 'include_live'
    | 'include_remixes'
    | 'include_acoustic'
    | 'include_compilations'
    | 'include_instrumentals'
    | 'auto_download'
    | 'lookback_days'
    | 'preferred_metadata_source'
  >,
): Promise<LibraryV2ArtistSettingsResponse> {
  const payload = await readJson<{
    success: boolean;
    error?: string;
    settings?: LibraryV2ArtistSettings;
    metadata_sources?: string[];
    global_metadata_source?: string | null;
  }>(
    apiClient.put(`library/v2/artists/${artistId}/settings`, {
      json: {
        monitor_new_items: settings.monitor_new_items,
        include_albums: settings.include_albums,
        include_eps: settings.include_eps,
        include_singles: settings.include_singles,
        include_live: settings.include_live,
        include_remixes: settings.include_remixes,
        include_acoustic: settings.include_acoustic,
        include_compilations: settings.include_compilations,
        include_instrumentals: settings.include_instrumentals,
        auto_download: settings.auto_download,
        lookback_days: settings.lookback_days,
        preferred_metadata_source: settings.preferred_metadata_source,
      },
    }),
  );
  if (!payload.success || !payload.settings) {
    throw new Error(payload.error || 'Artist settings could not be saved');
  }
  return {
    settings: payload.settings,
    metadata_sources: payload.metadata_sources ?? [],
    global_metadata_source: payload.global_metadata_source ?? null,
    artist_stats: null,
  };
}

export const LIBRARY_V2_ALBUM_TYPES = ['album', 'ep', 'single', 'compilation', 'live'] as const;
export type LibraryV2AlbumType = (typeof LIBRARY_V2_ALBUM_TYPES)[number];

/** Entity names accepted by the central field-level metadata override store. */
export type LibraryV2MetadataEntity =
  | 'artist'
  | 'release_group'
  | 'track'
  | 'release_edition'
  | 'recording';

export async function updateLibraryV2MetadataOverrides(
  entity: LibraryV2MetadataEntity,
  entityId: number,
  values: Record<string, unknown>,
  clear: string[] = [],
): Promise<Record<string, unknown>> {
  const payload = await readJson<{
    success: boolean;
    overrides?: Record<string, unknown>;
    error?: string;
  }>(
    apiClient.patch(`library/v2/metadata-overrides/${entity}/${entityId}`, {
      json: { set: values, clear },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Edit failed');
  return payload.overrides ?? {};
}

/** Trigger the wishlist processor — the real "Search Monitored": every
 *  monitored missing/upgradable track is mirrored to the wishlist, so
 *  processing it searches and downloads them through the normal pipeline. */
export async function processWishlist(): Promise<string> {
  const payload = await readJson<{
    success: boolean;
    message?: string;
    data?: { message?: string } | null;
    error?: string | { message?: string } | null;
  }>(apiClient.post('wishlist/process', { json: {} }));
  const error = typeof payload.error === 'string' ? payload.error : payload.error?.message;
  if (!payload.success) throw new Error(error || 'Wishlist processing failed to start');
  return payload.message ?? payload.data?.message ?? 'Wishlist processing started';
}

export async function deleteLibraryV2Entity(
  entity: 'artists' | 'albums',
  id: number,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.delete(`library/v2/${entity}/${id}`),
  );
  if (!payload.success) throw new Error(payload.error || 'Delete failed');
}

/** Impact preview for artist delete: owned releases cascade, featured
 *  appearances on other artists' releases are only detached. */
export interface LibraryV2ArtistDeletePreview {
  artist: string;
  albums: number;
  tracks: number;
  file_links: number;
  detached_albums: number;
}

export async function fetchLibraryV2ArtistDeletePreview(
  id: number,
): Promise<LibraryV2ArtistDeletePreview> {
  const payload = await readJson<
    { success: boolean; error?: string } & LibraryV2ArtistDeletePreview
  >(apiClient.get(`library/v2/artists/${id}/delete-preview`));
  if (!payload.success) throw new Error(payload.error || 'Delete preview failed');
  return payload;
}

export interface LibraryV2FileDeletePreviewItem {
  file_ids: number[];
  track_ids: number[];
  stored_paths: string[];
  path: string | null;
  root: string | null;
  size: number | null;
  deletable: boolean;
  reason: string | null;
  album_title: string | null;
  track_titles: string[];
}

export interface LibraryV2FileDeletePreview {
  entity: 'artists' | 'albums';
  entity_id: number;
  title: string;
  configured_roots: string[];
  files: LibraryV2FileDeletePreviewItem[];
  file_count: number;
  deletable_count: number;
  /** Paths that exist but lie outside the library — these block the delete. */
  unsafe_count: number;
  /** Rows whose file is already gone: nothing to unlink, only a row to retire.
   *  Counted apart from `unsafe_count` because one already-deleted file used
   *  to veto deleting every other file on the album. */
  missing_count?: number;
  total_size: number;
  preview_token: string;
}

export interface LibraryV2FileDeleteOperation {
  id: string;
  status: 'planned' | 'executing' | 'completed' | 'partial';
  mode: 'database_only' | 'permanent';
  actor: string;
  actor_profile_id: number | null;
  file_count: number;
  total_size: number;
  items: Array<{
    id: number;
    status: 'planned' | 'deleting' | 'deleted' | 'removed' | 'failed';
    error: string | null;
    resolved_path: string;
    file_ids: number[];
  }>;
}

export async function removeLibraryV2FileRecords(
  entity: 'artists' | 'albums',
  id: number,
  fileIds?: number[],
): Promise<LibraryV2FileDeleteOperation> {
  const payload = await readJson<{
    success: boolean;
    error?: string;
    operation?: LibraryV2FileDeleteOperation;
  }>(
    apiClient.post(`library/v2/${entity}/${id}/file-remove`, {
      json: fileIds?.length ? { file_ids: fileIds } : {},
    }),
  );
  if (!payload.success || !payload.operation) {
    throw new Error(payload.error || 'File records could not be removed');
  }
  return payload.operation;
}

export async function fetchLibraryV2FileDeletePreview(
  entity: 'artists' | 'albums',
  id: number,
  fileIds?: number[],
): Promise<LibraryV2FileDeletePreview> {
  const searchParams = fileIds?.length
    ? new URLSearchParams({ file_ids: fileIds.join(',') })
    : undefined;
  const payload = await readJson<{ success: boolean; error?: string } & LibraryV2FileDeletePreview>(
    apiClient.get(`library/v2/${entity}/${id}/file-delete-preview`, { searchParams }),
  );
  if (!payload.success) throw new Error(payload.error || 'File delete preview failed');
  return payload;
}

export async function deleteLibraryV2Files(
  entity: 'artists' | 'albums',
  id: number,
  previewToken: string,
  fileIds?: number[],
): Promise<LibraryV2FileDeleteOperation> {
  const payload = await readJson<{
    success: boolean;
    error?: string;
    operation?: LibraryV2FileDeleteOperation;
  }>(
    apiClient.post(`library/v2/${entity}/${id}/file-delete`, {
      json: {
        preview_token: previewToken,
        ...(fileIds?.length ? { file_ids: fileIds } : {}),
      },
    }),
  );
  if (!payload.success || !payload.operation) {
    throw new Error(payload.error || 'Physical file deletion failed');
  }
  return payload.operation;
}

export interface LibraryV2ArtistTrackFile {
  file_id: number;
  track_id: number;
  track_title: string | null;
  track_number: number | null;
  disc_number: number | null;
  album_id: number;
  album_title: string | null;
  path: string;
  size: number | null;
  format: string | null;
  bitrate: number | null;
  sample_rate: number | null;
  bit_depth: number | null;
  quality_tier: string | null;
  file_state: string;
  is_primary: boolean;
  added_at: string | null;
}

/** C2 (Manage Track Files): every physical file this artist owns, flat and
 *  paginated — feeds the "Files" tab whose selection drives the ADR-05
 *  preview/delete above. */
export async function fetchLibraryV2ArtistTrackFiles(
  artistId: number,
  { search = '', page = 1, limit = 100 }: { search?: string; page?: number; limit?: number } = {},
): Promise<{ files: LibraryV2ArtistTrackFile[]; pagination: LibraryV2Pagination }> {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  params.set('page', String(page));
  params.set('limit', String(limit));
  const payload = await readJson<{
    success: boolean;
    error?: string;
    files?: LibraryV2ArtistTrackFile[];
    pagination?: LibraryV2Pagination;
  }>(apiClient.get(`library/v2/artists/${artistId}/track-files`, { searchParams: params }));
  if (!payload.success) throw new Error(payload.error || 'Track files failed');
  return {
    files: payload.files ?? [],
    pagination: payload.pagination ?? {
      page,
      limit,
      total_count: 0,
      total_pages: 0,
      has_prev: false,
      has_next: false,
    },
  };
}

export type LibraryV2HistoryCategory =
  | 'grabbed'
  | 'imported'
  | 'failed'
  | 'quarantined'
  | 'blocklist'
  | 'moved'
  | 'deleted'
  | 'override'
  | 'maintenance'
  | 'info';

export interface LibraryV2HistoryEntry {
  date: string | null;
  event_type: string;
  category: LibraryV2HistoryCategory;
  title: string | null;
  detail: string | null;
  source: string | null;
  status?: 'passed' | 'failed' | 'skipped' | 'not_run' | 'error' | null;
  payload?: Record<string, unknown>;
}

export async function fetchLibraryV2ArtistHistory(
  artistId: number,
): Promise<LibraryV2HistoryEntry[]> {
  const payload = await readJson<{
    success: boolean;
    history?: LibraryV2HistoryEntry[];
    error?: string;
  }>(apiClient.get(`library/v2/artists/${artistId}/history`));
  if (!payload.success) throw new Error(payload.error || 'History failed');
  return payload.history ?? [];
}

/** §52.9: the merged search→grab→quality→quarantine→import pipeline for one
 * track, including attempts that never produced a `lib2_track_files` row. */
export async function fetchLibraryV2TrackHistory(
  trackId: number,
): Promise<LibraryV2HistoryEntry[]> {
  const payload = await readJson<{
    success: boolean;
    history?: LibraryV2HistoryEntry[];
    error?: string;
  }>(apiClient.get(`library/v2/tracks/${trackId}/history`));
  if (!payload.success) throw new Error(payload.error || 'History failed');
  return payload.history ?? [];
}

/** §52.9 album branch: same merged resolver as artist/track history, scoped
 * to just this release. */
export async function fetchLibraryV2AlbumHistory(
  albumId: number,
): Promise<LibraryV2HistoryEntry[]> {
  const payload = await readJson<{
    success: boolean;
    history?: LibraryV2HistoryEntry[];
    error?: string;
  }>(apiClient.get(`library/v2/albums/${albumId}/history`));
  if (!payload.success) throw new Error(payload.error || 'History failed');
  return payload.history ?? [];
}

export interface LibraryV2TagDiffField {
  field: string;
  file_value: unknown;
  db_value: unknown;
  changed: boolean;
}

export interface LibraryV2TagPreviewTrack {
  track_id: number;
  title: string | null;
  track_number: number | null;
  album_id: number;
  album_title: string | null;
  album_type: string | null;
  file_path: string | null;
  diff: LibraryV2TagDiffField[];
  has_changes: boolean;
  error?: string;
}

export async function fetchLibraryV2TagPreview(
  entity: 'artists' | 'albums',
  id: number,
): Promise<{ tracks: LibraryV2TagPreviewTrack[]; changed_count: number; truncated: boolean }> {
  const payload = await readJson<{
    success: boolean;
    tracks?: LibraryV2TagPreviewTrack[];
    changed_count?: number;
    truncated?: boolean;
    error?: string;
  }>(apiClient.get(`library/v2/${entity}/${id}/tag-preview`, { timeout: 120_000 }));
  if (!payload.success) throw new Error(payload.error || 'Tag preview failed');
  return {
    tracks: payload.tracks ?? [],
    changed_count: payload.changed_count ?? 0,
    truncated: payload.truncated ?? false,
  };
}

export async function writeLibraryV2Tags(trackIds: number[], embedCover = true): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post('library/v2/tags/write', {
      json: { track_ids: trackIds, embed_cover: embedCover },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Write tags failed');
  if (!payload.job_id) throw new Error('Tag writer did not return a job id');
  return payload.job_id;
}

/** iss27-02: the "N tag gaps" click — re-fetches the track's album metadata
 *  from providers (best-effort) before writing tags, so a field the
 *  catalogue never had a chance to get (not just one it already had). */
export async function fillLibraryV2TagGaps(trackId: number): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post(`library/v2/tracks/${trackId}/fill-tag-gaps`, { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Fill tag gaps failed');
  if (!payload.job_id) throw new Error('Fill tag gaps did not return a job id');
  return payload.job_id;
}

export interface LibraryV2DuplicateSide {
  track_id: number;
  album_title: string | null;
  monitored: boolean;
  file: {
    path: string;
    format: string | null;
    bitrate: number | null;
    sample_rate: number | null;
    bit_depth: number | null;
  } | null;
}

export interface LibraryV2DuplicatePair {
  title: string | null;
  single: LibraryV2DuplicateSide;
  album: LibraryV2DuplicateSide;
}

/** Re-home every file link onto the other track of a validated duplicate pair.
 *  Files on disk are untouched (run Rename/Reorganize afterwards); the now
 *  fileless source is unmonitored so it is not immediately re-downloaded. */
export async function moveLibraryV2TrackFile(
  fromTrackId: number,
  toTrackId: number,
): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/tracks/${fromTrackId}/move-file`, {
      json: { to_track_id: toTrackId },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Move failed');
}

/** Unlink a duplicate pair: the single stops pointing at the album version
 *  (it becomes its own canonical recording again). */
export async function unlinkLibraryV2Duplicate(trackId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/tracks/${trackId}/canonical`, {
      json: { canonical_track_id: null },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Unlink failed');
}

export async function fetchLibraryV2Duplicates(
  artistId: number,
): Promise<LibraryV2DuplicatePair[]> {
  const payload = await readJson<{
    success: boolean;
    pairs?: LibraryV2DuplicatePair[];
    error?: string;
  }>(apiClient.get(`library/v2/artists/${artistId}/duplicates`));
  if (!payload.success) throw new Error(payload.error || 'Duplicates failed');
  return payload.pairs ?? [];
}

/** Trigger a repair job (Stats → Repair jobs) immediately. Artist scope uses
 *  the lib2 id so the server can derive an exact file allowlist. */
export async function runRepairJob(
  jobId: string,
  artist?: { id: number; name: string },
): Promise<void> {
  const payload = await readJson<{ success?: boolean; error?: string }>(
    apiClient.post(`repair/jobs/${jobId}/run`, {
      json: artist ? { artist_id: artist.id, artist_name: artist.name } : {},
    }),
  );
  if (payload.error) throw new Error(payload.error);
}

/** Heal the unmapped-native-artist backlog: resolve featured/wishlist/
 *  discography artists (no legacy row) by name — id + artwork — and split true
 *  collaboration names into their real components. Returns a job id to poll via
 *  fetchLibraryV2JobStatus; the result carries `{scanned, matched, split,
 *  unmatched}`. */
export async function reconcileUnmappedArtists(): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post('library/v2/maintenance/reconcile-unmapped-artists', { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Reconcile failed');
  if (!payload.job_id) throw new Error('Reconcile did not return a job id');
  return payload.job_id;
}

/** Re-assert the wanted projection into the Wishlist (§69.1): re-add
 *  monitored+missing tracks whose Wishlist entry was downloaded/cleared/aged
 *  away and prune no-longer-wanted entries. Returns a job id to poll via
 *  fetchLibraryV2JobStatus; the result carries `{scanned, wanted, wishlisted,
 *  mirrored}`. */
export async function reconcileWishlist(): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post('library/v2/maintenance/reconcile-wishlist', { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Reconcile failed');
  if (!payload.job_id) throw new Error('Reconcile did not return a job id');
  return payload.job_id;
}

export async function startLibraryV2UpgradeScan(): Promise<string> {
  // iss29-C02: same 409-is-not-an-error contract as `refreshLibraryV2` — the
  // global Automatic Search is exactly the button two tabs are most likely to
  // press at once, and the server hands back the running job's id.
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post('library/v2/upgrade-scan', { json: {}, throwHttpErrors: false }),
  );
  if (!payload.job_id) throw new Error(payload.error || 'Upgrade scan failed');
  return payload.job_id;
}

/** Lidarr-style scoped Automatic Search (deep-dive C1): searches missing
 *  tracks AND, if the quality profile allows it, upgrades — for exactly this
 *  artist/album/track, never the whole wishlist. Replaces the client-side
 *  best-pick heuristic (A4) and the mislabeled album-row global search (A3).
 *  Returns a job id to poll via fetchLibraryV2JobStatus; the job's `result`
 *  carries `{checked, queued, searching, batch_id}`. */
export async function startLibraryV2ScopedSearch(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post(`library/v2/${entity}/${id}/search`, {
      json: {},
      throwHttpErrors: false,
    }),
  );
  // 409 means this exact scoped job is already running. The server returns
  // its job id so another tab/remount attaches to it instead of showing an
  // error while the real search continues.
  if (!payload.job_id) throw new Error(payload.error || 'Search did not return a job id');
  return payload.job_id;
}

/** §73/I6: live download-queue status for this scope's tracks — a plain GET,
 *  never throws on its own absence of data (see queryOptions below, which
 *  swallows fetch errors so the badge is a pure overlay, never blocking). */
export async function fetchLibraryV2QueueStatus(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
): Promise<LibraryV2QueueStatusResponse> {
  return readJson<LibraryV2QueueStatusResponse>(
    apiClient.get(`library/v2/${entity}/${id}/queue-status`),
  );
}

export function libraryV2QueueStatusQueryOptions(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'queue-status', entity, id],
    queryFn: () => fetchLibraryV2QueueStatus(entity, id),
    enabled: id > 0,
    refetchInterval: 3000,
    // A nice-to-have overlay must never surface as a page error or a
    // console-visible failure — callers read `.data?.tracks ?? {}` and treat
    // "no data yet" the same as "nothing in flight."
    retry: false,
    throwOnError: false,
  });
}

/** Analyze an album's files and write track+album ReplayGain tags. Returns a
 *  job id to poll via fetchLibraryV2JobStatus (legacy Enrich→ReplayGain). */
export async function startLibraryV2AlbumReplayGain(albumId: number): Promise<string> {
  const payload = await readJson<{ success: boolean; job_id?: string; error?: string }>(
    apiClient.post(`library/v2/albums/${albumId}/replaygain`, { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'ReplayGain analysis failed');
  if (!payload.job_id) throw new Error('ReplayGain did not return a job id');
  return payload.job_id;
}

/** Analyze one track and write its track-level ReplayGain tags (synchronous).
 *  Returns the track gain in dB. */
export async function analyzeLibraryV2TrackReplayGain(trackId: number): Promise<number | null> {
  const payload = await readJson<{
    success: boolean;
    track_gain_db?: number | null;
    error?: string;
  }>(apiClient.post(`library/v2/tracks/${trackId}/replaygain`, { json: {} }));
  if (!payload.success) throw new Error(payload.error || 'ReplayGain analysis failed');
  return payload.track_gain_db ?? null;
}

/** Fetch + write lyrics for one track from LRClib (synchronous) — the "LR"
 *  badge's missing→click path (deep-dive B3). */
export async function fetchLibraryV2TrackLyrics(trackId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; fetched?: boolean; error?: string }>(
    apiClient.post(`library/v2/tracks/${trackId}/fetch-lyrics`, { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Lyrics fetch failed');
}

/** Re-query one metadata provider for one entity (legacy Enrich parity, §44).
 *  Delegates to the same per-provider worker the legacy Enhanced View's
 *  Enrich dropdown uses, then resyncs the refreshed fields onto the lib2 row. */
export async function enrichLibraryV2Entity(
  entity: 'artists' | 'albums' | 'tracks',
  id: number,
  service: string,
): Promise<{ message?: string; resynced: boolean }> {
  const payload = await readJson<{
    success: boolean;
    message?: string;
    error?: string;
    resynced: boolean;
  }>(apiClient.post(`library/v2/${entity}/${id}/enrich`, { json: { service } }));
  if (!payload.success) throw new Error(payload.error || payload.message || 'Enrichment failed');
  return { message: payload.message, resynced: payload.resynced };
}

/** Sources authed/configured on this instance — for the artist-level
 *  "Reorganize All" source picker (docs §50, no per-album ID coverage check). */
export async function fetchLibraryV2ReorganizeSourcesGlobal(): Promise<
  LibraryV2ReorganizeSource[]
> {
  const payload = await readJson<{
    success: boolean;
    sources: LibraryV2ReorganizeSource[];
    error?: string;
  }>(apiClient.get('library/v2/reorganize/sources'));
  if (!payload.success) throw new Error(payload.error || 'Failed to load reorganize sources');
  return payload.sources;
}

/** Sources this album has a stored provider ID for AND an authed client —
 *  for the per-album source picker (docs §50). */
export async function fetchLibraryV2AlbumReorganizeSources(
  albumId: number,
): Promise<LibraryV2ReorganizeSource[]> {
  const payload = await readJson<{
    success: boolean;
    sources: LibraryV2ReorganizeSource[];
    error?: string;
  }>(apiClient.get(`library/v2/albums/${albumId}/reorganize/sources`));
  if (!payload.success) throw new Error(payload.error || 'Failed to load reorganize sources');
  return payload.sources;
}

/** Preview current-vs-proposed file paths for one lib2 album, without moving
 *  anything (docs §50). */
export async function previewLibraryV2AlbumReorganize(
  albumId: number,
  _options: Record<string, never> = {},
): Promise<LibraryV2ReorganizePreview> {
  const payload = await readJson<LibraryV2ReorganizePreview & { error?: string }>(
    apiClient.post(`library/v2/albums/${albumId}/reorganize/preview`, {
      // The plan comes from the catalogue, so the body names no source and no
      // mode. The long timeout stays: it was needed for a cold provider lookup
      // that no longer happens, and a big album is still a lot of path work.
      json: {},
      timeout: 120_000,
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Reorganize preview failed');
  return payload;
}

/** Enqueue one lib2 album for reorganize — returns immediately, the queue
 *  worker processes items FIFO (docs §50). */
export async function applyLibraryV2AlbumReorganize(
  albumId: number,
  // `options` is kept as an empty-object parameter rather than dropped: the
  // artist-wide variant still takes one, and the two call sites read alike.
  _options: Record<string, never> = {},
): Promise<{ queued: boolean; queueId?: string; reason?: string }> {
  const payload = await readJson<{
    success: boolean;
    queued?: boolean;
    queue_id?: string;
    reason?: string;
    error?: string;
  }>(apiClient.post(`library/v2/albums/${albumId}/reorganize`, { json: {} }));
  if (!payload.success) throw new Error(payload.error || 'Reorganize failed');
  return { queued: Boolean(payload.queued), queueId: payload.queue_id, reason: payload.reason };
}

/** Enqueue every album of one lib2 artist for reorganize (docs §50). Same
 *  source/mode pick applied to every album — no per-album overrides. */
export async function applyLibraryV2ArtistReorganizeAll(
  artistId: number,
  options: { source?: string | null; mode?: 'api' | 'tags' } = {},
): Promise<{ enqueued: number; alreadyQueued: number; totalAlbums: number }> {
  const payload = await readJson<{
    success: boolean;
    enqueued?: number;
    already_queued?: number;
    total_albums?: number;
    error?: string;
  }>(
    apiClient.post(`library/v2/artists/${artistId}/reorganize-all`, {
      json: { source: options.source ?? null, mode: options.mode ?? 'api' },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Reorganize-all failed');
  return {
    enqueued: payload.enqueued ?? 0,
    alreadyQueued: payload.already_queued ?? 0,
    totalAlbums: payload.total_albums ?? 0,
  };
}

interface RawReorganizeQueueItem {
  queue_id: string;
  album_id: string;
  album_title: string;
  artist_name: string;
  status: LibraryV2ReorganizeQueueItem['status'];
  result_status: string | null;
  current_track: string | null;
  progress_total: number;
  progress_processed: number;
  finished_at: number | null;
}

function normalizeReorganizeQueueItem(raw: RawReorganizeQueueItem): LibraryV2ReorganizeQueueItem {
  return {
    queueId: raw.queue_id,
    albumId: raw.album_id,
    albumTitle: raw.album_title,
    artistName: raw.artist_name,
    status: raw.status,
    resultStatus: raw.result_status ?? null,
    currentTrack: raw.current_track ?? null,
    progressTotal: raw.progress_total ?? 0,
    progressProcessed: raw.progress_processed ?? 0,
    finishedAt: raw.finished_at ?? null,
  };
}

/** Snapshot of the (legacy, shared) reorganize queue — the lib2 Reorganize
 *  modals poll this so an enqueued move has visible live status instead of
 *  being fire-and-forget (deep-dive G7). Same endpoint the legacy Enhanced
 *  View's status panel uses; album/artist ids in items are LEGACY ids
 *  (the lib2→legacy reorganize bridge, docs §50), so lib2 callers match by
 *  `queueId` (per-album apply) or `artistName` (best-effort for the
 *  artist-wide bulk apply, which doesn't get per-item ids back). */
export async function fetchLibraryV2ReorganizeQueueSnapshot(): Promise<LibraryV2ReorganizeQueueSnapshot> {
  const payload = await readJson<{
    success: boolean;
    active?: RawReorganizeQueueItem | null;
    queued?: RawReorganizeQueueItem[];
    recent?: RawReorganizeQueueItem[];
    error?: string;
  }>(apiClient.get('library/reorganize/queue'));
  if (!payload.success) throw new Error(payload.error || 'Failed to load the reorganize queue');
  return {
    active: payload.active ? normalizeReorganizeQueueItem(payload.active) : null,
    queued: (payload.queued ?? []).map(normalizeReorganizeQueueItem),
    recent: (payload.recent ?? []).map(normalizeReorganizeQueueItem),
  };
}

/** Candidate cover-art images for an album, for the art picker (docs §49). */
export async function fetchLibraryV2AlbumArtOptions(
  albumId: number,
  options: { refresh?: boolean } = {},
): Promise<LibraryV2ArtCandidate[]> {
  const params = new URLSearchParams();
  if (options.refresh) params.set('refresh', '1');
  const payload = await readJson<{
    success: boolean;
    candidates: LibraryV2ArtCandidate[];
    error?: string;
  }>(
    apiClient.get(`library/v2/albums/${albumId}/art-options`, {
      searchParams: params,
      // iss27-03: the backend fans out to ~7 providers under its own bounded
      // budget (core.metadata.artist_image._CANDIDATE_GATHER_TIMEOUT_S) —
      // ky's 10s default was tighter than that budget plus network/JSON
      // overhead, so a fully-successful backend answer could still arrive
      // after the client had already given up and blanked the picker.
      timeout: 20_000,
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to load cover art options');
  return payload.candidates;
}

/** Apply a cover chosen in the picker (docs §49). Pins the choice so a later
 *  refresh won't clobber it; returns the local artwork URL to re-render. */
export async function applyLibraryV2AlbumArt(albumId: number, url: string): Promise<string> {
  const payload = await readJson<{ success: boolean; image_url?: string; error?: string }>(
    apiClient.post(`library/v2/albums/${albumId}/art`, {
      json: { url },
      // dd28-01: the apply path is strictly slower than the art-options GET it
      // follows — a bounded remote download (up to 5 revalidated redirect hops
      // at (3.05, 15)s each), two optimize=True JPEG encodes, a DB write and
      // the per-entity artwork build lock. ky's 10s default aborted the client
      // while the server went on to finish the change, surfacing a raw ky
      // error over an apply that had actually succeeded.
      timeout: 45_000,
    }),
  );
  if (!payload.success || !payload.image_url) {
    throw new Error(payload.error || 'Failed to apply cover art');
  }
  return payload.image_url;
}

/** Stop overriding an album cover. The current image remains until the next
 * artwork rebuild/server refresh supplies the baseline again. */
export async function releaseLibraryV2AlbumArt(albumId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.delete(`library/v2/albums/${albumId}/art`),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to release cover art');
}

/** Candidate photos for an artist, for the image picker (deep-dive A9). */
export async function fetchLibraryV2ArtistArtOptions(
  artistId: number,
  options: { refresh?: boolean } = {},
): Promise<LibraryV2ArtCandidate[]> {
  const params = new URLSearchParams();
  if (options.refresh) params.set('refresh', '1');
  const payload = await readJson<{
    success: boolean;
    candidates: LibraryV2ArtCandidate[];
    error?: string;
  }>(
    apiClient.get(`library/v2/artists/${artistId}/art-options`, {
      searchParams: params,
      // iss27-03: see the matching comment on fetchLibraryV2AlbumArtOptions.
      timeout: 20_000,
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to load photo options');
  return payload.candidates;
}

/** Apply a photo chosen in the picker (deep-dive A9). Pins the choice so a
 *  later refresh won't clobber it; returns the local artwork URL to re-render. */
export async function applyLibraryV2ArtistArt(artistId: number, url: string): Promise<string> {
  const payload = await readJson<{ success: boolean; image_url?: string; error?: string }>(
    apiClient.post(`library/v2/artists/${artistId}/art`, {
      json: { url },
      // dd28-01: see the matching comment on applyLibraryV2AlbumArt.
      timeout: 45_000,
    }),
  );
  if (!payload.success || !payload.image_url) {
    throw new Error(payload.error || 'Failed to apply photo');
  }
  return payload.image_url;
}

/** Stop overriding an artist photo and follow automatic/server artwork again. */
export async function releaseLibraryV2ArtistArt(artistId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.delete(`library/v2/artists/${artistId}/art`),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to release artist photo');
}

export async function fetchLibraryV2JobStatus(jobId?: string): Promise<LibraryV2JobState> {
  const params = new URLSearchParams();
  if (jobId) params.set('job_id', jobId);
  return readJson<LibraryV2JobState>(
    apiClient.get('library/v2/jobs/status', { searchParams: params }),
  );
}

export async function fetchLibraryV2Track(trackId: number): Promise<LibraryV2Track> {
  const payload = await readJson<TrackResponse>(apiClient.get(`library/v2/tracks/${trackId}`));
  if (!payload.success || !payload.track) throw new Error(payload.error || 'Track not found');
  return payload.track;
}

export async function startLibraryV2Import(reset = false): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post('library/v2/import', { json: { reset } }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to start import');
}

export async function fetchLibraryV2ImportStatus(): Promise<LibraryV2ImportState> {
  return readJson<LibraryV2ImportState>(apiClient.get('library/v2/import/status'));
}

export function libraryV2ImportStatusQueryOptions(refetchIntervalMs = 1000) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'import-status'],
    queryFn: fetchLibraryV2ImportStatus,
    /**
     * iss29-A03: `running` is the IN-PROCESS flag, and only the manual Import
     * button ever sets it. The autostart bootstrap — the one an upgrading
     * installation actually runs — never touches it, and `bootstrap.status` is
     * its only descriptor. Polling on `running` alone meant the first status
     * fetch saw `bootstrap.status === 'running'`, React Query stopped the
     * timer, and the predicate was only re-evaluated after a fetch that would
     * now never happen (`refetchOnWindowFocus` is globally false). The page sat
     * on the same percentage for the entire migration.
     */
    refetchInterval: (query) =>
      query.state.data?.running ||
      query.state.data?.artwork_cache.running ||
      query.state.data?.bootstrap?.status === 'running' ||
      query.state.data?.bootstrap?.status === 'pending'
        ? refetchIntervalMs
        : false,
  });
}

export function libraryV2EnabledQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'enabled'],
    queryFn: fetchLibraryV2Enabled,
  });
}

export function libraryV2ArtistsQueryOptions(
  search: Pick<LibraryV2Search, 'q' | 'sort' | 'page' | 'monitored'> & { includeSize?: boolean },
) {
  return queryOptions({
    queryKey: [
      ...LIBRARY_V2_QUERY_KEY,
      'artists',
      search.q,
      search.sort,
      search.monitored,
      search.page,
      Boolean(search.includeSize),
    ],
    queryFn: () => fetchLibraryV2Artists(search),
  });
}

export function libraryV2ArtistQueryOptions(artistId: number) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist', artistId],
    queryFn: () => fetchLibraryV2Artist(artistId),
    enabled: artistId > 0,
  });
}

export function libraryV2AlbumQueryOptions(albumId: number, options: { resolve?: boolean } = {}) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'album', albumId, options.resolve ?? false],
    queryFn: () => fetchLibraryV2Album(albumId, options),
    enabled: albumId > 0,
    // Resolving a provider tracklist is a chain of blocking provider calls,
    // so the server does it off the request thread and says `pending`. Poll
    // until it lands instead of making the user wait on the open request —
    // that was what hung the page and held the DB write lock.
    refetchInterval: (query) =>
      query.state.data?.tracklist_sync?.status === 'pending' ? 2000 : false,
  });
}

export function libraryV2ArtistMatchStatusQueryOptions(artistId: number) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-match', artistId],
    queryFn: () => fetchLibraryV2ArtistMatchStatus(artistId),
    enabled: artistId > 0,
    staleTime: 30_000,
  });
}

export function libraryV2ArtistAliasesQueryOptions(artistId: number) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-aliases', artistId],
    queryFn: () => fetchLibraryV2ArtistAliases(artistId),
    enabled: artistId > 0,
  });
}

export function libraryV2AlbumMatchStatusQueryOptions(albumId: number) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'album-match', albumId],
    queryFn: () => fetchLibraryV2AlbumMatchStatus(albumId),
    enabled: albumId > 0,
    staleTime: 30_000,
  });
}

export function libraryV2TrackSourceInfoQueryOptions(trackId: number, enabled: boolean) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'track-source-info', trackId],
    queryFn: () => fetchLibraryV2TrackSourceInfo(trackId),
    enabled: enabled && trackId > 0,
    staleTime: 30_000,
  });
}

interface FileTagsResponse extends LibraryV2FileTags {
  success: boolean;
  error?: string;
}

/** Live embedded tags + lyrics read straight from the file (§18.1). */
export async function fetchLibraryV2TrackFileTags(trackId: number): Promise<LibraryV2FileTags> {
  const payload = await readJson<FileTagsResponse>(
    apiClient.get(`library/v2/tracks/${trackId}/file-tags`),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to read file tags');
  return payload;
}

export async function editTrackFileTag(trackId: number, key: string, value: string): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post(`library/v2/tracks/${trackId}/file-tags/edit`, {
      json: { key, value },
    }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to edit tag');
}

export function libraryV2TrackFileTagsQueryOptions(trackId: number, enabled: boolean) {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'track-file-tags', trackId],
    queryFn: () => fetchLibraryV2TrackFileTags(trackId),
    enabled: enabled && trackId > 0,
    staleTime: 30_000,
  });
}

export function libraryV2QualityProfilesQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'quality-profiles'],
    queryFn: fetchLibraryV2QualityProfiles,
  });
}

// --- UI display preferences (B5) ---------------------------------------------

interface UiPreferencesResponse {
  success: boolean;
  preferences: LibraryV2UiPreferences;
  error?: string;
}

/** Deep-partial patch for the PUT endpoint's shallow-merge-per-section
 *  contract (core/library2/ui_preferences.py's ``_merge_section``): each
 *  section field, and each field within `columns`, is independently optional. */
type UiPreferencesPatch = {
  track_table?: {
    columns?: Partial<LibraryV2TrackTableColumns>;
    column_order?: (keyof LibraryV2TrackTableColumns)[];
    column_widths?: Record<string, number | null>;
    show_all_match_providers?: boolean;
    visible_match_providers?: Record<string, boolean>;
    quality_show_format?: boolean;
    quality_show_resolution?: boolean;
    quality_show_bitrate?: boolean;
  };
  artist_table?: {
    columns?: Partial<LibraryV2ArtistTableColumns>;
    column_order?: (keyof LibraryV2ArtistTableColumns)[];
  };
};

export async function fetchLibraryV2UiPreferences(): Promise<LibraryV2UiPreferences> {
  const payload = await readJson<UiPreferencesResponse>(apiClient.get('library/v2/ui-preferences'));
  if (!payload.success) throw new Error(payload.error || 'Failed to load UI preferences');
  return payload.preferences;
}

/** Deep-partial patch — merged server-side onto the stored (or default)
 *  preferences one section deep, so callers only send what changed. */
export async function updateLibraryV2UiPreferences(
  patch: UiPreferencesPatch,
): Promise<LibraryV2UiPreferences> {
  const payload = await readJson<UiPreferencesResponse>(
    apiClient.put('library/v2/ui-preferences', { json: patch }),
  );
  if (!payload.success) throw new Error(payload.error || 'Failed to update UI preferences');
  return payload.preferences;
}

export function libraryV2UiPreferencesQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'ui-preferences'],
    queryFn: fetchLibraryV2UiPreferences,
    staleTime: 60_000,
  });
}

// --- Mirror outbox (lib2 → legacy wishlist/watchlist, audit P0-04) -----------

export interface LibraryV2MirrorStatus {
  pending: number;
  failed: number;
}

export async function fetchLibraryV2MirrorStatus(): Promise<LibraryV2MirrorStatus> {
  const payload = await readJson<{
    success: boolean;
    pending?: number;
    failed?: number;
    error?: string;
  }>(apiClient.get('library/v2/mirror-status'));
  if (!payload.success) throw new Error(payload.error || 'Failed to load mirror status');
  return { pending: payload.pending ?? 0, failed: payload.failed ?? 0 };
}

export async function retryLibraryV2Mirror(): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.post('library/v2/mirror-retry', { json: {} }),
  );
  if (!payload.success) throw new Error(payload.error || 'Mirror retry failed');
}

export function libraryV2MirrorStatusQueryOptions() {
  return queryOptions({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'mirror-status'],
    queryFn: fetchLibraryV2MirrorStatus,
    refetchInterval: 60_000,
  });
}

export function invalidateLibraryV2(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
}

// --- Interactive Search → SoulSync download pipeline -------------------------
// Reuses the existing multi-source file search (`/api/search`) and download
// (`/api/download`) endpoints — the same pipeline the main Search page drives,
// honouring the configured source priorities (download_source.mode/hybrid_order).

export interface SourceSearchResult {
  result_type: 'track' | 'album';
  username: string;
  filename: string;
  size: number;
  bitrate?: number | null;
  sample_rate?: number | null;
  bit_depth?: number | null;
  duration?: number | null;
  quality?: string | null;
  artist?: string | null;
  title?: string | null;
  album?: string | null;
  track_count?: number | null;
  free_upload_slots?: number | null;
  queue_length?: number | null;
  quality_score?: number | null;
  // Album-result fields (when result_type === 'album'):
  album_title?: string | null;
  album_path?: string | null;
  dominant_quality?: string | null;
  total_size?: number | null;
  tracks?: Array<Record<string, unknown>>;
  _source_metadata?: {
    protocol?: string;
    indexer?: string;
    grabs?: number;
    seeders?: number;
    leechers?: number;
    publish_date?: string | null;
  } | null;
}

/** Rank a search result's quality for sorting/picking (lossless hi-res >
 *  lossless > high lossy > rest). The SINGLE shared implementation for both
 *  Interactive Search's "Quality" column/tiebreak and Automatic Search's
 *  auto-grab pick (review A12) — two independent copies had drifted apart
 *  before, so extending one (e.g. AIFF/DSD) silently didn't extend the
 *  other, letting Interactive Search show a different "best" result than
 *  Automatic Search actually grabbed. */
export function rankSearchResultQuality(r: SourceSearchResult): number {
  const q = ((r.result_type === 'album' ? r.dominant_quality : r.quality) ?? '').toLowerCase();
  const bitDepth = r.bit_depth ?? 0;
  const lossless = q.includes('flac') || q.includes('alac') || q.includes('wav');
  if (lossless && bitDepth > 16) return 4;
  if (lossless) return 3;
  const kbps = bitrateKbps(r.bitrate) ?? 0;
  if (kbps >= 256 || q.includes('320')) return 2;
  if (q) return 1;
  return 0;
}

export interface DownloadOptions {
  skipAcoustid?: boolean;
  qualityCheck?: boolean;
}

/** The Library-v2 entity a manual grab acts for. Only the ids travel — the
 *  server re-validates them and resolves the entity's EFFECTIVE quality
 *  profile itself, so the client can't dictate the profile (audit P1-16).
 *  `qualityProfileId` is display-only (search-modal preview badge). */
export interface Lib2EntityRef {
  trackId?: number;
  albumId?: number;
  qualityProfileId?: number;
}

function lib2EntityFields(entity?: Lib2EntityRef): Record<string, number> {
  return {
    ...(entity?.trackId ? { lib2_track_id: entity.trackId } : {}),
    ...(entity?.albumId ? { lib2_album_id: entity.albumId } : {}),
  };
}

export async function searchSources(
  query: string,
  source?: string,
  entity?: Lib2EntityRef,
): Promise<SourceSearchResult[]> {
  const payload = await readJson<{ results?: SourceSearchResult[]; error?: string }>(
    apiClient.post('search', {
      json: { query, ...(source ? { source } : {}), ...lib2EntityFields(entity) },
      timeout: 90_000, // source search (Soulseek etc.) can take up to ~75s
    }),
  );
  if (payload.error) throw new Error(payload.error);
  return payload.results ?? [];
}

export interface DownloadSearchSource {
  name: string;
  display_name: string;
}

export interface DownloadSearchSources {
  mode: string;
  sources: DownloadSearchSource[];
}

export async function listSearchSources(): Promise<DownloadSearchSources> {
  try {
    const payload = await readJson<{
      mode?: string;
      sources?: Array<{ name?: string; display_name?: string } | string>;
    }>(apiClient.get('search/sources'));
    const raw = payload.sources ?? [];
    return {
      mode: payload.mode ?? 'unknown',
      sources: raw
        .map((source) => {
          const name = typeof source === 'string' ? source : (source.name ?? '');
          const displayName =
            typeof source === 'string' ? source : (source.display_name ?? source.name ?? '');
          return { name, display_name: displayName };
        })
        .filter((source) => source.name),
    };
  } catch {
    return { mode: 'unknown', sources: [] };
  }
}

export async function startSourceDownload(
  result: SourceSearchResult,
  options: DownloadOptions = {},
  entity?: Lib2EntityRef,
): Promise<void> {
  const checks = {
    skip_acoustid: options.skipAcoustid === true,
    quality_check: options.qualityCheck !== false,
    ...lib2EntityFields(entity),
  };
  // The track payload is rebuilt field by field rather than passed through,
  // and it used to omit two the server reads: `bitrate` (web_server.py:7732,
  // recorded on the download row) and `album_name` (:7730, and the import
  // pipeline's album folder). A track result carries its album in `album`, not
  // `album_title` -- only album results use that key -- which is how the field
  // went missing on exactly the path that needs it (frontend-audit FE-03).
  //
  // Still an explicit allowlist, not a spread of the whole result: the server
  // sanitizes client metadata (`sanitize_client_import_metadata`), and a blind
  // passthrough would send provider internals and `_source_metadata` with every
  // grab.
  const json =
    result.result_type === 'album'
      ? {
          result_type: 'album',
          album_name: result.album_title,
          tracks: result.tracks ?? [],
          ...checks,
        }
      : {
          result_type: 'track',
          username: result.username,
          filename: result.filename,
          size: result.size,
          title: result.title,
          artist: result.artist,
          album_name: result.album ?? result.album_title ?? undefined,
          quality: result.quality,
          bitrate: result.bitrate ?? undefined,
          sample_rate: result.sample_rate ?? undefined,
          bit_depth: result.bit_depth ?? undefined,
          duration: result.duration ?? undefined,
          ...checks,
        };
  /**
   * `throwHttpErrors: false` for the same reason `refreshLibraryV2` uses it
   * (iss29-C02): the meaning is in the BODY, not the status. A blocklisted
   * artist answers 409 with `{success:false, blocked:true, blocked_entity_type,
   * blocked_name}` -- no `error`, no `message`. ky is created with `retry: 0`
   * and default `throwHttpErrors`, so `readJson` threw an HTTPError before
   * `payload.blocked` was ever evaluated: the blocklist branch below was
   * unreachable, and the user saw a bare HTTP status string instead of being
   * told which artist is blocked and why.
   */
  const payload = await readJson<{
    success?: boolean;
    error?: string;
    message?: string;
    blocked?: boolean;
    blocked_entity_type?: string;
    blocked_name?: string;
  }>(apiClient.post('download', { json, timeout: 30_000, throwHttpErrors: false }));
  if (payload.blocked) {
    const what = payload.blocked_name
      ? `${payload.blocked_entity_type || 'This'} "${payload.blocked_name}"`
      : 'This artist';
    throw new Error(`${what} is blocklisted. Remove it from the blocklist to download.`);
  }
  if (payload.error) throw new Error(payload.error);
  // A non-2xx with neither `blocked` nor `error` must still fail -- without
  // this, dropping throwHttpErrors turns every other server error into a
  // silent success.
  if (payload.success === false) throw new Error(payload.message || 'Download was refused');
}

/** Auto-grab: search and download the best result (for non-interactive "Search"). */
export async function autoGrabBest(
  query: string,
  options: DownloadOptions = {},
  entity?: Lib2EntityRef,
): Promise<SourceSearchResult | null> {
  const all = await searchSources(query, undefined, entity);
  if (all.length === 0) return null;
  // A track action must not grab a release-bundle result (audit P1-18):
  // the pipeline would import one arbitrary file of a whole album.
  const pool = entity?.trackId ? all.filter((r) => r.result_type === 'track') : all;
  if (pool.length === 0) return null;
  // Prefer the same quality tier Interactive Search ranks best, then
  // highest quality_score, then most upload slots.
  const score = (r: SourceSearchResult) =>
    rankSearchResultQuality(r) * 1e6 + (r.quality_score ?? 0) * 100 + (r.free_upload_slots ?? 0);
  const best = [...pool].sort((a, b) => score(b) - score(a))[0];
  await startSourceDownload(best, options, entity);
  return best;
}

/** rev25-02: state of a cover the server resolves in the background. */
export type LibraryV2ArtworkState =
  | { state: 'ready'; version: number }
  | { state: 'pending' }
  | { state: 'unavailable' };

interface ArtworkStatusResponse {
  success: boolean;
  states: Record<string, LibraryV2ArtworkState>;
}

/** Ask which of these entities' covers finished building.
 *
 *  A cold cover answers 404 and builds off-thread, but an `<img>` cannot read
 *  the pending header and a fixed retry ladder expires before a slow provider
 *  walk does. One batched poll per tick replaces up to four blind retries per
 *  image. */
export async function fetchLibraryV2ArtworkStatus(
  kind: 'artist' | 'album',
  ids: number[],
): Promise<Record<string, LibraryV2ArtworkState>> {
  if (ids.length === 0) return {};
  const payload = await readJson<ArtworkStatusResponse>(
    apiClient.get('library/v2/artwork/status', {
      searchParams: { kind, ids: ids.join(',') },
    }),
  );
  return payload.states ?? {};
}
