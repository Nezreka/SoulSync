import { Link, Outlet } from '@tanstack/react-router';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { ImportQueueEntry } from '../-import.types';
import styles from './import-page.module.css';

import {
  getQueueProgressPercent,
  getQueueStatusText,
  getStagingStatsText,
} from '../-import.helpers';
import { useImportQueueWorkflow } from '../-import.store';
import { fallbackImage, RefreshIcon, useImportStaging } from './import-shared';

export function ImportPage() {
  useReactPageShell('import');

  const { refreshStaging, stagingFiles, stagingPath, stagingQuery } = useImportStaging();

  return (
    <div id="import-page" data-testid="import-page">
      <div className={styles.importPageContainer}>
        <ImportHeader
          error={stagingQuery.error}
          fileCountText={getStagingStatsText(stagingFiles)}
          loading={stagingQuery.isLoading}
          stagingPath={stagingPath}
          onRefresh={refreshStaging}
        />
        <ImportProcessingQueue />
        <ImportTabNav />
        <section className={`${styles.importPageTabContent} ${styles.active}`}>
          <Outlet />
        </section>
      </div>
    </div>
  );
}

function ImportHeader({
  error,
  fileCountText,
  loading,
  stagingPath,
  onRefresh,
}: {
  error: unknown;
  fileCountText: string;
  loading: boolean;
  stagingPath: string;
  onRefresh: () => void;
}) {
  return (
    <header className={styles.importPageHeader}>
      <div className={styles.importPageTitleRow}>
        <h1 className={styles.importPageTitle}>
          <img src="/static/import.png" className="page-header-icon" alt="" />
          <span>Import Music</span>
        </h1>
        <button
          type="button"
          className={styles.importPageRefreshBtn}
          title="Re-scan import folder"
          onClick={onRefresh}
        >
          <RefreshIcon />
          Refresh
        </button>
      </div>
      <div className={styles.importPageStagingBar} id="import-staging-bar">
        <span className={styles.importStagingPath} id="import-page-staging-path">
          {error ? 'Import folder: error' : `Import: ${stagingPath}`}
        </span>
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
      className={`${styles.importPageQueue} ${queue.length === 0 ? styles.hidden : ''}`}
      id="import-page-queue"
    >
      <div className={styles.importPageQueueHeader}>
        <span className={styles.importPageQueueTitle}>Processing</span>
        <button
          type="button"
          className={styles.importPageQueueClear}
          id="import-page-queue-clear"
          style={{ display: hasFinished ? undefined : 'none' }}
          onClick={clearFinishedJobs}
        >
          Clear finished
        </button>
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
  const statusClass =
    entry.status === 'error' || (entry.status === 'done' && entry.errors.length > 0)
      ? styles.error
      : entry.status === 'done'
        ? styles.done
        : '';

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
        <div className={`${styles.importPageQueueArt} ${styles.importPageQueueArtEmpty}`}>
          A
        </div>
      )}
      <div className={styles.importPageQueueInfo}>
        <div className={styles.importPageQueueName}>{entry.label}</div>
        <div className={styles.importPageQueueDetail}>{entry.sublabel}</div>
      </div>
      <div className={styles.importPageQueueProgress}>
        <div className={styles.importPageQueueBar}>
          <div
            className={`${styles.importPageQueueFill} ${
              entry.status === 'error' ? styles.error : ''
            }`}
            style={{ width: `${getQueueProgressPercent(entry)}%` }}
          />
        </div>
        <div className={`${styles.importPageQueueStatus} ${statusClass}`}>{statusText}</div>
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
        activeProps={{ className: `${styles.importPageTab} ${styles.active}` }}
        id="import-page-tab-auto"
      >
        Auto
      </Link>
      <Link
        to="/import/album"
        className={styles.importPageTab}
        activeProps={{ className: `${styles.importPageTab} ${styles.active}` }}
        id="import-page-tab-album"
      >
        Albums
      </Link>
      <Link
        to="/import/singles"
        className={styles.importPageTab}
        activeProps={{ className: `${styles.importPageTab} ${styles.active}` }}
        id="import-page-tab-singles"
      >
        Singles
      </Link>
    </nav>
  );
}
