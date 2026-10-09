/**
 * Match & import, several at once.
 *
 * Every download still needs its own match, so this is a review list, not an
 * auto-import: SoulSync fills in its best guess for each one, the user fixes
 * the wrong ones (Change opens the normal match window in pick-only mode) and
 * unticks anything to leave alone, then one Import sends them in turn.
 */

import { useEffect, useRef, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';

import { AdlMatchModal, guessMatch, type MatchTarget, type PickedMatch } from './adl-match-modal';
import styles from './adl-match.module.css';

type RowStatus = 'guessing' | 'ready' | 'none' | 'importing' | 'done' | 'failed';

interface Row {
  target: MatchTarget;
  status: RowStatus;
  pick: PickedMatch | null;
  include: boolean;
  error: string;
}

/** guesses run a few at a time: each is a catalogue search */
const GUESS_CONCURRENCY = 3;

function initials(text: string): string {
  return text
    .split(/\s+/)
    .filter((w) => /^[A-Za-z0-9]/.test(w))
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join('');
}

export function AdlBulkMatchModal({
  targets,
  onClose,
  onFinished,
}: {
  /** null when closed */
  targets: MatchTarget[] | null;
  onClose: () => void;
  /** after an import run, with how many went through */
  onFinished: (imported: number) => void;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [changing, setChanging] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [ran, setRan] = useState(false);
  // a reopen with a new selection must not let the old guesses land in it
  const runId = useRef(0);

  useEffect(() => {
    if (!targets) return;
    const id = ++runId.current;
    setRows(
      targets.map((target) => ({
        target,
        status: 'guessing',
        pick: null,
        include: true,
        error: '',
      })),
    );
    setChanging(null);
    setRunning(false);
    setRan(false);
    let next = 0;
    const worker = async () => {
      while (next < targets.length) {
        const index = next++;
        const pick = await guessMatch(targets[index]).catch(() => null);
        if (id !== runId.current) return;
        setRows((prev) =>
          prev.map((row, i) =>
            i === index && row.status === 'guessing'
              ? { ...row, pick, status: pick ? 'ready' : 'none', include: Boolean(pick) }
              : row,
          ),
        );
      }
    };
    for (let i = 0; i < Math.min(GUESS_CONCURRENCY, targets.length); i++) void worker();
  }, [targets]);

  const update = (index: number, patch: Partial<Row>) =>
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const sendable = rows.filter((row) => row.include && row.pick && row.status === 'ready');
  const guessing = rows.some((row) => row.status === 'guessing');

  const importAll = async () => {
    if (running || sendable.length === 0) return;
    setRunning(true);
    let imported = 0;
    for (let index = 0; index < rows.length; index++) {
      const row = rows[index];
      if (!row.include || !row.pick || row.status !== 'ready') continue;
      update(index, { status: 'importing', error: '' });
      const outcome = await row.pick.hit
        .submit(row.target, { season: row.pick.season, episode: row.pick.episode })
        .catch((err: unknown) => ({
          ok: false,
          error: err instanceof Error ? err.message : 'The match failed.',
        }));
      if (outcome.ok) imported++;
      update(index, outcome.ok ? { status: 'done' } : { status: 'failed', error: outcome.error });
    }
    setRunning(false);
    setRan(true);
    onFinished(imported);
  };

  const failed = rows.filter((row) => row.status === 'failed').length;
  const done = rows.filter((row) => row.status === 'done').length;
  const changingRow = changing != null ? rows[changing] : null;

  return (
    <>
      <DialogFrame
        open={targets !== null && changing === null}
        onOpenChange={(open) => {
          if (!open && !running) onClose();
        }}
        className={`${styles.popup} ${styles.bulkPopup}`}
      >
        <DialogHeader title={`Match & import ${rows.length} downloads`} closeLabel="Close">
          <span className={styles.eyebrow}>Match &amp; import</span>
          <span className={styles.raw}>
            {guessing
              ? 'Finding a match for each one…'
              : 'Check each match. Change the wrong ones, untick anything to leave alone.'}
          </span>
        </DialogHeader>
        <DialogBody>
          <ul className={styles.bulkList} aria-label="Downloads to match">
            {rows.map((row, index) => {
              const settled = row.status === 'done' || row.status === 'importing';
              return (
                <li
                  key={`${row.target.client}:${row.target.id}`}
                  className={styles.bulkRow}
                  data-status={row.status}
                >
                  <input
                    type="checkbox"
                    className={styles.bulkCheck}
                    aria-label={`Import ${row.target.name}`}
                    checked={row.include && Boolean(row.pick)}
                    disabled={!row.pick || settled || running}
                    onChange={(event) => update(index, { include: event.target.checked })}
                  />
                  {row.pick?.hit.image ? (
                    <img className={styles.art} src={row.pick.hit.image} alt="" />
                  ) : (
                    <span className={styles.art} aria-hidden="true">
                      {row.pick ? initials(row.pick.hit.title) : '?'}
                    </span>
                  )}
                  <span className={styles.hitText}>
                    <span className={styles.hitTitle}>
                      {row.status === 'guessing'
                        ? 'Finding a match…'
                        : row.pick
                          ? row.pick.hit.title
                          : 'No match found'}
                    </span>
                    {row.pick?.hit.meta ? (
                      <span className={styles.hitMeta}>{row.pick.hit.meta}</span>
                    ) : null}
                    <span className={styles.bulkRaw} title={row.target.name}>
                      {row.target.name}
                    </span>
                    {row.status === 'failed' ? (
                      <span className={styles.bulkError} role="alert">
                        {row.error}
                      </span>
                    ) : null}
                  </span>
                  {row.status === 'done' ? (
                    <span className={styles.bulkDone}>Matched</span>
                  ) : row.status === 'importing' ? (
                    <span className={styles.bulkWorking}>Matching…</span>
                  ) : (
                    <button
                      type="button"
                      className={styles.secondary}
                      disabled={row.status === 'guessing' || running}
                      onClick={() => setChanging(index)}
                    >
                      {row.pick ? 'Change' : 'Find'}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </DialogBody>
        <DialogFooter>
          {ran ? (
            <span className={styles.bulkSummary}>
              {done} matched{failed ? `, ${failed} refused` : ''}. Each imports once its download is
              complete.
            </span>
          ) : null}
          {ran && sendable.length === 0 ? (
            <button type="button" className={styles.primary} onClick={onClose}>
              Done
            </button>
          ) : (
            <>
              <button type="button" className={styles.ghost} onClick={onClose} disabled={running}>
                {ran ? 'Close' : 'Cancel'}
              </button>
              <button
                type="button"
                className={styles.primary}
                disabled={running || sendable.length === 0}
                onClick={() => void importAll()}
              >
                {running
                  ? 'Matching…'
                  : sendable.length
                    ? `Import ${sendable.length}`
                    : 'Nothing picked'}
              </button>
            </>
          )}
        </DialogFooter>
      </DialogFrame>
      <AdlMatchModal
        target={changingRow ? changingRow.target : null}
        onClose={() => setChanging(null)}
        onMatched={() => setChanging(null)}
        onPick={(pick) => {
          if (changing != null) {
            // a refused row picked again gets another go
            update(changing, { pick, status: 'ready', include: true, error: '' });
          }
          setChanging(null);
        }}
      />
    </>
  );
}
