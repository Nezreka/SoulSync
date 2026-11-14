import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { type ComponentPropsWithoutRef, type ReactNode, useEffect, useRef, useState } from 'react';

import type { ShellBridge } from '@/platform/shell/bridge';

import { DialogFrame } from '@/components/dialog';
import { EmptyState } from '@/components/primitives';
import { useReactPageShell, useShellStatus } from '@/platform/shell/route-controllers';

import type {
  LastfmListeningImportStatus,
  ListenbrainzListeningImportStatus,
  StatsAlbumRow,
  StatsArtistRow,
  StatsClock,
  StatsDbStoragePayload,
  StatsHealth,
  StatsLibraryDiskUsagePayload,
  StatsListeningEventsFilter,
  StatsListeningEventTrack,
  StatsNeglectedAlbum,
  StatsOverview,
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
  listenbrainzListeningImportStatusQueryOptions,
  listeningStatsStatusQueryOptions,
  resolveStatsTrack,
  runLastfmListeningImport,
  runListenbrainzListeningImport,
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
  groupDbStorageTables,
  hasStatsData,
  heatIntensity,
  isNewSincePrevious,
  parsePlayedAt,
  periodSentence,
  statDelta,
  statsRangePhrase,
  WEEKDAY_LABELS,
  visibleStatsEnrichmentServices,
} from '../-stats.helpers';
import { STATS_TAB_VALUES } from '../-stats.types';
import { fetchAlbumPlayTracks } from '../-year.api';
import { Route } from '../route';
import styles from './stats-page.module.css';
import { YearStory } from './year-story';

const STATS_DETAIL_QUERY_KEY = ['stats-listening-events'] as const;

/** How many of a shelf or list show before "Show all". */
const TRACKS_SHOWN = 10;
const RECENT_SHOWN = 8;
const NEVER_SHOWN = 8;

const PLAY_ICON = (
  <svg viewBox="0 0 16 16" aria-hidden="true">
    <path d="M4 2.5v11l9-5.5z" />
  </svg>
);

export function StatsPage() {
  const bridge = useReactPageShell('stats');

  const navigate = useNavigate({ from: Route.fullPath });
  const queryClient = useQueryClient();
  const { range, tab, story } = Route.useSearch();
  const syncTimeoutRef = useRef<number | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [listeningDetailFilter, setListeningDetailFilter] =
    useState<StatsListeningEventsFilter | null>(null);

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
  const listenbrainzImportQuery = useQuery({
    ...listenbrainzListeningImportStatusQueryOptions(),
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
    retry: false,
    staleTime: 30_000,
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
    mutationFn: () => runLastfmListeningImport(),
    onSuccess: () => {
      window.showToast?.('Last.fm listening import started', 'info');
      void lastfmImportQuery.refetch();
    },
    onError: (error) => {
      window.showToast?.(error instanceof Error ? error.message : 'Last.fm import failed', 'error');
    },
  });

  const listenbrainzMutation = useMutation({
    mutationFn: () => runListenbrainzListeningImport(),
    onSuccess: () => {
      window.showToast?.('ListenBrainz listening import started', 'info');
      void listenbrainzImportQuery.refetch();
    },
    onError: (error) => {
      window.showToast?.(
        error instanceof Error ? error.message : 'ListenBrainz import failed',
        'error',
      );
    },
  });

  useEffect(() => {
    const onProgress = () => {
      void lastfmImportQuery.refetch();
      void invalidateStatsQueries(queryClient);
    };
    window.addEventListener('ss:lastfm-import-progress', onProgress);
    return () => window.removeEventListener('ss:lastfm-import-progress', onProgress);
  }, [lastfmImportQuery, queryClient]);

  useEffect(() => {
    const onProgress = () => {
      void listenbrainzImportQuery.refetch();
      void invalidateStatsQueries(queryClient);
    };
    window.addEventListener('ss:listenbrainz-import-progress', onProgress);
    return () => window.removeEventListener('ss:listenbrainz-import-progress', onProgress);
  }, [listenbrainzImportQuery, queryClient]);

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

  const topArtist = cachedStats?.top_artists?.[0];
  const sentence = periodSentence(range, overview, {
    topGenre: cachedStats?.genres?.[0]?.genre,
    peak: cachedStats?.clock?.peak,
    activeDays: cachedStats?.rhythm?.active_days,
  });
  const play = (track: { title: string; artist: string; album: string }) =>
    playStatsTrack(bridge, track);

  return (
    <div className="page-shell pgf-shell">
      <div id="stats-container" className={`pgf-page ${styles.page}`} data-testid="stats-page">
        <header className={styles.head}>
          <div className={styles.headMain}>
            <h1 className="pgf-title">Stats</h1>
            <p className={styles.headSub}>
              {tab === 'library'
                ? 'What is in your library today'
                : hasData
                  ? `${(overview.total_plays ?? 0).toLocaleString('en-US')} plays ${statsRangePhrase(range)}`
                  : 'Your listening, once there is some'}
            </p>
          </div>
          <div className={styles.headActs}>
            {/* The story is a listening fact, so it does not belong on the
              Library tab where nothing else is about the person. */}
            {tab === 'listening' ? (
              <button
                type="button"
                className={styles.cap}
                onClick={openStory}
                data-testid="stats-year-button"
              >
                Your Year
              </button>
            ) : null}
            {/* Library stats are not range-scoped: disk usage is what it is
              today, so the picker is hidden rather than left inert. */}
            <div
              id="stats-time-range"
              className={styles.seg}
              role="group"
              aria-label="Listening stats range"
              hidden={tab !== 'listening'}
            >
              {(['7d', '30d', '12m', 'all'] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  className={styles.segButton}
                  aria-pressed={range === option}
                  onClick={() => onRangeChange(option)}
                >
                  {getStatsRangeLabel(option)}
                </button>
              ))}
            </div>
          </div>
        </header>

        <div className={styles.subBar}>
          <div className={styles.seg} role="tablist" aria-label="Stats section">
            {STATS_TAB_VALUES.map((option) => (
              <button
                key={option}
                type="button"
                role="tab"
                aria-selected={tab === option}
                className={styles.segButton}
                onClick={() => onTabChange(option)}
              >
                {option === 'listening' ? 'Listening' : 'Library'}
              </button>
            ))}
          </div>

          <div className={styles.sync}>
            {isStandalone ? (
              <span
                className={styles.syncItem}
                role="note"
                title="SoulSync standalone does not use an external media server, so manual listening stats sync is unavailable."
              >
                Standalone mode: manual sync unavailable
              </span>
            ) : (
              <span className={styles.syncItem}>
                <span className={styles.syncDot} aria-hidden="true" />
                <span>{lastSynced ? `Last synced: ${lastSynced}` : 'Not synced yet'}</span>
                <button
                  id="stats-sync-btn"
                  type="button"
                  className={styles.linkButton}
                  onClick={() => syncMutation.mutate()}
                  disabled={syncing}
                  aria-label="Sync listening stats"
                  title="Sync now"
                >
                  {syncing ? 'Syncing…' : 'Sync now'}
                </button>
              </span>
            )}
            {isImportCardYours(lastfmImportQuery.data) ? (
              <LastfmImportControl
                status={lastfmImportQuery.data}
                onRun={() => lastfmMutation.mutate()}
                running={lastfmMutation.isPending}
              />
            ) : null}
            {isImportCardYours(listenbrainzImportQuery.data) ? (
              <ListenbrainzImportControl
                status={listenbrainzImportQuery.data}
                onRun={() => listenbrainzMutation.mutate()}
                running={listenbrainzMutation.isPending}
              />
            ) : null}
          </div>
        </div>

        {story === 'year' ? <YearStory onClose={closeStory} /> : null}

        {cachedStatsQuery.isPending ? (
          <SectionLoadingState />
        ) : cachedStatsQuery.error ? (
          <SectionErrorState message={getErrorMessage(cachedStatsQuery.error)} />
        ) : tab === 'library' ? (
          /* Operational facts. Deliberately NOT range-scoped: disk usage and
           database size are what they are right now. */
          <div className={styles.view}>
            <StatsLibraryHealth
              health={cachedStats?.health ?? {}}
              databaseBytes={dbStorageQuery.data?.total_file_size}
            />
            <div className={styles.two}>
              <StatsSectionCard title="Library disk usage">
                <StatsDiskUsage payload={diskUsageQuery.data} error={diskUsageQuery.error} />
              </StatsSectionCard>
              <StatsSectionCard title="Database storage">
                <StatsDbStorage payload={dbStorageQuery.data} error={dbStorageQuery.error} />
              </StatsSectionCard>
            </div>
          </div>
        ) : hasData ? (
          <div className={styles.view}>
            <section className={`${styles.card} ${styles.hero}`} aria-label="This period">
              <HeroArtist artist={topArtist} />
              <div className={styles.heroBody}>
                {sentence.length ? (
                  <p className={styles.heroSay}>
                    {sentence.map((part, index) =>
                      part.strong ? <b key={index}>{part.text}</b> : part.text,
                    )}
                  </p>
                ) : null}
                <OverviewFigures
                  overview={overview}
                  previous={cachedStats?.previous ?? null}
                  periodLabel={PREVIOUS_PERIOD_LABEL[range] ?? null}
                />
              </div>
            </section>

            <div className={styles.two}>
              <StatsSectionCard
                title="Listening activity"
                note="Plays over time. Click a bar to see what you played."
              >
                <StatsActivityChart
                  timeline={cachedStats?.timeline ?? []}
                  busiest={cachedStats?.rhythm?.busiest_day}
                  onDateSelect={(date) => setListeningDetailFilter({ type: 'date', date })}
                />
              </StatsSectionCard>
              <StatsSectionCard title="When you listen" note="Hour by weekday">
                <StatsListeningClock
                  clock={cachedStats?.clock}
                  rhythm={cachedStats?.rhythm}
                  onCellSelect={(weekday, hour) =>
                    setListeningDetailFilter({ type: 'weekday_hour', weekday, hour })
                  }
                />
              </StatsSectionCard>
            </div>

            <StatsTopArtists artists={cachedStats?.top_artists ?? []} />
            <StatsTopAlbums albums={cachedStats?.top_albums ?? []} />

            <div className={styles.two}>
              <StatsTopTracks tracks={cachedStats?.top_tracks ?? []} onPlay={play} />
              <StatsSectionCard
                title="What you own and what you play"
                note="By genre, as a share of each"
              >
                <StatsOwnVsPlayCard
                  rows={cachedStats?.own_vs_play ?? []}
                  genres={cachedStats?.genres ?? []}
                />
              </StatsSectionCard>
            </div>

            <div className={styles.two}>
              <StatsSectionCard title="Recently played">
                <StatsRecentPlays tracks={cachedStats?.recent ?? []} onPlay={play} />
              </StatsSectionCard>
              <StatsSectionCard
                title="Never played"
                note="The biggest albums you own and have not started"
              >
                <StatsNeverPlayed albums={cachedStats?.neglected ?? []} />
              </StatsSectionCard>
            </div>
          </div>
        ) : (
          <StatsEmptyState />
        )}
        {listeningDetailFilter ? (
          <StatsListeningDetailModal
            payload={listeningDetailsQuery.data}
            filter={listeningDetailFilter}
            loading={listeningDetailsQuery.isPending && !listeningDetailsQuery.data}
            error={listeningDetailsQuery.error}
            onClose={() => setListeningDetailFilter(null)}
            onPlay={play}
          />
        ) : null}
      </div>
    </div>
  );
}

/**
 * What the change is measured against, per range. 'all' is absent on purpose:
 * there is no period before everything, so its figures carry no comparison.
 */
const PREVIOUS_PERIOD_LABEL: Partial<Record<StatsRange, string>> = {
  '7d': 'vs previous 7 days',
  '30d': 'vs previous 30 days',
  '12m': 'vs previous 12 months',
};

/**
 * your own history comes from your own accounts (#1293). a card for a service
 * you didn't connect would fill the shared history, which you don't read.
 */
function isImportCardYours(
  status: { history_scope?: string; own_account?: boolean } | undefined,
): boolean {
  return !(status?.history_scope === 'profile' && !status.own_account);
}

type HistoryImportControlProps = {
  onRun: () => void;
  running: boolean;
};

export function LastfmImportControl({
  status,
  ...props
}: HistoryImportControlProps & { status: LastfmListeningImportStatus | undefined }) {
  return (
    <HistoryImportControl
      {...props}
      service="Last.fm"
      status={status}
      configured={
        !!status?.api_key_configured && !!(status.username || status.authenticated_user_available)
      }
    />
  );
}

export function ListenbrainzImportControl({
  status,
  ...props
}: HistoryImportControlProps & { status: ListenbrainzListeningImportStatus | undefined }) {
  return (
    <HistoryImportControl
      {...props}
      service="ListenBrainz"
      status={status}
      configured={!!status?.token_configured}
    />
  );
}

function HistoryImportControl({
  service,
  status,
  configured,
  running,
  onRun,
}: HistoryImportControlProps & {
  service: string;
  status: LastfmListeningImportStatus | ListenbrainzListeningImportStatus | undefined;
  configured: boolean;
}) {
  const active = running || !!status?.running;
  const progress = clampProgress(status?.progress);
  const needsResume = status?.status === 'partial' || status?.status === 'cancelled';
  const failed = status?.status === 'error';
  const label = !status
    ? 'Checking settings…'
    : !configured
      ? 'Configure in Settings'
      : active
        ? `Syncing${progress == null ? '…' : ` · ${progress}%`}`
        : failed
          ? 'Sync failed'
          : needsResume
            ? 'Sync paused'
            : status.last_success_at
              ? 'Up to date'
              : 'Ready to sync';
  const detail = active
    ? `${status?.phase || 'Starting sync'} · ${formatCompactNumber(status?.inserted || 0)} added`
    : failed
      ? status?.error || 'Check your account in Settings and retry.'
      : status?.last_success_at
        ? `Last synced ${status.last_success_at}${status.next_run_in_seconds ? ` · next check in ${formatShortDuration(status.next_run_in_seconds)}` : ''}`
        : 'Uses your account from Settings. Sync once to enable hourly updates.';

  return (
    <span className={styles.syncItem} role="group" aria-label={`${service} history sync`}>
      <b>{service}</b>
      <span title="Manage this account in Settings">
        {status?.username || 'Account from Settings'}
      </span>
      <span className={failed ? styles.syncBad : styles.syncQuiet} role="status" title={detail}>
        {label}
      </span>
      <button
        type="button"
        className={styles.linkButton}
        onClick={onRun}
        disabled={active || !configured || !status?.success}
        aria-label={`${active ? 'Syncing' : needsResume ? 'Resume' : failed ? 'Retry' : 'Sync'} ${service} history`}
        aria-busy={active}
        title={configured ? detail : `Configure ${service} in Settings`}
      >
        {active ? 'Syncing…' : needsResume ? 'Resume sync' : failed ? 'Retry sync' : 'Sync now'}
      </button>
      {active || needsResume ? (
        <progress
          className={styles.syncProgress}
          aria-label={`${service} sync progress`}
          max={100}
          value={progress ?? undefined}
        />
      ) : null}
    </span>
  );
}

function clampProgress(value: unknown): number | null {
  if (value == null) return null;
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

/** A cover or portrait, or the name's first letter where there is none. */
function Art({ src, name }: { src?: string | null; name: string }) {
  const [failed, setFailed] = useState<string | null>(null);
  if (src && src !== failed) {
    return (
      <img
        className={styles.artImg}
        src={src}
        alt=""
        loading="lazy"
        onError={() => setFailed(src)}
      />
    );
  }
  return (
    <span className={styles.artFallback} aria-hidden="true">
      {(name || '?').trim()[0] ?? '?'}
    </span>
  );
}

/** The period's most played artist, as the picture the page opens on. */
function HeroArtist({ artist }: { artist: StatsArtistRow | undefined }) {
  if (!artist) return null;
  const body = (
    <>
      <Art src={artist.image_url} name={artist.name} />
      <span className={styles.heroShade} aria-hidden="true" />
      <span className={styles.heroCaption}>
        <small>Most played artist</small>
        <b>{artist.name}</b>
        <span>
          {formatCompactNumber(artist.play_count)} plays
          {artist.global_listeners
            ? ` · ${formatCompactNumber(artist.global_listeners)} listeners worldwide`
            : ''}
        </span>
      </span>
    </>
  );
  return artist.id != null ? (
    <ArtistDetailLink
      artistId={artist.id}
      className={styles.heroArt}
      aria-label={`Open artist detail for ${artist.name}`}
      data-hero-artist=""
    >
      {body}
    </ArtistDetailLink>
  ) : (
    <div className={styles.heroArt}>{body}</div>
  );
}

function OverviewFigures({
  overview,
  periodLabel,
  previous,
}: {
  overview: Partial<StatsOverview>;
  periodLabel: string | null;
  previous: Partial<StatsOverview> | null;
}) {
  const figures = [
    { key: 'total_plays', label: 'plays', value: formatCompactNumber(overview.total_plays) },
    {
      key: 'total_time_ms',
      label: 'listening time',
      value: formatListeningTime(overview.total_time_ms),
    },
    {
      key: 'unique_artists',
      label: 'artists',
      value: formatCompactNumber(overview.unique_artists),
    },
    { key: 'unique_albums', label: 'albums', value: formatCompactNumber(overview.unique_albums) },
    { key: 'unique_tracks', label: 'tracks', value: formatCompactNumber(overview.unique_tracks) },
  ] as const;

  return (
    <div id="stats-overview" className={styles.figures}>
      {figures.map((figure) => (
        <div key={figure.key} className={styles.figure}>
          <b>{figure.value}</b>
          <span>{figure.label}</span>
          <StatsCardDelta
            current={overview[figure.key]}
            previous={previous?.[figure.key]}
            periodLabel={periodLabel}
          />
        </div>
      ))}
    </div>
  );
}

/**
 * The change against the equivalent previous period, in words.
 *
 * Renders NOTHING rather than something misleading, in three cases: no
 * previous period at all (range 'all'), a previous period of zero (there is no
 * honest percentage for growth from nothing, so that shows "new" instead), and
 * a partial payload. Only growth takes the accent: a quieter month is not an
 * error, and five red chips made it look like one.
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
    return <em className={styles.deltaUp}>new {periodLabel}</em>;
  }
  const delta = statDelta(current, previous);
  if (!delta) return null;
  if (delta.direction === 'flat') return <em>no change {periodLabel}</em>;
  return (
    <em className={delta.direction === 'up' ? styles.deltaUp : undefined}>
      {delta.direction} {delta.pct}% {periodLabel}
    </em>
  );
}

/**
 * The shape of a listening week, plays by weekday and hour, plus the habit
 * numbers beside it.
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
    return <div className={styles.none}>No plays in this range yet.</div>;
  }

  const peakSlot = formatPeakSlot(peak?.weekday, peak?.hour);

  return (
    <div>
      <div className={styles.clock}>
        {grid.map((row, weekday) => (
          <div key={weekday} className={styles.clockRow}>
            <span className={styles.clockDay}>{WEEKDAY_LABELS[weekday]}</span>
            {row.map((plays, hour) => (
              <button
                key={hour}
                type="button"
                className={`${styles.clockCell} ${plays > 0 ? styles.clockCellActive : ''}`}
                data-stats-mark=""
                style={plays > 0 ? { opacity: heatIntensity(plays, peakPlays) } : undefined}
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
        <div className={styles.clockRow} aria-hidden="true">
          <span className={styles.clockDay} />
          {[0, 6, 12, 18].map((h) => (
            <span key={h} className={styles.clockAxisLabel}>
              {formatHourLabel(h)}
            </span>
          ))}
        </div>
      </div>

      <div className={styles.rhythm}>
        {peakSlot ? (
          <div className={styles.rhythmItem}>
            <b>{peakSlot}</b>
            <span>peak slot</span>
          </div>
        ) : null}
        {rhythm ? (
          <>
            <div className={styles.rhythmItem}>
              <b>{rhythm.current_streak}</b>
              <span>day{rhythm.current_streak === 1 ? '' : 's'} in a row</span>
            </div>
            <div className={styles.rhythmItem}>
              <b>{rhythm.longest_streak}</b>
              <span>longest streak</span>
            </div>
            <div className={styles.rhythmItem}>
              <b>{rhythm.busiest_day.plays}</b>
              <span>busiest day</span>
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
 * Sorted by the size of the DISAGREEMENT, not by size: the biggest genre is
 * something the user already knows. What they play most is the line under it.
 */
function StatsOwnVsPlayCard({
  genres,
  rows,
}: {
  genres: Array<{ genre: string; play_count: number; percentage: number }>;
  rows: StatsOwnVsPlay[];
}) {
  const top = genres.slice(0, 6);
  const widest = Math.max(1, ...rows.map((row) => Math.max(row.owned_pct, row.played_pct)));
  const share = (pct: number) => `${Math.min(100, (pct / widest) * 100)}%`;

  return (
    <div>
      {rows.length === 0 ? (
        <div className={styles.none}>Not enough tagged artists to compare yet.</div>
      ) : (
        <>
          {rows.map((row, index) => (
            <div
              key={row.genre}
              className={styles.genreRow}
              style={{ '--stats-i': index } as React.CSSProperties}
            >
              <span className={styles.genreName} title={row.genre}>
                {row.genre}
              </span>
              <span className={styles.genreBars}>
                <i
                  className={styles.genrePlayed}
                  style={{ width: share(row.played_pct) }}
                  title={`${row.played_pct}% of your plays (${row.plays})`}
                />
                <i
                  className={styles.genreOwned}
                  style={{ width: share(row.owned_pct) }}
                  title={`${row.owned_pct}% of your library (${row.owned_tracks} tracks)`}
                />
              </span>
              <span
                className={`${styles.genreGap} ${row.gap > 0 ? styles.genreGapUp : ''}`}
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
          <div className={styles.legend}>
            <span>
              <i className={styles.genrePlayed} />
              Share of your plays
            </span>
            <span>
              <i className={styles.genreOwned} />
              Share of your library
            </span>
            <span>The number is the gap, in points</span>
          </div>
        </>
      )}
      {top.length ? (
        <p id="stats-genre-chart" className={styles.genreTop}>
          Most played:{' '}
          {top.map((genre, index) => (
            <span key={genre.genre}>
              {index ? ' · ' : ''}
              <b>{genre.genre}</b> {genre.percentage}%
            </span>
          ))}
        </p>
      ) : null}
    </div>
  );
}

function StatsNeverPlayed({ albums }: { albums: StatsNeglectedAlbum[] }) {
  const [all, setAll] = useState(false);
  if (!albums.length) return <div className={styles.none}>Nothing you own is unplayed.</div>;
  const shown = all ? albums : albums.slice(0, NEVER_SHOWN);
  return (
    <div>
      <div className={styles.rows}>
        {shown.map((album) => (
          <div key={album.id} className={`${styles.row} ${styles.rowPlain}`}>
            <div className={styles.rowText}>
              <span className={styles.rowName}>{album.name}</span>
              <span className={styles.rowMeta}>{album.artist}</span>
            </div>
            <span className={styles.rowCount}>{album.tracks} tracks</span>
          </div>
        ))}
      </div>
      <ShowAll total={albums.length} shown={NEVER_SHOWN} all={all} onToggle={setAll} />
    </div>
  );
}

function ShowAll({
  all,
  onToggle,
  shown,
  total,
}: {
  all: boolean;
  onToggle: (next: boolean) => void;
  shown: number;
  total: number;
}) {
  if (total <= shown) return null;
  return (
    <button type="button" className={styles.linkButton} onClick={() => onToggle(!all)}>
      {all ? 'Show fewer' : `Show all ${total}`}
    </button>
  );
}

function StatsSectionCard({
  action,
  children,
  note,
  title,
}: {
  action?: ReactNode;
  children: ReactNode;
  note?: string;
  title: string;
}) {
  return (
    <section className={styles.card} aria-label={title}>
      <div className={styles.cardHead}>
        <h2>{title}</h2>
        {note ? <span>{note}</span> : null}
        {action}
      </div>
      {children}
    </section>
  );
}

/** a timeline date as people read it: 2026-10-29 is "Oct 29", a month
 *  bucket 2026-10 is "Oct 2026". anything else passes through. */
export function statsDateLabel(value: string): string {
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (day) {
    const d = new Date(+day[1], +day[2] - 1, +day[3]);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  const month = /^(\d{4})-(\d{2})$/.exec(value);
  if (month) {
    const d = new Date(+month[1], +month[2] - 1, 1);
    return d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
  }
  return value;
}

/**
 * Plays per day (or per month on the long ranges) as plain bars.
 *
 * Its own bars rather than a chart library's: a bar here is a button that
 * opens what was played that day, and a real button gets a focus ring, a
 * label and a keyboard for free.
 */
function StatsActivityChart({
  busiest,
  onDateSelect,
  timeline,
}: {
  busiest?: { date: string | null; plays: number };
  onDateSelect: (date: string) => void;
  timeline: Array<{ date: string; plays: number }>;
}) {
  const [hover, setHover] = useState<{ date: string; plays: number } | null>(null);
  if (!timeline.length) return <div className={styles.none}>Nothing to chart yet.</div>;
  const peak = Math.max(1, ...timeline.map((point) => point.plays));
  const shownPoint = hover ?? (busiest?.date ? { date: busiest.date, plays: busiest.plays } : null);
  const middle = timeline[Math.floor(timeline.length / 2)];

  return (
    <div id="stats-timeline-chart">
      <div className={styles.bars}>
        {timeline.map((point, index) => (
          <button
            key={point.date}
            type="button"
            className={`${styles.bar} ${point.plays === peak ? styles.barPeak : ''}`}
            data-stats-mark=""
            style={
              {
                height: `${Math.max(2, (point.plays / peak) * 100)}%`,
                '--stats-i': index,
              } as React.CSSProperties
            }
            aria-label={`${statsDateLabel(point.date)}: ${point.plays} plays`}
            disabled={point.plays <= 0}
            onMouseEnter={() => setHover(point)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(point)}
            onBlur={() => setHover(null)}
            onClick={() => onDateSelect(point.date)}
          />
        ))}
      </div>
      <div className={styles.barLabels} aria-hidden="true">
        <span>{statsDateLabel(timeline[0].date)}</span>
        {timeline.length > 2 ? <span>{statsDateLabel(middle.date)}</span> : null}
        <span>{statsDateLabel(timeline[timeline.length - 1].date)}</span>
      </div>
      <p className={styles.readout}>
        {shownPoint ? (
          <>
            {hover ? '' : 'Busiest: '}
            <b>{statsDateLabel(shownPoint.date)}</b>, {shownPoint.plays.toLocaleString('en-US')}{' '}
            plays
          </>
        ) : (
          ' '
        )}
      </p>
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
      to="/library"
      search={{ artist: Number(artistId), view: 'discovery', immersive: true }}
      {...linkProps}
    >
      {children}
    </Link>
  );
}

/**
 * The period's artists as a shelf of portraits. One row until asked for the
 * rest: twenty five faces is a wall, and the first nine are the answer.
 */
function StatsTopArtists({ artists }: { artists: StatsArtistRow[] }) {
  const [all, setAll] = useState(false);
  const most = artists[0]?.play_count || 1;
  return (
    <StatsSectionCard
      title="Top artists"
      note="An artist opens their page"
      action={<ShelfToggle total={artists.length} all={all} onToggle={setAll} />}
    >
      {artists.length === 0 ? <EmptyListState message="No data yet" /> : null}
      <div id="stats-top-artists" className={`${styles.shelf} ${all ? styles.shelfAll : ''}`}>
        {artists.map((artist, index) => {
          const body = (
            <>
              <span className={`${styles.tileArt} ${styles.tileArtRound}`}>
                <Art src={artist.image_url} name={artist.name} />
              </span>
              <span className={styles.tileText}>
                <span className={styles.tileName}>
                  {artist.name}
                  {artist.soul_id && !String(artist.soul_id).startsWith('soul_unnamed_') ? (
                    <img src="/static/trans2.png" className={styles.soulBadge} alt="SoulID" />
                  ) : null}
                </span>
                <span className={styles.tileMeta}>
                  {formatCompactNumber(artist.play_count)} plays
                  {artist.global_listeners
                    ? ` · ${formatCompactNumber(artist.global_listeners)} listeners`
                    : ''}
                </span>
                <span className={styles.meter}>
                  <i style={{ width: `${Math.round((artist.play_count / most) * 100)}%` }} />
                </span>
              </span>
            </>
          );
          const key = `${artist.name}-${artist.id ?? index}`;
          return artist.id != null ? (
            <ArtistDetailLink
              key={key}
              artistId={artist.id}
              className={`${styles.tile} ${styles.tileCentered}`}
              aria-label={`Open artist detail for ${artist.name}`}
            >
              {body}
            </ArtistDetailLink>
          ) : (
            <div key={key} className={`${styles.tile} ${styles.tileCentered}`} aria-disabled="true">
              {body}
            </div>
          );
        })}
      </div>
    </StatsSectionCard>
  );
}

function ShelfToggle({
  all,
  onToggle,
  total,
}: {
  all: boolean;
  onToggle: (next: boolean) => void;
  total: number;
}) {
  // a shelf row holds about nine at full width; below that there is no "rest"
  if (total <= 6) return null;
  return (
    <button type="button" className={styles.linkButton} onClick={() => onToggle(!all)}>
      {all ? 'Show fewer' : `Show all ${total}`}
    </button>
  );
}

/**
 * The period's albums as covers. A cover plays the album when the library
 * has it: the tracks load at click time, since at most one gets played.
 */
function StatsTopAlbums({ albums }: { albums: StatsAlbumRow[] }) {
  const [all, setAll] = useState(false);
  const [loading, setLoading] = useState<string | number | null>(null);

  const play = async (album: StatsAlbumRow) => {
    if (album.id == null || loading !== null) return;
    setLoading(album.id);
    try {
      const tracks = await fetchAlbumPlayTracks(album.id);
      if (!tracks.length) {
        window.showToast?.(`No owned tracks for ${album.name} yet`, 'info');
        return;
      }
      await window.playTrackList?.(tracks, album.name);
    } catch {
      window.showToast?.('Could not play that album', 'error');
    } finally {
      setLoading(null);
    }
  };

  return (
    <StatsSectionCard
      title="Top albums"
      note="A cover plays the album"
      action={<ShelfToggle total={albums.length} all={all} onToggle={setAll} />}
    >
      {albums.length === 0 ? <EmptyListState message="No data yet" /> : null}
      <div id="stats-top-albums" className={`${styles.shelf} ${all ? styles.shelfAll : ''}`}>
        {albums.map((album, index) => {
          const playable = album.id != null && album.id !== '';
          const art = (
            <span className={styles.tileArt}>
              <Art src={album.image_url} name={album.name} />
              <span className={styles.rank}>{index + 1}</span>
              {playable ? (
                <span className={styles.playBadge} aria-hidden="true">
                  {loading === album.id ? '…' : PLAY_ICON}
                </span>
              ) : null}
            </span>
          );
          return (
            <div key={`${album.name}-${index}`} className={styles.tile}>
              {playable ? (
                <button
                  type="button"
                  className={styles.tileButton}
                  title="Play"
                  aria-label={`Play ${album.name}`}
                  onClick={() => void play(album)}
                >
                  {art}
                </button>
              ) : (
                art
              )}
              <span className={styles.tileText}>
                <span className={styles.tileName}>{album.name}</span>
                <span className={styles.tileMeta}>
                  <ArtistDetailLink artistId={album.artist_id} className={styles.artistLink}>
                    {album.artist || ''}
                  </ArtistDetailLink>
                  {` · ${formatCompactNumber(album.play_count)} plays`}
                </span>
              </span>
            </div>
          );
        })}
      </div>
    </StatsSectionCard>
  );
}

function StatsTopTracks({
  tracks,
  onPlay,
}: {
  tracks: StatsTrackRow[];
  onPlay: (track: { title: string; artist: string; album: string }) => Promise<void>;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? tracks : tracks.slice(0, TRACKS_SHOWN);
  return (
    <StatsSectionCard title="Top tracks">
      {tracks.length === 0 ? <EmptyListState message="No data yet" /> : null}
      <div id="stats-top-tracks" className={styles.rows}>
        {shown.map((track, index) => (
          <div key={`${track.name}-${index}`} className={styles.row}>
            <span className={styles.rowNum}>{index + 1}</span>
            <span className={styles.rowArt}>
              <Art src={track.image_url} name={track.name} />
            </span>
            <div className={styles.rowText}>
              <span className={styles.rowName}>{track.name}</span>
              <span className={styles.rowMeta}>
                <ArtistDetailLink artistId={track.artist_id} className={styles.artistLink}>
                  {track.artist || ''}
                </ArtistDetailLink>
                {track.album ? ` · ${track.album}` : ''}
              </span>
            </div>
            <span className={styles.rowCount}>{formatCompactNumber(track.play_count)} plays</span>
            <button
              type="button"
              className={styles.iconButton}
              onClick={() =>
                void onPlay({
                  title: track.name,
                  artist: track.artist || '',
                  album: track.album || '',
                })
              }
              title="Play"
            >
              {PLAY_ICON}
            </button>
          </div>
        ))}
      </div>
      <ShowAll total={tracks.length} shown={TRACKS_SHOWN} all={all} onToggle={setAll} />
    </StatsSectionCard>
  );
}

function StatsRecentPlays({
  tracks,
  onPlay,
}: {
  tracks: StatsRecentTrack[];
  onPlay: (track: { title: string; artist: string; album: string }) => Promise<void>;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? tracks : tracks.slice(0, RECENT_SHOWN);
  return (
    <div>
      <div id="stats-recent-plays" className={styles.rows}>
        {tracks.length === 0 ? <EmptyListState message="No recent plays" /> : null}
        {shown.map((track, index) => (
          <div
            key={`${track.title}-${track.artist ?? ''}-${track.album ?? ''}-${track.played_at ?? ''}-${index}`}
            className={`${styles.row} ${styles.rowRecent}`}
          >
            <div className={styles.rowText}>
              <span className={styles.rowName}>{track.title}</span>
              <span className={styles.rowMeta}>{track.artist || ''}</span>
            </div>
            <span className={styles.rowCount}>{formatRelativePlayedAt(track.played_at)}</span>
            <button
              type="button"
              className={styles.iconButton}
              onClick={() =>
                void onPlay({
                  title: track.title,
                  artist: track.artist || '',
                  album: track.album || '',
                })
              }
              title="Play"
            >
              {PLAY_ICON}
            </button>
          </div>
        ))}
      </div>
      <ShowAll total={tracks.length} shown={RECENT_SHOWN} all={all} onToggle={setAll} />
    </div>
  );
}

function getListeningDetailFallbackTitle(filter: StatsListeningEventsFilter): string {
  if (filter.type === 'date') return formatDetailDateTitle(filter.date);
  if (filter.type === 'weekday_hour') {
    return `${WEEKDAY_LABELS[filter.weekday] ?? 'Selected day'} ${formatHourLabel(filter.hour)}`;
  }
  return formatHourLabel(filter.hour);
}

function formatDetailDateTitle(date: string): string {
  if (/^\d{4}-\d{2}$/.test(date)) {
    const parsedMonth = new Date(`${date}-01T00:00:00`);
    if (!Number.isNaN(parsedMonth.getTime())) {
      return new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' }).format(
        parsedMonth,
      );
    }
  }

  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(parsed);
}
function StatsListeningDetailModal({
  error,
  filter,
  loading,
  onClose,
  onPlay,
  payload,
}: {
  error: unknown;
  filter: StatsListeningEventsFilter;
  loading: boolean;
  onClose: () => void;
  onPlay: (track: { title: string; artist: string; album: string }) => Promise<void>;
  payload:
    | {
        title?: string;
        total?: number;
        limit?: number;
        has_more?: boolean;
        items?: StatsListeningEventTrack[];
      }
    | undefined;
}) {
  const tracks = payload?.items ?? [];
  const total = payload?.total ?? 0;
  const title = payload?.title || getListeningDetailFallbackTitle(filter);
  const summary = loading
    ? 'Finding the matching plays...'
    : payload?.has_more
      ? `Showing latest ${formatCompactNumber(tracks.length)} plays`
      : `${formatCompactNumber(total)} ${total === 1 ? 'play' : 'plays'}`;
  return (
    <DialogFrame
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      viewportClassName={styles.statsDetailBackdrop}
      className={styles.statsDetailModal}
      aria-label="Listening details"
    >
      <div className={styles.statsDetailHeader}>
        <div>
          <div className={styles.statsDetailEyebrow}>Listening Details</div>
          <h3>{title}</h3>
          <p>{summary}</p>
        </div>
        <button
          type="button"
          className={styles.statsDetailClose}
          onClick={onClose}
          aria-label="Close"
        >
          x
        </button>
      </div>

      {error ? <SectionSubtleError message={getErrorMessage(error)} /> : null}
      {!error && loading ? (
        <div className={styles.statsDetailLoading}>Loading listening details...</div>
      ) : null}
      {!error && !loading && tracks.length === 0 ? (
        <EmptyListState message="No plays found" />
      ) : null}

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
                    <ArtistDetailLink artistId={track.artist_db_id} className={styles.artistLink}>
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
                {track.server_source ? (
                  <span className={styles.statsDetailSource}>{track.server_source}</span>
                ) : null}
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
    </DialogFrame>
  );
}

function StatsLibraryHealth({
  databaseBytes,
  health,
}: {
  databaseBytes?: number;
  health: StatsHealth;
}) {
  const shellStatus = useShellStatus();
  const jiosaavnEnabled = shellStatus?._experimental?.jiosaavn_enabled === true;
  const bandcampEnabled = shellStatus?._experimental?.bandcamp_enabled === true;
  const enrichmentServices = visibleStatsEnrichmentServices(jiosaavnEnabled, bandcampEnabled);
  const formatEntries = Object.entries(health.format_breakdown ?? {}).sort((a, b) => b[1] - a[1]);
  const formatTotal = formatEntries.reduce((sum, [, count]) => sum + count, 0) || 1;
  const years = (health.total_duration_ms ?? 0) / 3_600_000 / 24 / 365;

  return (
    <>
      <section id="stats-library-health" className={styles.card} aria-label="Library health">
        <div className={`${styles.figures} ${styles.figuresFour}`}>
          <div className={styles.figure}>
            <b>{formatCompactNumber(health.total_tracks ?? 0)}</b>
            <span>tracks</span>
          </div>
          <div className={styles.figure}>
            <b>{formatTotalDuration(health.total_duration_ms)}</b>
            <span>of music</span>
            {years >= 0.1 ? <em>{years.toFixed(1)} years end to end</em> : null}
          </div>
          <div className={styles.figure}>
            <b>{health.unplayed_percentage || 0}%</b>
            <span>never played</span>
            <em>{formatCompactNumber(health.unplayed_count)} tracks</em>
          </div>
          <div className={styles.figure}>
            <b>{databaseBytes ? formatBytes(databaseBytes) : '—'}</b>
            <span>database</span>
          </div>
        </div>
      </section>

      <StatsSectionCard title="Formats" note="By track count">
        <div className={styles.formatBar}>
          {formatEntries.map(([format, count]) => (
            <i
              key={format}
              style={{ flex: count }}
              title={`${format}: ${count} tracks (${((count / formatTotal) * 100).toFixed(1)}%)`}
            />
          ))}
        </div>
        <div className={styles.formatLegend}>
          {formatEntries.map(([format, count]) => (
            <span key={format}>
              {format}
              <b>{count.toLocaleString('en-US')}</b>
            </span>
          ))}
        </div>
      </StatsSectionCard>

      <StatsSectionCard
        title="Metadata coverage"
        note="Share of your tracks each source has matched"
      >
        <div id="stats-enrichment-coverage" className={styles.coverage}>
          {[...enrichmentServices]
            .map((service) => ({
              ...service,
              percent: health.enrichment_coverage?.[service.key] || 0,
            }))
            .sort((a, b) => b.percent - a.percent)
            .map((service) => (
              <div key={service.key} className={styles.coverageRow}>
                <span>{service.label}</span>
                <span className={styles.track}>
                  <i
                    className={service.percent < 50 ? styles.trackLow : undefined}
                    style={{ width: `${service.percent}%` }}
                  />
                </span>
                <b>{service.percent}%</b>
              </div>
            ))}
        </div>
      </StatsSectionCard>
    </>
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
    <div>
      <div className={styles.total}>
        <b>{hasData ? formatBytes(payload?.total_bytes) : '—'}</b>
        <span>
          {hasData
            ? `${tracksWithSize.toLocaleString()} tracks measured${
                tracksWithoutSize > 0
                  ? ` (+${tracksWithoutSize.toLocaleString()} pending next Deep Scan)`
                  : ''
              }`
            : payload?.refreshing
              ? 'Measuring your library…'
              : payload?.report_failed
                ? 'Couldn’t measure your library just now. Try again in a minute.'
                : tracksWithoutSize > 0
                  ? `Run a Deep Scan to populate (${tracksWithoutSize.toLocaleString()} tracks pending)`
                  : 'No tracks in library yet'}
        </span>
      </div>
      <div className={styles.coverage}>
        {formats.map(([format, bytes]) => (
          <div key={format} className={styles.coverageRow}>
            <span>{format.toUpperCase()}</span>
            <span className={styles.track}>
              <i style={{ width: `${Math.max(2, Math.round((bytes / max) * 100))}%` }} />
            </span>
            <b>{formatBytes(bytes)}</b>
          </div>
        ))}
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

  const tables = groupDbStorageTables(payload?.tables ?? []);
  const method = payload?.method;
  const max = Math.max(1, ...tables.map((table) => table.size));

  return (
    <div id="stats-db-storage-chart">
      <div className={styles.total}>
        <b>{formatDbStorageValue(payload?.total_file_size || 0, method)}</b>
        <span>
          {payload?.refreshing && !tables.length
            ? 'Measuring tables…'
            : payload?.report_failed && !tables.length
              ? 'Couldn’t measure tables'
              : 'Total Size'}
        </span>
      </div>
      <div className={styles.coverage}>
        {tables.map((table) => (
          <div key={table.name} className={styles.coverageRow}>
            <span title={table.name}>{table.name}</span>
            <span className={styles.track}>
              <i style={{ width: `${Math.max(2, Math.round((table.size / max) * 100))}%` }} />
            </span>
            <b>{formatDbStorageValue(table.size, method)}</b>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatsEmptyState() {
  return (
    <EmptyState
      className={styles.statsEmpty}
      icon="📊"
      title="No Listening Data Yet"
      description={
        <>
          Enable &quot;Listening Stats&quot; in Settings to start tracking your listening activity
          from your media server.
        </>
      }
    />
  );
}

function SectionLoadingState() {
  return <div className={styles.statsLoading}>Loading listening stats...</div>;
}

function SectionErrorState({ message }: { message: string }) {
  return (
    <EmptyState
      className={styles.statsEmpty}
      title="Failed to load listening stats"
      description={message}
    />
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
  const parsed = parsePlayedAt(value);
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
