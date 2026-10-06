import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  LIBRARY_V2_QUERY_KEY,
  libraryV2ImportStatusQueryOptions,
  startLibraryV2Import,
} from '../-library-v2.api';
import styles from './migration-banner.module.css';

/**
 * The library upgrade holds downloads, sync and automations until it finishes.
 * Its state used to be visible only on an empty Library page, so a failed
 * migration looked like the rest of the app had quietly stopped working. This
 * says so on every page, with the error and a retry.
 */
export function MigrationBanner() {
  const queryClient = useQueryClient();
  const status = useQuery({ ...libraryV2ImportStatusQueryOptions(30_000), retry: false });
  const retry = useMutation({
    mutationFn: () => startLibraryV2Import(false, false),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: [...LIBRARY_V2_QUERY_KEY, 'import-status'] }),
  });
  const bootstrap = status.data?.bootstrap;
  if (bootstrap?.status !== 'running' && bootstrap?.status !== 'failed') return null;

  const failed = bootstrap.status === 'failed';
  const progress = bootstrap.total
    ? ` — ${Math.round((bootstrap.current / bootstrap.total) * 100)}%`
    : '';
  return (
    <div className={`${styles.banner} ${failed ? styles.failed : ''}`} role="status">
      <span className={styles.text}>
        {failed
          ? 'The library upgrade failed. Downloads, sync and automations stay paused until it completes.'
          : `Upgrading your library${progress}. Downloads, sync and automations resume when it finishes.`}
        {failed && bootstrap.last_error ? (
          <span className={styles.detail} title={bootstrap.last_error}>
            {bootstrap.last_error}
          </span>
        ) : null}
      </span>
      {failed ? (
        <button
          type="button"
          className={styles.action}
          disabled={retry.isPending}
          onClick={() => retry.mutate()}
        >
          {retry.isPending ? 'Retrying…' : 'Retry now'}
        </button>
      ) : null}
    </div>
  );
}
