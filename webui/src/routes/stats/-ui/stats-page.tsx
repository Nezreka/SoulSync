import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { type ReactNode, useEffect, useRef, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { ShellBridge } from '@/platform/shell/bridge';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type {
  StatsAlbumRow,
  StatsArtistRow,
  StatsDbStoragePayload,
  StatsHealth,
  StatsLibraryDiskUsagePayload,
  StatsRange,
  StatsRecentTrack,
  StatsTrackRow,
} from '../-stats.types';

import {
  invalidateStatsQueries,
  listeningStatsStatusQueryOptions,
  resolveStatsTrack,
  statsCachedQueryOptions,
  statsDbStorageQueryOptions,
  statsLibraryDiskUsageQueryOptions,
  streamStatsTrack,
  triggerListeningStatsSync,
} from '../-stats.api';
import {
  EMPTY_STATS_OVERVIEW,
  formatBytes,
  formatCompactNumber,
  formatDbStorageValue,
  formatListeningTime,
  formatRelativePlayedAt,
  formatTotalDuration,
  getStatsRangeLabel,
  getTopArtistBubbles,
  groupDbStorageTables,
  hasStatsData,
  STATS_DB_STORAGE_COLORS,
  STATS_ENRICHMENT_SERVICES,
  STATS_GENRE_COLORS,
} from '../-stats.helpers';
import { Route } from '../route';
import styles from './stats-page.module.css';

const STATS_TOOLTIP_STYLE = {
  background: 'rgba(12, 12, 12, 0.96)',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: 10,
  color: '#fff',
} as const;

const STATS_TOOLTIP_WRAPPER_STYLE = {
  zIndex: 3,
} as const;

const STATS_CHART_CURSOR = {
  fill: 'rgba(var(--accent-rgb), 0.12)',
} as const;

export function StatsPage() {
  const bridge = useReactPageShell('stats');

  const navigate = useNavigate({ from: Route.fullPath });
  const queryClient = useQueryClient();
  const { range } = Route.useSearch();
  const syncTimeoutRef = useRef<number | null>(null);
  const [syncing, setSyncing] = useState(false);

  const cachedStatsQuery = useQuery({
    ...statsCachedQueryOptions(range),
  });
  const listeningStatusQuery = useQuery({
    ...listeningStatsStatusQueryOptions(),
  });
  const dbStorageQuery = useQuery({
    ...statsDbStorageQueryOptions(),
  });
  const diskUsageQuery = useQuery({
    ...statsLibraryDiskUsageQueryOptions(),
  });

  useEffect(() => {
    return () => {
      if (syncTimeoutRef.current) {
        window.clearTimeout(syncTimeoutRef.current);
      }
    };
  }, []);

  const syncMutation = useMutation({
    mutationFn: triggerListeningStatsSync,
    onMutate: () => {
      setSyncing(true);
    },
    onSuccess: () => {
      window.showToast?.('Syncing listening data...', 'info');
      syncTimeoutRef.current = window.setTimeout(() => {
        void invalidateStatsQueries(queryClient);
        setSyncing(false);
        window.showToast?.('Listening stats updated', 'success');
      }, 5000);
    },
    onError: (error) => {
      setSyncing(false);
      window.showToast?.(error instanceof Error ? error.message : 'Sync failed', 'error');
    },
  });

  const cachedStats = cachedStatsQuery.data;
  const overview = cachedStats?.overview ?? EMPTY_STATS_OVERVIEW;
  const hasData = hasStatsData(overview);
  const lastSynced = listeningStatusQuery.data?.stats?.last_poll ?? null;

  const onRangeChange = (nextRange: StatsRange) => {
    void navigate({
      to: Route.fullPath,
      search: { range: nextRange },
      replace: true,
    });
  };

  const openArtistDetail = (artistId: string | number, artistName: string) => {
    bridge.navigateToArtistDetail(artistId, artistName);
  };

  return (
    <div id="stats-container" className={styles.statsContainer} data-testid="stats-page">
      <header className={styles.statsHeader}>
        <div className={styles.statsHeaderTitle}>
          <img src="/static/trans2.png" alt="Stats" className={styles.headerIcon} />
          <h1 className={styles.headerTitle}>Listening Stats</h1>
        </div>
        <div className={styles.statsHeaderControls}>
          <div
            id="stats-time-range"
            className={styles.statsTimeRange}
            role="tablist"
            aria-label="Listening stats range"
          >
            {(['7d', '30d', '12m', 'all'] as const).map((option) => (
              <button
                key={option}
                type="button"
                className={`${styles.statsRangeButton} ${
                  range === option ? styles.statsRangeButtonActive : ''
                }`}
                onClick={() => onRangeChange(option)}
              >
                {getStatsRangeLabel(option)}
              </button>
            ))}
          </div>
          <div className={styles.statsSyncControls}>
            <span className={styles.statsLastSynced}>
              {lastSynced ? `Last synced: ${lastSynced}` : 'Not synced yet'}
            </span>
            <button
              id="stats-sync-btn"
              type="button"
              className={`${styles.statsSyncButton} ${syncing ? styles.statsSyncButtonSyncing : ''}`}
              onClick={() => syncMutation.mutate()}
              disabled={syncing}
              aria-label="Sync listening stats"
              title="Sync now"
            >
              <span aria-hidden="true">↻</span>
            </button>
          </div>
        </div>
      </header>

      {cachedStatsQuery.isPending ? (
        <SectionLoadingState />
      ) : cachedStatsQuery.error ? (
        <SectionErrorState message={getErrorMessage(cachedStatsQuery.error)} />
      ) : hasData ? (
        <>
          <OverviewCards overview={overview} />
          <div className={styles.statsMainGrid}>
            <div className={styles.statsLeftCol}>
              <StatsSectionCard title="Listening Activity">
                <div id="stats-timeline-chart" className={styles.chartContainer}>
                  <StatsActivityChart timeline={cachedStats?.timeline ?? []} />
                </div>
              </StatsSectionCard>
              <StatsSectionCard title="Genre Breakdown">
                <div className={styles.statsGenreChartContainer}>
                  <div id="stats-genre-chart" className={styles.statsGenreChartWrap}>
                    <StatsGenreChart genres={cachedStats?.genres ?? []} />
                  </div>
                  <StatsGenreLegend genres={cachedStats?.genres ?? []} />
                </div>
              </StatsSectionCard>
              <StatsSectionCard title="Recently Played">
                <StatsRecentPlays
                  tracks={cachedStats?.recent ?? []}
                  onPlay={(track) => playStatsTrack(bridge, track)}
                />
              </StatsSectionCard>
            </div>
            <div className={styles.statsRightCol}>
              <StatsSectionCard title="Top Artists">
                <TopArtistsVisual
                  artists={cachedStats?.top_artists ?? []}
                  onArtistSelect={(artistId, artistName) => openArtistDetail(artistId, artistName)}
                />
                <StatsRankedArtists
                  artists={cachedStats?.top_artists ?? []}
                  onArtistSelect={(artistId, artistName) => openArtistDetail(artistId, artistName)}
                />
              </StatsSectionCard>
              <StatsSectionCard title="Top Albums">
                <StatsRankedAlbums
                  albums={cachedStats?.top_albums ?? []}
                  onArtistSelect={(artistId, artistName) => openArtistDetail(artistId, artistName)}
                />
              </StatsSectionCard>
              <StatsSectionCard title="Top Tracks">
                <StatsRankedTracks
                  tracks={cachedStats?.top_tracks ?? []}
                  onArtistSelect={(artistId, artistName) => openArtistDetail(artistId, artistName)}
                  onPlay={(track) => playStatsTrack(bridge, track)}
                />
              </StatsSectionCard>
            </div>
          </div>

          <StatsSectionCard title="Library Health" fullWidth>
            <StatsLibraryHealth health={cachedStats?.health ?? {}} />
          </StatsSectionCard>

          <StatsSectionCard title="Library Disk Usage" fullWidth>
            <StatsDiskUsage payload={diskUsageQuery.data} error={diskUsageQuery.error} />
          </StatsSectionCard>

          <StatsSectionCard title="Database Storage" fullWidth>
            <StatsDbStorage payload={dbStorageQuery.data} error={dbStorageQuery.error} />
          </StatsSectionCard>
        </>
      ) : (
        <StatsEmptyState />
      )}
    </div>
  );
}

function OverviewCards({
  overview,
}: {
  overview: Partial<{
    total_plays: number;
    total_time_ms: number;
    unique_artists: number;
    unique_albums: number;
    unique_tracks: number;
  }>;
}) {
  const cards = [
    { label: 'Total Plays', value: formatCompactNumber(overview.total_plays) },
    { label: 'Listening Time', value: formatListeningTime(overview.total_time_ms) },
    { label: 'Artists', value: formatCompactNumber(overview.unique_artists) },
    { label: 'Albums', value: formatCompactNumber(overview.unique_albums) },
    { label: 'Tracks', value: formatCompactNumber(overview.unique_tracks) },
  ];

  return (
    <div id="stats-overview" className={styles.statsOverview}>
      {cards.map((card) => (
        <div key={card.label} className={styles.statsCard}>
          <div className={styles.statsCardValue}>{card.value}</div>
          <div className={styles.statsCardLabel}>{card.label}</div>
        </div>
      ))}
    </div>
  );
}

function StatsSectionCard({
  children,
  fullWidth = false,
  title,
}: {
  children: ReactNode;
  fullWidth?: boolean;
  title: string;
}) {
  return (
    <section className={`${styles.statsSectionCard} ${fullWidth ? styles.statsFullWidth : ''}`}>
      <div className={styles.statsSectionTitle}>{title}</div>
      {children}
    </section>
  );
}

function StatsActivityChart({ timeline }: { timeline: Array<{ date: string; plays: number }> }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={timeline} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.04)" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          allowDecimals={false}
          width={28}
        />
        <Tooltip
          contentStyle={STATS_TOOLTIP_STYLE}
          wrapperStyle={STATS_TOOLTIP_WRAPPER_STYLE}
          cursor={STATS_CHART_CURSOR}
        />
        <Bar
          dataKey="plays"
          radius={[4, 4, 0, 0]}
          fill="rgba(var(--accent-rgb), 0.55)"
          stroke="rgba(var(--accent-rgb), 0.8)"
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

function StatsGenreChart({
  genres,
}: {
  genres: Array<{ genre: string; play_count: number; percentage: number }>;
}) {
  const topGenres = genres.slice(0, 10).map((genre, index) => ({
    ...genre,
    fill: STATS_GENRE_COLORS[index % STATS_GENRE_COLORS.length],
  }));
  return (
    <ResponsiveContainer width={180} height={180}>
      <PieChart>
        <Pie
          data={topGenres}
          dataKey="play_count"
          nameKey="genre"
          innerRadius={52}
          outerRadius={84}
          paddingAngle={2}
          stroke="transparent"
        />
        <Tooltip contentStyle={STATS_TOOLTIP_STYLE} wrapperStyle={STATS_TOOLTIP_WRAPPER_STYLE} />
      </PieChart>
    </ResponsiveContainer>
  );
}

function StatsGenreLegend({
  genres,
}: {
  genres: Array<{ genre: string; play_count: number; percentage: number }>;
}) {
  const topGenres = genres.slice(0, 10);

  return (
    <div className={styles.statsGenreLegend}>
      {topGenres.map((genre, index) => (
        <div key={genre.genre} className={styles.statsGenreLegendItem}>
          <span
            className={styles.statsGenreDot}
            style={{ background: STATS_GENRE_COLORS[index % STATS_GENRE_COLORS.length] }}
          />
          <span>{genre.genre}</span>
          <span className={styles.statsGenrePct}>{genre.percentage}%</span>
        </div>
      ))}
    </div>
  );
}

function TopArtistsVisual({
  artists,
  onArtistSelect,
}: {
  artists: StatsArtistRow[];
  onArtistSelect: (artistId: string | number, artistName: string) => void;
}) {
  const topArtists = getTopArtistBubbles(artists);
  if (topArtists.length === 0) return null;

  return (
    <div className={styles.statsTopArtistsVisual}>
      <div className={styles.statsArtistBubbles}>
        {topArtists.map(({ artist, percent, size }) => {
          const isClickable = artist.id !== null && artist.id !== undefined;
          return (
            <button
              key={`${artist.name}-${artist.id ?? 'unknown'}`}
              type="button"
              className={styles.statsArtistBubble}
              onClick={() => {
                if (isClickable) {
                  onArtistSelect(artist.id as string | number, artist.name);
                }
              }}
              disabled={!isClickable}
            >
              <div
                className={styles.statsBubbleImage}
                style={{
                  width: size,
                  height: size,
                  backgroundImage: artist.image_url ? `url(${artist.image_url})` : undefined,
                }}
              >
                {!artist.image_url ? (
                  <span className={styles.statsBubbleInitial}>{artist.name[0] ?? '?'}</span>
                ) : null}
              </div>
              <div className={styles.statsBubbleBarContainer}>
                <div className={styles.statsBubbleBar} style={{ width: `${percent}%` }} />
              </div>
              <div className={styles.statsBubbleName}>{artist.name}</div>
              <div className={styles.statsBubbleCount}>
                {formatCompactNumber(artist.play_count)}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function StatsRankedArtists({
  artists,
  onArtistSelect,
}: {
  artists: StatsArtistRow[];
  onArtistSelect: (artistId: string | number, artistName: string) => void;
}) {
  return (
    <div id="stats-top-artists" className={styles.statsRankedList}>
      {artists.length === 0 ? <EmptyListState message="No data yet" /> : null}
      {artists.map((artist, index) => (
        <div key={`${artist.name}-${artist.id ?? index}`} className={styles.statsRankedItem}>
          <span className={styles.statsRankedNum}>{index + 1}</span>
          {artist.image_url ? (
            <img className={styles.statsRankedImage} src={artist.image_url} alt="" />
          ) : (
            <div className={styles.statsRankedImageFallback} />
          )}
          <div className={styles.statsRankedInfo}>
            <div className={styles.statsRankedName}>
              {artist.id ? (
                <button
                  type="button"
                  className={styles.statsArtistLink}
                  onClick={() => onArtistSelect(artist.id as string | number, artist.name)}
                >
                  {artist.name}
                </button>
              ) : (
                artist.name
              )}
              {artist.soul_id && !String(artist.soul_id).startsWith('soul_unnamed_') ? (
                <img src="/static/trans2.png" className={styles.statsSoulIdBadge} alt="SoulID" />
              ) : null}
            </div>
            <div className={styles.statsRankedMeta}>
              {artist.global_listeners
                ? `${formatCompactNumber(artist.global_listeners)} global listeners`
                : ''}
            </div>
          </div>
          <span className={styles.statsRankedCount}>
            {formatCompactNumber(artist.play_count)} plays
          </span>
        </div>
      ))}
    </div>
  );
}

function StatsRankedAlbums({
  albums,
  onArtistSelect,
}: {
  albums: StatsAlbumRow[];
  onArtistSelect: (artistId: string | number, artistName: string) => void;
}) {
  return (
    <div id="stats-top-albums" className={styles.statsRankedList}>
      {albums.length === 0 ? <EmptyListState message="No data yet" /> : null}
      {albums.map((album, index) => (
        <div key={`${album.name}-${index}`} className={styles.statsRankedItem}>
          <span className={styles.statsRankedNum}>{index + 1}</span>
          {album.image_url ? (
            <img className={styles.statsRankedImage} src={album.image_url} alt="" />
          ) : (
            <div className={styles.statsRankedImageFallback} />
          )}
          <div className={styles.statsRankedInfo}>
            <div className={styles.statsRankedName}>{album.name}</div>
            <div className={styles.statsRankedMeta}>
              {album.artist_id ? (
                <button
                  type="button"
                  className={styles.statsArtistLink}
                  onClick={() =>
                    onArtistSelect(album.artist_id as string | number, album.artist || '')
                  }
                >
                  {album.artist || ''}
                </button>
              ) : (
                album.artist || ''
              )}
            </div>
          </div>
          <span className={styles.statsRankedCount}>
            {formatCompactNumber(album.play_count)} plays
          </span>
        </div>
      ))}
    </div>
  );
}

function StatsRankedTracks({
  tracks,
  onArtistSelect,
  onPlay,
}: {
  tracks: StatsTrackRow[];
  onArtistSelect: (artistId: string | number, artistName: string) => void;
  onPlay: (track: { title: string; artist: string; album: string }) => Promise<void>;
}) {
  return (
    <div id="stats-top-tracks" className={styles.statsRankedList}>
      {tracks.length === 0 ? <EmptyListState message="No data yet" /> : null}
      {tracks.map((track, index) => (
        <div key={`${track.name}-${index}`} className={styles.statsRankedItem}>
          <span className={styles.statsRankedNum}>{index + 1}</span>
          {track.image_url ? (
            <img className={styles.statsRankedImage} src={track.image_url} alt="" />
          ) : (
            <div className={styles.statsRankedImageFallback} />
          )}
          <div className={styles.statsRankedInfo}>
            <div className={styles.statsRankedName}>{track.name}</div>
            <div className={styles.statsRankedMeta}>
              {track.artist_id ? (
                <button
                  type="button"
                  className={styles.statsArtistLink}
                  onClick={() =>
                    onArtistSelect(track.artist_id as string | number, track.artist || '')
                  }
                >
                  {track.artist || ''}
                </button>
              ) : (
                track.artist || ''
              )}
              {track.album ? ` · ${track.album}` : ''}
            </div>
          </div>
          <button
            type="button"
            className={`${styles.statsPlayButton} ${styles.statsPlayButtonSmall}`}
            onClick={() =>
              void onPlay({
                title: track.name,
                artist: track.artist || '',
                album: track.album || '',
              })
            }
            title="Play"
          >
            ▶
          </button>
          <span className={styles.statsRankedCount}>
            {formatCompactNumber(track.play_count)} plays
          </span>
        </div>
      ))}
    </div>
  );
}

function StatsRecentPlays({
  tracks,
  onPlay,
}: {
  tracks: StatsRecentTrack[];
  onPlay: (track: { title: string; artist: string; album: string }) => Promise<void>;
}) {
  return (
    <div id="stats-recent-plays" className={styles.statsRecentList}>
      {tracks.length === 0 ? <EmptyListState message="No recent plays" /> : null}
      {tracks.map((track, index) => (
        <div
          key={`${track.title}-${track.artist ?? ''}-${track.album ?? ''}-${track.played_at ?? ''}-${index}`}
          className={styles.statsRecentItem}
        >
          <button
            type="button"
            className={`${styles.statsPlayButton} ${styles.statsPlayButtonSmall}`}
            onClick={() =>
              void onPlay({
                title: track.title,
                artist: track.artist || '',
                album: track.album || '',
              })
            }
            title="Play"
          >
            ▶
          </button>
          <span className={styles.statsRecentTitle}>{track.title}</span>
          <span className={styles.statsRecentArtist}>{track.artist || ''}</span>
          <span className={styles.statsRecentTime}>{formatRelativePlayedAt(track.played_at)}</span>
        </div>
      ))}
    </div>
  );
}

function StatsLibraryHealth({ health }: { health: StatsHealth }) {
  const totalTracks = health.total_tracks ?? 0;
  const formatEntries = Object.entries(health.format_breakdown ?? {});
  const formatTotal = formatEntries.reduce((sum, [, count]) => sum + count, 0) || 1;
  const formatColors: Record<string, string> = {
    FLAC: '#3b82f6',
    MP3: '#f97316',
    Opus: '#a855f7',
    AAC: '#14b8a6',
    OGG: '#eab308',
    WAV: '#ec4899',
    Other: '#555555',
  };

  return (
    <div id="stats-library-health">
      <div className={styles.statsHealthGrid}>
        <div className={`${styles.statsHealthItem} ${styles.statsHealthItemWide}`}>
          <div className={styles.statsHealthLabel}>Format Breakdown</div>
          <div className={styles.statsFormatBar}>
            {formatEntries.map(([format, count]) => {
              const percentage = ((count / formatTotal) * 100).toFixed(1);
              return (
                <div
                  key={format}
                  className={styles.statsFormatSegment}
                  style={{
                    flex: count,
                    background: formatColors[format] || formatColors.Other,
                  }}
                  title={`${format}: ${count} tracks (${percentage}%)`}
                >
                  {Number(percentage) > 8 ? format : ''}
                </div>
              );
            })}
          </div>
        </div>
        <div className={styles.statsHealthItem}>
          <div className={styles.statsHealthValue}>
            {formatCompactNumber(health.unplayed_count)} ({health.unplayed_percentage || 0}%)
          </div>
          <div className={styles.statsHealthLabel}>Unplayed Tracks</div>
        </div>
        <div className={styles.statsHealthItem}>
          <div className={styles.statsHealthValue}>
            {formatTotalDuration(health.total_duration_ms)}
          </div>
          <div className={styles.statsHealthLabel}>Total Duration</div>
        </div>
        <div className={styles.statsHealthItem}>
          <div className={styles.statsHealthValue}>{formatCompactNumber(totalTracks)}</div>
          <div className={styles.statsHealthLabel}>Total Tracks</div>
        </div>
      </div>
      <div id="stats-enrichment-coverage" className={styles.statsEnrichment}>
        {STATS_ENRICHMENT_SERVICES.map((service) => {
          const percent = health.enrichment_coverage?.[service.key] || 0;
          return (
            <div key={service.key} className={styles.statsEnrichItem}>
              <span className={styles.statsEnrichName}>{service.label}</span>
              <div className={styles.statsEnrichBar}>
                <div
                  className={styles.statsEnrichFill}
                  style={{ width: `${percent}%`, background: service.color }}
                />
              </div>
              <span className={styles.statsEnrichPct}>{percent}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatsDiskUsage({
  error,
  payload,
}: {
  error: unknown;
  payload: StatsLibraryDiskUsagePayload | undefined;
}) {
  if (error) {
    return <SectionSubtleError message={getErrorMessage(error)} />;
  }

  const hasData = payload?.has_data && !!payload.total_bytes;
  const formats = Object.entries(payload?.by_format ?? {}).sort((a, b) => b[1] - a[1]);
  const max = formats[0]?.[1] || 1;
  const tracksWithSize = payload?.tracks_with_size || 0;
  const tracksWithoutSize = payload?.tracks_without_size || 0;

  return (
    <div className={styles.statsDiskUsageWrap}>
      <div className={styles.statsDiskTotalRow}>
        <div className={styles.statsDiskTotalValue}>
          {hasData ? formatBytes(payload?.total_bytes) : '—'}
        </div>
        <div className={styles.statsDiskTotalMeta}>
          {hasData
            ? `${tracksWithSize.toLocaleString()} tracks measured${
                tracksWithoutSize > 0
                  ? ` (+${tracksWithoutSize.toLocaleString()} pending next Deep Scan)`
                  : ''
              }`
            : tracksWithoutSize > 0
              ? `Run a Deep Scan to populate (${tracksWithoutSize.toLocaleString()} tracks pending)`
              : 'No tracks in library yet'}
        </div>
      </div>
      <div className={styles.statsDiskFormats}>
        {formats.map(([format, bytes]) => {
          const width = Math.max(2, Math.round((bytes / max) * 100));
          return (
            <div key={format} className={styles.statsDiskFormatRow}>
              <span className={styles.statsDiskFormatName}>{format.toUpperCase()}</span>
              <div className={styles.statsDiskFormatBar}>
                <div className={styles.statsDiskFormatFill} style={{ width: `${width}%` }} />
              </div>
              <span className={styles.statsDiskFormatSize}>{formatBytes(bytes)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatsDbStorage({
  error,
  payload,
}: {
  error: unknown;
  payload: StatsDbStoragePayload | undefined;
}) {
  if (error) {
    return <SectionSubtleError message={getErrorMessage(error)} />;
  }

  const tables = groupDbStorageTables(payload?.tables ?? []).map((table, index) => ({
    ...table,
    fill: STATS_DB_STORAGE_COLORS[index % STATS_DB_STORAGE_COLORS.length],
  }));
  const method = payload?.method;

  return (
    <div className={styles.statsDbStorageWrap}>
      <div id="stats-db-storage-chart" className={styles.statsDbChartContainer}>
        <ResponsiveContainer width={180} height={180}>
          <PieChart>
            <Pie
              data={tables}
              dataKey="size"
              nameKey="name"
              innerRadius={52}
              outerRadius={84}
              paddingAngle={2}
              stroke="transparent"
            />
            <Tooltip
              contentStyle={STATS_TOOLTIP_STYLE}
              wrapperStyle={STATS_TOOLTIP_WRAPPER_STYLE}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className={styles.statsDbTotal}>
          <div className={styles.statsDbTotalValue}>
            {formatDbStorageValue(payload?.total_file_size || 0, method)}
          </div>
          <div className={styles.statsDbTotalLabel}>Total Size</div>
        </div>
      </div>
      <div className={styles.statsDbLegend}>
        {tables.map((table, index) => (
          <div key={table.name} className={styles.statsDbLegendItem}>
            <span
              className={styles.statsDbLegendDot}
              style={{
                background: STATS_DB_STORAGE_COLORS[index % STATS_DB_STORAGE_COLORS.length],
              }}
            />
            <span className={styles.statsDbLegendName}>{table.name}</span>
            <span className={styles.statsDbLegendSize}>
              {formatDbStorageValue(table.size, method)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatsEmptyState() {
  return (
    <div className={styles.statsEmpty}>
      <div className={styles.statsEmptyIcon}>📊</div>
      <h3>No Listening Data Yet</h3>
      <p>
        Enable &quot;Listening Stats&quot; in Settings to start tracking your listening activity
        from your media server.
      </p>
    </div>
  );
}

function SectionLoadingState() {
  return <div className={styles.statsLoading}>Loading listening stats...</div>;
}

function SectionErrorState({ message }: { message: string }) {
  return (
    <div className={styles.statsEmpty}>
      <h3>Failed to load listening stats</h3>
      <p>{message}</p>
    </div>
  );
}

function SectionSubtleError({ message }: { message: string }) {
  return <div className={styles.statsSubtleError}>{message}</div>;
}

function EmptyListState({ message }: { message: string }) {
  return <div className={styles.emptyListState}>{message}</div>;
}

async function playStatsTrack(
  bridge: ShellBridge,
  track: { title: string; artist: string; album: string },
) {
  const resolvedTrack = await resolveStatsTrack(track.title, track.artist);
  if (resolvedTrack) {
    await bridge.playLibraryTrack(
      {
        id: resolvedTrack.id,
        title: resolvedTrack.title,
        file_path: resolvedTrack.file_path,
        bitrate: resolvedTrack.bitrate,
        artist_id: resolvedTrack.artist_id,
        album_id: resolvedTrack.album_id,
        _stats_image: resolvedTrack.image_url || null,
      },
      resolvedTrack.album_title || track.album,
      resolvedTrack.artist_name || track.artist,
    );
    return;
  }

  bridge.showLoadingOverlay(`Searching for ${track.title}...`);
  try {
    const streamResult = await streamStatsTrack(track.title, track.artist, track.album);
    bridge.hideLoadingOverlay();

    if (streamResult) {
      await bridge.startStream(streamResult);
      return;
    }

    window.showToast?.('Track not found in library or any source', 'error');
  } catch (error) {
    bridge.hideLoadingOverlay();
    window.showToast?.(getErrorMessage(error), 'error');
  }
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error';
}
