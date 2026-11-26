/**
 * The pill and panel for a bulk Sync / Sync & download run (#2309, #2320).
 *
 * Both read the run from its store, not from a tab, so the panel can be
 * hidden and brought back while the run goes on. The pill sits in the page
 * header whenever a run is going or has just ended; clicking it opens the
 * same panel. Escape, the close button and a click outside all just hide it.
 */

import { useEffect, useRef, useState } from 'react';

import { bulkSummary } from '../-sync.bulk-run';
import {
  bulkPillLabel,
  dismissBulkRun,
  hideBulkPanel,
  openBulkPanel,
  stopBulkRun,
  useBulkRun,
} from '../-sync.bulk-store';
import styles from './playlists.module.css';
import { usePopoverDismiss } from './use-popover-dismiss';

const STATE_TEXT = {
  waiting: 'Waiting',
  running: 'Syncing…',
  done: 'Done',
  failed: 'Failed',
  stopped: 'Not started',
} as const;

export function BulkRunHost({ onFinished }: { onFinished?: () => void }) {
  const { run, open, justFinished, finishedCount } = useBulkRun();
  const [pill, setPill] = useState<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);

  // reload the list once per finished run, not on mount for an old one
  const seen = useRef(finishedCount);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;
  useEffect(() => {
    if (finishedCount === seen.current) return;
    seen.current = finishedCount;
    finishedRef.current?.();
  }, [finishedCount]);

  if (!run) return null;
  const showPill = run.running || justFinished || open;

  return (
    <>
      {showPill && (
        <button
          ref={setPill}
          type="button"
          className={`pl-bulk-pill ${styles.bulkPill}`}
          data-state={run.running ? 'running' : 'done'}
          aria-expanded={open}
          aria-label={`${bulkPillLabel(run)}. Show progress`}
          onClick={() => (open ? hideBulkPanel() : openBulkPanel())}
        >
          {run.running && <span className={styles.spin} aria-hidden="true" />}
          {bulkPillLabel(run)}
        </button>
      )}
      {open && <BulkPanel panelRef={panelRef} anchor={pill} />}
    </>
  );
}

function BulkPanel({
  panelRef,
  anchor,
}: {
  panelRef: React.RefObject<HTMLElement | null>;
  anchor: HTMLElement | null;
}) {
  const { run } = useBulkRun();
  usePopoverDismiss({ ref: panelRef, anchor, onClose: hideBulkPanel });
  if (!run) return null;
  return (
    <section
      ref={panelRef}
      className={styles.bulkPanel}
      role="dialog"
      aria-label="Syncing the selected playlists"
    >
      <header className={styles.bulkHead}>
        <b aria-live="polite">
          {run.mode === 'sync' ? 'Sync' : 'Sync & download'} · {bulkSummary(run)}
        </b>
        {run.running ? (
          <button
            type="button"
            className={styles.bulkBtn}
            disabled={run.stopping}
            onClick={stopBulkRun}
          >
            {run.stopping ? 'Stopping after this one…' : 'Stop after this one'}
          </button>
        ) : null}
        <button
          type="button"
          className={styles.bulkBtn}
          onClick={dismissBulkRun}
          title={run.running ? 'Hide this. The sync keeps going.' : undefined}
        >
          {run.running ? 'Hide' : 'Close'}
        </button>
      </header>
      <ol className={styles.bulkList}>
        {run.items.map((item) => (
          <li key={item.id} data-state={item.state}>
            <span className={styles.bulkName}>{item.name}</span>
            <span className={styles.bulkState}>
              {item.state === 'failed' && item.note
                ? `Failed: ${item.note}`
                : STATE_TEXT[item.state]}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
