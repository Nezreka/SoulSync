import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useState } from 'react';

import { AudiobookRouteToggle } from '@/features/audiobook-routes/audiobook-route-toggle';
import { playTrackByMetadata } from '@/features/playback/play-track';
import { getShellBridge } from '@/platform/shell/bridge';
import { ActionMenu } from '@/routes/artist-detail/-ui/action-menu';

import type {
  WatchlistArtistConfig,
  WatchlistArtistLibrary,
  WatchlistArtistConfigResponse,
  WatchlistMetadataSource,
} from '../-watchlist.types';
import type { LibraryAlbum } from '../../library/-library.types';

import {
  removeWatchlistArtist,
  saveWatchlistArtistConfig,
  WATCHLIST_QUERY_KEY,
  watchlistArtistConfigQueryOptions,
  type WatchlistArtistConfigUpdate,
} from '../-watchlist.api';
import {
  autoDownloadPrefFromSelect,
  autoDownloadSelectValue,
  describeAutoDownload,
  effectiveAutoDownload,
  formatRelativeScanTime,
  formatSince,
} from '../-watchlist.helpers';
import { loadAlbumTracks } from '../../library/-library.api';
import { albumQueueTrack } from '../../library/-library.helpers';
import { hideOnError } from './hide-on-error';
import { WatchlistLinkedProviders } from './watchlist-linked-providers';

/**
 * One panel per artist (#1822). It replaces three views that each showed part
 * of the same artist: the inspector drawer, the full-screen detail and the
 * settings modal. Overview is who they are and what they are to you (your
 * plays, what you own), Rules is what the watchlist grabs,
 * Sources is where it looks. Rules and Sources share one draft and one save.
 */

export type ArtistPanelTab = 'overview' | 'rules' | 'sources';

const RELEASE_TYPE_KEYS = ['include_albums', 'include_eps', 'include_singles'] as const;

const CONTENT_FILTER_KEYS = [
  'include_live',
  'include_remixes',
  'include_acoustic',
  'include_compilations',
  'include_instrumentals',
  'include_other_artists',
] as const;

type IncludeKey = (typeof RELEASE_TYPE_KEYS)[number] | (typeof CONTENT_FILTER_KEYS)[number];

const OPTION_COPY: Record<IncludeKey, { title: string; description: string }> = {
  include_albums: { title: 'Albums', description: 'Full-length studio albums' },
  include_eps: { title: 'EPs', description: 'Extended plays (4-6 tracks)' },
  include_singles: { title: 'Singles', description: 'Single tracks and 2-3 track releases' },
  include_live: { title: 'Live', description: 'Live performances and concerts' },
  include_remixes: { title: 'Remixes', description: 'Remix versions and edits' },
  include_acoustic: { title: 'Acoustic', description: 'Acoustic and stripped versions' },
  include_compilations: { title: 'Compilations', description: 'Greatest hits and collections' },
  include_instrumentals: {
    title: 'Instrumentals',
    description: 'Instrumental, karaoke and backing track versions',
  },
  include_other_artists: {
    title: 'Other artists',
    description: 'Tracks by other artists on tributes, soundtracks and compilations',
  },
};

/** The fixed lookback options from the vanilla <select>. */
const LOOKBACK_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Use Global Setting' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
  { value: '180', label: 'Last 6 months' },
  { value: '365', label: 'Last year' },
  { value: '730', label: 'Last 2 years' },
  { value: '1825', label: 'Last 5 years' },
  { value: '36500', label: 'Entire discography' },
];

const METADATA_SOURCE_META: Record<
  WatchlistMetadataSource,
  { label: string; idKey: keyof WatchlistArtistConfigResponse }
> = {
  spotify: { label: 'Spotify', idKey: 'spotify_artist_id' },
  deezer: { label: 'Deezer', idKey: 'deezer_artist_id' },
  itunes: { label: 'Apple Music', idKey: 'itunes_artist_id' },
  discogs: { label: 'Discogs', idKey: 'discogs_artist_id' },
  musicbrainz: { label: 'MusicBrainz', idKey: 'musicbrainz_artist_id' },
};

const METADATA_SOURCE_ORDER: WatchlistMetadataSource[] = [
  'spotify',
  'deezer',
  'itunes',
  'discogs',
  'musicbrainz',
];

/** at least one release type must be selected. */
export function artistConfigIsSavable(config: Pick<WatchlistArtistConfig, IncludeKey>): boolean {
  return RELEASE_TYPE_KEYS.some((key) => config[key]);
}

/** "1,234,567" — the vanilla hero used the app's formatNumber for followers. */
export function formatFollowers(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '0';
  return value.toLocaleString('en-US');
}

/**
 * Which provider the "View Discography" link should use.
 *
 * Prefers the id belonging to the ACTIVE music source so the discography page
 * opens against the provider the rest of the app is using, then falls back
 * through the remaining matches. iTunes is the explicit fallback before the
 * generic chain, matching the vanilla ladder exactly.
 *
 * A pinned source is safe in exactly two cases, and the server reports both:
 * its provider is alive (`available_sources`), or its id resolves straight to
 * a LIBRARY artist (`library_resolvable_sources`). Pinning anything else
 * navigates into a guaranteed "provider is unavailable" 503. With no server
 * knowledge at all every source stays eligible.
 */
export function pickDiscographySource(
  payload: Pick<
    WatchlistArtistConfigResponse,
    | 'spotify_artist_id'
    | 'itunes_artist_id'
    | 'deezer_artist_id'
    | 'discogs_artist_id'
    | 'musicbrainz_artist_id'
    | 'global_metadata_source'
    | 'available_sources'
    | 'library_resolvable_sources'
  >,
): { id: string; source: string } | null {
  const active = (payload.global_metadata_source || '').toLowerCase();
  const available = payload.available_sources;
  const resolvable = payload.library_resolvable_sources;
  const serverKnows = available != null || resolvable != null;
  const pick = (source: string, id: string | null | undefined): string | null => {
    if (!id) return null;
    if (!serverKnows) return id;
    return available?.includes(source) || resolvable?.includes(source) ? id : null;
  };

  const spotify = pick('spotify', payload.spotify_artist_id);
  const itunes = pick('itunes', payload.itunes_artist_id);
  const deezer = pick('deezer', payload.deezer_artist_id);
  const discogs = pick('discogs', payload.discogs_artist_id);
  const musicbrainz = pick('musicbrainz', payload.musicbrainz_artist_id);

  if (active.includes('spotify') && spotify) return { id: spotify, source: 'spotify' };
  if (active.includes('discogs') && discogs) return { id: discogs, source: 'discogs' };
  if (active.includes('deezer') && deezer) return { id: deezer, source: 'deezer' };
  if (active.includes('musicbrainz') && musicbrainz)
    return { id: musicbrainz, source: 'musicbrainz' };
  if (itunes) return { id: itunes, source: 'itunes' };

  if (spotify) return { id: spotify, source: 'spotify' };
  if (discogs) return { id: discogs, source: 'discogs' };
  if (deezer) return { id: deezer, source: 'deezer' };
  if (musicbrainz) return { id: musicbrainz, source: 'musicbrainz' };
  return null;
}

/** the fields a save sends, in the server's shape. */
function updateFromDraft(draft: WatchlistArtistConfig): WatchlistArtistConfigUpdate {
  return {
    include_albums: draft.include_albums,
    include_eps: draft.include_eps,
    include_singles: draft.include_singles,
    include_live: draft.include_live,
    include_remixes: draft.include_remixes,
    include_acoustic: draft.include_acoustic,
    include_compilations: draft.include_compilations,
    include_instrumentals: draft.include_instrumentals,
    include_other_artists: draft.include_other_artists,
    // The preference, never the legacy boolean: the server reads
    // `auto_download` as a deliberate choice, so sending it on every save
    // would opt every artist the user ever opened out of the global default.
    auto_download_pref: draft.auto_download_pref ?? null,
    quality_profile_id: draft.quality_profile_id,
    lookback_days: draft.lookback_days,
    preferred_metadata_source: draft.preferred_metadata_source,
    // always said, so the server knows which rules a save means (#1822)
    follow_defaults: Boolean(draft.follow_defaults),
  };
}

interface Props {
  profileId: number;
  artistId: string;
  initialTab: ArtistPanelTab;
  onClose: () => void;
}

export function WatchlistArtistPanel({ profileId, artistId, initialTab, onClose }: Props) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const configQuery = useQuery(watchlistArtistConfigQueryOptions(profileId, artistId));
  const payload = configQuery.data;

  const [tab, setTab] = useState<ArtistPanelTab>(initialTab);
  useEffect(() => setTab(initialTab), [initialTab, artistId]);
  const [bioOpen, setBioOpen] = useState(false);
  const [heroFailed, setHeroFailed] = useState(false);
  useEffect(() => {
    setBioOpen(false);
    setHeroFailed(false);
  }, [artistId]);

  const [draft, setDraft] = useState<WatchlistArtistConfig | null>(null);
  // Adopt the server config once, and again if a re-link refetches it. Editing
  // starts from what is stored, never from invented defaults.
  useEffect(() => {
    if (payload?.config) setDraft(payload.config);
  }, [payload?.config]);

  const dirty = useMemo(() => {
    if (!draft || !payload?.config) return false;
    return (
      JSON.stringify(updateFromDraft(draft)) !== JSON.stringify(updateFromDraft(payload.config))
    );
  }, [draft, payload?.config]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const save = useMutation({
    mutationFn: (update: WatchlistArtistConfigUpdate) =>
      saveWatchlistArtistConfig(artistId, update),
    onSuccess: async () => {
      window.showToast?.('Artist preferences saved', 'success');
      await queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY });
    },
    onError: (error: Error) =>
      window.showToast?.(`Error saving preferences: ${error.message}`, 'error'),
  });

  const remove = useMutation({
    mutationFn: () => removeWatchlistArtist(artistId),
    onSuccess: async () => {
      onClose();
      await queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY });
      try {
        window.updateWatchlistButtonCount?.();
      } catch {
        /* non-fatal */
      }
    },
    onError: (error: Error) =>
      window.showToast?.(`Error removing from watchlist: ${error.message}`, 'error'),
  });

  const artist = payload?.artist;
  const config = payload?.config;
  const name = artist?.name ?? '';

  const onSave = () => {
    if (!draft) return;
    if (!draft.follow_defaults && !artistConfigIsSavable(draft)) {
      window.showToast?.('Please select at least one release type', 'error');
      return;
    }
    save.mutate(updateFromDraft(draft));
  };

  const onRemove = async () => {
    // the old detail view removed on one click with no way back
    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove Artist',
      message: `Remove ${name || 'this artist'} from your watchlist?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    remove.mutate();
  };

  // changing a rule on an artist that follows the defaults gives it its own
  // rules, starting from the defaults it was getting
  const setFlag = (key: IncludeKey, value: boolean) =>
    setDraft((previous) =>
      previous ? { ...previous, follow_defaults: false, [key]: value } : previous,
    );
  const setFollow = (follow: boolean) =>
    setDraft((previous) => {
      if (!previous) return previous;
      if (!follow) return { ...previous, follow_defaults: false };
      // show the rules it will get
      const defaults = payload?.config?.defaults ?? {};
      const next = { ...previous, follow_defaults: true };
      for (const key of [...RELEASE_TYPE_KEYS, ...CONTENT_FILTER_KEYS]) {
        const value = defaults[key];
        if (typeof value === 'boolean') next[key] = value;
      }
      return next;
    });
  const following = Boolean(draft?.follow_defaults);

  const discography = payload ? pickDiscographySource(payload) : null;
  const releases = payload?.recent_releases ?? [];
  const library = payload?.library ?? null;
  const listening = payload?.listening ?? null;
  // the library page's own album play: every queued row has a file
  const playLibraryAlbum = async (album: WatchlistArtistLibrary['recent_albums'][number]) => {
    if (!library) return;
    const owned: LibraryAlbum = {
      id: album.id,
      title: album.title,
      year: album.year,
      thumb_url: album.thumb_url || null,
      artist_id: library.artist_id,
      artist_name: name,
    };
    try {
      const tracks = await loadAlbumTracks(album.id);
      if (!tracks.length) {
        window.showToast?.(`No playable tracks in ${album.title}`, 'info');
        return;
      }
      await window.playTrackList?.(
        tracks.map((track) => albumQueueTrack(track, owned)),
        album.title,
      );
    } catch {
      window.showToast?.(`Could not play ${album.title}`, 'error');
    }
  };
  const openLibraryArtist = () => {
    if (!library) return;
    onClose();
    void window.navigateToPage?.('artist-detail', {
      artistId: library.artist_id,
      artistName: name,
    });
  };
  const metaTags = [artist?.style, artist?.mood, artist?.label].filter((value): value is string =>
    Boolean(value),
  );
  const globalSource = payload?.global_metadata_source || 'deezer';
  const globalSourceLabel =
    METADATA_SOURCE_META[globalSource as WatchlistMetadataSource]?.label ?? globalSource;
  const scanText = config?.last_scan_timestamp
    ? formatRelativeScanTime(config.last_scan_timestamp)
    : 'Never scanned';
  const addedText = config?.date_added
    ? `added ${new Date(config.date_added).toLocaleDateString()}`
    : '';

  const heroImage = !heroFailed && artist?.image_url ? artist.image_url : null;
  const headText = (
    <div className="wap-head-text">
      <h2 className="wap-name" title={name}>
        {name || 'Loading…'}
      </h2>
      <span className="wap-sub">
        {config ? (
          <span className={`wap-pill${config.follow_defaults ? '' : ' wap-pill--custom'}`}>
            {config.follow_defaults ? 'Defaults' : 'Custom rules'}
          </span>
        ) : null}
        {scanText}
        {addedText ? ` · ${addedText}` : ''}
      </span>
    </div>
  );
  const headActions = (
    <>
      <ActionMenu
        className="wap-menu"
        items={[
          {
            key: 'remove',
            label: 'Remove from watchlist',
            danger: true,
            disabled: remove.isPending,
            onSelect: () => void onRemove(),
          },
        ]}
        trigger={(props) => (
          <button
            type="button"
            className="watchlist-inspector-close-btn"
            aria-label="Artist actions"
            {...props}
            ref={props.ref as (el: HTMLButtonElement | null) => void}
          >
            ⋯
          </button>
        )}
      />
      <button
        type="button"
        className="watchlist-inspector-close-btn"
        aria-label="Close"
        onClick={onClose}
      >
        ✕
      </button>
    </>
  );

  return (
    <div className="watchlist-inspector-backdrop" onClick={onClose}>
      <div
        className="watchlist-inspector-drawer wap"
        role="dialog"
        aria-modal="true"
        aria-label={name ? `${name} on your watchlist` : 'Watchlist artist'}
        onClick={(event) => event.stopPropagation()}
      >
        {heroImage ? (
          <div className="wap-hero">
            <img
              className="wap-hero-img"
              src={heroImage}
              alt=""
              onError={() => setHeroFailed(true)}
            />
            <div className="wap-hero-actions">{headActions}</div>
            <div className="wap-hero-text">{headText}</div>
          </div>
        ) : (
          <div className="wap-head">
            <span className="wap-avatar wap-avatar--empty" aria-hidden="true" />
            {headText}
            {headActions}
          </div>
        )}

        <div className="wap-tabs" role="tablist" aria-label="Artist panel">
          {(['overview', 'rules', 'sources'] as const).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={`wap-tab${tab === key ? ' is-on' : ''}`}
              onClick={() => setTab(key)}
            >
              {key === 'overview' ? 'Overview' : key === 'rules' ? 'Rules' : 'Sources'}
            </button>
          ))}
        </div>

        {configQuery.isError ? (
          <p className="wap-note">Could not load this artist&apos;s settings.</p>
        ) : null}

        {tab === 'overview' ? (
          <div className="wap-body">
            {artist?.followers || artist?.popularity ? (
              <div className="wap-stats">
                {artist.followers ? (
                  <div>
                    <b>{formatFollowers(artist.followers)}</b>
                    <span>Followers</span>
                  </div>
                ) : null}
                {artist.popularity ? (
                  <div>
                    <b>{artist.popularity}/100</b>
                    <span>Popularity</span>
                  </div>
                ) : null}
              </div>
            ) : null}
            {artist?.genres && artist.genres.length > 0 ? (
              <div className="wap-tags">
                {artist.genres.map((genre) => (
                  <span key={genre}>{genre}</span>
                ))}
              </div>
            ) : null}
            {metaTags.length > 0 ? (
              <div className="wap-tags wap-tags--quiet">
                {metaTags.map((tag) => (
                  <span key={tag}>{tag}</span>
                ))}
              </div>
            ) : null}
            {listening ? (
              <section className="wap-section">
                <h3>Your listening</h3>
                <div className="wap-listen">
                  <div className="wap-listen-top">
                    <div className="wap-listen-hero">
                      <b>{listening.play_count.toLocaleString()}</b>
                      <span>{listening.play_count === 1 ? 'play' : 'plays'}</span>
                    </div>
                    <PlaysByMonth months={listening.months ?? []} />
                  </div>
                  <p className="wap-listen-meta">
                    {listening.plays_30d.toLocaleString()} in the last 30 days
                    {listening.last_played
                      ? ` · last played ${formatSince(listening.last_played)}`
                      : ''}
                  </p>
                  {listening.top_tracks.length > 0 ? (
                    <ol className="wap-toptracks">
                      {listening.top_tracks.map((track) => (
                        <li key={track.title}>
                          <button
                            type="button"
                            title={`Play ${track.title}`}
                            onClick={() =>
                              void playTrackByMetadata(
                                getShellBridge(),
                                track.title,
                                name,
                                track.album,
                              )
                            }
                          >
                            <span className="wap-toptrack-art" aria-hidden="true">
                              {track.thumb_url ? (
                                <img src={track.thumb_url} alt="" onError={hideOnError} />
                              ) : null}
                            </span>
                            <span className="wap-toptrack-text">
                              <span>{track.title}</span>
                              {/* a single's album is its own title, so it says nothing */}
                              {track.album &&
                              track.album.toLowerCase() !== track.title.toLowerCase() ? (
                                <small>{track.album}</small>
                              ) : null}
                            </span>
                            <span className="wap-toptrack-count">
                              {track.play_count.toLocaleString()}
                              <small>{track.play_count === 1 ? 'play' : 'plays'}</small>
                            </span>
                          </button>
                        </li>
                      ))}
                    </ol>
                  ) : null}
                </div>
              </section>
            ) : null}
            {library ? (
              <section className="wap-section">
                <div className="wap-section-head">
                  <h3>In your library</h3>
                  <button type="button" className="wap-link" onClick={openLibraryArtist}>
                    Open
                  </button>
                </div>
                <span className="wap-hint">
                  {library.album_count} {library.album_count === 1 ? 'album' : 'albums'} ·{' '}
                  {library.track_count} {library.track_count === 1 ? 'track' : 'tracks'}
                </span>
                {library.recent_albums.length > 0 ? (
                  <div className="wap-releases">
                    {library.recent_albums.map((album) => (
                      <button
                        key={String(album.id)}
                        type="button"
                        className="wap-release-btn"
                        title={`Play ${album.title}`}
                        onClick={() => void playLibraryAlbum(album)}
                      >
                        <span className="wap-release-art">
                          {album.thumb_url ? (
                            <img src={album.thumb_url} alt="" onError={hideOnError} />
                          ) : (
                            <span className="wap-cover-empty" aria-hidden="true" />
                          )}
                          <span className="wap-release-play" aria-hidden="true">
                            ▶
                          </span>
                        </span>
                        <span>{album.title}</span>
                        <small>
                          {formatSince(album.added_at)
                            ? `added ${formatSince(album.added_at)}`
                            : album.year || `${album.track_count} tracks`}
                        </small>
                      </button>
                    ))}
                  </div>
                ) : null}
              </section>
            ) : null}
            {releases.length > 0 ? (
              <section className="wap-section">
                <h3>Recent releases</h3>
                <div className="wap-releases">
                  {releases.map((release) => (
                    <div key={`${release.album_name}-${release.release_date ?? ''}`}>
                      {release.album_cover_url ? (
                        <img src={release.album_cover_url} alt="" />
                      ) : (
                        <span className="wap-cover-empty" aria-hidden="true" />
                      )}
                      <span>{release.album_name}</span>
                      <small>
                        {release.release_date || 'Unknown date'}
                        {release.track_count ? ` · ${release.track_count} tracks` : ''}
                      </small>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
            {artist?.summary ? (
              <section className="wap-section">
                <h3>About</h3>
                <p className={`wap-bio${bioOpen ? '' : ' is-clamped'}`}>{artist.summary}</p>
                <button
                  type="button"
                  className="wap-link wap-link--start"
                  onClick={() => setBioOpen((open) => !open)}
                >
                  {bioOpen ? 'Less' : 'More'}
                </button>
              </section>
            ) : null}
            <button
              type="button"
              className="wl-chip wl-chip--cta wap-discog"
              disabled={!discography}
              title={
                payload && !discography
                  ? 'This artist is only matched on disabled metadata sources. ' +
                    'Match it to your active source under Sources.'
                  : undefined
              }
              onClick={() => {
                if (!discography) return;
                onClose();
                void navigate({
                  to: '/artist-detail/$source/$id',
                  params: { source: discography.source, id: discography.id },
                });
              }}
            >
              View Discography
            </button>
          </div>
        ) : null}

        {tab === 'rules' && draft ? (
          // the id is what helper.js keys its contextual help off
          <div className="wap-body" id="watchlist-artist-config-modal">
            <label className="wap-follow">
              <span>
                <b>Follow watchlist defaults</b>
                <small className="wap-hint">
                  {following
                    ? 'Uses the defaults, and changes with them. Change a rule below to give this artist its own.'
                    : 'This artist has its own rules.'}
                </small>
              </span>
              <input
                type="checkbox"
                role="switch"
                checked={following}
                onChange={(event) => setFollow(event.target.checked)}
              />
            </label>

            <section className="config-section wap-section wap-grab">
              <h3>What to grab</h3>
              <div className="wap-chips">
                {RELEASE_TYPE_KEYS.map((key) => (
                  <ToggleChip
                    key={key}
                    checked={draft[key]}
                    onChange={(value) => setFlag(key, value)}
                    {...OPTION_COPY[key]}
                  />
                ))}
              </div>
            </section>

            <section className="config-section wap-section wap-include">
              <h3>Also include</h3>
              <div className="wap-chips">
                {CONTENT_FILTER_KEYS.map((key) => (
                  <ToggleChip
                    key={key}
                    checked={draft[key]}
                    onChange={(value) => setFlag(key, value)}
                    {...OPTION_COPY[key]}
                  />
                ))}
              </div>
            </section>

            <section className="config-section wap-section">
              <label className="wap-label" htmlFor="watchlist-config-auto-download">
                New releases from this artist
              </label>
              {/* Three states, not a checkbox. "Follow the global" is a real,
                  distinct answer: it is what lets the defaults move every artist
                  the user never had an opinion about. */}
              <select
                id="watchlist-config-auto-download"
                className="form-select"
                value={autoDownloadSelectValue(draft.auto_download_pref)}
                onChange={(event) =>
                  setDraft((previous) =>
                    previous
                      ? {
                          ...previous,
                          auto_download_pref: autoDownloadPrefFromSelect(event.target.value),
                          // Keep the legacy mirror in step so anything still
                          // reading the boolean shows the same answer.
                          auto_download: effectiveAutoDownload(
                            autoDownloadPrefFromSelect(event.target.value),
                            previous.global_auto_download ?? true,
                          ),
                        }
                      : previous,
                  )
                }
              >
                <option value="inherit">
                  Use the global default (currently{' '}
                  {draft.global_auto_download === false ? 'off' : 'on'})
                </option>
                <option value="always">Always download</option>
                <option value="never">Never download — follow only</option>
              </select>
              <small className="wap-hint">
                {describeAutoDownload(draft.auto_download_pref, draft.global_auto_download ?? true)}
              </small>
            </section>

            <details className="wap-advanced">
              <summary>Advanced</summary>
              <div className="wap-advanced-body">
                <label className="wap-label" htmlFor="watchlist-config-quality-profile">
                  Quality Profile
                </label>
                <select
                  id="watchlist-config-quality-profile"
                  className="form-select"
                  value={draft.quality_profile_id != null ? String(draft.quality_profile_id) : ''}
                  onChange={(event) =>
                    setDraft((previous) =>
                      previous
                        ? {
                            ...previous,
                            quality_profile_id: event.target.value
                              ? Number.parseInt(event.target.value, 10)
                              : null,
                          }
                        : previous,
                    )
                  }
                >
                  {/* Explicit "Use default" so an artist with no saved profile —
                      or one whose profile was since deleted — never silently
                      adopts the first listed profile on save. */}
                  <option value="">Use default</option>
                  {(payload?.quality_profiles ?? []).map((profile) => (
                    <option key={profile.id} value={String(profile.id)}>
                      {profile.name || `Profile ${profile.id}`}
                      {profile.is_default ? ' (Default)' : ''}
                    </option>
                  ))}
                </select>
                <small className="wap-hint">Every release this artist queues uses it.</small>

                <label className="wap-label" htmlFor="watchlist-config-lookback">
                  Scan lookback
                </label>
                <select
                  id="watchlist-config-lookback"
                  className="form-select"
                  value={draft.lookback_days != null ? String(draft.lookback_days) : ''}
                  onChange={(event) =>
                    setDraft((previous) =>
                      previous
                        ? {
                            ...previous,
                            lookback_days: event.target.value
                              ? Number.parseInt(event.target.value, 10)
                              : null,
                          }
                        : previous,
                    )
                  }
                >
                  {LOOKBACK_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <small className="wap-hint">
                  How far back the first scan of this artist looks.
                </small>

                {artist?.name ? (
                  <>
                    <span className="wap-label">Audiobooks</span>
                    <small className="wap-hint">
                      File this artist&apos;s downloads in the audiobook library, for spoken-word
                      releases like children&apos;s audio plays. Applies to every download.
                    </small>
                    <AudiobookRouteToggle name={String(artist.name)} />
                  </>
                ) : null}
              </div>
            </details>
          </div>
        ) : null}

        {tab === 'sources' && draft ? (
          <div className="wap-body">
            <section className="wap-section">
              <h3>Scan source</h3>
              <small className="wap-hint">
                Which provider is asked for this artist&apos;s new releases.
              </small>
              <div className="config-metadata-source-selector">
                <button
                  type="button"
                  className={`config-msrc-btn${!draft.preferred_metadata_source ? ' active' : ''}`}
                  title={`Use global default (${globalSourceLabel})`}
                  onClick={() =>
                    setDraft((previous) =>
                      previous ? { ...previous, preferred_metadata_source: null } : previous,
                    )
                  }
                >
                  <span className="config-msrc-label">Default ({globalSourceLabel})</span>
                </button>
                {/* Only providers this artist is actually matched on can be
                    chosen as its scan source. */}
                {METADATA_SOURCE_ORDER.filter((key) =>
                  Boolean(payload?.[METADATA_SOURCE_META[key].idKey]),
                ).map((key) => (
                  <button
                    key={key}
                    type="button"
                    className={`config-msrc-btn${draft.preferred_metadata_source === key ? ' active' : ''}`}
                    title={METADATA_SOURCE_META[key].label}
                    onClick={() =>
                      setDraft((previous) =>
                        previous ? { ...previous, preferred_metadata_source: key } : previous,
                      )
                    }
                  >
                    <span className="config-msrc-label">{METADATA_SOURCE_META[key].label}</span>
                  </button>
                ))}
              </div>
            </section>
            {payload ? (
              <WatchlistLinkedProviders
                profileId={profileId}
                artistId={artistId}
                payload={payload}
              />
            ) : null}
          </div>
        ) : null}

        {dirty ? (
          <div className="wap-savebar">
            <span>Unsaved changes</span>
            <button
              type="button"
              className="wl-chip wl-chip--slate"
              onClick={() => payload?.config && setDraft(payload.config)}
            >
              Discard
            </button>
            <button
              type="button"
              className="wl-chip wl-chip--cta"
              disabled={save.isPending}
              onClick={onSave}
            >
              {save.isPending ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ToggleChip({
  checked,
  onChange,
  title,
  description,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  title: string;
  description: string;
}) {
  return (
    <label className={`wap-chip${checked ? ' is-on' : ''}`} title={description}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      {title}
    </label>
  );
}

/**
 * plays per month, the last 12. one series, so no legend: the section names
 * it. a month with no plays keeps a stub so the year still reads as a year.
 */
function PlaysByMonth({ months }: { months: Array<{ month: string; plays: number }> }) {
  if (months.length === 0) return null;
  const peak = Math.max(1, ...months.map((entry) => entry.plays));
  const label = (key: string, style: 'short' | 'long') => {
    const [year, month] = key.split('-').map(Number);
    return new Date(year, month - 1, 1).toLocaleString(undefined, {
      month: style,
      ...(style === 'long' ? { year: 'numeric' } : {}),
    });
  };
  return (
    <figure className="wap-months" aria-label="Plays per month, the last 12 months">
      <div className="wap-months-bars">
        {months.map((entry) => {
          const tip = `${label(entry.month, 'long')}: ${entry.plays.toLocaleString()} ${
            entry.plays === 1 ? 'play' : 'plays'
          }`;
          return (
            <span key={entry.month} className="wap-months-col" data-tip={tip} aria-label={tip}>
              <span
                className={`wap-months-bar${entry.plays ? '' : ' is-empty'}`}
                style={entry.plays ? { height: `${(entry.plays / peak) * 100}%` } : undefined}
              />
            </span>
          );
        })}
      </div>
      <figcaption className="wap-months-axis">
        <span>{label(months[0].month, 'short')}</span>
        <span>{label(months[months.length - 1].month, 'short')}</span>
      </figcaption>
    </figure>
  );
}
