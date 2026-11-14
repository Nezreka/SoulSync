/**
 * managing a title's files from its own page. each of these shows what it
 * will do before it does it, and what it did can be put back: a rename or a
 * delete of the picked files, filing a run of files under other episodes,
 * moving the title to another library, and the history of all of it.
 */

import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

import type { FileChange } from '@/features/video-manager/types';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { fetchBulkStatus, fetchChanges, startBulk, undoBatch } from '@/features/video-manager/api';
import {
  ChangeList,
  EpisodesTab,
  MoveDialog,
  PadlockIcon,
} from '@/features/video-manager/manager-extras';

import type { TitleKind } from './types';

import {
  type DeletePreview,
  type RenamePreview,
  bulkFiles,
  fetchDeletePreview,
  fetchRenamePreview,
  setPadlock,
} from './api';
import { formatBytes } from './format';
import { invalidate, lastChange, type Undoable } from './library-store';
import s from './video-library.module.css';

function toast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
  window.showToast?.(message, type);
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function baseName(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** a change was made: say so, and offer to put it back */
export function announce(change: Omit<Undoable, 'batch'>, batch: string | null | undefined): void {
  if (batch) lastChange.set({ ...change, batch });
}

/* ── rename the picked files ──────────────────────────────────────────────── */

export function RenameDialog({
  kind,
  titleId,
  fileIds,
  scope,
  onClose,
  onDone,
}: {
  kind: TitleKind;
  titleId: number;
  fileIds: number[];
  /** what was picked, in words: "Season 2", "3 episodes" */
  scope: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [plan, setPlan] = useState<RenamePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    let live = true;
    fetchRenamePreview(fileIds)
      .then((p) => live && setPlan(p))
      .catch((e: Error) => live && setError(e.message || 'Couldn’t work out the names'));
    return () => {
      live = false;
    };
    // the picked files are fixed for the life of the dialog
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async () => {
    if (!plan) return;
    setWorking(true);
    try {
      const res = await bulkFiles('rename', fileIds, kind, { titleId });
      const text = res.failed
        ? `Renamed ${res.done}, ${res.failed} couldn’t be renamed`
        : `Renamed ${plural(res.done, 'file')}`;
      toast(text, res.failed ? 'warning' : 'success');
      announce({ kind, id: titleId, text }, res.batch);
      onDone();
    } catch (e) {
      setWorking(false);
      toast((e as Error).message || 'Rename failed', 'error');
    }
  };

  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && onClose()}
      aria-label="Rename to your template"
      className={s.dialog}
    >
      <DialogHeader title="Rename to your template">
        <span className={s.muted}>{scope}</span>
      </DialogHeader>
      <DialogBody>
        {error ? <p className={s.empty}>{error}</p> : null}
        {!plan && !error ? <p className={s.empty}>Working out the names…</p> : null}
        {plan ? (
          <>
            <div className={s.planSummary}>
              <p>
                {plan.to_rename ? (
                  <>
                    <b>{plural(plan.to_rename, 'file')}</b> will be renamed.
                  </>
                ) : (
                  <>Every file here is already named by your template.</>
                )}
                {plan.already_named && plan.to_rename
                  ? ` ${plural(plan.already_named, 'file is', 'files are')} already right.`
                  : ''}
                {plan.unreachable
                  ? ` ${plural(plan.unreachable, 'file')} can’t be reached from here and will be left as ${plan.unreachable === 1 ? 'it is' : 'they are'}.`
                  : ''}
              </p>
            </div>
            <ul className={s.plan}>
              {plan.changes.map((change) => (
                <li key={change.file_id} className={s.planGroup}>
                  <div className={s.planRow} data-role="remove">
                    <span className={s.planVerb}>Now</span>
                    <span className={s.planName} title={change.current}>
                      {baseName(change.current)}
                    </span>
                  </div>
                  <div className={s.planRow} data-role="keep">
                    <span className={s.planVerb}>After</span>
                    <span className={s.planName} title={change.expected}>
                      {baseName(change.expected)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
            {plan.more ? <p className={s.note}>And {plural(plan.more, 'more file')}.</p> : null}
            <p className={s.note}>
              Each file is renamed where it sits, its subtitles move with it, and nothing is
              overwritten. Undo puts the old names back.
            </p>
          </>
        ) : null}
      </DialogBody>
      <DialogFooter>
        <button type="button" className={s.quietBtn} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={s.btnPrimary}
          disabled={!plan?.to_rename || working}
          onClick={() => void run()}
        >
          {working
            ? 'Renaming…'
            : plan?.to_rename
              ? `Rename ${plural(plan.to_rename, 'file')}`
              : 'Nothing to rename'}
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}

/* ── delete the picked files ──────────────────────────────────────────────── */

export function DeleteDialog({
  kind,
  titleId,
  fileIds,
  scope,
  onClose,
  onDone,
}: {
  kind: TitleKind;
  titleId: number;
  fileIds: number[];
  scope: string;
  onClose: () => void;
  /** `emptied`: an episode or the movie has no copy left */
  onDone: (emptied: boolean) => void;
}) {
  const [plan, setPlan] = useState<DeletePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [stop, setStop] = useState(false);

  useEffect(() => {
    let live = true;
    fetchDeletePreview(fileIds)
      .then((p) => live && setPlan(p))
      .catch((e: Error) => live && setError(e.message || 'Couldn’t work out the files'));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reachable = plan ? plan.files - plan.unreachable : 0;
  const run = async () => {
    if (!plan) return;
    setWorking(true);
    try {
      const res = await bulkFiles('delete', fileIds, kind, { titleId, stopHunting: stop });
      const text =
        (res.failed
          ? `Removed ${res.done}, ${res.failed} couldn’t be removed`
          : `Removed ${plural(res.done, 'file')}`) +
        (res.unmonitored
          ? `. ${plural(res.unmonitored, 'episode is', 'episodes are')} no longer hunted`
          : '');
      toast(text, res.failed ? 'warning' : 'success');
      // only a recycled file can come back
      if (plan.recycle) announce({ kind, id: titleId, text }, res.batch);
      onDone(plan.emptied > 0);
    } catch (e) {
      setWorking(false);
      toast((e as Error).message || 'Delete failed', 'error');
    }
  };

  const what = kind === 'movie' ? 'movie' : 'episode';
  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && onClose()}
      aria-label="Delete files"
      className={s.dialog}
    >
      <DialogHeader title={plan && !plan.recycle ? 'Delete for good' : 'Move to the recycle bin'}>
        <span className={s.muted}>{scope}</span>
      </DialogHeader>
      <DialogBody>
        {error ? <p className={s.empty}>{error}</p> : null}
        {!plan && !error ? <p className={s.empty}>Looking at the files…</p> : null}
        {plan ? (
          <>
            <div className={s.planSummary}>
              <p>
                {reachable ? (
                  <>
                    <b>{plural(reachable, 'file')}</b>
                    {plan.size_bytes ? `, ${formatBytes(plan.size_bytes)},` : ''}{' '}
                    {plan.recycle
                      ? `${reachable === 1 ? 'moves' : 'move'} to the recycle bin, and can be put back until it empties.`
                      : `${reachable === 1 ? 'is' : 'are'} deleted permanently. Recycling is turned off in your library settings.`}
                  </>
                ) : (
                  <>Nothing here can be removed from SoulSync.</>
                )}
                {plan.emptied
                  ? ` ${kind === 'movie' ? 'The movie' : plural(plan.emptied, 'episode')} will have no copy left and will show as missing.`
                  : ''}
                {plan.unreachable
                  ? ` ${plural(plan.unreachable, 'file')} only your media server can see will be left alone. Delete ${plan.unreachable === 1 ? 'it' : 'them'} there.`
                  : ''}
              </p>
            </div>
            <ul className={s.plan}>
              {plan.items.map((item) => (
                <li key={item.file_id} className={s.planGroup}>
                  <div className={s.planRow} data-role={item.reachable ? 'remove' : 'skip'}>
                    <span className={s.planVerb}>
                      {item.reachable ? (kind === 'show' ? item.label : 'Remove') : 'Stays'}
                    </span>
                    <span className={s.planName} title={item.filename}>
                      {item.filename}
                    </span>
                    <span className={s.planMeta}>
                      {item.reachable ? formatBytes(item.size_bytes) : 'out of reach'}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
            {plan.more ? <p className={s.note}>And {plural(plan.more, 'more file')}.</p> : null}
            {kind === 'show' && plan.emptied ? (
              <label className={s.switchRow}>
                <input
                  type="checkbox"
                  role="switch"
                  checked={stop}
                  onChange={(e) => setStop(e.target.checked)}
                />
                <span className={s.switchText}>
                  <span className={s.switchTitle}>
                    Stop hunting {plan.emptied === 1 ? `this ${what}` : `these ${what}s`}
                  </span>
                  <span className={s.switchSub}>
                    Left on, a missing episode in a monitored season is fetched again.
                  </span>
                </span>
              </label>
            ) : null}
          </>
        ) : null}
      </DialogBody>
      <DialogFooter>
        <button type="button" className={s.quietBtn} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={s.btnDanger}
          disabled={!plan || !reachable || working}
          onClick={() => void run()}
        >
          {working
            ? 'Removing…'
            : !plan || !reachable
              ? 'Nothing to remove'
              : plan.recycle === false
                ? `Delete ${plural(reachable, 'file')}`
                : `Move ${plural(reachable, 'file')} to the recycle bin`}
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}

/* ── file a run of files under other episodes ─────────────────────────────── */

export function RenumberDialog({
  showId,
  season,
  fileIds,
  onClose,
  onChanged,
}: {
  showId: number;
  /** the season in view on the page */
  season?: number;
  /** the files of the episodes ticked on the page */
  fileIds?: number[];
  onClose: () => void;
  onChanged: () => void;
}) {
  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && onClose()}
      aria-label="Renumber episodes"
      className={s.dialogWide}
    >
      <DialogHeader title="Renumber episodes" />
      <DialogBody>
        <div className={s.borrowed}>
          <EpisodesTab
            showId={showId}
            startSeason={season}
            startPicked={fileIds}
            onChanged={(undoable) => {
              if (undoable)
                announce({ kind: 'show', id: showId, text: undoable.text }, undoable.batch);
              onChanged();
            }}
          />
        </div>
      </DialogBody>
      <DialogFooter>
        <button type="button" className={s.quietBtn} onClick={onClose}>
          Done
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}

/* ── what was done to this title's files ──────────────────────────────────── */

export function TitleHistoryDialog({
  kind,
  id,
  onClose,
  onChanged,
}: {
  kind: TitleKind;
  id: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [changes, setChanges] = useState<FileChange[] | null>(null);
  const [total, setTotal] = useState(0);
  const load = useCallback(() => {
    fetchChanges({ kind, id }, 200)
      .then((out) => {
        setChanges(out.items);
        setTotal(out.total);
      })
      .catch(() => setChanges([]));
  }, [kind, id]);
  useEffect(load, [load]);
  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && onClose()}
      aria-label="File history"
      className={s.dialogWide}
    >
      <DialogHeader title="File history">
        <span className={s.muted}>Every rename, move and delete made by hand, newest first.</span>
      </DialogHeader>
      <DialogBody>
        {changes === null ? <p className={s.empty}>Reading the history…</p> : null}
        {changes && changes.length === 0 ? (
          <p className={s.empty}>
            None of this {kind === 'movie' ? 'movie' : 'show'}’s files have been renamed, moved or
            deleted by hand.
          </p>
        ) : null}
        {changes && changes.length ? (
          <div className={s.borrowed}>
            <ChangeList
              changes={changes}
              showTitle={false}
              onUndone={() => {
                load();
                onChanged();
              }}
            />
          </div>
        ) : null}
        {changes && total > changes.length ? (
          <p className={s.note}>
            Showing the newest {changes.length} of {total}.
          </p>
        ) : null}
        <p className={s.note}>
          Undo puts a file back where it was. A deleted file comes back while the recycle bin still
          holds it.
        </p>
      </DialogBody>
      <DialogFooter>
        <button type="button" className={s.quietBtn} onClick={onClose}>
          Done
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}

/* ── move the title to another library ────────────────────────────────────── */

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

/** the move runs as the library's one background job: start it, then watch it */
async function runMove(kind: TitleKind, id: number, libraryId: number): Promise<void> {
  working.set('Moving the files…');
  try {
    await startBulk(kind, [id], 'move', { library_id: libraryId, by_hand: true });
    let status = await fetchBulkStatus();
    // a show crossing to another drive takes a while: there is no deadline
    while (status.running) {
      if (status.total) working.set('Moving the files…');
      await wait(1000);
      status = await fetchBulkStatus();
    }
    if (status.error) throw new Error(status.error);
    const moved = status.changed ?? 0;
    const text = status.failed
      ? 'Some files could not be moved. They are where they were.'
      : moved
        ? `Moved ${plural(moved, 'file')} to the other library`
        : 'Filed in the other library. No file had to move.';
    toast(text, status.failed ? 'warning' : 'success');
    announce({ kind, id, text }, status.batch);
  } catch (e) {
    toast((e as Error).message || 'The move did not start', 'error');
  } finally {
    working.set(null);
    invalidate(kind, id);
  }
}

export function MoveTitleDialog({
  kind,
  id,
  onClose,
}: {
  kind: TitleKind;
  id: number;
  onClose: () => void;
}) {
  return (
    <MoveDialog
      kind={kind}
      side={kind === 'movie' ? 'movies' : 'shows'}
      ids={[id]}
      byHand
      onClose={onClose}
      onRun={(libraryId) => {
        onClose();
        void runMove(kind, id, libraryId);
      }}
    />
  );
}

/* ── the strip a change leaves behind ─────────────────────────────────────── */

type Plain = () => void;
const workingSubs = new Set<Plain>();
let workingText: string | null = null;
const working = {
  get: () => workingText,
  set(next: string | null) {
    workingText = next;
    for (const sub of Array.from(workingSubs)) sub();
  },
  subscribe(sub: Plain) {
    workingSubs.add(sub);
    return () => {
      workingSubs.delete(sub);
    };
  },
};

/**
 * the line at the foot of the page after a change: what happened, and Undo.
 * it belongs to the title it was made on and goes away by itself.
 */
export function UndoToast({ kind, id }: { kind: TitleKind; id: number }) {
  const change = useSyncExternalStore(lastChange.subscribe, lastChange.get);
  const busyText = useSyncExternalStore(working.subscribe, working.get);
  const [busy, setBusy] = useState(false);
  const mine = change && change.kind === kind && change.id === id ? change : null;

  useEffect(() => {
    if (!mine) return;
    const timer = window.setTimeout(() => {
      if (lastChange.get() === mine) lastChange.set(null);
    }, 30000);
    return () => window.clearTimeout(timer);
  }, [mine]);
  // a change made on another title's page is not this page's to offer
  useEffect(
    () => () => {
      const now = lastChange.get();
      if (now && now.kind === kind && now.id === id) lastChange.set(null);
    },
    [kind, id],
  );

  if (busyText) {
    return (
      <div className={s.undoToast} role="status">
        <span className={s.undoSpin} aria-hidden="true" />
        <span className={s.undoText}>{busyText}</span>
      </div>
    );
  }
  if (!mine) return null;

  const run = async () => {
    setBusy(true);
    try {
      const res = await undoBatch(mine.batch);
      toast(
        `${plural(res.undone, 'file')} put back` +
          (res.failed.length ? `, ${res.failed.length} could not be` : '') +
          (res.not_renamed ? `, ${res.not_renamed} kept the newer file name` : ''),
        res.failed.length || res.not_renamed ? 'warning' : 'success',
      );
    } catch (e) {
      toast((e as Error).message || 'That could not be undone', 'error');
    }
    setBusy(false);
    lastChange.set(null);
    invalidate(kind, id);
    window.SoulSyncVideoDetail?.reloadPage?.();
  };

  return (
    <div className={s.undoToast} role="status">
      <span className={s.undoText}>{mine.text}</span>
      <button type="button" className={s.undoBtn} disabled={busy} onClick={() => void run()}>
        {busy ? 'Undoing…' : 'Undo'}
      </button>
      <button
        type="button"
        className={s.undoClose}
        aria-label="Dismiss"
        onClick={() => lastChange.set(null)}
      >
        <span aria-hidden="true">×</span>
      </button>
    </div>
  );
}

/* ── the padlock ──────────────────────────────────────────────────────────── */

export function PadlockButton({
  kind,
  id,
  locked,
  onChanged,
}: {
  kind: TitleKind;
  id: number;
  locked: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const flip = async () => {
    setBusy(true);
    try {
      await setPadlock(kind, id, !locked);
      toast(
        locked
          ? 'Unlocked. Scans and maintenance look after it again.'
          : 'Locked. Automation leaves this title and its files alone.',
      );
      onChanged();
    } catch (e) {
      toast((e as Error).message || 'Couldn’t change the padlock', 'error');
    }
    setBusy(false);
  };
  return (
    <button
      type="button"
      className={s.padlock}
      aria-pressed={locked}
      disabled={busy}
      title={
        locked
          ? 'Locked: renames, clean-ups, upgrades and scans leave this title alone. What you do on this page still goes through. Click to unlock.'
          : 'Lock this title so automation leaves it and its files alone'
      }
      onClick={() => void flip()}
    >
      <PadlockIcon open={!locked} />
      {locked ? 'Locked' : 'Lock'}
    </button>
  );
}
