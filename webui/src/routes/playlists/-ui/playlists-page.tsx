import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { PersonalizedPlaylist, PlaylistKind, PlaylistTrack } from '../-playlists.types';

import {
  activatePlaylist,
  deletePlaylist,
  invalidatePlaylistsQueries,
  kindsQueryOptions,
  playlistDetailQueryOptions,
  playlistsQueryOptions,
  refreshPlaylist,
  updatePlaylistConfig,
  updateRefreshInterval,
} from '../-playlists.api';
import styles from './playlists-page.module.css';

const REFRESH_OPTIONS = [
  { value: 6, label: 'Every 6 hours' },
  { value: 12, label: 'Every 12 hours' },
  { value: 24, label: 'Every day' },
  { value: 48, label: 'Every 2 days' },
  { value: 168, label: 'Every week' },
];

export function PlaylistsPage() {
  useReactPageShell('playlists');

  const kindsQuery = useQuery(kindsQueryOptions());
  const playlistsQuery = useQuery(playlistsQueryOptions());

  const kinds = kindsQuery.data?.kinds ?? [];
  const playlists = playlistsQuery.data?.playlists ?? [];

  const activeKinds = useMemo(() => new Set(playlists.map((pl) => pl.kind)), [playlists]);

  const libraryKinds = useMemo(
    () => kinds.filter((k) => k.tags.includes('library') && !activeKinds.has(k.kind)),
    [kinds, activeKinds],
  );
  const discoveryKinds = useMemo(
    () => kinds.filter((k) => k.tags.includes('discovery') && !activeKinds.has(k.kind)),
    [kinds, activeKinds],
  );
  const otherKinds = useMemo(
    () =>
      kinds.filter(
        (k) =>
          !k.tags.includes('library') && !k.tags.includes('discovery') && !activeKinds.has(k.kind),
      ),
    [kinds, activeKinds],
  );

  const sortedPlaylists = useMemo(() => {
    return [...playlists].sort((a, b) => {
      const aActive = a.auto_refresh ? 1 : 0;
      const bActive = b.auto_refresh ? 1 : 0;
      if (aActive !== bActive) return bActive - aActive;
      const aTime = a.last_generated_at ? new Date(a.last_generated_at).getTime() : 0;
      const bTime = b.last_generated_at ? new Date(b.last_generated_at).getTime() : 0;
      return bTime - aTime;
    });
  }, [playlists]);

  const isLoading = kindsQuery.isLoading || playlistsQuery.isLoading;
  const isError = kindsQuery.isError || playlistsQuery.isError;
  const errorMessage =
    kindsQuery.error?.message || playlistsQuery.error?.message || 'Failed to load playlists';

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <div className={styles.headerTitle}>
          <h1 className={styles.title}>Auto-Playlists</h1>
          <p className={styles.subtitle}>
            Auto-generated playlists from your library and discovery pool. Activate a kind to start
            receiving fresh tracks on a schedule.
          </p>
        </div>
      </div>

      <div className={styles.content}>
        {isLoading && (
          <div className={styles.loadingState}>
            <p className={styles.loadingText}>Loading playlists...</p>
          </div>
        )}

        {isError && (
          <div className={styles.errorState}>
            <p className={styles.errorText}>{errorMessage}</p>
          </div>
        )}

        {!isLoading && !isError && (
          <>
            {sortedPlaylists.length > 0 && (
              <section className={styles.section}>
                <h2 className={styles.sectionTitle}>Active Auto-Playlists</h2>
                <div className={styles.grid}>
                  {sortedPlaylists.map((pl) => (
                    <PlaylistCard
                      key={`${pl.kind}-${pl.variant}`}
                      playlist={pl}
                    />
                  ))}
                </div>
              </section>
            )}

            {(libraryKinds.length > 0 || discoveryKinds.length > 0 || otherKinds.length > 0) && (
              <section className={styles.section}>
                <h2 className={styles.sectionTitle}>Create New</h2>
                {libraryKinds.length > 0 && (
                  <KindGroup title="From Your Library" kinds={libraryKinds} />
                )}
                {discoveryKinds.length > 0 && (
                  <KindGroup title="Discovery" kinds={discoveryKinds} />
                )}
                {otherKinds.length > 0 && <KindGroup title="Other" kinds={otherKinds} />}
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function KindGroup({ title, kinds }: { title: string; kinds: PlaylistKind[] }) {
  if (kinds.length === 0) return null;
  return (
    <div className={styles.kindGroup}>
      <h3 className={styles.kindGroupTitle}>{title}</h3>
      <div className={styles.grid}>
        {kinds.map((kind) => (
          <KindCard key={kind.kind} kind={kind} />
        ))}
      </div>
    </div>
  );
}

function KindCard({ kind }: { kind: PlaylistKind }) {
  const queryClient = useQueryClient();

  const activateMutation = useMutation({
    mutationFn: () => activatePlaylist(kind.kind, '', 24),
    onSuccess: () => {
      void invalidatePlaylistsQueries(queryClient);
      window.showToast?.('Playlist activated', 'success');
    },
    onError: (err: Error) => {
      window.showToast?.(err.message || 'Failed to activate playlist', 'error');
    },
  });

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div className={styles.cardInfo}>
          <h4 className={styles.cardTitle}>{kind.name_template.replace('{variant}', '').trim()}</h4>
          <p className={styles.cardDesc}>{kind.description}</p>
          <div className={styles.cardTags}>
            {kind.tags.map((tag) => (
              <span key={tag} className={styles.tag}>
                {tag}
              </span>
            ))}
          </div>
        </div>
        <div className={styles.cardActions}>
          <button
            className={`${styles.btn} ${styles.btnPrimary}`}
            onClick={() => activateMutation.mutate()}
            disabled={activateMutation.isPending}
            aria-label={`Activate ${kind.name_template.replace('{variant}', '').trim()}`}
          >
            {activateMutation.isPending ? 'Activating...' : 'Activate'}
          </button>
        </div>
      </div>
    </div>
  );
}

function PlaylistCard({
  playlist,
}: {
  playlist: PersonalizedPlaylist;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameValue, setNameValue] = useState(playlist.name);

  const detailQuery = useQuery({
    ...playlistDetailQueryOptions(playlist.kind, playlist.variant),
    enabled: expanded,
  });

  const tracks = detailQuery.data?.tracks ?? [];

  const refreshMutation = useMutation({
    mutationFn: () => refreshPlaylist(playlist.kind, playlist.variant),
    onSuccess: () => {
      void invalidatePlaylistsQueries(queryClient);
      window.showToast?.('Playlist refreshed', 'success');
    },
    onError: (err: Error) => {
      window.showToast?.(err.message || 'Failed to refresh playlist', 'error');
    },
  });

  const deactivateMutation = useMutation({
    mutationFn: () => deletePlaylist(playlist.kind, playlist.variant),
    onSuccess: () => {
      void invalidatePlaylistsQueries(queryClient);
      window.showToast?.('Playlist deactivated', 'success');
    },
    onError: (err: Error) => {
      window.showToast?.(err.message || 'Failed to deactivate playlist', 'error');
    },
  });

  const updateNameMutation = useMutation({
    mutationFn: async (newName: string) => {
      return updatePlaylistConfig(playlist.kind, playlist.variant, {
        extra: { ...playlist.config.extra, name: newName },
      });
    },
    onSuccess: () => {
      void invalidatePlaylistsQueries(queryClient);
      setEditingName(false);
    },
    onError: (err: Error) => {
      window.showToast?.(err.message || 'Failed to rename playlist', 'error');
      setEditingName(false);
      setNameValue(playlist.name);
    },
  });

  const intervalMutation = useMutation({
    mutationFn: (hours: number) =>
      updateRefreshInterval(playlist.kind, playlist.variant, hours),
    onSuccess: () => {
      void invalidatePlaylistsQueries(queryClient);
    },
    onError: (err: Error) => {
      window.showToast?.(err.message || 'Failed to update interval', 'error');
    },
  });

  const updateConfigMutation = useMutation({
    mutationFn: async (overrides: Record<string, unknown>) => {
      return updatePlaylistConfig(playlist.kind, playlist.variant, overrides);
    },
    onSuccess: () => {
      void invalidatePlaylistsQueries(queryClient);
    },
    onError: (err: Error) => {
      window.showToast?.(err.message || 'Failed to update config', 'error');
    },
  });

  useEffect(() => {
    setNameValue(playlist.name);
  }, [playlist.name]);

  const lastGenerated = playlist.last_generated_at
    ? new Date(playlist.last_generated_at).toLocaleString()
    : 'Never';

  const handleHeaderKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setExpanded(!expanded);
    }
  };

  return (
    <div className={`${styles.card} ${playlist.is_stale ? styles.cardStale : ''}`}>
      <div
        className={styles.cardHeader}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={handleHeaderKeyDown}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-label={`${playlist.name} playlist details`}
      >
        <div className={styles.cardInfo}>
          <div className={styles.cardNameRow}>
            {editingName ? (
              <form
                className={styles.nameForm}
                onSubmit={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  if (nameValue.trim()) {
                    updateNameMutation.mutate(nameValue.trim());
                  }
                }}
              >
                <input
                  className={styles.nameInput}
                  value={nameValue}
                  onChange={(e) => setNameValue(e.target.value)}
                  onBlur={() => {
                    if (nameValue.trim() && nameValue !== playlist.name) {
                      updateNameMutation.mutate(nameValue.trim());
                    } else {
                      setEditingName(false);
                      setNameValue(playlist.name);
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                      setEditingName(false);
                      setNameValue(playlist.name);
                    }
                  }}
                  autoFocus
                  onClick={(e) => e.stopPropagation()}
                  aria-label="Playlist name"
                />
              </form>
            ) : (
              <h4
                className={styles.cardTitle}
                onClick={(e) => {
                  e.stopPropagation();
                  setEditingName(true);
                }}
                title="Click to rename"
              >
                {playlist.name}
              </h4>
            )}
            {playlist.is_stale && <span className={styles.staleBadge}>Stale</span>}
            {playlist.auto_refresh && <span className={styles.autoRefreshBadge}>Auto</span>}
          </div>
          <p className={styles.cardMeta}>
            {playlist.track_count} tracks · Last generated: {lastGenerated}
          </p>
          {playlist.last_generation_error && (
            <p className={styles.cardError}>{playlist.last_generation_error}</p>
          )}
        </div>
        <div className={styles.cardActions}>
          <button
            className={`${styles.btn} ${styles.btnDanger}`}
            onClick={(e) => {
              e.stopPropagation();
              if (window.confirm(`Deactivate "${playlist.name}"? This will remove the playlist.`)) {
                deactivateMutation.mutate();
              }
            }}
            disabled={deactivateMutation.isPending}
            aria-label={`Deactivate ${playlist.name}`}
          >
            {deactivateMutation.isPending ? 'Deactivating...' : 'Deactivate'}
          </button>
          <button
            className={styles.btn}
            onClick={(e) => {
              e.stopPropagation();
              refreshMutation.mutate();
            }}
            disabled={refreshMutation.isPending}
            aria-label={`Refresh ${playlist.name}`}
          >
            {refreshMutation.isPending ? 'Refreshing...' : 'Refresh'}
          </button>
          {playlist.auto_refresh && (
            <select
              className={styles.intervalSelect}
              value={playlist.refresh_interval_hours}
              onChange={(e) => {
                e.stopPropagation();
                intervalMutation.mutate(Number(e.target.value));
              }}
              onClick={(e) => e.stopPropagation()}
              aria-label={`Refresh interval for ${playlist.name}`}
            >
              {REFRESH_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {expanded && (
        <div className={styles.cardBody}>
          <div className={styles.configSection}>
            <h5 className={styles.configSectionTitle}>Configuration</h5>
            <div className={styles.configGrid}>
              <ConfigField
                label="Track Limit"
                value={playlist.config.limit}
                onChange={(v) => updateConfigMutation.mutate({ limit: Number(v) || 50 })}
                type="number"
                min={1}
                max={2000}
              />
              <ConfigField
                label="Max Days Since Added"
                value={(playlist.config.extra?.max_days_since_added as string | number) ?? ''}
                onChange={(v) =>
                  updateConfigMutation.mutate({
                    extra: {
                      ...playlist.config.extra,
                      max_days_since_added: v ? Number(v) : null,
                    },
                  })
                }
                type="number"
                min={1}
                max={3650}
                placeholder="All time"
              />
            </div>
          </div>

          <div className={styles.trackSection}>
            <h5 className={styles.configSectionTitle}>Tracks ({tracks.length})</h5>
            {detailQuery.isLoading && <p className={styles.loadingText}>Loading tracks...</p>}
            {detailQuery.isError && <p className={styles.errorText}>Failed to load tracks</p>}
            {tracks.length > 0 && (
              <div className={styles.trackList}>
                {tracks.slice(0, 100).map((track, i) => (
                  <TrackRow key={i} track={track} position={i + 1} />
                ))}
                {tracks.length > 100 && (
                  <p className={styles.trackOverflow}>...and {tracks.length - 100} more</p>
                )}
              </div>
            )}
            {!detailQuery.isLoading && !detailQuery.isError && tracks.length === 0 && (
              <p className={styles.emptyText}>No tracks yet. Click Refresh to generate.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ConfigField({
  label,
  value,
  onChange,
  type,
  min,
  max,
  placeholder,
}: {
  label: string;
  value: string | number;
  onChange: (value: string) => void;
  type: 'text' | 'number';
  min?: number;
  max?: number;
  placeholder?: string;
}) {
  return (
    <label className={styles.configField}>
      <span className={styles.configLabel}>{label}</span>
      <input
        className={styles.configInput}
        type={type}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        min={min}
        max={max}
        placeholder={placeholder}
      />
    </label>
  );
}

function TrackRow({ track, position }: { track: PlaylistTrack; position: number }) {
  const duration = track.duration_ms
    ? `${Math.floor(track.duration_ms / 60000)}:${String(Math.floor((track.duration_ms % 60000) / 1000)).padStart(2, '0')}`
    : '';

  return (
    <div className={styles.trackRow}>
      <span className={styles.trackPos}>{position}</span>
      <div className={styles.trackInfo}>
        <span className={styles.trackName}>{track.track_name}</span>
        <span className={styles.trackArtist}>{track.artist_name}</span>
      </div>
      {track.album_name && <span className={styles.trackAlbum}>{track.album_name}</span>}
      {duration && <span className={styles.trackDuration}>{duration}</span>}
    </div>
  );
}
