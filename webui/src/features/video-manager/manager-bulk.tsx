/**
 * the bar that acts on whatever is selected.
 *
 * the few things people do most sit on the bar; everything else is one menu
 * away. anything that moves or removes files shows what it will do first.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';

import type { BulkAction, BulkStatus, Facets, Side, TitleKind, Undoable, ViewMode } from './types';

import {
  type CollectionRef,
  type DeletePreview,
  type RenamePreview,
  fetchBulkStatus,
  fetchCollections,
  fetchDeletePreview,
  fetchRenamePreview,
  startBulk,
} from './api';
import { formatSize, noun, plural } from './format';
import { MoveDialog } from './manager-extras';
import s from './video-manager.module.css';

function toast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
  window.showToast?.(message, type);
}

type Ask =
  | { kind: 'rename' }
  | { kind: 'delete' }
  | { kind: 'profile' }
  | { kind: 'genre'; remove: boolean }
  | { kind: 'rating' }
  | { kind: 'series' }
  | { kind: 'collection' }
  | { kind: 'move' }
  | null;

/** what a finished job says about itself */
export function describeDone(status: BulkStatus, side: Side): string {
  const titles = `${status.ok.toLocaleString()} ${noun(side, status.ok)}`;
  const failed =
    (status.failed ? `, ${status.failed.toLocaleString()} could not be done` : '') +
    (status.held ? `, ${plural(status.held, 'locked title', 'locked titles')} left alone` : '');
  switch (status.action) {
    case 'rename':
      return `${plural(status.changed ?? 0, 'file', 'files')} renamed${failed}`;
    case 'move':
      return `${plural(status.changed ?? 0, 'file', 'files')} moved${failed}`;
    case 'wish':
      return status.changed
        ? `${plural(status.changed, 'item', 'items')} added to your Wishlist${failed}`
        : `Nothing to add: the Wishlist already has them${failed}`;
    case 'subtitles':
      return status.changed
        ? `${plural(status.changed, 'subtitle', 'subtitles')} queued${failed}`
        : `No subtitles are missing${failed}`;
    case 'delete':
      return `${plural(status.changed ?? status.ok, 'title', 'titles')} deleted${failed}`;
    default:
      return `${status.label || 'Done'}: ${titles}${failed}`;
  }
}

function Menu({
  label,
  children,
  align = 'left',
}: {
  label: ReactNode;
  children: (close: () => void) => ReactNode;
  align?: 'left' | 'right';
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
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
  return (
    <div className={s.menuWrap} ref={ref}>
      <button
        type="button"
        className={s.barBtn}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {label}
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <path
            d="M3 4.5l3 3 3-3"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.5}
            strokeLinecap="round"
          />
        </svg>
      </button>
      {open ? (
        <div className={s.menu} role="menu" data-align={align}>
          {children(() => setOpen(false))}
        </div>
      ) : null}
    </div>
  );
}

function Item({
  children,
  onSelect,
  danger,
}: {
  children: ReactNode;
  onSelect: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={s.menuItem}
      data-danger={danger || undefined}
      onClick={onSelect}
    >
      {children}
    </button>
  );
}

export function BulkBar({
  side,
  view,
  ids,
  facets,
  onClear,
  onDone,
}: {
  side: Side;
  view: ViewMode;
  ids: number[];
  facets: Facets | null;
  onClear: () => void;
  /** a run that moved files hands back what Undo would reverse */
  onDone: (undoable?: Undoable) => void;
}) {
  const kind: TitleKind = side === 'shows' ? 'show' : 'movie';
  const [ask, setAsk] = useState<Ask>(null);
  const [job, setJob] = useState<BulkStatus | null>(null);
  const timer = useRef<number | null>(null);
  const files = view === 'files';

  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );

  const follow = useCallback(() => {
    const tick = async () => {
      try {
        const status = await fetchBulkStatus();
        setJob(status);
        if (status.running) {
          timer.current = window.setTimeout(() => void tick(), 600);
          return;
        }
        setJob(null);
        const said = describeDone(status, side);
        if (status.error) toast(status.error, 'error');
        else toast(said, status.failed ? 'warning' : 'success');
        if (status.action === 'wish')
          document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
        onDone(status.batch && status.changed ? { batch: status.batch, text: said } : undefined);
      } catch {
        setJob(null);
        onDone();
      }
    };
    void tick();
  }, [onDone, side]);

  const run = useCallback(
    async (action: BulkAction, params: Record<string, unknown> = {}) => {
      setAsk(null);
      try {
        const res = await startBulk(kind, ids, action, params);
        if (action === 'collection_add') {
          toast(`${plural(res.added ?? 0, 'title', 'titles')} added to the collection`);
          onDone();
          return;
        }
        setJob({ running: true, done: 0, total: ids.length, ok: 0, failed: 0, label: res.label });
        follow();
      } catch (e) {
        toast(e instanceof Error ? e.message : 'That could not be started', 'error');
      }
    },
    [follow, ids, kind, onDone],
  );

  /** the files view acts on files themselves */
  const runFiles = async (action: 'rename' | 'delete') => {
    if (action === 'delete') {
      const yes = await window.showConfirmDialog?.({
        title: `Delete ${plural(ids.length, 'file', 'files')}`,
        message:
          'The files go to the recycle folder when one is set up, otherwise they are deleted. The titles stay in your library.',
        confirmText: 'Delete',
        destructive: true,
      });
      if (!yes) return;
    }
    try {
      const response = await fetch('/api/video/files/bulk', {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, file_ids: ids, kind }),
      });
      const data = (await response.json()) as {
        success?: boolean;
        done?: number;
        unchanged?: number;
        failed?: number;
        error?: string;
        batch?: string | null;
      };
      if (!response.ok || !data.success) throw new Error(data.error || 'That did not work');
      const verb = action === 'rename' ? 'renamed' : 'deleted';
      const said =
        `${plural(data.done ?? 0, 'file', 'files')} ${verb}` +
        (data.unchanged ? `, ${data.unchanged} already right` : '') +
        (data.failed ? `, ${data.failed} could not be done` : '');
      toast(said, data.failed ? 'warning' : 'success');
      onDone(data.batch ? { batch: data.batch, text: said } : undefined);
    } catch (e) {
      toast(e instanceof Error ? e.message : 'That did not work', 'error');
    }
  };

  const running = Boolean(job?.running);
  const pct = job && job.total ? Math.round((100 * job.done) / job.total) : 0;

  return (
    <>
      <div className={s.bar} role="region" aria-label="Selection">
        {running ? (
          <div className={s.barJob}>
            <span className={s.barLabel}>{job?.label || 'Working'}</span>
            <span className={s.barTrack} aria-hidden="true">
              <span style={{ width: `${pct}%` }} />
            </span>
            <span className={s.barCount}>
              {job?.done ?? 0} / {job?.total ?? ids.length}
            </span>
          </div>
        ) : (
          <>
            <span className={s.barCount}>
              <b>{ids.length.toLocaleString()}</b> selected
            </span>
            <div className={s.barActions}>
              {files ? (
                <>
                  <button
                    type="button"
                    className={s.barBtn}
                    onClick={() => void runFiles('rename')}
                  >
                    Rename to template
                  </button>
                  <button
                    type="button"
                    className={s.barBtn}
                    data-danger=""
                    onClick={() => void runFiles('delete')}
                  >
                    Delete files
                  </button>
                </>
              ) : (
                <>
                  <Menu label="Monitor">
                    {(close) => (
                      <>
                        <Item onSelect={() => (close(), void run('monitored', { value: true }))}>
                          Monitor
                        </Item>
                        <Item onSelect={() => (close(), void run('monitored', { value: false }))}>
                          Stop monitoring
                        </Item>
                      </>
                    )}
                  </Menu>
                  <button
                    type="button"
                    className={s.barBtn}
                    onClick={() => void run('wish', { mode: 'missing' })}
                  >
                    Get missing
                  </button>
                  <button
                    type="button"
                    className={s.barBtn}
                    onClick={() => void run('wish', { mode: 'upgrade' })}
                  >
                    Look for upgrades
                  </button>
                  <button
                    type="button"
                    className={s.barBtn}
                    onClick={() => setAsk({ kind: 'rename' })}
                  >
                    Rename
                  </button>
                  <Menu label="More" align="right">
                    {(close) => (
                      <>
                        <p className={s.menuHead}>Acquisition</p>
                        <Item onSelect={() => (close(), setAsk({ kind: 'profile' }))}>
                          Quality profile
                        </Item>
                        <Item onSelect={() => (close(), void run('subtitles'))}>
                          Fetch missing subtitles
                        </Item>
                        {side === 'shows' ? (
                          <Item onSelect={() => (close(), setAsk({ kind: 'series' }))}>
                            Series type
                          </Item>
                        ) : null}
                        <p className={s.menuHead}>Details</p>
                        <Item onSelect={() => (close(), setAsk({ kind: 'genre', remove: false }))}>
                          Add a genre
                        </Item>
                        <Item onSelect={() => (close(), setAsk({ kind: 'genre', remove: true }))}>
                          Remove a genre
                        </Item>
                        <Item onSelect={() => (close(), setAsk({ kind: 'rating' }))}>
                          Content rating
                        </Item>
                        <Item onSelect={() => (close(), setAsk({ kind: 'collection' }))}>
                          Add to a collection
                        </Item>
                        <Item onSelect={() => (close(), void run('refresh_art'))}>
                          Refresh artwork
                        </Item>
                        <Item onSelect={() => (close(), void run('rematch'))}>Identify again</Item>
                        <p className={s.menuHead}>Files</p>
                        <Item onSelect={() => (close(), setAsk({ kind: 'move' }))}>
                          Move to another library
                        </Item>
                        <p className={s.menuHead}>Padlock</p>
                        <Item onSelect={() => (close(), void run('lock', { value: true }))}>
                          Lock
                        </Item>
                        <Item onSelect={() => (close(), void run('lock', { value: false }))}>
                          Unlock
                        </Item>
                        <p className={s.menuHead}>Watched</p>
                        <Item onSelect={() => (close(), void run('watched', { value: true }))}>
                          Mark watched
                        </Item>
                        <Item onSelect={() => (close(), void run('watched', { value: false }))}>
                          Mark unwatched
                        </Item>
                        <p className={s.menuHead}>Remove</p>
                        <Item danger onSelect={() => (close(), setAsk({ kind: 'delete' }))}>
                          Delete from library
                        </Item>
                      </>
                    )}
                  </Menu>
                </>
              )}
            </div>
            <button
              type="button"
              className={s.barClose}
              aria-label="Clear the selection"
              title="Clear"
              onClick={onClear}
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
          </>
        )}
      </div>

      {ask?.kind === 'rename' ? (
        <RenameDialog
          kind={kind}
          ids={ids}
          onClose={() => setAsk(null)}
          onRun={() => void run('rename')}
        />
      ) : null}
      {ask?.kind === 'delete' ? (
        <DeleteDialog
          kind={kind}
          side={side}
          ids={ids}
          onClose={() => setAsk(null)}
          onRun={() => void run('delete', { confirm: ids.length })}
        />
      ) : null}
      {ask?.kind === 'profile' ? (
        <ChoiceDialog
          title="Quality profile"
          note={`For ${ids.length.toLocaleString()} ${noun(side, ids.length)}. What they already have stays; searches and upgrades follow the new profile.`}
          options={[
            { value: '', label: 'Default' },
            ...(facets?.quality_profiles ?? []).map((p) => ({
              value: String(p.id),
              label: p.name,
            })),
          ]}
          confirm="Set profile"
          onClose={() => setAsk(null)}
          onRun={(value) =>
            void run('quality_profile', { profile_id: value ? Number(value) : null })
          }
        />
      ) : null}
      {ask?.kind === 'series' ? (
        <ChoiceDialog
          title="Series type"
          note="How releases of these shows are named, which decides how they are searched for."
          options={[
            { value: 'standard', label: 'Standard (S01E02)' },
            { value: 'daily', label: 'Daily (by air date)' },
            { value: 'anime', label: 'Anime (absolute numbers)' },
          ]}
          confirm="Set type"
          onClose={() => setAsk(null)}
          onRun={(value) => void run('series_type', { value })}
        />
      ) : null}
      {ask?.kind === 'genre' ? (
        <TextDialog
          title={ask.remove ? 'Remove a genre' : 'Add a genre'}
          note={`${ask.remove ? 'From' : 'To'} ${ids.length.toLocaleString()} ${noun(side, ids.length)}. The genre is locked on each, so a scan does not undo it.`}
          placeholder="Genre"
          suggestions={(facets?.genres ?? []).map((g) => g.value)}
          confirm={ask.remove ? 'Remove' : 'Add'}
          onClose={() => setAsk(null)}
          onRun={(genre) => void run(ask.remove ? 'genre_remove' : 'genre_add', { genre })}
        />
      ) : null}
      {ask?.kind === 'rating' ? (
        <TextDialog
          title="Content rating"
          note={`For ${ids.length.toLocaleString()} ${noun(side, ids.length)}.`}
          placeholder={side === 'shows' ? 'TV-14' : 'PG-13'}
          suggestions={
            side === 'shows'
              ? ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA']
              : ['G', 'PG', 'PG-13', 'R', 'NC-17']
          }
          confirm="Set rating"
          onClose={() => setAsk(null)}
          onRun={(value) => void run('content_rating', { value })}
        />
      ) : null}
      {ask?.kind === 'move' ? (
        <MoveDialog
          kind={kind}
          side={side}
          ids={ids}
          onClose={() => setAsk(null)}
          onRun={(libraryId) => void run('move', { library_id: libraryId })}
        />
      ) : null}
      {ask?.kind === 'collection' ? (
        <CollectionDialog
          kind={kind}
          onClose={() => setAsk(null)}
          onRun={(id) => void run('collection_add', { collection_id: id })}
        />
      ) : null}
    </>
  );
}

/* ── the dialogs ──────────────────────────────────────────────────────────── */

function Frame({
  title,
  children,
  footer,
  onClose,
}: {
  title: string;
  children: ReactNode;
  footer: ReactNode;
  onClose: () => void;
}) {
  return (
    <DialogFrame open onOpenChange={(open) => !open && onClose()} className={s.dialog}>
      <DialogHeader title={title} closeLabel="Close" compact />
      <DialogBody>
        <div className={s.dialogBody}>{children}</div>
      </DialogBody>
      <DialogFooter>{footer}</DialogFooter>
    </DialogFrame>
  );
}

function RenameDialog({
  kind,
  ids,
  onClose,
  onRun,
}: {
  kind: TitleKind;
  ids: number[];
  onClose: () => void;
  onRun: () => void;
}) {
  const [plan, setPlan] = useState<RenamePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    fetchRenamePreview(kind, ids)
      .then((p) => live && setPlan(p))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);
  return (
    <Frame
      title="Rename to your template"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="pgf-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="pgf-btn pgf-btn--main"
            disabled={!plan || plan.to_rename === 0}
            onClick={onRun}
          >
            {plan && plan.to_rename
              ? `Rename ${plural(plan.to_rename, 'file', 'files')}`
              : 'Rename'}
          </button>
        </>
      }
    >
      {error ? <p className={s.dialogNote}>{error}</p> : null}
      {!plan && !error ? <p className={s.dialogNote}>Reading the files…</p> : null}
      {plan ? (
        <>
          <p className={s.dialogNote}>
            {plan.to_rename ? (
              <>
                <b>{plural(plan.to_rename, 'file', 'files')}</b> of {plan.files.toLocaleString()}{' '}
                will be renamed and moved to where your naming template puts them.
              </>
            ) : (
              <>Every file is already named by your template.</>
            )}
            {plan.already_named ? (
              <> {plural(plan.already_named, 'is', 'are')} already right.</>
            ) : null}
            {plan.unreachable ? (
              <>
                {' '}
                {plural(plan.unreachable, 'file', 'files')} could not be reached from here and will
                be skipped.
              </>
            ) : null}
          </p>
          {plan.changes.length ? (
            <ul className={s.changes}>
              {plan.changes.map((change) => (
                <li key={change.file_id}>
                  <span className={s.changeFrom}>{change.current}</span>
                  <span className={s.changeTo}>{change.expected}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {plan.more ? (
            <p className={s.dialogNote}>And {plan.more.toLocaleString()} more.</p>
          ) : null}
        </>
      ) : null}
    </Frame>
  );
}

function DeleteDialog({
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
  onRun: () => void;
}) {
  const [plan, setPlan] = useState<DeletePreview | null>(null);
  const [typed, setTyped] = useState('');
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    fetchDeletePreview(kind, ids)
      .then((p) => live && setPlan(p))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);
  const count = String(ids.length);
  return (
    <Frame
      title={`Delete ${ids.length.toLocaleString()} ${noun(side, ids.length)}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="pgf-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className={`pgf-btn ${s.dangerBtn}`}
            disabled={!plan || typed.trim() !== count}
            onClick={onRun}
          >
            Delete
          </button>
        </>
      }
    >
      {error ? <p className={s.dialogNote}>{error}</p> : null}
      {!plan && !error ? <p className={s.dialogNote}>Working out what goes…</p> : null}
      {plan ? (
        <>
          <p className={s.dialogNote}>
            This removes <b>{plural(plan.titles, 'title', 'titles')}</b> from your library with{' '}
            <b>{plural(plan.files, 'file', 'files')}</b>
            {plan.size_bytes ? <> ({formatSize(plan.size_bytes)})</> : null}. Files go to the
            recycle folder when one is set up. Searches for them stop, and they are not downloaded
            again.
          </p>
          {plan.sample.length ? (
            <p className={s.dialogNote}>
              {plan.sample.join(', ')}
              {plan.titles > plan.sample.length
                ? `, and ${plan.titles - plan.sample.length} more`
                : ''}
            </p>
          ) : null}
          {plan.blocked.length ? (
            <p className={s.dialogWarn}>
              {plural(plan.blocked.length, 'title', 'titles')} cannot be deleted right now and will
              be left:{' '}
              {plan.blocked
                .slice(0, 3)
                .map((b) => `${b.title} (${b.reason})`)
                .join('; ')}
            </p>
          ) : null}
          <label className={s.field}>
            <span className={s.fieldLabel}>Type {count} to confirm</span>
            <input
              className={s.input}
              inputMode="numeric"
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
          </label>
        </>
      ) : null}
    </Frame>
  );
}

function ChoiceDialog({
  title,
  note,
  options,
  confirm,
  onClose,
  onRun,
}: {
  title: string;
  note: string;
  options: { value: string; label: string }[];
  confirm: string;
  onClose: () => void;
  onRun: (value: string) => void;
}) {
  const [value, setValue] = useState(options[0]?.value ?? '');
  return (
    <Frame
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="pgf-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="pgf-btn pgf-btn--main" onClick={() => onRun(value)}>
            {confirm}
          </button>
        </>
      }
    >
      <p className={s.dialogNote}>{note}</p>
      <div className={s.options} role="radiogroup" aria-label={title}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={value === option.value}
            className={s.option}
            onClick={() => setValue(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </Frame>
  );
}

function TextDialog({
  title,
  note,
  placeholder,
  suggestions,
  confirm,
  onClose,
  onRun,
}: {
  title: string;
  note: string;
  placeholder: string;
  suggestions: string[];
  confirm: string;
  onClose: () => void;
  onRun: (value: string) => void;
}) {
  const [value, setValue] = useState('');
  const clean = value.trim();
  return (
    <Frame
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="pgf-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="pgf-btn pgf-btn--main"
            disabled={!clean}
            onClick={() => onRun(clean)}
          >
            {confirm}
          </button>
        </>
      }
    >
      <p className={s.dialogNote}>{note}</p>
      <input
        className={s.input}
        autoFocus
        placeholder={placeholder}
        aria-label={title}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && clean && onRun(clean)}
      />
      {suggestions.length ? (
        <div className={s.suggest}>
          {suggestions.slice(0, 14).map((item) => (
            <button key={item} type="button" className={s.chip} onClick={() => setValue(item)}>
              {item}
            </button>
          ))}
        </div>
      ) : null}
    </Frame>
  );
}

function CollectionDialog({
  kind,
  onClose,
  onRun,
}: {
  kind: TitleKind;
  onClose: () => void;
  onRun: (id: number) => void;
}) {
  const [list, setList] = useState<CollectionRef[] | null>(null);
  const [value, setValue] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    fetchCollections(kind)
      .then((c) => {
        if (!live) return;
        setList(c);
        setValue(c[0]?.id ?? null);
      })
      .catch(() => live && setList([]));
    return () => {
      live = false;
    };
  }, [kind]);
  return (
    <Frame
      title="Add to a collection"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="pgf-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="pgf-btn pgf-btn--main"
            disabled={value == null}
            onClick={() => value != null && onRun(value)}
          >
            Add
          </button>
        </>
      }
    >
      {list === null ? <p className={s.dialogNote}>Loading your collections…</p> : null}
      {list && !list.length ? (
        <p className={s.dialogNote}>
          You have no {kind} collections yet. Make one in Collection Studio first.
        </p>
      ) : null}
      {list && list.length ? (
        <div className={s.options} role="radiogroup" aria-label="Collection">
          {list.map((c) => (
            <button
              key={c.id}
              type="button"
              role="radio"
              aria-checked={value === c.id}
              className={s.option}
              onClick={() => setValue(c.id)}
            >
              {c.name}
            </button>
          ))}
        </div>
      ) : null}
    </Frame>
  );
}
