import { useQuery } from '@tanstack/react-query';
import { Outlet } from '@tanstack/react-router';
import clsx from 'clsx';
import { useState } from 'react';

import { Button } from '@/components/form/form';
import { PageHeader } from '@/components/page-header';
import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { ImportQueueEntry } from '../-import.types';

import { importInboxQueryOptions } from '../-import.api';
import { getQueueProgressPercent, getQueueStatusText } from '../-import.helpers';
import { useImportQueueWorkflow } from '../-import.store';
import styles from './import-page.module.css';
import { fallbackImage, GearIcon, RefreshIcon, useInboxRefresh } from './import-shared';
import { SettingsDrawer } from './settings-drawer';

/**
 * The page shell: header, the client-side processing jobs, and whichever
 * child is showing (the inbox, or the matcher for one item). The header's
 * refresh re-reads the import folder; the gear opens the worker's settings,
 * which used to sit inline above the results and get scrolled past.
 */
export function ImportPage() {
  useReactPageShell('import');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const inbox = useQuery(importInboxQueryOptions());
  const { refresh, refreshing } = useInboxRefresh();

  return (
    <div id="import-page" data-testid="import-page">
      <div className={styles.page}>
        <PageHeader
          icon={<img src="/static/import.png" alt="" />}
          title="Import"
          subtitle="What is in your import folder, and what to do about it"
          actions={
            <>
              <Button
                variant="secondary"
                title="Re-read the import folder"
                aria-busy={refreshing || inbox.isFetching}
                disabled={refreshing}
                onClick={refresh}
              >
                <RefreshIcon />
                {refreshing ? 'Refreshing…' : 'Refresh'}
              </Button>
              <button
                type="button"
                className={styles.iconButton}
                id="import-page-settings"
                title="Auto-import settings"
                aria-label="Auto-import settings"
                onClick={() => setSettingsOpen(true)}
              >
                <GearIcon />
              </button>
            </>
          }
        />
        <ImportJobs />
        <Outlet />
      </div>
      <SettingsDrawer open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  );
}

/** Imports the page itself is running (the matcher's "Import N tracks"). */
function ImportJobs() {
  const { clearFinishedJobs, queue } = useImportQueueWorkflow();
  if (queue.length === 0) return null;
  const hasFinished = queue.some((entry) => entry.status !== 'running');

  return (
    <section className={styles.jobs} id="import-page-queue" aria-label="Imports in progress">
      <div className={styles.jobsHead}>
        <span className={styles.paneTitle}>Importing</span>
        {hasFinished ? (
          <Button
            variant="ghost"
            size="sm"
            id="import-page-queue-clear"
            onClick={clearFinishedJobs}
          >
            Clear finished
          </Button>
        ) : null}
      </div>
      {queue.map((entry) => (
        <ImportJob key={entry.id} entry={entry} />
      ))}
    </section>
  );
}

function ImportJob({ entry }: { entry: ImportQueueEntry }) {
  const failed = entry.status === 'error' || (entry.status === 'done' && entry.errors.length > 0);
  return (
    <div className={styles.job}>
      {entry.imageUrl ? (
        <img className={styles.jobArt} src={entry.imageUrl} alt="" onError={fallbackImage} />
      ) : (
        <div className={styles.jobArt} />
      )}
      <div className={styles.jobBody}>
        <div className={styles.jobTitle}>{entry.label}</div>
        <div className={styles.jobDetail}>{entry.sublabel}</div>
        {entry.blockedByMediaServer ? (
          <ul className={styles.jobErrors}>
            <li title={entry.errors[0]}>
              {entry.errors[0]} <a href="/settings">Go to Settings</a>
            </li>
          </ul>
        ) : entry.errors.length > 0 ? (
          <ul className={styles.jobErrors}>
            {entry.errors.map((err, i) => (
              <li key={i} title={err}>
                {err}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className={styles.jobSide}>
        <div className={styles.progress}>
          <div
            className={styles.progressFill}
            style={{ width: `${getQueueProgressPercent(entry)}%` }}
          />
        </div>
        <div
          className={clsx(styles.jobStatus, {
            [styles.error]: failed,
            [styles.done]: entry.status === 'done' && !failed,
          })}
        >
          {getQueueStatusText(entry)}
        </div>
      </div>
    </div>
  );
}
