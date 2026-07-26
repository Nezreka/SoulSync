import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import type {
  WatchlistArtistConfig,
  WatchlistArtistConfigResponse,
  WatchlistMetadataSource,
} from '../-watchlist.types';

import {
  saveWatchlistArtistConfig,
  WATCHLIST_QUERY_KEY,
  watchlistArtistConfigQueryOptions,
  type WatchlistArtistConfigUpdate,
} from '../-watchlist.api';
import { WatchlistLinkedProviders } from './watchlist-linked-providers';

const RELEASE_TYPE_KEYS = ['include_albums', 'include_eps', 'include_singles'] as const;

const CONTENT_FILTER_KEYS = [
  'include_live',
  'include_remixes',
  'include_acoustic',
  'include_compilations',
  'include_instrumentals',
] as const;

type IncludeKey = (typeof RELEASE_TYPE_KEYS)[number] | (typeof CONTENT_FILTER_KEYS)[number];

const OPTION_COPY: Record<IncludeKey, { icon: string; title: string; description: string }> = {
  include_albums: { icon: '💿', title: 'Albums', description: 'Full-length studio albums' },
  include_eps: { icon: '🎵', title: 'EPs', description: 'Extended plays (4-6 tracks)' },
  include_singles: {
    icon: '🎶',
    title: 'Singles',
    description: 'Single tracks and 2-3 track releases',
  },
  include_live: {
    icon: '🎤',
    title: 'Include Live Versions',
    description: 'Check to include live performances and concerts',
  },
  include_remixes: {
    icon: '🎧',
    title: 'Include Remixes',
    description: 'Check to include remix versions and edits',
  },
  include_acoustic: {
    icon: '🎸',
    title: 'Include Acoustic Versions',
    description: 'Check to include acoustic and stripped versions',
  },
  include_compilations: {
    icon: '📀',
    title: 'Include Compilations',
    description: 'Check to include greatest hits and collections',
  },
  include_instrumentals: {
    icon: '🎹',
    title: 'Include Instrumentals',
    description: 'Check to include instrumental, karaoke, and backing track versions',
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
  { label: string; color: string; idKey: keyof WatchlistArtistConfigResponse }
> = {
  spotify: { label: 'Spotify', color: '#1DB954', idKey: 'spotify_artist_id' },
  deezer: { label: 'Deezer', color: '#A238FF', idKey: 'deezer_artist_id' },
  itunes: { label: 'Apple Music', color: '#FC3C44', idKey: 'itunes_artist_id' },
  discogs: { label: 'Discogs', color: '#333', idKey: 'discogs_artist_id' },
  musicbrainz: { label: 'MusicBrainz', color: '#BA478F', idKey: 'musicbrainz_artist_id' },
};

const METADATA_SOURCE_ORDER: WatchlistMetadataSource[] = [
  'spotify',
  'deezer',
  'itunes',
  'discogs',
  'musicbrainz',
];

/** Same rule as the global modal: at least one release type must be selected. */
export function artistConfigIsSavable(config: Pick<WatchlistArtistConfig, IncludeKey>): boolean {
  return RELEASE_TYPE_KEYS.some((key) => config[key]);
}

/** "1,234,567" — the vanilla hero used the app's formatNumber for followers. */
export function formatFollowers(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '0';
  return value.toLocaleString('en-US');
}

interface Props {
  profileId: number;
  artistId: string;
  globalOverrideActive: boolean;
  onClose: () => void;
}

export function WatchlistArtistConfigModal({
  profileId,
  artistId,
  globalOverrideActive,
  onClose,
}: Props) {
  const queryClient = useQueryClient();
  const configQuery = useQuery(watchlistArtistConfigQueryOptions(profileId, artistId));
  const payload = configQuery.data;

  const [draft, setDraft] = useState<WatchlistArtistConfig | null>(null);

  // Adopt the server config once, and again if a re-link refetches it. Editing
  // starts from what is stored, never from invented defaults.
  useEffect(() => {
    if (payload?.config) setDraft(payload.config);
  }, [payload?.config]);

  const save = useMutation({
    mutationFn: (update: WatchlistArtistConfigUpdate) =>
      saveWatchlistArtistConfig(artistId, update),
    onSuccess: async () => {
      window.showToast?.('Artist preferences saved successfully', 'success');
      await queryClient.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY });
      onClose();
    },
    onError: (error: Error) =>
      window.showToast?.(`Error saving preferences: ${error.message}`, 'error'),
  });

  const onSave = () => {
    if (!draft) return;
    if (!artistConfigIsSavable(draft)) {
      window.showToast?.('Please select at least one release type', 'error');
      return;
    }
    save.mutate({
      include_albums: draft.include_albums,
      include_eps: draft.include_eps,
      include_singles: draft.include_singles,
      include_live: draft.include_live,
      include_remixes: draft.include_remixes,
      include_acoustic: draft.include_acoustic,
      include_compilations: draft.include_compilations,
      include_instrumentals: draft.include_instrumentals,
      auto_download: draft.auto_download,
      quality_profile_id: draft.quality_profile_id,
      lookback_days: draft.lookback_days,
      preferred_metadata_source: draft.preferred_metadata_source,
    });
  };

  const artist = payload?.artist;
  const setFlag = (key: IncludeKey, value: boolean) =>
    setDraft((previous) => (previous ? { ...previous, [key]: value } : previous));

  const globalSource = payload?.global_metadata_source || 'deezer';
  const globalSourceLabel =
    METADATA_SOURCE_META[globalSource as WatchlistMetadataSource]?.label ?? globalSource;

  return (
    // The ids are load-bearing, not decoration: style.css keys the z-index
    // bump off #watchlist-artist-config-modal-overlay, and helper.js's
    // HELPER_CONTENT keys its contextual help off #watchlist-artist-config-modal.
    // The vanilla markup owned them until it was deleted.
    <div id="watchlist-artist-config-modal-overlay" className="modal-overlay" onClick={onClose}>
      <div
        id="watchlist-artist-config-modal"
        className="watchlist-artist-config-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Artist settings"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="watchlist-artist-config-header">
          <div className="watchlist-artist-config-hero">
            {artist?.image_url ? (
              <img
                src={artist.image_url}
                alt={artist.name}
                className="watchlist-artist-config-hero-image"
                loading="lazy"
              />
            ) : null}
            <div className="watchlist-artist-config-hero-info">
              <h2 className="watchlist-artist-config-hero-name">{artist?.name ?? ''}</h2>
              <div className="watchlist-artist-config-hero-stats">
                <div className="watchlist-artist-config-stat">
                  <span className="watchlist-artist-config-stat-value">
                    {formatFollowers(artist?.followers)}
                  </span>
                  <span className="watchlist-artist-config-stat-label">Followers</span>
                </div>
                <div className="watchlist-artist-config-stat">
                  <span className="watchlist-artist-config-stat-value">
                    {artist?.popularity ?? 0}/100
                  </span>
                  <span className="watchlist-artist-config-stat-label">Popularity</span>
                </div>
              </div>
              {artist?.genres && artist.genres.length > 0 ? (
                <div className="watchlist-artist-config-hero-genres">
                  {artist.genres.slice(0, 3).map((genre) => (
                    <span key={genre} className="watchlist-artist-config-genre-tag">
                      {genre}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
          <span
            className="watchlist-artist-config-close"
            role="button"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </span>
        </div>

        <div className="watchlist-artist-config-content">
          <div className="watchlist-artist-config-body">
            {/* Sits at the top of the body, as the vanilla code inserted it. */}
            {globalOverrideActive ? (
              <div className="global-override-notice watchlist-global-override-banner">
                <span>⚠️</span>
                <span>
                  Global override is active — these per-artist settings are currently ignored during
                  scans.
                </span>
              </div>
            ) : null}

            {configQuery.isError ? (
              <p className="config-section-subtitle">Could not load this artist&apos;s settings.</p>
            ) : null}

            {draft ? (
              <>
                <div className="config-section">
                  <h3 className="config-section-title">Auto-Download</h3>
                  <p className="config-section-subtitle">
                    When on, new releases are added to the wishlist and downloaded automatically.
                    Turn off to <strong>follow only</strong> — still see new releases in scans, but
                    pick what to download yourself.
                  </p>
                  <div className="config-options">
                    <ConfigOption
                      checked={draft.auto_download}
                      onChange={(value) =>
                        setDraft((previous) =>
                          previous ? { ...previous, auto_download: value } : previous,
                        )
                      }
                      icon="⬇️"
                      title="Auto-download new releases"
                      description="Off = follow only (discover but don't auto-add to wishlist)"
                    />
                  </div>
                </div>

                <div className="config-section">
                  <h3 className="config-section-title">Download Preferences</h3>
                  <p className="config-section-subtitle">
                    Select which types of releases to monitor for this artist
                  </p>

                  <div className="form-group" style={{ margin: '12px 0 16px' }}>
                    <label
                      htmlFor="watchlist-config-quality-profile"
                      style={{ display: 'block', marginBottom: 6, fontWeight: 600 }}
                    >
                      Quality Profile
                    </label>
                    <select
                      id="watchlist-config-quality-profile"
                      className="form-select"
                      value={
                        draft.quality_profile_id != null ? String(draft.quality_profile_id) : ''
                      }
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
                    <small style={{ display: 'block', marginTop: 6, opacity: 0.7 }}>
                      All Albums, EPs and Singles queued by this Watchlist artist use this profile.
                    </small>
                  </div>

                  <div className="config-options">
                    {RELEASE_TYPE_KEYS.map((key) => (
                      <ConfigOption
                        key={key}
                        checked={draft[key]}
                        onChange={(value) => setFlag(key, value)}
                        {...OPTION_COPY[key]}
                      />
                    ))}
                  </div>
                </div>

                <div className="config-section">
                  <h3 className="config-section-title">Content Filters</h3>
                  <p className="config-section-subtitle">
                    Check to INCLUDE, leave unchecked to EXCLUDE (default: all excluded)
                  </p>
                  <div className="config-options">
                    {CONTENT_FILTER_KEYS.map((key) => (
                      <ConfigOption
                        key={key}
                        checked={draft[key]}
                        onChange={(value) => setFlag(key, value)}
                        {...OPTION_COPY[key]}
                      />
                    ))}
                  </div>
                </div>

                <div className="config-section">
                  <h3 className="config-section-title">Scan Lookback</h3>
                  <p className="config-section-subtitle">
                    How far back to look for releases on first scan of this artist
                  </p>
                  <select
                    className="form-select"
                    aria-label="Scan lookback"
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
                </div>

                <div className="config-section">
                  <h3 className="config-section-title">Scan Source</h3>
                  <p className="config-section-subtitle">
                    Override which metadata provider is used when scanning this artist for new
                    releases
                  </p>
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
                      <span className="config-msrc-icon">🌐</span>
                      <span className="config-msrc-label">Default ({globalSourceLabel})</span>
                    </button>
                    {/* Only providers this artist is actually matched on can be
                        chosen as its scan source. */}
                    {METADATA_SOURCE_ORDER.filter((key) =>
                      Boolean(payload?.[METADATA_SOURCE_META[key].idKey]),
                    ).map((key) => {
                      const meta = METADATA_SOURCE_META[key];
                      const isActive = draft.preferred_metadata_source === key;
                      return (
                        <button
                          key={key}
                          type="button"
                          className={`config-msrc-btn${isActive ? ' active' : ''}`}
                          style={isActive ? { borderColor: meta.color } : undefined}
                          title={meta.label}
                          onClick={() =>
                            setDraft((previous) =>
                              previous ? { ...previous, preferred_metadata_source: key } : previous,
                            )
                          }
                        >
                          <span className="config-msrc-label">{meta.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {payload ? (
                  <WatchlistLinkedProviders
                    profileId={profileId}
                    artistId={artistId}
                    payload={payload}
                  />
                ) : null}
              </>
            ) : null}
          </div>

          <div className="watchlist-artist-config-footer">
            <div className="config-modal-actions">
              <button className="btn btn--secondary" type="button" onClick={onClose}>
                Cancel
              </button>
              <button
                className="btn btn--primary"
                type="button"
                disabled={!draft || save.isPending}
                onClick={onSave}
              >
                {save.isPending ? 'Saving...' : 'Save Preferences'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConfigOption({
  checked,
  onChange,
  icon,
  title,
  description,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  icon: string;
  title: string;
  description: string;
}) {
  return (
    <label className="config-option">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <div className="config-option-content">
        <div className="config-option-icon">{icon}</div>
        <div className="config-option-text">
          <span className="config-option-title">{title}</span>
          <span className="config-option-description">{description}</span>
        </div>
      </div>
    </label>
  );
}
