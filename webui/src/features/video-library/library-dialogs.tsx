/**
 * the library tab's three bigger decisions, each shown in full before
 * anything moves: which copies go, where a show is brought together, and
 * which episode a file really is.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';

import type { LibraryFile, ShowFolder, ShowLibrary, TitleKind } from './types';

import {
  type ConsolidatePlan,
  type ConsolidateState,
  type ExtraCopiesPlan,
  fetchConsolidatePlan,
  fetchConsolidateState,
  fetchExtraCopies,
  reassignFile,
  removeExtraCopies,
  startConsolidate,
  stopConsolidate,
} from './api';
import { driveLabel, formatBytes, pad2 } from './format';
import { lastChange } from './library-store';
import s from './video-library.module.css';

function toast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
  window.showToast?.(message, type);
}

/* ── keep the best copy ───────────────────────────────────────────────────── */

export function CopiesDialog({
  kind,
  id,
  recycle,
  onClose,
  onDone,
}: {
  kind: TitleKind;
  id: number;
  recycle: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [plan, setPlan] = useState<ExtraCopiesPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    let live = true;
    fetchExtraCopies(kind, id)
      .then((p) => live && setPlan(p))
      .catch((e: Error) => live && setError(e.message || 'Couldn’t work out the copies'));
    return () => {
      live = false;
    };
  }, [kind, id]);

  const run = async () => {
    setWorking(true);
    try {
      const res = await removeExtraCopies(kind, id);
      toast(
        res.failed
          ? `Removed ${res.removed}, ${res.failed} couldn’t be removed`
          : `Removed ${res.removed} cop${res.removed === 1 ? 'y' : 'ies'}, ${formatBytes(res.size_bytes)} freed`,
        res.failed ? 'warning' : 'success',
      );
      onDone();
    } catch (e) {
      setWorking(false);
      toast((e as Error).message || 'Couldn’t remove the copies', 'error');
    }
  };

  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && onClose()}
      aria-label="Keep the best copy"
      className={s.dialog}
    >
      <DialogHeader title="Keep the best copy">
        <span className={s.muted}>The best is the highest resolution, then the largest file.</span>
      </DialogHeader>
      <DialogBody>
        {error ? <p className={s.empty}>{error}</p> : null}
        {!plan && !error ? <p className={s.empty}>Looking at the copies…</p> : null}
        {plan && !plan.groups.length ? <p className={s.empty}>There are no extra copies.</p> : null}
        <ul className={s.plan}>
          {(plan?.groups ?? []).map((group) => (
            <li key={group.keep.file_id} className={s.planGroup}>
              {kind === 'show' ? <div className={s.planLabel}>{group.label}</div> : null}
              <div className={s.planRow} data-role="keep">
                <span className={s.planVerb}>Keep</span>
                <span className={s.planName} title={group.keep.filename}>
                  {group.keep.filename}
                </span>
                <span className={s.planMeta}>
                  {group.keep.resolution_label} · {formatBytes(group.keep.size_bytes)}
                </span>
              </div>
              {group.remove.map((f) => (
                <div
                  key={f.file_id}
                  className={s.planRow}
                  data-role={f.reachable ? 'remove' : 'skip'}
                >
                  <span className={s.planVerb}>{f.reachable ? 'Remove' : 'Stays'}</span>
                  <span className={s.planName} title={f.filename}>
                    {f.filename}
                  </span>
                  <span className={s.planMeta}>
                    {f.reachable
                      ? `${f.resolution_label} · ${formatBytes(f.size_bytes)}`
                      : 'only your media server can see it'}
                  </span>
                </div>
              ))}
            </li>
          ))}
        </ul>
        <p className={s.note}>
          {recycle
            ? 'Removed copies go to the recycle bin, where they can be restored until it empties.'
            : 'Recycling is off in your library settings, so removed copies are deleted for good.'}
        </p>
      </DialogBody>
      <DialogFooter>
        <button type="button" className={s.quietBtn} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={s.btnPrimary}
          disabled={!plan?.files || working}
          onClick={() => void run()}
        >
          {working
            ? 'Removing…'
            : plan?.files
              ? `Remove ${plan.files} cop${plan.files === 1 ? 'y' : 'ies'} (${formatBytes(plan.size_bytes)})`
              : 'Nothing to remove'}
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}

/* ── bring a show into one folder ─────────────────────────────────────────── */

export function ConsolidateDialog({
  show,
  running,
  onClose,
  onDone,
}: {
  show: ShowLibrary;
  /** a move already under way when the tab opened */
  running: ConsolidateState | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const folders: ShowFolder[] = show.summary.folders;
  const [target, setTarget] = useState<string>(running?.target || folders[0]?.path || '');
  const [plan, setPlan] = useState<ConsolidatePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<ConsolidateState | null>(running);
  const [starting, setStarting] = useState(false);
  const seq = useRef(0);
  const finished = useRef(false);

  useEffect(() => {
    if (state?.known || !target) return;
    const mine = ++seq.current;
    setPlan(null);
    setError(null);
    fetchConsolidatePlan(show.id, target)
      .then((p) => mine === seq.current && setPlan(p))
      .catch((e: Error) => mine === seq.current && setError(e.message || 'Couldn’t plan the move'));
  }, [show.id, target, state?.known]);

  const poll = useCallback(() => {
    fetchConsolidateState(show.id)
      .then((next) => setState(next))
      .catch(() => {});
  }, [show.id]);

  useEffect(() => {
    if (!state?.running) return;
    const t = window.setInterval(poll, 1000);
    return () => window.clearInterval(t);
  }, [state?.running, poll]);

  useEffect(() => {
    if (state?.known && !state.running && !finished.current) {
      finished.current = true;
      toast(
        state.failed
          ? `Moved ${state.moved ?? 0}, ${state.failed} couldn’t be moved`
          : `Moved ${state.moved ?? 0} file${state.moved === 1 ? '' : 's'} into one folder`,
        state.failed ? 'warning' : 'success',
      );
    }
  }, [state]);

  const start = async () => {
    setStarting(true);
    try {
      await startConsolidate(show.id, target);
      finished.current = false;
      setState({ known: true, running: true, target, total: plan?.files, done: 0 });
      poll();
    } catch (e) {
      toast((e as Error).message || 'Couldn’t start the move', 'error');
    } finally {
      setStarting(false);
    }
  };

  const moving = Boolean(state?.known);
  const pct =
    state?.bytes_total && state.bytes_total > 0
      ? Math.min(100, Math.round(((state.bytes_done ?? 0) / state.bytes_total) * 100))
      : state?.total
        ? Math.round(((state.done ?? 0) / state.total) * 100)
        : 0;
  const close = () => (moving && !state?.running ? onDone() : onClose());

  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && close()}
      aria-label="Bring together"
      className={s.dialog}
    >
      <DialogHeader title="Bring together">
        <span className={s.muted}>
          {show.title} is in {folders.length} folders. Pick the one to keep.
        </span>
      </DialogHeader>
      <DialogBody>
        {moving ? (
          <div className={s.progress} aria-live="polite">
            <div className={s.progressHead}>
              <span>
                {state?.running
                  ? `Moving ${Math.min((state.done ?? 0) + 1, state.total ?? 0)} of ${state.total ?? 0}`
                  : state?.stopped
                    ? 'Stopped'
                    : 'Done'}
              </span>
              <span className={s.muted}>
                {formatBytes(state?.bytes_done) || '0 MB'} of {formatBytes(state?.bytes_total)}
              </span>
            </div>
            <div
              className={s.meter}
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span style={{ width: `${pct}%` }} />
            </div>
            <div className={s.progressFile}>{state?.current || ' '}</div>
            {!state?.running ? (
              <p className={s.note}>
                {state?.moved ?? 0} moved
                {state?.failed ? `, ${state.failed} couldn’t be moved` : ''}
                {state?.skipped ? `, ${state.skipped} left where they were` : ''}. Your media server
                is rescanning.
              </p>
            ) : null}
            {(state?.errors ?? []).map((e) => (
              <p key={e.filename} className={s.planError}>
                {e.filename}: {e.error}
              </p>
            ))}
          </div>
        ) : (
          <>
            <div className={s.targets} role="radiogroup" aria-label="Folder to keep">
              {folders.map((folder) => (
                <label
                  key={folder.path ?? ''}
                  className={s.target}
                  data-picked={target === folder.path ? 'yes' : 'no'}
                >
                  <input
                    type="radio"
                    name="vlt-target"
                    checked={target === folder.path}
                    onChange={() => setTarget(folder.path ?? '')}
                  />
                  <span className={s.targetBody}>
                    <span className={s.targetName}>
                      {driveLabel(folder.root) ? (
                        <span className={s.drive}>{driveLabel(folder.root)}</span>
                      ) : null}
                      {folder.name || folder.path}
                    </span>
                    <span className={s.path}>{folder.path}</span>
                  </span>
                  <span className={s.targetCount}>
                    {folder.files} file{folder.files === 1 ? '' : 's'}
                    {folder.size_bytes ? ` · ${formatBytes(folder.size_bytes)}` : ''}
                  </span>
                </label>
              ))}
            </div>
            {error ? <p className={s.planError}>{error}</p> : null}
            {!plan && !error ? <p className={s.empty}>Working out what would move…</p> : null}
            {plan ? (
              <div className={s.planSummary}>
                <p>
                  <b>
                    {plan.files} file{plan.files === 1 ? '' : 's'}
                  </b>{' '}
                  ({formatBytes(plan.size_bytes) || '0 MB'}) move into that folder, each into its
                  season’s folder. Names stay as they are, subtitles go along, and nothing is
                  overwritten.
                </p>
                {plan.copy_bytes ? (
                  <p>
                    {formatBytes(plan.copy_bytes)} is on another drive, so it is copied and then
                    removed from the old place. That takes a while.
                    {plan.free_bytes != null
                      ? ` The folder’s drive has ${formatBytes(plan.free_bytes)} free.`
                      : ''}
                  </p>
                ) : null}
                {!plan.enough_room ? (
                  <p className={s.planError}>There isn’t enough free space for the copies.</p>
                ) : null}
                {plan.skipped.length ? (
                  <details className={s.skipped}>
                    <summary>
                      {plan.skipped.length} file{plan.skipped.length === 1 ? '' : 's'} stay where
                      they are
                    </summary>
                    <ul>
                      {plan.skipped.map((f) => (
                        <li key={f.file_id}>
                          {f.filename} <span className={s.muted}>· {f.reason}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </DialogBody>
      <DialogFooter>
        {moving ? (
          state?.running ? (
            <>
              <p className={s.note}>You can close this. The move carries on.</p>
              <button
                type="button"
                className={s.quietBtn}
                onClick={() => void stopConsolidate(show.id)}
              >
                Stop after this file
              </button>
              <button type="button" className={s.btn} onClick={onClose}>
                Close
              </button>
            </>
          ) : (
            <button type="button" className={s.btnPrimary} onClick={onDone}>
              Done
            </button>
          )
        ) : (
          <>
            <button type="button" className={s.quietBtn} onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className={s.btnPrimary}
              disabled={!plan?.files || !plan.enough_room || starting}
              onClick={() => void start()}
            >
              {plan?.files
                ? `Move ${plan.files} file${plan.files === 1 ? '' : 's'}`
                : 'Nothing to move'}
            </button>
          </>
        )}
      </DialogFooter>
    </DialogFrame>
  );
}

/* ── this file is really another episode ──────────────────────────────────── */

export function ReassignDialog({
  show,
  file,
  from,
  onClose,
  onDone,
}: {
  show: ShowLibrary;
  file: LibraryFile;
  from: { season: number; episode: number };
  onClose: () => void;
  onDone: () => void;
}) {
  const [season, setSeason] = useState(from.season);
  const [picked, setPicked] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const current = show.seasons.find((x) => x.season_number === season);

  const apply = async () => {
    if (picked == null) return;
    setSaving(true);
    try {
      const res = await reassignFile(file.id, picked);
      toast(
        res.renamed
          ? 'Moved, and renamed to match the episode'
          : res.action === 'unchanged'
            ? res.message || 'Already under that episode'
            : 'Moved. The file kept its name, so your media server may file it back on its next scan.',
        res.renamed || res.action === 'unchanged' ? 'success' : 'warning',
      );
      if (res.batch) {
        lastChange.set({
          batch: res.batch,
          kind: 'show',
          id: show.id,
          text: 'Filed under another episode',
        });
      }
      onDone();
    } catch (e) {
      setSaving(false);
      toast((e as Error).message || 'Couldn’t move the file', 'error');
    }
  };

  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && onClose()}
      aria-label="Move to another episode"
      className={s.dialog}
    >
      <DialogHeader title="Move to another episode">
        <span className={s.muted}>{file.filename}</span>
      </DialogHeader>
      <DialogBody>
        <label className={s.field}>
          <span>Season</span>
          <select
            className={s.select}
            value={season}
            onChange={(e) => {
              setSeason(Number(e.target.value));
              setPicked(null);
            }}
          >
            {show.seasons.map((x) => (
              <option key={x.season_number} value={x.season_number}>
                {x.title}
              </option>
            ))}
          </select>
        </label>
        <ul className={s.matches} role="listbox" aria-label="Episodes">
          {(current?.episodes ?? []).map((ep) => {
            const here = season === from.season && ep.episode_number === from.episode;
            return (
              <li key={ep.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={picked === ep.id}
                  className={s.pick}
                  data-picked={picked === ep.id ? 'yes' : 'no'}
                  disabled={here}
                  onClick={() => setPicked(ep.id)}
                >
                  <span className={s.pickNum}>{pad2(ep.episode_number)}</span>
                  <span className={s.pickTitle}>{ep.title || `Episode ${ep.episode_number}`}</span>
                  <span className={s.muted}>
                    {here ? 'It’s here now' : ep.has_file ? 'Has a file' : 'No file yet'}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        <p className={s.note}>
          Where SoulSync can reach the file it is renamed for the new episode, so your media server
          agrees.
        </p>
      </DialogBody>
      <DialogFooter>
        <button type="button" className={s.quietBtn} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={s.btnPrimary}
          disabled={picked == null || saving}
          onClick={() => void apply()}
        >
          {saving ? 'Moving…' : 'Move here'}
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}
