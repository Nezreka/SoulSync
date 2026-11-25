/**
 * Filled Tags: what SoulSync wrote into files on its own.
 *
 * With "Fill empty tags automatically" on, a tag that is blank in a file and
 * known to the library is filled (a genre enrichment found, a disc total, a
 * year). This card says how much was filled this week, lists the runs, opens a
 * run to show each file's tags before and after, and undoes a file or a whole
 * run. An undone tag is never filled again; its finding comes back instead.
 */

import { useCallback, useState } from 'react';

import type { TagFillFile, TagFillRun, TagFillSummary } from '../-tag-fills.api';

import {
  MODE_WORDS,
  fetchTagFillFiles,
  fetchTagFills,
  fileName,
  fillWords,
  runWords,
  startTagFill,
  undoTagFill,
  undoTagFillRun,
} from '../-tag-fills.api';
import { usePolledStatus } from '../-tools.polling';
import { TagChangeTable } from './tag-changes';
import styles from './tag-fills-card.module.css';
import { ToolCard, ToolProgress } from './tool-card';

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

const undoing = (state: TagFillSummary) => Boolean(state.undo?.running);
const files = (n: number) => `${n.toLocaleString()} file${n === 1 ? '' : 's'}`;

export function TagFillsCard() {
  const poll = usePolledStatus<TagFillSummary>({
    fetcher: fetchTagFills,
    isRunning: undoing,
    intervalMs: 1500,
  });
  const state = poll.state;
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [opened, setOpened] = useState<TagFillFile[] | null>(null);

  const showRun = useCallback(async (run: string | null) => {
    setOpen(run);
    setOpened(null);
    if (!run) return;
    setOpened((await fetchTagFillFiles(run)) ?? []);
  }, []);

  const mode = state?.mode ?? 'fill';
  const running = Boolean(state && undoing(state));
  const runs = (state?.runs ?? []).slice(0, 6);

  const onFillNow = async () => {
    setBusy(true);
    try {
      const result = await startTagFill();
      if (result.ok) toast('Filling empty tags in the background', 'success');
      else toast(`Error: ${result.error}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  const onUndoRun = async (run: TagFillRun) => {
    const ok = await window.showConfirmDialog?.({
      title: 'Undo Filled Tags',
      message:
        `Take the tags this run filled back out of ${files(run.files - run.undone)}?\n\n` +
        'A tag that was changed since stays as it is. What is undone is not filled again: ' +
        'it shows up as a Library Re-tag finding instead.',
      confirmText: 'Undo',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const result = await undoTagFillRun(run.batch_id);
      if (!result.ok) {
        toast(`Error: ${result.error}`, 'error');
        return;
      }
      toast('Undoing', 'success');
      await poll.refresh();
      poll.arm();
    } finally {
      setBusy(false);
    }
  };

  const onUndoFile = async (file: TagFillFile) => {
    setBusy(true);
    try {
      const result = await undoTagFill(file.id);
      if (!result.ok) {
        toast(`Error: ${result.error}`, 'error');
        return;
      }
      toast(
        result.kept?.length
          ? `Undone. Kept ${result.kept.join(', ')}: changed since it was filled`
          : 'Undone',
        'success',
      );
      await poll.refresh();
      if (open) setOpened((await fetchTagFillFiles(open)) ?? []);
    } finally {
      setBusy(false);
    }
  };

  const undo = state?.undo;
  const phase = running
    ? `Undoing… ${(undo?.done ?? 0).toLocaleString()} / ${(undo?.total ?? 0).toLocaleString()} files`
    : mode === 'off'
      ? 'Off'
      : 'Fills blank tags in small batches while nothing else runs';

  return (
    <ToolCard
      id="tag-fills-card"
      icon="tags"
      title="Filled Tags"
      info={
        <>
          When the library learns something a file&rsquo;s tags leave blank (a genre, a year, a
          track total), SoulSync writes it in. A tag the file already has is never replaced unless
          the setting is Fill and update. Hand-tagged and locked albums, files still downloading or
          seeding, and read-only files are left alone. Every fill can be undone here, and what is
          undone is not filled again. Set it in Settings &gt; Library &gt; Post-Processing.
        </>
      }
      stats={[
        {
          label: 'This Week:',
          value: state ? fillWords(state.fields, state.files, state.covers) : '—',
          valueId: 'tag-fills-week',
        },
        { label: 'Setting:', value: state ? MODE_WORDS[mode] : '—', valueId: 'tag-fills-mode' },
      ]}
      controls={
        <button
          type="button"
          id="tag-fills-now"
          disabled={busy || running || mode === 'off'}
          onClick={() => void onFillNow()}
        >
          Fill Now
        </button>
      }
      progress={
        <ToolProgress
          phase={phase}
          details=""
          percent={running && undo?.total ? ((undo.done ?? 0) / undo.total) * 100 : 0}
          phaseId="tag-fills-phase"
          barId="tag-fills-bar"
          detailsId="tag-fills-details"
        />
      }
    >
      {mode === 'off' && state ? (
        <p className={styles.note}>
          Blank tags are filed as Library Re-tag findings for you to apply. Turn on Fill empty tags
          automatically in Settings &gt; Library &gt; Post-Processing to have them filled.
        </p>
      ) : null}
      {runs.length > 0 ? (
        <ul className={styles.runs} aria-label="Recent fills">
          {runs.map((run) => {
            const shown = open === run.batch_id;
            const left = run.files - run.undone;
            return (
              <li key={run.batch_id} className={styles.run}>
                <div className={styles.runHead}>
                  <span>{runWords(run)}</span>
                  <span className={styles.actions}>
                    <button
                      type="button"
                      className={styles.pill}
                      aria-expanded={shown}
                      onClick={() => void showRun(shown ? null : run.batch_id)}
                    >
                      {shown ? 'Hide' : 'Show'}
                    </button>
                    {left > 0 ? (
                      <button
                        type="button"
                        className={styles.pill}
                        disabled={busy || running}
                        onClick={() => void onUndoRun(run)}
                      >
                        Undo
                      </button>
                    ) : null}
                  </span>
                </div>
                {shown ? (
                  opened === null ? (
                    <p className={styles.note}>Loading…</p>
                  ) : (
                    <ul className={styles.files} aria-label="Files in this run">
                      {opened.map((file) => (
                        <li key={file.id} className={styles.file}>
                          <div className={styles.runHead}>
                            <span className={styles.name} title={file.path || ''}>
                              {fileName(file.path)}
                            </span>
                            {file.status === 'filled' ? (
                              <button
                                type="button"
                                className={styles.pill}
                                disabled={busy || running}
                                onClick={() => void onUndoFile(file)}
                              >
                                Undo
                              </button>
                            ) : (
                              <span className={styles.done}>Undone</span>
                            )}
                          </div>
                          <TagChangeTable
                            rows={file.fields}
                            beforeLabel="Was"
                            afterLabel="Filled"
                          />
                          {file.cover ? (
                            <p className={styles.note}>
                              Cover added from the album&rsquo;s artwork
                            </p>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : state && mode !== 'off' ? (
        <p className={styles.note}>Nothing filled yet.</p>
      ) : null}
    </ToolCard>
  );
}
