/**
 * Update File Locations: the music library folder moved, the records follow.
 *
 * Check reads every record and groups the ones whose file is now somewhere
 * else by old and new folder ("12,340 files: /app/Transfer to /music"), and
 * counts what it would leave alone and why. Update moves the records of the
 * ticked folders (never a file) in the background, and every run is listed
 * with an Undo. A library scan does the same on its own when the old folder is
 * plainly gone; those runs are listed here too.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { FileLocationGroup, FileLocationRun, FileLocationState } from '../-file-locations.api';

import {
  fetchFileLocationStatus,
  leftAloneSummary,
  startFileLocationApply,
  startFileLocationPreview,
  stopFileLocationJob,
  undoFileLocationRun,
} from '../-file-locations.api';
import { usePolledStatus } from '../-tools.polling';
import styles from './file-locations-card.module.css';
import { ToolCard, ToolProgress } from './tool-card';

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

const isRunning = (state: FileLocationState) => state.status === 'running';
const groupKey = (g: Pick<FileLocationGroup, 'old_root' | 'new_root'>) =>
  `${g.old_root}\u0000${g.new_root}`;
const files = (n: number) => `${n.toLocaleString()} file${n === 1 ? '' : 's'}`;

function phaseText(state: FileLocationState | null): string {
  if (!state) return 'Ready to check';
  if (state.status === 'running') {
    if (state.action === 'undo') return 'Putting records back…';
    return state.action === 'apply' ? 'Updating file locations…' : 'Checking file locations…';
  }
  if (state.status === 'error') return `Failed: ${state.error || 'unknown error'}`;
  if (state.status === 'done' && state.result) {
    if (state.action === 'undo') {
      return `Undone: ${files(state.result.restored ?? 0)} put back`;
    }
    if (state.action === 'apply') {
      return `${state.result.stopped ? 'Stopped' : 'Done'}: ${files(state.result.repointed)} updated`;
    }
    const n = state.preview?.counts.repoint ?? 0;
    return n ? `${files(n)} can be updated` : 'Every file is where its record says';
  }
  return 'Ready to check';
}

function runLabel(run: FileLocationRun): string {
  const when = (run.finished_at || run.started_at || '').slice(0, 16).replace('T', ' ');
  const who =
    run.kind === 'scan' ? 'library scan' : run.kind === 'watch' ? 'folder watch' : 'Tools';
  const what = run.status === 'undone' ? 'undone' : files(run.repointed);
  return `${when} · ${who} · ${what}`;
}

export function FileLocationsCard() {
  // every folder pair is ticked until the user unticks it
  const [unticked, setUnticked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const seenPreview = useRef<FileLocationState['preview']>(null);

  const poll = usePolledStatus<FileLocationState>({
    fetcher: fetchFileLocationStatus,
    isRunning,
    intervalMs: 1500,
  });
  const state = poll.state;
  const running = Boolean(state && isRunning(state));
  const preview = state?.preview ?? null;

  // a fresh check starts with every folder pair ticked again
  useEffect(() => {
    if (preview && preview !== seenPreview.current) {
      if (seenPreview.current) setUnticked(new Set());
      seenPreview.current = preview;
    }
  }, [preview]);

  const start = useCallback(
    async (action: () => Promise<{ ok: boolean; error?: string }>, started: string) => {
      setBusy(true);
      try {
        const result = await action();
        if (!result.ok) {
          toast(`Error: ${result.error}`, 'error');
          return;
        }
        toast(started, 'success');
        await poll.refresh();
        poll.arm();
      } finally {
        setBusy(false);
      }
    },
    [poll],
  );

  const onCheck = () => void start(startFileLocationPreview, 'Checking file locations');

  const picked = (preview?.groups ?? []).filter((g) => !unticked.has(groupKey(g)));
  const pickedCount = picked.reduce((sum, g) => sum + g.count, 0);

  const onApply = async () => {
    if (!picked.length) return;
    const ok = await window.showConfirmDialog?.({
      title: 'Update File Locations',
      message:
        `Point ${files(pickedCount)} at where they are now?\n\n` +
        picked.map((g) => `${g.old_root} → ${g.new_root}`).join('\n') +
        '\n\nOnly the library records change. No file is moved, and the run can be undone here.',
      confirmText: 'Update',
    });
    if (!ok) return;
    await start(
      () =>
        startFileLocationApply(picked.map(({ old_root, new_root }) => ({ old_root, new_root }))),
      'Updating file locations',
    );
  };

  const onUndo = async (run: FileLocationRun) => {
    const ok = await window.showConfirmDialog?.({
      title: 'Undo File Locations',
      message: `Put the ${files(run.repointed)} from this run back to the locations they had before?`,
      confirmText: 'Undo',
    });
    if (!ok) return;
    await start(() => undoFileLocationRun(run.id), 'Undoing');
  };

  const toggle = (key: string) =>
    setUnticked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const total = state?.total ?? 0;
  const checked = state?.checked ?? 0;
  const percent = running ? (total > 0 ? (checked / total) * 100 : 0) : state?.result ? 100 : 0;
  const leftAlone = preview ? leftAloneSummary(preview.counts) : [];
  const leftAloneCount = preview
    ? Object.entries(preview.counts)
        .filter(([key]) => key !== 'repoint' && key !== 'present')
        .reduce((sum, [, n]) => sum + (n ?? 0), 0)
    : null;
  const runs = (state?.runs ?? []).slice(0, 4);
  const lastRun = runs.find((run) => run.status !== 'undone');

  return (
    <ToolCard
      id="file-locations-card"
      icon="transfer"
      title="Update File Locations"
      info={
        <>
          Moved your music library folder? This points the library&rsquo;s records at where the
          files are now, so the old folder stops showing up. Only a file found in one place, the
          same size, under a library folder, is updated. Files are never moved, and every run can be
          undone.
        </>
      }
      stats={[
        { label: 'Can Update:', value: preview ? (preview.counts.repoint ?? 0) : '—' },
        { label: 'Left Alone:', value: leftAloneCount ?? '—' },
        { label: 'Last Run:', value: lastRun ? lastRun.repointed : 'Never' },
      ]}
      controls={
        <>
          {running ? (
            <button type="button" onClick={() => void start(stopFileLocationJob, 'Stopping')}>
              Stop
            </button>
          ) : (
            <button type="button" id="file-locations-check" disabled={busy} onClick={onCheck}>
              Check Locations
            </button>
          )}
          {preview && preview.groups.length > 0 && !running ? (
            <button
              type="button"
              id="file-locations-apply"
              disabled={busy || !picked.length}
              onClick={() => void onApply()}
            >
              Update {files(pickedCount)}
            </button>
          ) : null}
        </>
      }
      progress={
        <ToolProgress
          phase={phaseText(state)}
          details={
            running || state?.result
              ? `${checked.toLocaleString()} / ${total.toLocaleString()} records`
              : ''
          }
          percent={percent}
          phaseId="file-locations-phase"
          barId="file-locations-bar"
          detailsId="file-locations-details"
        />
      }
    >
      {preview && preview.groups.length > 0 ? (
        <ul className={styles.groups} aria-label="Folders that moved">
          {preview.groups.map((group) => {
            const key = groupKey(group);
            const example = group.examples?.[0];
            return (
              <li key={key} className={styles.group}>
                <label className={styles.groupLabel}>
                  <input
                    type="checkbox"
                    checked={!unticked.has(key)}
                    disabled={running}
                    onChange={() => toggle(key)}
                  />
                  <span>
                    <strong>{files(group.count)}</strong>
                    <span className={styles.roots}>
                      {group.old_root || '(no folder)'} → {group.new_root}
                    </span>
                    {example ? (
                      <span className={styles.example} title={`${example.old}\n${example.new}`}>
                        e.g. {example.new}
                      </span>
                    ) : null}
                  </span>
                </label>
              </li>
            );
          })}
          {preview.other_groups > 0 ? (
            <li className={styles.note}>
              and {files(preview.other_groups)} in other folders: update these first, then check
              again
            </li>
          ) : null}
        </ul>
      ) : null}
      {leftAlone.length > 0 ? (
        <p className={styles.note}>Left alone: {leftAlone.join(' · ')}</p>
      ) : null}
      {runs.length > 0 ? (
        <ul className={styles.runs} aria-label="Recent runs">
          {runs.map((run) => (
            <li key={run.id} className={styles.run}>
              <span>{runLabel(run)}</span>
              {run.status !== 'undone' && run.status !== 'running' && run.repointed > 0 ? (
                <button
                  type="button"
                  className={styles.undo}
                  disabled={running || busy}
                  onClick={() => void onUndo(run)}
                >
                  Undo
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </ToolCard>
  );
}
