import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  LIBRARY_V2_QUERY_KEY,
  libraryV2ImportStatusQueryOptions,
  startLibraryV2Import,
} from '../-library-v2.api';
import styles from './migration-banner.module.css';

/** The bootstrap's stage ids as the banner says them; finalize:* is one step. */
const STAGE_LABELS: Record<string, string> = {
  wanted: 'wanted tracks',
  tags: 'file tags',
  artist_rollup: 'artist list',
};

function stageLabel(stage: string | null | undefined): string {
  if (!stage) return '';
  if (stage.startsWith('finalize')) return 'finishing';
  return STAGE_LABELS[stage] ?? stage.replace(/_/g, ' ');
}

/**
 * The library upgrade holds downloads, sync and automations until it finishes.
 * Its state used to be visible only on an empty Library page, so a failed
 * migration looked like the rest of the app had quietly stopped working. This
 * says so on every page, with the error and a retry.
 */
export function MigrationBanner() {
  const queryClient = useQueryClient();
  const status = useQuery({
    ...libraryV2ImportStatusQueryOptions(),
    retry: false,
    // its own cadence: only an upgrade that is under way or stuck needs watching
    refetchInterval: (query) =>
      ['pending', 'running', 'failed'].includes(query.state.data?.bootstrap?.status ?? '')
        ? 30_000
        : false,
  });
  const retry = useMutation({
    mutationFn: () => startLibraryV2Import(false, false),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: [...LIBRARY_V2_QUERY_KEY, 'import-status'] }),
  });
  const bootstrap = status.data?.bootstrap;
  if (bootstrap?.status !== 'running' && bootstrap?.status !== 'failed') return null;

  const failed = bootstrap.status === 'failed';
  // the stage and its numbers: a percentage alone ran from 0 to 100 once per
  // stage, which read as an upgrade starting over (#1062 review, point 4)
  const stage = stageLabel(bootstrap.stage);
  const progress = bootstrap.total
    ? ` — ${stage} ${bootstrap.current.toLocaleString()} of ${bootstrap.total.toLocaleString()}` +
      ` (${Math.round((bootstrap.current / bootstrap.total) * 100)}%)`
    : stage
      ? ` — ${stage}`
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
