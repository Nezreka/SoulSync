/**
 * #2109: pick several artists on the Library list (cards or table) and run one
 * of the artist actions the page already has on all of them: Watchlist,
 * Automatic Search, discography refresh, quality profile and the review lock.
 *
 * A run belongs to the page, not the bar: it carries on while you scroll,
 * filter or open an artist, and the bar says how far it got, what it skipped
 * and what failed. Anything that undoes a choice asks first, in SoulSync's own
 * confirm dialog.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { getShellProfileContext } from '@/platform/shell/bridge';

import type { LibraryV2ArtistSummary } from '../-library-v2.types';

import {
  LIBRARY_V2_QUERY_KEY,
  fetchLibraryV2ArtistIds,
  fetchLibraryV2JobStatus,
  libraryV2QualityProfilesQueryOptions,
  setLibraryV2Monitored,
  setLibraryV2QualityProfile,
  startLibraryV2DiscographyRefresh,
  startLibraryV2ScopedSearch,
  type LibraryV2ListFilters,
} from '../-library-v2.api';
import { LOCK_QUERY_KEY, setLocksBulk } from '../-review-lock.api';
import css from './library-bulk.module.css';
import styles from './library-v2-page.module.css';

// --- selection -----------------------------------------------------------------

export interface ArtistSelection {
  selected: ReadonlySet<number>;
  /** a click; with shift, everything between it and the last click */
  toggle: (id: number, shift?: boolean) => void;
  setMany: (ids: number[], on: boolean) => void;
  clear: () => void;
}

/** the page's order decides what a shift-click range covers */
export function rangeBetween(order: readonly number[], from: number, to: number): number[] {
  const a = order.indexOf(from);
  const b = order.indexOf(to);
  if (a < 0 || b < 0) return [to];
  const [lo, hi] = a < b ? [a, b] : [b, a];
  return order.slice(lo, hi + 1);
}

export function useArtistSelection(order: readonly number[]): ArtistSelection {
  const [selected, setSelected] = useState<ReadonlySet<number>>(() => new Set());
  const anchor = useRef<number | null>(null);
  const toggle = useCallback(
    (id: number, shift = false) => {
      // read the anchor now: react runs the updater later, after it moved
      const from = anchor.current;
      setSelected((prev) => {
        const next = new Set(prev);
        if (shift && from !== null) {
          // the range takes the state of the row you shift-clicked into
          const on = !prev.has(id);
          for (const each of rangeBetween(order, from, id)) {
            if (on) next.add(each);
            else next.delete(each);
          }
        } else if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
      anchor.current = id;
    },
    [order],
  );
  const setMany = useCallback((ids: number[], on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }, []);
  const clear = useCallback(() => {
    anchor.current = null;
    setSelected(new Set());
  }, []);
  return { selected, toggle, setMany, clear };
}

/** the checkbox on a card or a table row */
export function SelectBox({
  checked,
  label,
  onToggle,
  className = '',
}: {
  checked: boolean;
  label: string;
  onToggle: (shift: boolean) => void;
  className?: string;
}) {
  return (
    <input
      type="checkbox"
      className={`${css.check} ${className}`}
      aria-label={label}
      checked={checked}
      onClick={(event) => {
        event.stopPropagation();
        // the change handler doesn't see the shift key
        onToggle(event.shiftKey);
      }}
      onChange={() => undefined}
    />
  );
}

// --- the run -------------------------------------------------------------------

export interface BulkRun {
  label: string;
  total: number;
  done: number;
  skipped: number;
  failed: number;
  /** the first few failures, named */
  errors: string[];
  running: boolean;
  stopped: boolean;
}

let current: BulkRun | null = null;
let stopRequested = false;
const listeners = new Set<() => void>();

function publish(next: BulkRun | null) {
  current = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useBulkRun(): BulkRun | null {
  return useSyncExternalStore(subscribe, () => current);
}

export function stopBulkRun() {
  stopRequested = true;
}

export function dismissBulkRun() {
  if (!current?.running) publish(null);
}

type Outcome = 'done' | 'skipped';

/**
 * Runs ``work`` over ``ids``, ``parallel`` at a time, publishing progress.
 * Stop lets what is in flight finish and starts nothing new.
 */
export async function runBulk(
  label: string,
  ids: number[],
  work: (id: number) => Promise<Outcome>,
  parallel = 3,
): Promise<BulkRun> {
  stopRequested = false;
  let run: BulkRun = {
    label,
    total: ids.length,
    done: 0,
    skipped: 0,
    failed: 0,
    errors: [],
    running: true,
    stopped: false,
  };
  publish(run);
  const queue = [...ids];
  const worker = async () => {
    while (queue.length && !stopRequested) {
      const id = queue.shift()!;
      try {
        const outcome = await work(id);
        run = {
          ...run,
          done: run.done + (outcome === 'done' ? 1 : 0),
          skipped: run.skipped + (outcome === 'skipped' ? 1 : 0),
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        run = {
          ...run,
          failed: run.failed + 1,
          errors: run.errors.length < 3 ? [...run.errors, message] : run.errors,
        };
      }
      publish(run);
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, parallel) }, worker));
  run = { ...run, running: false, stopped: stopRequested };
  publish(run);
  return run;
}

const JOB_POLL_MS = 1500;
const JOB_LIMIT_MS = 15 * 60 * 1000;

/** a background job to its end, so the next artist starts after this one */
export async function waitForJob(jobId: string, poll = JOB_POLL_MS): Promise<void> {
  const started = Date.now();
  for (;;) {
    const state = await fetchLibraryV2JobStatus(jobId);
    if (!state.running) {
      if (state.error) throw new Error(state.error);
      return;
    }
    if (Date.now() - started > JOB_LIMIT_MS) throw new Error('Still running, left to finish');
    await new Promise((resolve) => setTimeout(resolve, poll));
  }
}

async function confirmed(options: {
  title: string;
  message: string;
  confirmText: string;
  destructive?: boolean;
}): Promise<boolean> {
  if (typeof window.showConfirmDialog !== 'function') return false;
  return Boolean(await window.showConfirmDialog(options));
}

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

// --- the bar -------------------------------------------------------------------

function isAdminProfile(): boolean {
  return getShellProfileContext()?.isAdmin === true;
}

export function ArtistBulkBar({
  selection,
  pageArtists,
  totalMatching,
  filters,
  canWrite,
  canWish,
}: {
  selection: ArtistSelection;
  pageArtists: LibraryV2ArtistSummary[];
  /** how many artists the list's filters match, every page */
  totalMatching: number;
  /** the list's own filters, for "select all that match" */
  filters: Parameters<typeof fetchLibraryV2ArtistIds>[0];
  canWrite: boolean;
  canWish: boolean;
}) {
  const queryClient = useQueryClient();
  const run = useBulkRun();
  const [profile, setProfile] = useState('');
  const [loadingAll, setLoadingAll] = useState(false);
  const profiles = useQuery({ ...libraryV2QualityProfilesQueryOptions(), enabled: canWrite });
  const count = selection.selected.size;
  const pageIds = pageArtists.map((a) => a.id);
  const allOnPage = pageIds.length > 0 && pageIds.every((id) => selection.selected.has(id));
  const busy = Boolean(run?.running);
  const known = new Map(pageArtists.map((a) => [a.id, a] as const));

  useEffect(() => {
    // a finished run's note goes once the selection does
    if (count === 0 && run && !run.running) {
      const timer = window.setTimeout(dismissBulkRun, 6000);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [count, run]);

  if (count === 0 && !run) return null;

  const ids = () => [...selection.selected];
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    void queryClient.invalidateQueries({ queryKey: LOCK_QUERY_KEY });
  };
  const finish = (result: BulkRun) => {
    refresh();
    const parts = [`${result.label}: ${plural(result.done, 'artist')} done`];
    if (result.skipped) parts.push(`${result.skipped} skipped`);
    if (result.failed) parts.push(`${result.failed} failed`);
    window.showToast?.(parts.join(', '), result.failed ? 'warning' : 'success');
  };

  const watch = async (on: boolean) => {
    const list = ids();
    if (
      !on &&
      !(await confirmed({
        title: 'Remove from Watchlist',
        message: `Take ${plural(list.length, 'artist')} off your Watchlist? New releases from them stop being added to your Wishlist. Nothing on disk is touched.`,
        confirmText: 'Remove',
        destructive: true,
      }))
    )
      return;
    finish(
      await runBulk(on ? 'Add to Watchlist' : 'Remove from Watchlist', list, async (id) => {
        const row = known.get(id);
        if (row && row.monitored === on) return 'skipped';
        await setLibraryV2Monitored('artists', id, on);
        return 'done';
      }),
    );
  };

  const search = async () => {
    const list = ids();
    if (
      list.length > 10 &&
      !(await confirmed({
        title: 'Automatic Search',
        message: `Search for the missing tracks and upgrades of ${plural(list.length, 'artist')}? They go one at a time, so this can take a while. You can keep using the page.`,
        confirmText: 'Search',
      }))
    )
      return;
    finish(
      await runBulk(
        'Automatic Search',
        list,
        async (id) => {
          await waitForJob(await startLibraryV2ScopedSearch('artists', id));
          return 'done';
        },
        1,
      ),
    );
  };

  const refreshDiscography = async () => {
    const list = ids();
    if (
      list.length > 5 &&
      !(await confirmed({
        title: 'Refresh discographies',
        message: `Fetch the full discography of ${plural(list.length, 'artist')} from your metadata sources? One artist at a time, a minute or so each for a big catalogue.`,
        confirmText: 'Refresh',
      }))
    )
      return;
    finish(
      await runBulk(
        'Refresh discography',
        list,
        async (id) => {
          await waitForJob(await startLibraryV2DiscographyRefresh(id));
          return 'done';
        },
        1,
      ),
    );
  };

  const lock = async (on: boolean) => {
    const list = ids();
    if (
      !on &&
      !(await confirmed({
        title: 'Unlock',
        message: `Unlock ${plural(list.length, 'artist')}? Automatic jobs may change their details and files again.`,
        confirmText: 'Unlock',
        destructive: true,
      }))
    )
      return;
    // one request for the lot: the lock route takes a list
    try {
      const updated = await setLocksBulk('artist', list, on);
      refresh();
      window.showToast?.(`${on ? 'Locked' : 'Unlocked'} ${plural(updated, 'artist')}`, 'success');
    } catch (error) {
      window.showToast?.(
        error instanceof Error ? error.message : 'Could not change the locks',
        'error',
      );
    }
  };

  const applyProfile = async () => {
    const chosen = (profiles.data ?? []).find((p) => String(p.id) === profile);
    if (!chosen) return;
    const list = ids();
    if (
      !(await confirmed({
        title: 'Change quality profile',
        message: `Give ${plural(list.length, 'artist')} the “${chosen.name}” quality profile? Their albums and tracks follow it unless they have their own.`,
        confirmText: 'Change',
      }))
    )
      return;
    setProfile('');
    finish(
      await runBulk('Quality profile', list, async (id) => {
        const row = known.get(id);
        if (row && row.quality_profile_explicit && row.quality_profile_id === chosen.id)
          return 'skipped';
        await setLibraryV2QualityProfile('artists', id, chosen.id);
        return 'done';
      }),
    );
  };

  const selectAllMatching = async () => {
    setLoadingAll(true);
    try {
      const all = await fetchLibraryV2ArtistIds(filters);
      selection.setMany(all, true);
    } catch (error) {
      window.showToast?.(
        error instanceof Error ? error.message : 'Could not select every artist',
        'error',
      );
    } finally {
      setLoadingAll(false);
    }
  };

  const admin = isAdminProfile();
  return (
    <div className={css.bar} role="region" aria-label="Selected artists">
      <div className={css.lead}>
        {count > 0 ? (
          <strong>{plural(count, 'artist')} selected</strong>
        ) : (
          <strong>Nothing selected</strong>
        )}
        <button
          type="button"
          className={css.link}
          onClick={() => selection.setMany(pageIds, !allOnPage)}
        >
          {allOnPage ? 'Unselect this page' : 'Select this page'}
        </button>
        {totalMatching > count ? (
          <button
            type="button"
            className={css.link}
            disabled={loadingAll}
            onClick={() => void selectAllMatching()}
          >
            {loadingAll ? 'Selecting…' : `Select all ${totalMatching.toLocaleString()}`}
          </button>
        ) : null}
        {count > 0 ? (
          <button type="button" className={css.link} onClick={selection.clear}>
            Clear
          </button>
        ) : null}
      </div>

      {run ? (
        <div className={css.progress} role="status" aria-live="polite">
          <span>
            {run.label}: {(run.done + run.skipped + run.failed).toLocaleString()} of{' '}
            {run.total.toLocaleString()}
            {run.skipped ? `, ${run.skipped} skipped` : ''}
            {run.failed ? `, ${run.failed} failed` : ''}
            {!run.running ? (run.stopped ? ', stopped' : ', finished') : ''}
          </span>
          <span className={css.track} aria-hidden="true">
            <span
              className={css.fill}
              style={{
                width: `${run.total ? ((run.done + run.skipped + run.failed) * 100) / run.total : 0}%`,
              }}
            />
          </span>
          {run.errors.length ? (
            <span className={css.errors} title={run.errors.join('\n')}>
              {run.errors[0]}
            </span>
          ) : null}
          {run.running ? (
            <button type="button" className={css.link} onClick={stopBulkRun}>
              Stop
            </button>
          ) : (
            <button type="button" className={css.link} onClick={dismissBulkRun}>
              Dismiss
            </button>
          )}
        </div>
      ) : null}

      {count > 0 ? (
        <div className={css.actions}>
          <button
            type="button"
            className={styles.btnGhost}
            disabled={busy || !canWish}
            onClick={() => void watch(true)}
          >
            Add to Watchlist
          </button>
          <button
            type="button"
            className={styles.btnGhost}
            disabled={busy || !canWish}
            onClick={() => void watch(false)}
          >
            Remove from Watchlist
          </button>
          <button
            type="button"
            className={styles.btnGhost}
            disabled={busy || !canWish}
            title="Search for missing tracks and upgrades, one artist at a time"
            onClick={() => void search()}
          >
            Search missing
          </button>
          <button
            type="button"
            className={styles.btnGhost}
            disabled={busy || !canWrite}
            onClick={() => void refreshDiscography()}
          >
            Refresh discography
          </button>
          {admin ? (
            <>
              <button
                type="button"
                className={styles.btnGhost}
                disabled={busy || !canWrite}
                onClick={() => void lock(true)}
              >
                Lock
              </button>
              <button
                type="button"
                className={styles.btnGhost}
                disabled={busy || !canWrite}
                onClick={() => void lock(false)}
              >
                Unlock
              </button>
            </>
          ) : null}
          {canWrite ? (
            <select
              aria-label="Quality profile for the selected artists"
              className={styles.select}
              disabled={busy}
              value={profile}
              onChange={(e) => setProfile(e.target.value)}
            >
              <option value="">Quality profile…</option>
              {(profiles.data ?? []).map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.name}
                </option>
              ))}
            </select>
          ) : null}
          {profile ? (
            <button
              type="button"
              className={styles.btnGhost}
              disabled={busy}
              onClick={() => void applyProfile()}
            >
              Apply
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export type { LibraryV2ListFilters };
