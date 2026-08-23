import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { type ComponentPropsWithoutRef, type ReactNode, useEffect, useRef, useState } from 'react';
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

import { PageHeader } from '@/components/page-header';
import { useReactPageShell, useShellStatus } from '@/platform/shell/route-controllers';

import type {
  LastfmListeningImportStatus,
  StatsAlbumRow,
  StatsArtistRow,
  StatsClock,
  StatsDbStoragePayload,
  StatsHealth,
  StatsLibraryDiskUsagePayload,
  StatsListeningEventsFilter,
  StatsListeningEventTrack,
  StatsNeglectedAlbum,
  StatsOwnVsPlay,
  StatsRange,
  StatsRecentTrack,
  StatsRhythm,
  StatsTab,
  StatsTrackRow,
} from '../-stats.types';

import {
  fetchStatsListeningEvents,
  invalidateStatsQueries,
  lastfmListeningImportStatusQueryOptions,
  listeningStatsStatusQueryOptions,
  resolveStatsTrack,
  runLastfmListeningImport,
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
  formatHourLabel,
  formatListeningTime,
  formatPeakSlot,
  formatRelativePlayedAt,
  formatTotalDuration,
  getStatsRangeLabel,
  getTopArtistBubbles,
  groupDbStorageTables,
  hasStatsData,
  heatIntensity,
  isNewSincePrevious,
  STATS_DB_STORAGE_COLORS,
  STATS_GENRE_COLORS,
  statDelta,
  WEEKDAY_LABELS,
  visibleStatsEnrichmentServices,
} from '../-stats.helpers';
import { STATS_TAB_VALUES } from '../-stats.types';
import { Route } from '../route';
import styles from './stats-page.module.css';
import { YearStory } from './year-story';

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

const ARTIST_DETAIL_SOURCE = 'library' as const;
const STATS_DETAIL_QUERY_KEY = ['stats', 'listening-events'] as const;

export function StatsPage() {
  const bridge = useReactPageShell('stats');

  const navigate = useNavigate({ from: Route.fullPath });
  const queryClient = useQueryClient();
  const { range, tab, story } = Route.useSearch();
  const syncTimeoutRef = useRef<number | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [lastfmUsername, setLastfmUsername] = useState('');
  const [listeningDetailFilter, setListeningDetailFilter] = useState<StatsListeningEventsFilter | null>(null);

  const cachedStatsQuery = useQuery({
    ...statsCachedQueryOptions(range),
  });
  const listeningStatusQuery = useQuery({
    ...listeningStatsStatusQueryOptions(),
  });
  const lastfmImportQuery = useQuery({
    ...lastfmListeningImportStatusQueryOptions(),
    refetchInterval: (query) => (query.state.data?.running ? 3000 : 60000),
  });
  const dbStorageQuery = useQuery({
    ...statsDbStorageQueryOptions(),
  });
  const diskUsageQuery = useQuery({
    ...statsLibraryDiskUsageQueryOptions(),
  });
  const listeningDetailsQuery = useQuery({
    queryKey: [...STATS_DETAIL_QUERY_KEY, range, listeningDetailFilter],
    queryFn: () => {
      if (!listeningDetailFilter) throw new Error('No listening detail selected');
      return fetchStatsListeningEvents(range, listeningDetailFilter);
    },
    enabled: !!listeningDetailFilter,
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

  const lastfmMutation = useMutation({
    mutationFn: () => runLastfmListeningImport(lastfmUsername),
    onSuccess: () => {
      window.showToast?.('Last.fm listening import started', 'info');
      void lastfmImportQuery.refetch();
    },
    onError: (error) => {
      window.showToast?.(error instanceof Error ? error.message : 'Last.fm import failed', 'error');
    },
  });

  useEffect(() => {
    const username = lastfmImportQuery.data?.username;
    if (username && !lastfmUsername) setLastfmUsername(username);
  }, [lastfmImportQuery.data?.username, lastfmUsername]);

  useEffect(() => {
    const onProgress = () => {
      void lastfmImportQuery.refetch();
      void invalidateStatsQueries(queryClient);
    };
    window.addEventListener('ss:lastfm-import-progress', onProgress);
    return () => window.removeEventListener('ss:lastfm-import-progress', onProgress);
  }, [lastfmImportQuery, queryClient]);

  const cachedStats = cachedStatsQuery.data;
  const overview = cachedStats?.overview ?? EMPTY_STATS_OVERVIEW;
  const hasData = hasStatsData(overview);
  const lastSynced = listeningStatusQuery.data?.stats?.last_poll ?? null;
  const shellStatus = useShellStatus();
  const isStandalone = shellStatus?.media_server?.type === 'soulsync';

  const onRangeChange = (nextRange: StatsRange) => {
    void navigate({
      to: Route.fullPath,
      search: { range: nextRange, tab, story },
      replace: true,
    });
  };

  const onTabChange = (nextTab: StatsTab) => {
    // Range travels with the tab so switching to Library and back does not
    // silently reset a range the user chose.
    void navigate({
      to: Route.fullPath,
      search: { range, tab: nextTab, story },
      replace: true,
    });
  };

  // Opening the story is a real history entry (no `replace`) so Back closes it
  // — the gesture people already reach for on a full-screen takeover. Closing
  // replaces, so Back from the closed page does not drop them straight back in.
  const openStory = () => {
    void navigate({ to: Route.fullPath, search: { range, tab, story: 'year' } });
  };

  const closeStory = () => {
    void navigate({
      to: Route.fullPath,
      search: { range, tab, story: undefined },
      replace: true,
    });
  };

  return (
    <div id="stats-container" className={styles.statsContainer} data-testid="stats-page">
      <PageHeader
        icon={<img src="/static/trans2.png" alt="" />}
        title="Listening Stats"
        actions={
          <>
            {/* The story is a listening fact, so it does not belong on the
                Library tab where nothing else is about the person. */}
            {tab === 'listening' ? (
              <button
                type="button"
                className={styles.statsYearButton}
                onClick={openStory}
                data-testid="stats-year-button"
              >
                Your Year
              </button>
            ) : null}
            <div className={styles.statsTabs} role="tablist" aria-label="Stats section">
              {STATS_TAB_VALUES.map((option) => (
                <button
                  key={option}
                  type="button"
                  role="tab"
                  aria-selected={tab === option}
                  className={`${styles.statsTab} ${tab === option ? styles.statsTabActive : ''}`}
                  onClick={() => onTabChange(option)}
                >
                  {option === 'listening' ? 'Listening' : 'Library'}
                </button>
              ))}
            </div>
            {/* Library stats are not range-scoped — disk usage is what it is
                today — so the picker is hidden rather than left inert. */}
            <div
              id="stats-time-range"
              className={styles.statsTimeRange}
              role="tablist"
              aria-label="Listening stats range"
              hidden={tab !== 'listening'}
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
              <LastfmImportControl
                status={lastfmImportQuery.data}
                username={lastfmUsername}
                onUsernameChange={setLastfmUsername}
                onRun={() => lastfmMutation.mutate()}
                running={lastfmMutation.isPending}
              />
              {isStandalone ? (
                <span
                  className={styles.statsStandaloneNotice}
                  role="note"
                  title="SoulSync standalone does not use an external media server, so manual listening stats sync is unavailable."
                >
                  Standalone mode: manual sync unavailable
                </span>
              ) : (
                <>
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
                </>
              )}
            </div>
          </>
        }
      />

      {story === 'year' ? <YearStory onClose={closeStory} /> : null}

      {cachedStatsQuery.isPending ? (
        <SectionLoadingState />
      ) : cachedStatsQuery.error ? (
        <SectionErrorState message={getErrorMessage(cachedStatsQuery.error)} />
      ) : tab === 'library' ? (
        /* Operational facts. Deliberately NOT range-scoped — disk usage and
           database size are what they are right now, so the range picker is
           hidden on this tab rather than sitting there doing nothing. */
        <>
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
      ) : hasData ? (
        <>
          <OverviewCards
            overview={overview}
            previous={cachedStats?.previous ?? null}
            periodLabel={PREVIOUS_PERIOD_LABEL[range] ?? null}
          />
          <div className={styles.statsMainGrid}>
            <div className={styles.statsLeftCol}>
              <StatsSectionCard title="When You Listen">
                <StatsListeningClock
                  clock={cachedStats?.clock}
                  rhythm={cachedStats?.rhythm}
                  onCellSelect={(weekday, hour) =>
                    setListeningDetailFilter({ type: 'weekday_hour', weekday, hour })
                  }
                />
              </StatsSectionCard>
              <StatsSectionCard title="Listening Activity">
                <div id="stats-timeline-chart" className={styles.chartContainer}>
                  <StatsActivityChart
                    timeline={cachedStats?.timeline ?? []}
                    onDateSelect={(date) => setListeningDetailFilter({ type: 'date', date })}
                  />
                </div>
              </StatsSectionCard>
              <StatsSectionCard title="Own vs Play">
                <StatsOwnVsPlayCard
                  rows={cachedStats?.own_vs_play ?? []}
                  neglected={cachedStats?.neglected ?? []}
                />
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
                <TopArtistsVisual artists={cachedStats?.top_artists ?? []} />
                <StatsRankedArtists artists={cachedStats?.top_artists ?? []} />
              </StatsSectionCard>
              <StatsSectionCard title="Top Albums">
                <StatsRankedAlbums albums={cachedStats?.top_albums ?? []} />
              </StatsSectionCard>
              <StatsSectionCard title="Top Tracks">
                <StatsRankedTracks
                  tracks={cachedStats?.top_tracks ?? []}
                  onPlay={(track) => playStatsTrack(bridge, track)}
                />
              </StatsSectionCard>
            </div>
          </div>
        </>
      ) : (
        <StatsEmptyState />
      )}
      {listeningDetailFilter ? (
        <StatsListeningDetailModal
          payload={listeningDetailsQuery.data}
          loading={listeningDetailsQuery.isPending}
          error={listeningDetailsQuery.error}
          onClose={() => setListeningDetailFilter(null)}
          onPlay={(track) => playStatsTrack(bridge, track)}
        />
      ) : null}
    </div>
  );
}

type OverviewShape = Partial<{
  total_plays: number;
  total_time_ms: number;
  unique_artists: number;
  unique_albums: number;
  unique_tracks: number;
}>;

/**
 * What the delta is measured against, per range. 'all' is absent on purpose —
 * there is no period before everything, so its tiles carry no comparison.
 */
const PREVIOUS_PERIOD_LABEL: Partial<Record<StatsRange, string>> = {
  '7d': 'vs previous 7 days',
  '30d': 'vs previous 30 days',
  '12m': 'vs previous 12 months',
};

function LastfmImportControl({
  onRun,
  onUsernameChange,
  running,
  status,
  username,
}: {
  onRun: () => void;
  onUsernameChange: (username: string) => void;
  running: boolean;
  status: LastfmListeningImportStatus | undefined;
  username: string;
}) {
  const active = !!status?.running;
  const pct = clampProgress(status?.progress);
  const canUseAuthenticatedUser = !!status?.authenticated_user_available;
  const needsUsername = !username && !status?.username && !canUseAuthenticatedUser;
  const disabled = running || active || !status?.api_key_configured || needsUsername;
  const subline = lastfmImportSubline(status);

  return (
    <div
      className={`${styles.lastfmImportControl} ${active ? styles.lastfmImportControlActive : ''}`}
      title={subline}
    >
      <div className={styles.lastfmImportMain}>
        <span className={styles.lastfmImportBrand}>Last.fm</span>
        <span className={styles.lastfmImportStatus}>{lastfmImportLabel(status)}</span>
      </div>
      <div className={styles.lastfmImportBar} aria-hidden="true">
        <div
          className={styles.lastfmImportFill}
          style={{ width: pct != null && (active || status?.status === 'partial') ? `${pct}%` : status?.status === 'complete' ? '100%' : '0%' }}
        />
      </div>
      {needsUsername ? (
        <input
          className={styles.lastfmImportInput}
          value={username}
          onChange={(event) => onUsernameChange(event.target.value)}
          placeholder="Last.fm username"
          aria-label="Last.fm username"
        />
      ) : null}
      <button
        type="button"
        className={styles.lastfmImportRun}
        onClick={onRun}
        disabled={disabled}
        title={status?.api_key_configured ? 'Sync Last.fm listening now' : 'Configure Last.fm API key first'}
      >
        {active ? 'Running' : 'Run'}
      </button>
    </div>
  );
}

function lastfmImportLabel(status: LastfmListeningImportStatus | undefined): string {
  if (!status?.api_key_configured) return 'not configured';
  if (status.running) return `${clampProgress(status.progress) ?? 0}%`;
  if (status.status === 'error') return 'needs attention';
  if (status.status === 'partial') return 'resume needed';
  if (status.last_success_at) return 'up to date';
  return 'ready';
}

function lastfmImportSubline(status: LastfmListeningImportStatus | undefined): string {
  if (!status?.api_key_configured) return 'Configure Last.fm API credentials in Settings.';
  if (status.running) {
    const inserted = formatCompactNumber(status.inserted || 0);
    const duplicates = formatCompactNumber(status.duplicates || 0);
    return `${status.phase || 'Importing'} · ${inserted} added · ${duplicates} skipped`;
  }
  if (status.status === 'error') return status.error || 'Last.fm import failed';
  if (status.status === 'partial') return 'Previous import stopped before the backfill finished. Run to continue.';
  if (!status.username && status.authenticated_user_available) {
    return 'Uses the Last.fm account authorized in Settings.';
  }
  if (status.last_success_at) {
    const next = status.next_run_in_seconds ? ` · next check in ${formatShortDuration(status.next_run_in_seconds)}` : '';
    return `Last checked ${status.last_success_at}${next}`;
  }
  return 'Run once to start hourly Last.fm listening sync.';
}

function clampProgress(value: unknown): number | null {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function formatShortDuration(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

function OverviewCards({
  overview,
  periodLabel,
  previous,
}: {
  overview: OverviewShape;
  periodLabel: string | null;
  previous: OverviewShape | null;
}) {
  const cards = [
    { key: 'total_plays', label: 'Total Plays', value: formatCompactNumber(overview.total_plays) },
    {
      key: 'total_time_ms',
      label: 'Listening Time',
      value: formatListeningTime(overview.total_time_ms),
    },
    {
      key: 'unique_artists',
      label: 'Artists',
      value: formatCompactNumber(overview.unique_artists),
    },
    { key: 'unique_albums', label: 'Albums', value: formatCompactNumber(overview.unique_albums) },
    { key: 'unique_tracks', label: 'Tracks', value: formatCompactNumber(overview.unique_tracks) },
  ] as const;

  return (
    <div id="stats-overview" className={styles.statsOverview}>
      {cards.map((card) => (
        <div key={card.label} className={styles.statsCard}>
          <div className={styles.statsCardValue}>{card.value}</div>
          <div className={styles.statsCardLabel}>{card.label}</div>
          <StatsCardDelta
            current={overview[card.key]}
            previous={previous?.[card.key]}
            periodLabel={periodLabel}
          />
        </div>
      ))}
    </div>
  );
}

/**
 * The change against the equivalent previous period.
 *
 * Renders NOTHING rather than something misleading, in three cases: no
 * previous period at all (range 'all'), a previous period of zero (there is no
 * honest percentage for growth from nothing — that shows "new" instead), and a
 * partial payload. A stats page that invents "+∞%" is worse than one that says
 * less.
 */
function StatsCardDelta({
  current,
  periodLabel,
  previous,
}: {
  current: number | undefined;
  periodLabel: string | null;
  previous: number | undefined;
}) {
  if (!periodLabel) return null;
  if (isNewSincePrevious(current, previous)) {
    return (
      <div className={`${styles.statsCardDelta} ${styles.statsCardDeltaNew}`}>
        new {periodLabel}
      </div>
    );
  }
  const delta = statDelta(current, previous);
  if (!delta) return null;
  const arrow = delta.direction === 'up' ? '↑' : delta.direction === 'down' ? '↓' : '·';
  return (
    <div className={`${styles.statsCardDelta} ${styles[`statsCardDelta_${delta.direction}`]}`}>
      {arrow} {delta.pct}% {periodLabel}
    </div>
  );
}

/**
 * The shape of a listening week — plays by weekday x hour — plus the habit
 * numbers beside it.
 *
 * The page could say how MUCH you listened and never WHEN. This is the most
 * personal thing the data can show, and it is one GROUP BY.
 *
 * The grid arrives dense (7x24, zeros included) from the backend, so this
 * renders it directly: a UI that has to fill gaps is a UI where an empty hour
 * and a missing hour look the same.
 */
function StatsListeningClock({
  clock,
  onCellSelect,
  rhythm,
}: {
  clock: StatsClock | undefined;
  onCellSelect: (weekday: number, hour: number) => void;
  rhythm: StatsRhythm | undefined;
}) {
  const grid = clock?.grid;
  const peak = clock?.peak;
  const peakPlays = peak?.plays ?? 0;

  if (!grid || !clock?.total) {
    return <div className={styles.statsClockEmpty}>No plays in this range yet.</div>;
  }

  const peakSlot = formatPeakSlot(peak?.weekday, peak?.hour);

  return (
    <div className={styles.statsClock}>
      <div className={styles.statsClockGrid}>
        {grid.map((row, weekday) => (
          <div key={weekday} className={styles.statsClockRow}>
            <span className={styles.statsClockDay}>{WEEKDAY_LABELS[weekday]}</span>
            {row.map((plays, hour) => (
              <button
                key={hour}
                type="button"
                className={`${styles.statsClockCell} ${plays > 0 ? styles.statsClockCellActive : ''}`}
                style={{ opacity: heatIntensity(plays, peakPlays) }}
                title={`${WEEKDAY_LABELS[weekday]} ${formatHourLabel(hour)} - ${plays} ${
                  plays === 1 ? 'play' : 'plays'
                }`}
                aria-label={`${WEEKDAY_LABELS[weekday]} ${formatHourLabel(hour)}: ${plays} plays`}
                disabled={plays <= 0}
                onClick={() => onCellSelect(weekday, hour)}
              />
            ))}
          </div>
        ))}
        <div className={styles.statsClockAxis}>
          <span className={styles.statsClockDay} />
          {[0, 6, 12, 18].map((h) => (
            <span key={h} className={styles.statsClockAxisLabel}>
              {formatHourLabel(h)}
            </span>
          ))}
        </div>
      </div>

      <div className={styles.statsRhythm}>
        {peakSlot ? (
          <div className={styles.statsRhythmItem}>
            <span className={styles.statsRhythmValue}>{peakSlot}</span>
            <span className={styles.statsRhythmLabel}>peak slot</span>
          </div>
        ) : null}
        {rhythm ? (
          <>
            <div className={styles.statsRhythmItem}>
              <span className={styles.statsRhythmValue}>{rhythm.current_streak}</span>
              <span className={styles.statsRhythmLabel}>
                day{rhythm.current_streak === 1 ? '' : 's'} in a row
              </span>
            </div>
            <div className={styles.statsRhythmItem}>
              <span className={styles.statsRhythmValue}>{rhythm.longest_streak}</span>
              <span className={styles.statsRhythmLabel}>longest streak</span>
            </div>
            <div className={styles.statsRhythmItem}>
              <span className={styles.statsRhythmValue}>{rhythm.busiest_day.plays}</span>
              <span className={styles.statsRhythmLabel}>busiest day</span>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

/**
 * What share of the library a genre is, against what share of the listening.
 *
 * The page's strongest claim to being worth visiting: Spotify has no library
 * and Plex has no acquisition history, so nobody else can tell you that you
 * own 40% metal and play 12% of it.
 *
 * Sorted by the size of the DISAGREEMENT, not by size — the biggest genre is
 * something the user already knows.
 */
function StatsOwnVsPlayCard({
  neglected,
  rows,
}: {
  neglected: StatsNeglectedAlbum[];
  rows: StatsOwnVsPlay[];
}) {
  if (!rows.length) {
    return <div className={styles.statsClockEmpty}>Not enough tagged artists to compare yet.</div>;
  }

  return (
    <div className={styles.statsOwnPlay}>
      {rows.map((row) => (
        <div key={row.genre} className={styles.statsOwnPlayRow}>
          <span className={styles.statsOwnPlayGenre} title={row.genre}>
            {row.genre}
          </span>
          <span className={styles.statsOwnPlayBars}>
            <span
              className={styles.statsOwnPlayOwned}
              style={{ width: `${Math.min(row.owned_pct, 100)}%` }}
              title={`${row.owned_pct}% of your library (${row.owned_tracks} tracks)`}
            />
            <span
              className={styles.statsOwnPlayPlayed}
              style={{ width: `${Math.min(row.played_pct, 100)}%` }}
              title={`${row.played_pct}% of your plays (${row.plays})`}
            />
          </span>
          <span
            className={`${styles.statsOwnPlayGap} ${
              row.gap >= 0 ? styles.statsOwnPlayGapUp : styles.statsOwnPlayGapDown
            }`}
            title={
              row.gap >= 0
                ? 'You play this more than you own it'
                : 'You own this more than you play it'
            }
          >
            {row.gap > 0 ? '+' : ''}
            {row.gap}
          </span>
        </div>
      ))}

      <div className={styles.statsOwnPlayLegend}>
        <span className={styles.statsOwnPlayKeyOwned} /> owned
        <span className={styles.statsOwnPlayKeyPlayed} /> played
      </div>

      {neglected.length ? (
        <div className={styles.statsNeglected}>
          <div className={styles.statsNeglectedTitle}>Never played ({neglected.length})</div>
          {neglected.slice(0, 5).map((album) => (
            <div key={album.id} className={styles.statsNeglectedRow}>
              <span className={styles.statsNeglectedName}>{album.name}</span>
              <span className={styles.statsNeglectedArtist}>{album.artist}</span>
              <span className={styles.statsNeglectedCount}>{album.tracks}</span>
            </div>
          ))}
        </div>
      ) : null}
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

function StatsActivityChart({
  onDateSelect,
  timeline,
}: {
  onDateSelect: (date: string) => void;
  timeline: Array<{ date: string; plays: number }>;
}) {
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
          cursor="pointer"
          onClick={(data: unknown) => {
            const row = data as { date?: string; payload?: { date?: string; plays?: number } };
            const date = row.payload?.date ?? row.date;
            const plays = row.payload?.plays ?? 0;
            if (date && plays > 0) onDateSelect(date);
          }}
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

function TopArtistsVisual({ artists }: { artists: StatsArtistRow[] }) {
  const topArtists = getTopArtistBubbles(artists);
  if (topArtists.length === 0) return null;

  return (
    <div className={styles.statsTopArtistsVisual}>
      <div className={styles.statsArtistBubbles}>
        {topArtists.map(({ artist, percent, size }) => {
          const isClickable = artist.id !== null && artist.id !== undefined;
          const bubbleContent = (
            <>
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
            </>
          );
          return isClickable ? (
            <ArtistDetailLink
              key={`${artist.name}-${artist.id ?? 'unknown'}`}
              artistId={artist.id}
              className={styles.statsArtistBubble}
              aria-label={`Open artist detail for ${artist.name}`}
            >
              {bubbleContent}
            </ArtistDetailLink>
          ) : (
            <div
              key={`${artist.name}-${artist.id ?? 'unknown'}`}
              className={`${styles.statsArtistBubble} ${styles.statsArtistBubbleDisabled}`}
              aria-disabled="true"
            >
              {bubbleContent}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ArtistDetailLink({
  artistId,
  children,
  ...linkProps
}: {
  artistId: string | number | null | undefined;
  children: ReactNode;
} & Omit<ComponentPropsWithoutRef<'a'>, 'children' | 'href'>) {
  if (artistId == null) {
    return <>{children}</>;
  }

  return (
    <Link
      to="/artist-detail/$source/$id"
      params={{ source: ARTIST_DETAIL_SOURCE, id: String(artistId) }}
      {...linkProps}
    >
      {children}
    </Link>
  );
}

function StatsRankedArtists({ artists }: { artists: StatsArtistRow[] }) {
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
              <ArtistDetailLink artistId={artist.id} className={styles.statsArtistLink}>
                {artist.name}
              </ArtistDetailLink>
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

function StatsRankedAlbums({ albums }: { albums: StatsAlbumRow[] }) {
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
              <ArtistDetailLink artistId={album.artist_id} className={styles.statsArtistLink}>
                {album.artist || ''}
              </ArtistDetailLink>
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
  onPlay,
}: {
  tracks: StatsTrackRow[];
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
              <ArtistDetailLink artistId={track.artist_id} className={styles.statsArtistLink}>
                {track.artist || ''}
              </ArtistDetailLink>
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

function StatsListeningDetailModal({
  error,
  loading,
  onClose,
  onPlay,
  payload,
}: {
  error: unknown;
  loading: boolean;
  onClose: () => void;
  onPlay: (track: { title: string; artist: string; album: string }) => Promise<void>;
  payload: { title?: string; total?: number; limit?: number; items?: StatsListeningEventTrack[] } | undefined;
}) {
  const tracks = payload?.items ?? [];
  const total = payload?.total ?? 0;
  return (
    <div className={styles.statsDetailBackdrop} role="presentation" onMouseDown={onClose}>
      <div
        className={styles.statsDetailModal}
        role="dialog"
        aria-modal="true"
        aria-label="Listening details"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className={styles.statsDetailHeader}>
          <div>
            <div className={styles.statsDetailEyebrow}>Listening Details</div>
            <h3>{payload?.title || 'Loading...'}</h3>
            <p>
              {loading
                ? 'Loading plays...'
                : `${formatCompactNumber(total)} ${total === 1 ? 'play' : 'plays'}${
                    total > tracks.length ? `, showing latest ${tracks.length}` : ''
                  }`}
            </p>
          </div>
          <button type="button" className={styles.statsDetailClose} onClick={onClose} aria-label="Close">
            x
          </button>
        </div>

        {error ? <SectionSubtleError message={getErrorMessage(error)} /> : null}
        {!error && loading ? <div className={styles.statsDetailLoading}>Loading listening details...</div> : null}
        {!error && !loading && tracks.length === 0 ? <EmptyListState message="No plays found" /> : null}

        {!error && tracks.length ? (
          <div className={styles.statsDetailList}>
            {tracks.map((track, index) => (
              <div
                key={`${track.title}-${track.artist ?? ''}-${track.played_at ?? ''}-${index}`}
                className={styles.statsDetailRow}
              >
                {track.image_url ? (
                  <img className={styles.statsDetailArt} src={track.image_url} alt="" />
                ) : (
                  <div className={styles.statsDetailArtFallback}>{(track.title || '?')[0]}</div>
                )}
                <div className={styles.statsDetailTrackMain}>
                  <div className={styles.statsDetailTrackTitle}>{track.title}</div>
                  <div className={styles.statsDetailTrackMeta}>
                    {track.artist_db_id ? (
                      <ArtistDetailLink artistId={track.artist_db_id} className={styles.statsArtistLink}>
                        {track.artist || 'Unknown artist'}
                      </ArtistDetailLink>
                    ) : (
                      <span>{track.artist || 'Unknown artist'}</span>
                    )}
                    {track.album ? ` · ${track.album}` : ''}
                  </div>
                </div>
                <div className={styles.statsDetailWhen}>
                  <span>{formatDetailPlayedAt(track.played_at)}</span>
                  {track.server_source ? <span>{track.server_source}</span> : null}
                </div>
                <button
                  type="button"
                  className={styles.statsDetailPlay}
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
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function StatsLibraryHealth({ health }: { health: StatsHealth }) {
  const shellStatus = useShellStatus();
  const jiosaavnEnabled = shellStatus?._experimental?.jiosaavn_enabled === true;
  const bandcampEnabled = shellStatus?._experimental?.bandcamp_enabled === true;
  const enrichmentServices = visibleStatsEnrichmentServices(jiosaavnEnabled, bandcampEnabled);
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
        {enrichmentServices.map((service) => {
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
  try {
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
  } catch {
    // Library resolve is best-effort; fall through to stream lookup on failure.
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

function formatDetailPlayedAt(value: string | null | undefined): string {
  if (!value) return '';
  const parsed = new Date(value.includes('T') ? value : value.replace(' ', 'T'));
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error';
}
