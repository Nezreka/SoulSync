/**
 * the manager's second row of tools: what was done to the files and putting
 * it back, saved views, moving titles to another library, filing episode
 * files under the right episode, and picking a poster.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';

import type { FileChange, SavedView, SeasonEpisodes, Side, TitleKind, Undoable } from './types';

import {
  type LibraryRef,
  type MovePreview,
  type PosterOption,
  type RenumberPlan,
  applyRenumber,
  fetchChanges,
  fetchEpisodes,
  fetchLibraries,
  fetchMovePreview,
  fetchPosterOptions,
  fetchViews,
  planRenumber,
  saveViews,
  setPoster,
  undoBatch,
  undoChange,
} from './api';
import { formatDate, formatSize, noun, plural } from './format';
import s from './video-manager.module.css';

function toast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
  window.showToast?.(message, type);
}

export function PadlockIcon({ open = false }: { open?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} aria-hidden="true">
      <path
        d={open ? 'M7 11V8a5 5 0 0 1 9.6-2M6 11h12v9H6z' : 'M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z'}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* ── history and undo ─────────────────────────────────────────────────────── */

const DID: Record<FileChange['action'], string> = {
  rename: 'Renamed',
  move: 'Moved',
  delete: 'Deleted',
  reassign: 'Filed under another episode',
};

function changeLine(change: FileChange): { from: string; to: string } {
  if (change.action === 'delete') return { from: change.from_path ?? '', to: '' };
  if (change.action === 'reassign') return { from: '', to: '' };
  if (change.action === 'rename') return { from: change.from_name, to: change.to_name };
  return { from: change.from_path ?? '', to: change.to_path ?? '' };
}

/** file changes, newest first, each with its own Undo */
export function ChangeList({
  changes,
  showTitle,
  onUndone,
}: {
  changes: FileChange[];
  showTitle: boolean;
  onUndone: () => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);
  const undo = async (change: FileChange) => {
    setBusy(change.id);
    try {
      const res = await undoChange(change.id);
      toast(res.message || 'Put back');
      onUndone();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'That could not be undone', 'error');
    } finally {
      setBusy(null);
    }
  };
  return (
    <ol className={s.history}>
      {changes.map((change) => {
        const line = changeLine(change);
        return (
          <li key={change.id} className={s.changeRow} data-undone={change.undone || undefined}>
            <div className={s.changeText}>
              <p className={s.historyWhat}>
                {DID[change.action] ?? change.action}
                {showTitle && change.title ? ` · ${change.title}` : ''}
                {change.label && change.kind === 'show' ? ` · ${change.label}` : ''}
              </p>
              {line.from ? <p className={s.changeFrom}>{line.from}</p> : null}
              {line.to ? <p className={s.changeTo}>{line.to}</p> : null}
              <p className={s.muted}>
                {formatDate(`${change.at.replace(' ', 'T')}Z`)}
                {change.undone ? ' · put back' : ''}
              </p>
            </div>
            {change.undone ? null : (
              <button
                type="button"
                className="pgf-btn"
                disabled={busy === change.id}
                onClick={() => void undo(change)}
              >
                {busy === change.id ? 'Undoing…' : 'Undo'}
              </button>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** every file change made by hand, across the library */
export function HistoryDialog({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: () => void;
}) {
  const [changes, setChanges] = useState<FileChange[] | null>(null);
  const [total, setTotal] = useState(0);
  const load = useCallback(() => {
    fetchChanges(undefined, 200)
      .then((out) => {
        setChanges(out.items);
        setTotal(out.total);
      })
      .catch(() => setChanges([]));
  }, []);
  useEffect(load, [load]);
  return (
    <DialogFrame open onOpenChange={(open) => !open && onClose()} className={s.dialogWide}>
      <DialogHeader title="File history" closeLabel="Close" compact />
      <DialogBody>
        <div className={s.dialogBody}>
          <p className={s.dialogNote}>
            Every rename, move and delete made by hand, newest first. Undo puts the file back where
            it was. A deleted file comes back while the recycle folder still holds it.
          </p>
          {changes === null ? <p className={s.dialogNote}>Loading…</p> : null}
          {changes && changes.length === 0 ? (
            <p className={s.dialogNote}>No files have been renamed, moved or deleted yet.</p>
          ) : null}
          {changes && changes.length ? (
            <ChangeList
              changes={changes}
              showTitle
              onUndone={() => {
                load();
                onChanged();
              }}
            />
          ) : null}
          {changes && total > changes.length ? (
            <p className={s.dialogNote}>
              Showing the newest {changes.length.toLocaleString()} of {total.toLocaleString()}.
            </p>
          ) : null}
        </div>
      </DialogBody>
    </DialogFrame>
  );
}

/** the line a finished run leaves behind: what it did, and Undo */
export function UndoStrip({
  undoable,
  onDone,
  onDismiss,
}: {
  undoable: Undoable;
  onDone: () => void;
  onDismiss: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const res = await undoBatch(undoable.batch);
      toast(
        `${plural(res.undone, 'file', 'files')} put back` +
          (res.failed.length ? `, ${res.failed.length} could not be` : '') +
          (res.not_renamed ? `, ${res.not_renamed} kept the newer file name` : ''),
        res.failed.length || res.not_renamed ? 'warning' : 'success',
      );
      onDone();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'That could not be undone', 'error');
    } finally {
      setBusy(false);
      onDismiss();
    }
  };
  return (
    <div className={s.undoStrip} role="status">
      <span>{undoable.text}</span>
      <button type="button" className={s.linkBtn} disabled={busy} onClick={() => void run()}>
        {busy ? 'Undoing…' : 'Undo'}
      </button>
      <button type="button" className={s.barClose} aria-label="Dismiss" onClick={onDismiss}>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          aria-hidden="true"
        >
          <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

/* ── saved views ──────────────────────────────────────────────────────────── */

export function ViewsMenu({
  current,
  onApply,
}: {
  /** what the page shows right now, to save under a name */
  current: SavedView['state'];
  onApply: (state: SavedView['state']) => void;
}) {
  const [open, setOpen] = useState(false);
  const [views, setViews] = useState<SavedView[]>([]);
  const [name, setName] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchViews()
      .then(setViews)
      .catch(() => setViews([]));
  }, []);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', key);
    };
  }, [open]);

  const store = async (next: SavedView[], said: string) => {
    try {
      setViews(await saveViews(next));
      toast(said);
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not save the view', 'error');
    }
  };
  const add = () => {
    const label = name.trim();
    if (!label) return;
    setName('');
    void store(
      [
        ...views.filter((v) => v.name.toLowerCase() !== label.toLowerCase()),
        { name: label, state: current },
      ],
      `Saved "${label}"`,
    );
  };

  return (
    <div className={s.menuWrap} ref={ref}>
      <button
        type="button"
        className={s.filterBtn}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.9}
          aria-hidden="true"
        >
          <path d="M6 4h12v16l-6-4-6 4z" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        Views
        {views.length ? <span className={s.filterCount}>{views.length}</span> : null}
      </button>
      {open ? (
        <div className={`${s.menu} ${s.viewsMenu}`} role="menu" data-align="right">
          {views.length ? (
            <>
              <p className={s.menuHead}>Saved views</p>
              {views.map((view) => (
                <div key={view.name} className={s.viewRow}>
                  <button
                    type="button"
                    role="menuitem"
                    className={s.menuItem}
                    onClick={() => {
                      setOpen(false);
                      onApply(view.state);
                    }}
                  >
                    {view.name}
                    <span className={s.muted}>
                      {view.state.tab === 'shows' ? 'Shows' : 'Movies'}
                    </span>
                  </button>
                  <button
                    type="button"
                    className={s.barClose}
                    aria-label={`Remove ${view.name}`}
                    title="Remove"
                    onClick={() =>
                      void store(
                        views.filter((v) => v.name !== view.name),
                        `Removed "${view.name}"`,
                      )
                    }
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={2}
                      aria-hidden="true"
                    >
                      <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
                    </svg>
                  </button>
                </div>
              ))}
            </>
          ) : (
            <p className={s.viewsEmpty}>
              A view keeps the filters, sort, layout and columns on screen now, so one click brings
              them back.
            </p>
          )}
          <p className={s.menuHead}>Save what is on screen</p>
          <form
            className={s.viewSave}
            onSubmit={(e) => {
              e.preventDefault();
              add();
            }}
          >
            <input
              className={s.input}
              value={name}
              maxLength={60}
              placeholder="Name this view"
              aria-label="View name"
              onChange={(e) => setName(e.target.value)}
            />
            <button type="submit" className="pgf-btn pgf-btn--main" disabled={!name.trim()}>
              Save
            </button>
          </form>
        </div>
      ) : null}
    </div>
  );
}

/* ── move to another library ──────────────────────────────────────────────── */

export function MoveDialog({
  kind,
  side,
  ids,
  onClose,
  onRun,
}: {
  kind: TitleKind;
  side: Side;
  ids: number[];
  onClose: () => void;
  onRun: (libraryId: number) => void;
}) {
  const [libraries, setLibraries] = useState<LibraryRef[] | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [plan, setPlan] = useState<MovePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchLibraries(kind)
      .then((libs) => live && setLibraries(libs))
      .catch(() => live && setLibraries([]));
    return () => {
      live = false;
    };
  }, [kind]);

  useEffect(() => {
    if (picked == null) return;
    let live = true;
    setPlan(null);
    setError(null);
    fetchMovePreview(kind, ids, picked)
      .then((p) => live && setPlan(p))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, picked]);

  const short = plan != null && !plan.enough_room;
  return (
    <DialogFrame open onOpenChange={(open) => !open && onClose()} className={s.dialog}>
      <DialogHeader
        title={`Move ${ids.length.toLocaleString()} ${noun(side, ids.length)}`}
        closeLabel="Close"
        compact
      />
      <DialogBody>
        <div className={s.dialogBody}>
          <p className={s.dialogNote}>
            The files move to the library's folder, named by your template, with their subtitles.
            The {noun(side, ids.length)} {ids.length === 1 ? 'is' : 'are'} then filed there, so
            anything downloaded later lands beside them.
          </p>
          {libraries === null ? <p className={s.dialogNote}>Loading…</p> : null}
          {libraries && libraries.length === 0 ? (
            <p className={s.dialogNote}>
              No library has a folder set. Add one in Settings, under Folders.
            </p>
          ) : null}
          {libraries && libraries.length ? (
            <div className={s.options} role="radiogroup" aria-label="Library">
              {libraries.map((lib) => (
                <button
                  key={lib.id}
                  type="button"
                  role="radio"
                  aria-checked={picked === lib.id}
                  className={s.option}
                  onClick={() => setPicked(lib.id)}
                >
                  {lib.name}
                  <span className={s.optionPath}>{lib.path}</span>
                </button>
              ))}
            </div>
          ) : null}
          {error ? <p className={s.dialogWarn}>{error}</p> : null}
          {picked != null && !plan && !error ? (
            <p className={s.dialogNote}>Working out what would move…</p>
          ) : null}
          {plan ? (
            <>
              <p className={s.dialogNote}>
                {plan.files ? (
                  <>
                    <b>{plural(plan.files, 'file', 'files')}</b> · {formatSize(plan.size_bytes)}{' '}
                    will move.
                    {plan.copy_bytes ? (
                      <>
                        {' '}
                        {formatSize(plan.copy_bytes)} crosses to another drive, which takes a while.
                      </>
                    ) : null}
                  </>
                ) : plan.skipped ? (
                  <>None of these files can be moved.</>
                ) : (
                  <>Nothing needs to move: every file is already there.</>
                )}
                {plan.skipped ? (
                  <>
                    {' '}
                    {plural(plan.skipped, 'file', 'files')} will be left in place
                    {plan.skipped_why ? `: ${plan.skipped_why}` : ''}.
                  </>
                ) : null}
                {plan.held ? (
                  <> {plural(plan.held, 'locked title', 'locked titles')} will be left alone.</>
                ) : null}
              </p>
              {short ? (
                <p className={s.dialogWarn}>
                  That drive has {formatSize(plan.free_bytes)} free, which is not enough.
                </p>
              ) : null}
              {plan.moves.length ? (
                <ul className={s.changes}>
                  {plan.moves.slice(0, 40).map((move) => (
                    <li key={move.from}>
                      <span className={s.changeFrom}>{move.from}</span>
                      <span className={s.changeTo}>{move.to}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : null}
        </div>
      </DialogBody>
      <DialogFooter>
        <button type="button" className="pgf-btn" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="pgf-btn pgf-btn--main"
          disabled={picked == null || !plan || plan.files === 0 || short}
          onClick={() => picked != null && onRun(picked)}
        >
          {plan?.files ? `Move ${plural(plan.files, 'file', 'files')}` : 'Move'}
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}

/* ── episodes: file a run of files under other episodes ───────────────────── */

const seasonName = (n: number) => (n === 0 ? 'Specials' : `Season ${n}`);
const code = (season: number, episode: number) =>
  `S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`;

export function EpisodesTab({
  showId,
  onChanged,
}: {
  showId: number;
  onChanged: (undoable?: Undoable) => void;
}) {
  const [seasons, setSeasons] = useState<SeasonEpisodes[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [picked, setPicked] = useState<Set<number>>(() => new Set());
  const [toSeason, setToSeason] = useState('');
  const [toEpisode, setToEpisode] = useState('1');
  const [plan, setPlan] = useState<RenumberPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    fetchEpisodes(showId)
      .then((out) => {
        setSeasons(out);
        setOpen(
          (prev) => prev ?? out.find((x) => x.episodes.some((e) => e.files.length))?.season ?? null,
        );
      })
      .catch(() => setSeasons([]));
  }, [showId]);
  useEffect(load, [load]);

  const ids = useMemo(() => [...picked], [picked]);
  const season = toSeason === '' ? null : Number(toSeason);
  const start = Number(toEpisode);
  const ready = ids.length > 0 && season != null && Number.isInteger(start) && start >= 0;

  // the plan follows the choice: nothing is filed until Apply
  useEffect(() => {
    setPlan(null);
    setError(null);
    if (!ready || season == null) return;
    let live = true;
    const timer = window.setTimeout(() => {
      planRenumber(showId, ids, season, start)
        .then((p) => live && setPlan(p))
        .catch((e: Error) => live && setError(e.message));
    }, 200);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [showId, ids, season, start, ready]);

  const toggle = (fileId: number) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(fileId)) next.delete(fileId);
      else next.add(fileId);
      return next;
    });
  const toggleSeason = (item: SeasonEpisodes, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      for (const e of item.episodes)
        for (const f of e.files) {
          if (on) next.add(f.file_id);
          else next.delete(f.file_id);
        }
      return next;
    });

  const apply = async () => {
    if (!ready || season == null) return;
    setBusy(true);
    try {
      const res = await applyRenumber(showId, ids, season, start);
      const text =
        `${plural(res.moved, 'file', 'files')} filed under ${seasonName(season)}` +
        (res.not_renamed ? `, ${res.not_renamed} kept the old file name` : '');
      toast(text, res.not_renamed ? 'warning' : 'success');
      setPicked(new Set());
      setPlan(null);
      load();
      onChanged(res.batch ? { batch: res.batch, text } : undefined);
    } catch (e) {
      toast(e instanceof Error ? e.message : 'That did not work', 'error');
    } finally {
      setBusy(false);
    }
  };

  if (seasons === null) return <p className={s.dialogNote}>Reading the episodes…</p>;
  if (seasons.length === 0)
    return <p className={s.dialogNote}>This show has no episodes in the library yet.</p>;

  return (
    <>
      <p className={s.dialogNote}>
        Files filed under the wrong episode? Tick them, say where the run starts, and they are filed
        in order and renamed to match. Specials are season 0.
      </p>

      {ids.length ? (
        <div className={s.renumber}>
          <p className={s.renumberHead}>
            <b>{plural(ids.length, 'file', 'files')}</b> ticked. File them under:
          </p>
          <div className={s.renumberRow}>
            <label className={s.field}>
              <span className={s.fieldLabel}>Season</span>
              <select
                className={s.select}
                value={toSeason}
                onChange={(e) => setToSeason(e.target.value)}
              >
                <option value="">Choose</option>
                {seasons.map((x) => (
                  <option key={x.season} value={String(x.season)}>
                    {seasonName(x.season)}
                  </option>
                ))}
              </select>
            </label>
            <label className={s.field}>
              <span className={s.fieldLabel}>Starting at episode</span>
              <input
                className={s.input}
                inputMode="numeric"
                value={toEpisode}
                onChange={(e) => setToEpisode(e.target.value.replace(/[^0-9]/g, ''))}
              />
            </label>
          </div>
          {error ? <p className={s.dialogWarn}>{error}</p> : null}
          {plan && plan.files === 0 ? (
            <p className={s.dialogNote}>They are already filed there.</p>
          ) : null}
          {plan && plan.files ? (
            <ul className={s.changes}>
              {plan.moves.map((move) => (
                <li key={move.file_id}>
                  <span className={s.changeTo}>
                    {move.from} to {move.to}
                  </span>
                  <span className={s.changeFrom} data-plain="">
                    {move.filename}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <div className={s.renumberActions}>
            <button type="button" className="pgf-btn" onClick={() => setPicked(new Set())}>
              Clear
            </button>
            <button
              type="button"
              className="pgf-btn pgf-btn--main"
              disabled={busy || !plan || plan.files === 0}
              onClick={() => void apply()}
            >
              {busy
                ? 'Filing…'
                : plan?.files
                  ? `File ${plural(plan.files, 'file', 'files')}`
                  : 'File'}
            </button>
          </div>
        </div>
      ) : null}

      <ul className={s.seasons}>
        {seasons.map((item) => {
          const files = item.episodes.reduce((sum, e) => sum + e.files.length, 0);
          const ticked = item.episodes.reduce(
            (sum, e) => sum + e.files.filter((f) => picked.has(f.file_id)).length,
            0,
          );
          const isOpen = open === item.season;
          return (
            <li key={item.season} className={s.season}>
              <div className={s.seasonHead}>
                <input
                  type="checkbox"
                  aria-label={`Tick every file in ${seasonName(item.season)}`}
                  disabled={!files}
                  checked={files > 0 && ticked === files}
                  onChange={(e) => toggleSeason(item, e.target.checked)}
                />
                <button
                  type="button"
                  className={s.seasonToggle}
                  aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : item.season)}
                >
                  <span>{seasonName(item.season)}</span>
                  <span className={s.muted}>
                    {files} of {plural(item.episodes.length, 'episode', 'episodes')} on disk
                  </span>
                </button>
              </div>
              {isOpen ? (
                <ul className={s.episodeList}>
                  {item.episodes.map((episode) => (
                    <li
                      key={episode.id}
                      className={s.episode}
                      data-empty={!episode.files.length || undefined}
                    >
                      <div className={s.episodeHead}>
                        <span className={s.episodeCode}>{code(item.season, episode.episode)}</span>
                        <span className={s.episodeTitle}>{episode.title || 'Untitled'}</span>
                        <span className={s.muted}>{formatDate(episode.air_date)}</span>
                      </div>
                      {episode.files.map((file) => (
                        <label key={file.file_id} className={s.episodeFile}>
                          <input
                            type="checkbox"
                            checked={picked.has(file.file_id)}
                            onChange={() => toggle(file.file_id)}
                          />
                          <span className={s.fileName}>{file.filename}</span>
                          <span className={s.muted}>{formatSize(file.size_bytes)}</span>
                        </label>
                      ))}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </>
  );
}

/* ── artwork ──────────────────────────────────────────────────────────────── */

export function PosterPicker({
  kind,
  id,
  tmdbId,
  onPicked,
  onClose,
}: {
  kind: TitleKind;
  id: number;
  tmdbId: number;
  onPicked: () => void;
  onClose: () => void;
}) {
  const [options, setOptions] = useState<PosterOption[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    fetchPosterOptions(kind, tmdbId)
      .then((out) => live && setOptions(out))
      .catch(() => live && setOptions([]));
    return () => {
      live = false;
    };
  }, [kind, tmdbId]);
  const pick = async (option: PosterOption) => {
    setBusy(option.full);
    try {
      await setPoster(kind, id, option.full);
      toast('Poster changed');
      onPicked();
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not set the poster', 'error');
    } finally {
      setBusy(null);
    }
  };
  return (
    <DialogFrame open onOpenChange={(open) => !open && onClose()} className={s.dialogWide}>
      <DialogHeader title="Choose a poster" closeLabel="Close" compact />
      <DialogBody>
        <div className={s.dialogBody}>
          {options === null ? <p className={s.dialogNote}>Loading posters…</p> : null}
          {options && options.length === 0 ? (
            <p className={s.dialogNote}>No other posters were found for this title.</p>
          ) : null}
          {options && options.length ? (
            <>
              <p className={s.dialogNote}>
                The one you pick is saved beside the files and sent to your media server.
              </p>
              <div className={s.posterGrid}>
                {options.map((option) => (
                  <button
                    key={option.full}
                    type="button"
                    className={s.posterOption}
                    disabled={busy !== null}
                    aria-busy={busy === option.full}
                    aria-label={option.lang ? `Poster, ${option.lang}` : 'Poster'}
                    onClick={() => void pick(option)}
                  >
                    <img src={option.thumb || option.full} alt="" loading="lazy" />
                  </button>
                ))}
              </div>
            </>
          ) : null}
        </div>
      </DialogBody>
    </DialogFrame>
  );
}
