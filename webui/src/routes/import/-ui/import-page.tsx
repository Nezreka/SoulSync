import { Link, Outlet } from '@tanstack/react-router';
import clsx from 'clsx';

import { Button } from '@/components/form/form';
import { Show } from '@/components/primitives';
import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { ImportQueueEntry } from '../-import.types';

import {
  getQueueProgressPercent,
  getQueueStatusText,
  getStagingStatsText,
} from '../-import.helpers';
import { useImportQueueWorkflow } from '../-import.store';
import styles from './import-page.module.css';
import { fallbackImage, RefreshIcon, useImportStaging } from './import-shared';

export function ImportPage() {
  useReactPageShell('import');

  const { refreshStaging, stagingFiles, stagingPath, stagingQuery } = useImportStaging();
  const isRefreshing = stagingQuery.isRefetching;
  const lastRefreshedAt =
    stagingQuery.dataUpdatedAt > 0 ? formatShortTime(stagingQuery.dataUpdatedAt) : null;

  return (
    <div id="import-page" data-testid="import-page">
      <div className={styles.importPageContainer}>
        <ImportHeader
          error={stagingQuery.error}
          fileCountText={getStagingStatsText(stagingFiles)}
          loading={stagingQuery.isLoading}
          stagingPath={stagingPath}
          refreshing={isRefreshing}
          lastRefreshedAt={lastRefreshedAt}
          onRefresh={refreshStaging}
        />
        <ImportProcessingQueue />
        <ImportTabNav />
        <section className={clsx(styles.importPageTabContent, styles.active)}>
          <Outlet />
        </section>
      </div>
    </div>
  );
}

function formatShortTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function ImportHeader({
  error,
  fileCountText,
  loading,
  stagingPath,
  refreshing,
  lastRefreshedAt,
  onRefresh,
}: {
  error: unknown;
  fileCountText: string;
  loading: boolean;
  stagingPath: string;
  refreshing: boolean;
  lastRefreshedAt: string | null;
  onRefresh: () => void;
}) {
  return (
    <header className={styles.importPageHeader}>
      <div className={styles.importPageTitleRow}>
        <h1 className={styles.importPageTitle}>
          <img src="/static/import.png" className="page-header-icon" alt="" />
          <span>Import Music</span>
        </h1>
        <Button
          variant="secondary"
          title="Re-scan import folder"
          aria-busy={refreshing}
          disabled={refreshing}
          onClick={onRefresh}
        >
          <RefreshIcon />
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </Button>
      </div>
      <div className={styles.importPageStagingBar} id="import-staging-bar">
        <span className={styles.importStagingPath} id="import-page-staging-path">
          {error ? 'Import folder: error' : `Import: ${stagingPath}`}
        </span>
        <Show when={lastRefreshedAt != null}>
          <span className={styles.importStagingRefreshAt}>
            {lastRefreshedAt ? `Last refreshed: ${lastRefreshedAt}` : null}
          </span>
        </Show>
        <span className={styles.importStagingStats} id="import-page-staging-stats">
          {loading ? 'loading...' : fileCountText}
        </span>
      </div>
    </header>
  );
}

function ImportProcessingQueue() {
  const { clearFinishedJobs, queue } = useImportQueueWorkflow();
  const hasFinished = queue.some((entry) => entry.status !== 'running');

  return (
    <section
      className={clsx(styles.importPageQueue, {
        [styles.hidden]: queue.length === 0,
      })}
      id="import-page-queue"
    >
      <div className={styles.importPageQueueHeader}>
        <span className={styles.importPageQueueTitle}>Processing</span>
        <Button
          variant="ghost"
          id="import-page-queue-clear"
          style={{ display: hasFinished ? undefined : 'none' }}
          onClick={clearFinishedJobs}
        >
          Clear finished
        </Button>
      </div>
      <div className={styles.importPageQueueList} id="import-page-queue-list">
        {queue.map((entry) => (
          <ImportQueueItem key={entry.id} entry={entry} />
        ))}
      </div>
    </section>
  );
}

function ImportQueueItem({ entry }: { entry: ImportQueueEntry }) {
  const statusText = getQueueStatusText(entry);
  const statusClass = clsx({
    [styles.error]:
      entry.status === 'error' || (entry.status === 'done' && entry.errors.length > 0),
    [styles.done]: entry.status === 'done',
  });

  return (
    <div className={styles.importPageQueueItem}>
      {entry.imageUrl ? (
        <img
          className={styles.importPageQueueArt}
          src={entry.imageUrl}
          alt=""
          onError={fallbackImage}
        />
      ) : (
        <div className={clsx(styles.importPageQueueArt, styles.importPageQueueArtEmpty)}>♪</div>
      )}
      <div className={styles.importPageQueueInfo}>
        <div className={styles.importPageQueueName}>{entry.label}</div>
        <div className={styles.importPageQueueDetail}>{entry.sublabel}</div>
        {entry.errors.length > 0 && (
          <ul className={styles.importPageQueueErrors}>
            {entry.errors.map((err, i) => (
              <li key={i} title={err}>
                {err}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className={styles.importPageQueueProgress}>
        <div className={styles.importPageQueueBar}>
          <div
            className={clsx(styles.importPageQueueFill, {
              [styles.error]: entry.status === 'error',
            })}
            style={{ width: `${getQueueProgressPercent(entry)}%` }}
          />
        </div>
        <div className={clsx(styles.importPageQueueStatus, statusClass)}>{statusText}</div>
      </div>
    </div>
  );
}

function ImportTabNav() {
  return (
    <nav className={styles.importPageTabBar} aria-label="Import modes">
      <Link
        to="/import/auto"
        className={styles.importPageTab}
        activeProps={{ className: clsx(styles.importPageTab, styles.active) }}
        id="import-page-tab-auto"
      >
        Auto
      </Link>
      <Link
        to="/import/album"
        className={styles.importPageTab}
        activeProps={{ className: clsx(styles.importPageTab, styles.active) }}
        id="import-page-tab-album"
      >
        Albums
      </Link>
      <Link
        to="/import/singles"
        className={styles.importPageTab}
        activeProps={{ className: clsx(styles.importPageTab, styles.active) }}
        id="import-page-tab-singles"
      >
        Singles
      </Link>
    </nav>
  );
}
