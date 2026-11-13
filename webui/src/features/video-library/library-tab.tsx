/**
 * the "your library" tab on a movie or show page: what you own of the title,
 * what is wrong with it, and the fix for each thing it names.
 *
 * the first version of this tab was a status page. it said "2 copies", "split
 * across 5 folders" and "9 files below your target" and offered nothing for
 * any of them. now every problem it reports sits in one list with its fix
 * beside it, and the files carry what a file is judged by (picture, sound,
 * subtitles, where it came from) with the actions on the file itself.
 *
 * the page around it is vanilla (video-detail.js). it mounts this through
 * window.VideoLibraryTab, and this hands the acquisition flows it doesn't own
 * (manual search, season grabs, subtitle search, the manage panel) back to the
 * page through window.SoulSyncVideoDetail.
 */

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { ActionMenu, type ActionMenuItem } from '@/routes/artist-detail/-ui/action-menu';

import type {
  LibraryEpisode,
  LibraryFile,
  LibrarySeason,
  MovieLibrary,
  ShowLibrary,
  TitleKind,
  TitleLibrary,
} from './types';

import {
  type AcquisitionState,
  bulkFiles,
  type ConsolidateState,
  fetchAcquisition,
  deleteFile,
  fetchConsolidateState,
  fetchTitleLibrary,
  releaseIdentity,
  renameFile,
  type WishEpisode,
  wishlistEpisodes,
  wishlistMovie,
} from './api';
import { FixMatchDialog } from './fix-match-dialog';
import {
  audioLabel,
  audioLanguagesLabel,
  codecLabel,
  dateLabel,
  driveLabel,
  formatBytes,
  rangeLabel,
  resolutionTone,
  runtimeLabel,
  subtitleLabels,
} from './format';
import { ConsolidateDialog, CopiesDialog } from './library-dialogs';
import { invalidate, onInvalidate } from './library-store';
import s from './video-library.module.css';

/** the page's own flows, which the tab borrows instead of re-implementing. */
export interface VideoDetailBridge {
  manualSearch?: (scope: 'movie' | 'season' | 'episode', season?: number, episode?: number) => void;
  grabSeason?: (season: number) => void;
  wishlistSeason?: (season: number) => void;
  clearSeasonFailures?: (season: number) => void;
  subtitleSearch?: (kind: 'movie' | 'episode', id: number, title: string, label: string) => void;
  openManage?: () => void;
  /** the title changed under the page (fixed match, deleted the last file). */
  reloadPage?: () => void;
  /** the show's library data, for the page's own episode list to draw from */
  onLibrary?: (data: ShowLibrary) => void;
  /** the picked episodes were acted on: untick them */
  clearSelection?: () => void;
}

/** where the page wants each part of the library island (video-detail.js) */
export interface PageSlots {
  /** the library line, in the hero */
  hero?: HTMLElement | null;
  /** the bar for the episodes picked in the page's list, above that list */
  select?: HTMLElement | null;
  /** where it lives, under the episode list */
  foot?: HTMLElement | null;
}

/** the page's episode list says which episodes are ticked */
export const SELECTION_EVENT = 'soulsync:video-ep-selection';

declare global {
  interface Window {
    SoulSyncVideoDetail?: VideoDetailBridge;
  }
}

export function bridge(): VideoDetailBridge {
  return window.SoulSyncVideoDetail ?? {};
}

function toast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
  window.showToast?.(message, type);
}

async function confirm(options: {
  title: string;
  message: string;
  confirmText: string;
  destructive?: boolean;
}): Promise<boolean> {
  if (!window.showConfirmDialog) return false;
  return window.showConfirmDialog({ cancelText: 'Cancel', ...options });
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/* ── small pieces ─────────────────────────────────────────────────────────── */

function ResBadge({ label, size = 'md' }: { label: string; size?: 'md' | 'lg' }) {
  return (
    <span className={s.res} data-tone={resolutionTone(label)} data-size={size}>
      {label || '—'}
    </span>
  );
}

function Chip({
  tone = 'muted',
  children,
  title,
}: {
  tone?: 'ok' | 'warn' | 'info' | 'muted';
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={s.chip} data-tone={tone} title={title}>
      {children}
    </span>
  );
}

function MenuButton({ label, items }: { label: string; items: ActionMenuItem[] }) {
  if (!items.length) return null;
  return (
    <ActionMenu
      items={items}
      className={s.menu}
      trigger={(props) => (
        <button
          type="button"
          className={s.more}
          aria-label={label}
          title={label}
          ref={props.ref}
          onClick={props.onClick}
          aria-haspopup={props['aria-haspopup']}
          aria-expanded={props['aria-expanded']}
        >
          <span aria-hidden="true">⋯</span>
        </button>
      )}
    />
  );
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Path copied');
  } catch {
    toast("Couldn't copy — select the path instead", 'error');
  }
}

function folderOf(path: string | null | undefined): string {
  if (!path) return '';
  const p = path.replace(/\\/g, '/');
  const i = p.lastIndexOf('/');
  return i > 0 ? p.slice(0, i) : p;
}

function folderName(path: string | null | undefined): string {
  const parts = String(path || '')
    .split(/[\\/]+/)
    .filter(Boolean);
  return parts[parts.length - 1] || '';
}

/* ── what needs attention ─────────────────────────────────────────────────── */

interface Issue {
  key: string;
  tone: 'warn' | 'info';
  title: string;
  detail?: string;
  action?: { label: string; onClick: () => void; busy?: boolean };
}

function Attention({ issues }: { issues: Issue[] }) {
  // a healthy title shows no section at all: the page is just the page
  if (!issues.length) return null;
  return (
    <section className={s.attention} aria-label="Needs attention">
      <h3 className={s.sectionTitle}>
        Needs attention <span className={s.count}>{issues.length}</span>
      </h3>
      <ul className={s.issues}>
        {issues.map((issue) => (
          <li key={issue.key} className={s.issue} data-tone={issue.tone} data-issue={issue.key}>
            <span className={s.issueDot} aria-hidden="true" />
            <div className={s.issueBody}>
              <div className={s.issueTitle}>{issue.title}</div>
              {issue.detail ? <div className={s.issueDetail}>{issue.detail}</div> : null}
            </div>
            {issue.action ? (
              <button
                type="button"
                className={s.btn}
                disabled={issue.action.busy}
                onClick={issue.action.onClick}
              >
                {issue.action.label}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ── one file ─────────────────────────────────────────────────────────────── */

export interface FileActions {
  canManage: boolean;
  recycle: boolean;
  onRename: (f: LibraryFile) => void;
  onDelete: (f: LibraryFile) => void;
  onSubtitles?: (f: LibraryFile) => void;
  /** search releases for this one title or episode */
  onUpgrade?: (f: LibraryFile) => void;
  /** a show's file can be handed to another episode */
  onReassign?: (f: LibraryFile) => void;
}

function fileMenu(f: LibraryFile, a: FileActions): ActionMenuItem[] {
  const items: ActionMenuItem[] = [];
  const reachable = f.reachable !== false;
  if (a.onUpgrade) {
    items.push({
      key: 'upgrade',
      label: f.below_cutoff ? 'Find an upgrade' : 'Search releases',
      onSelect: () => a.onUpgrade?.(f),
    });
  }
  if (a.onSubtitles) {
    items.push({ key: 'subs', label: 'Search subtitles', onSelect: () => a.onSubtitles?.(f) });
  }
  if (a.canManage) {
    items.push({
      key: 'rename',
      label: 'Rename to template',
      disabled: !reachable || f.naming?.conforms === true || f.naming == null,
      title: !reachable
        ? "SoulSync can't reach this file"
        : f.naming?.conforms
          ? 'Already named by your template'
          : undefined,
      onSelect: () => a.onRename(f),
    });
  }
  if (a.canManage && a.onReassign) {
    items.push({
      key: 'reassign',
      label: 'Move to another episode…',
      onSelect: () => a.onReassign?.(f),
    });
  }
  if (a.canManage && f.path) {
    items.push({ key: 'copy', label: 'Copy path', onSelect: () => void copyText(f.path ?? '') });
  }
  if (a.canManage) {
    items.push({
      key: 'delete',
      label: a.recycle ? 'Delete to recycle bin' : 'Delete file',
      danger: true,
      disabled: !reachable,
      title: reachable ? undefined : 'Only your media server can delete this file',
      onSelect: () => a.onDelete(f),
    });
  }
  return items;
}

/** what a whole season shares is said once above it, not on every episode. */
interface Quiet {
  unreachable?: boolean;
  below?: boolean;
}

/** one labelled fact on a file card: a small label, the value, a quiet second line */
function Fact({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className={s.spec}>
      <div className={s.specLabel}>{label}</div>
      <div className={s.specValue}>{value}</div>
      {sub ? (
        <div className={s.specSub} title={sub}>
          {sub}
        </div>
      ) : null}
    </div>
  );
}

/** the subtitle cell: how many there are and where, then which */
function subtitleFact(f: LibraryFile): { value: string; sub: string; all: string } {
  const labelled = subtitleLabels(f.subtitles, f.subtitle_languages);
  if (labelled == null) {
    return {
      value: 'Not known',
      sub: f.reachable === false ? 'SoulSync can’t see the folder' : 'Not read yet',
      all: '',
    };
  }
  if (!labelled.length) {
    return {
      value: 'None',
      // the folder was looked at but the file's own tracks were not read yet
      sub: f.subtitle_languages == null ? 'No subtitle files beside it' : 'No subtitles',
      all: '',
    };
  }
  const inFile = labelled.filter((label) => label.endsWith(' (in file)')).length;
  const where =
    inFile === labelled.length
      ? 'in the file'
      : inFile === 0
        ? 'beside the file'
        : `, ${inFile} in the file`;
  const value = where.startsWith(',')
    ? `${labelled.length}${where}`
    : `${labelled.length} ${where}`;
  // every track inside the file: said once in the value, not after each language
  const names =
    inFile === labelled.length
      ? labelled.map((label) => label.replace(/ \(in file\)$/, ''))
      : labelled;
  return {
    value,
    sub: names.slice(0, 3).join(', ') + (names.length > 3 ? ` and ${names.length - 3} more` : ''),
    all: names.join(', '),
  };
}

/** the last folders of a path, as a trail: "MOVIES / Heat (1995)" */
function folderTrail(path: string | null | undefined): string {
  const parts = folderOf(path).split('/').filter(Boolean);
  return parts.slice(-2).join(' / ');
}

/**
 * one file, as a card. the same on a movie and under an episode.
 *
 * it was a row: every fact on one line and a pile of chips under it, so
 * nothing had a place. now the resolution leads, four labelled facts sit in a
 * grid (picture, sound, subtitles, where), the file name carries its naming
 * status, and the foot says where the file came from. two copies sit side by
 * side so they can be compared; the best one is outlined.
 */
function FileCard({
  f,
  actions,
  quiet = {},
  label,
  extra,
}: {
  f: LibraryFile;
  actions: FileActions;
  quiet?: Quiet;
  label: string;
  /** another copy of the same movie or episode is the best one */
  extra?: boolean;
}) {
  const named = f.quality && f.quality !== f.resolution_label ? f.quality : '';
  const codec = codecLabel(f.video_codec);
  const range = rangeLabel(f.dynamic_range);
  const audio = audioLabel(f);
  const languages = audioLanguagesLabel(f.audio_languages);
  const subs = subtitleFact(f);
  const drive = driveLabel(f.root);
  const trail = folderTrail(f.path);
  const source = f.source;
  const far = f.reachable === false && !quiet.unreachable;
  return (
    <article className={s.fileCard} data-file-id={f.id} data-best={f.best ? 'yes' : 'no'}>
      <header className={s.fileHead}>
        <span className={s.resTile} data-tone={resolutionTone(f.resolution_label)}>
          {f.resolution_label || '—'}
        </span>
        <div className={s.fileHeadText}>
          <div className={s.fileTitleRow}>
            <span className={s.fileTitle}>
              {named || [codec, f.resolution_label].filter(Boolean).join(' ') || 'File'}
            </span>
            {f.best ? <span className={s.best}>Best copy</span> : null}
            {extra && !f.best ? <span className={s.extra}>Extra copy</span> : null}
          </div>
          <div className={s.fileSub}>
            {[formatBytes(f.size_bytes), runtimeLabel(f.runtime_seconds)]
              .filter(Boolean)
              .join(' · ')}
          </div>
        </div>
        <MenuButton label={`${label} actions`} items={fileMenu(f, actions)} />
      </header>
      <div className={s.specs}>
        <Fact label="Picture" value={range || codec || 'Not known'} sub={range ? codec : ''} />
        <Fact
          label="Sound"
          value={audio || 'Not known'}
          sub={languages || (f.audio_languages == null ? 'Languages not read yet' : '')}
        />
        <Fact label="Subtitles" value={subs.value} sub={subs.sub} />
        {f.path ? <Fact label="Where" value={drive || 'Library'} sub={trail} /> : null}
      </div>
      <div className={s.fileFoot}>
        <div className={s.specLabel}>File</div>
        <div className={s.fileName} title={f.path || f.filename}>
          {f.filename}
        </div>
        {f.below_cutoff && !quiet.below ? (
          <div className={s.status} data-tone="warn">
            <span className={s.statusDot} aria-hidden="true" />
            Below your quality target
          </div>
        ) : null}
        {f.naming ? (
          f.naming.conforms ? (
            <div className={s.status} data-tone="ok">
              <span className={s.statusDot} aria-hidden="true" />
              Named by your template
            </div>
          ) : (
            <div className={s.status} data-tone="warn">
              <span className={s.statusDot} aria-hidden="true" />
              <span>Your template names it {f.naming.expected_name ?? 'differently'}</span>
              {actions.canManage ? (
                <button type="button" className={s.linkBtn} onClick={() => actions.onRename(f)}>
                  Rename
                </button>
              ) : null}
            </div>
          )
        ) : null}
        {far ? (
          <div
            className={s.status}
            data-tone="muted"
            title="The file is on a drive only your media server can see. Its facts come from the server."
          >
            <span className={s.statusDot} aria-hidden="true" />
            Only your media server can see this file
          </div>
        ) : null}
      </div>
      <footer className={s.fileSource} title={source?.release ?? undefined}>
        {source
          ? [
              `Downloaded${source.at ? ` ${dateLabel(source.at)}` : ''}`,
              source.indexer || source.via ? `from ${source.indexer || source.via}` : '',
            ]
              .filter(Boolean)
              .join(' ')
          : 'No download on record'}
      </footer>
    </article>
  );
}

/** a title's or an episode's files, side by side */
export function FileCards({
  files,
  actions,
  quiet,
  label,
}: {
  files: LibraryFile[];
  actions: FileActions;
  quiet?: Quiet;
  label: string;
}) {
  return (
    <div className={s.fileCards} data-count={Math.min(files.length, 2)}>
      {/* the best copy leads, whatever order the files were stored in */}
      {[...files]
        .sort((a, b) => Number(Boolean(b.best)) - Number(Boolean(a.best)))
        .map((f) => (
          <FileCard
            key={f.id}
            f={f}
            actions={actions}
            quiet={quiet}
            label={label}
            extra={files.length > 1}
          />
        ))}
    </div>
  );
}

/* ── shared action handlers ───────────────────────────────────────────────── */

export function useFileActions(reload: () => void, kind: TitleKind) {
  const onRename = useCallback(
    async (f: LibraryFile) => {
      const want = f.naming?.expected_name;
      const ok = await confirm({
        title: 'Rename to your template?',
        message: want
          ? `“${f.filename}” becomes “${want}”. Subtitles and sidecars move with it, and nothing is overwritten.`
          : `“${f.filename}” is renamed to your naming template.`,
        confirmText: 'Rename',
      });
      if (!ok) return;
      try {
        const res = await renameFile(f.id);
        toast(res.message || 'Renamed');
        reload();
      } catch (e) {
        toast((e as Error).message || 'Rename failed', 'error');
      }
    },
    [reload],
  );
  const onDelete = useCallback(
    async (f: LibraryFile, recycle: boolean, last: boolean) => {
      const what = kind === 'movie' ? 'movie' : 'episode';
      const ok = await confirm({
        title: recycle ? 'Move to the recycle bin?' : 'Delete this file for good?',
        message:
          `“${f.filename}” ${recycle ? 'moves to the recycle bin, where it can be restored until it expires.' : 'is deleted permanently. Recycling is turned off in your library settings.'}` +
          (last ? ` It’s the only copy, so the ${what} will show as missing.` : ''),
        confirmText: recycle ? 'Move to recycle bin' : 'Delete',
        destructive: true,
      });
      if (!ok) return;
      try {
        const res = await deleteFile(f.id);
        toast(res.recycled ? 'Moved to the recycle bin' : 'File deleted');
        reload();
        if (res.remaining === 0) bridge().reloadPage?.();
      } catch (e) {
        toast((e as Error).message || 'Delete failed', 'error');
      }
    },
    [kind, reload],
  );
  return { onRename, onDelete };
}

function useIdentity(kind: TitleKind, id: number, reload: () => void) {
  const [fixing, setFixing] = useState(false);
  const onRelease = useCallback(async () => {
    const ok = await confirm({
      title: 'Use the server’s match again?',
      message: 'The next scan takes your media server’s title and ids for this again.',
      confirmText: 'Use server match',
    });
    if (!ok) return;
    try {
      await releaseIdentity(kind, id);
      toast('Released — the next scan uses your server’s match');
      reload();
    } catch (e) {
      toast((e as Error).message || 'Couldn’t release the match', 'error');
    }
  }, [kind, id, reload]);
  return { fixing, setFixing, onRelease };
}

/** rename a set of files after one confirm, and say what happened to each */
async function renameMany(files: LibraryFile[], kind: TitleKind, reload: () => void) {
  const ok = await confirm({
    title: `Rename ${plural(files.length, 'file')} to your template?`,
    message:
      'Each file is renamed where it sits, its subtitles and sidecars move with it, and nothing is overwritten. A file that would collide is skipped.',
    confirmText: `Rename ${files.length}`,
  });
  if (!ok) return;
  try {
    const res = await bulkFiles(
      'rename',
      files.map((f) => f.id),
      kind,
    );
    toast(
      res.failed
        ? `Renamed ${res.done}, ${res.failed} couldn’t be renamed`
        : `Renamed ${plural(res.done, 'file')}`,
      res.failed ? 'warning' : 'success',
    );
  } catch (e) {
    toast((e as Error).message || 'Rename failed', 'error');
  }
  reload();
}

/* ── movie ────────────────────────────────────────────────────────────────── */

function MovieView({
  data,
  reload,
  heroSlot,
}: {
  data: MovieLibrary;
  reload: () => void;
  heroSlot?: HTMLElement | null;
}) {
  const { onRename, onDelete } = useFileActions(reload, 'movie');
  const { fixing, setFixing, onRelease } = useIdentity('movie', data.id, reload);
  const [copies, setCopies] = useState(false);
  const [busy, setBusy] = useState(false);
  const best = data.files.find((f) => f.best) ?? data.files[0];
  const q = data.quality;
  const b = bridge();
  const actions: FileActions = {
    canManage: data.can_manage,
    recycle: data.recycle,
    onRename,
    onDelete: (f) => onDelete(f, data.recycle, data.files.length === 1),
    onSubtitles: b.subtitleSearch
      ? () => b.subtitleSearch?.('movie', data.id, data.title, '')
      : undefined,
    onUpgrade: b.manualSearch ? () => b.manualSearch?.('movie') : undefined,
  };

  const upgrade = async () => {
    if (!data.identity.tmdb_id) return;
    setBusy(true);
    try {
      await wishlistMovie({
        tmdb_id: data.identity.tmdb_id,
        title: data.title,
        year: data.year,
        library_id: data.id,
      });
      toast('On the wishlist and searching. A better copy replaces this one when it lands.');
    } catch (e) {
      toast((e as Error).message || 'Couldn’t start the search', 'error');
    }
    setBusy(false);
  };

  // a grab that is running or stuck is this title's news too. it used to be a
  // second "Needs attention" block further down the page
  const [acq, setAcq] = useState<AcquisitionState | null>(null);
  useEffect(() => {
    let live = true;
    fetchAcquisition('movie', data.id)
      .then((state) => live && setAcq(state))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [data.id]);
  const stuck = acq?.counts?.failed ?? 0;
  const moving = (acq?.counts?.queued ?? 0) + (acq?.counts?.downloading ?? 0);

  const drifted = data.files.filter((f) => f.reachable && f.naming && !f.naming.conforms);
  const extra = data.extra_copies;
  const issues: Issue[] = [];
  if (stuck) {
    issues.push({
      key: 'stuck',
      tone: 'warn',
      title: stuck === 1 ? 'A download for this movie is stuck' : `${stuck} downloads are stuck`,
      detail: 'The last tries failed. Search again to pick another release.',
      action: b.manualSearch
        ? { label: 'Search releases', onClick: () => b.manualSearch?.('movie') }
        : undefined,
    });
  } else if (moving) {
    issues.push({
      key: 'moving',
      tone: 'info',
      title: 'Downloading now',
      detail: 'A grab for this movie is on its way. It shows here when it lands.',
    });
  }
  if (!data.identity.tmdb_id) {
    issues.push({
      key: 'match',
      tone: 'warn',
      title: 'SoulSync couldn’t tell which movie this is',
      detail: 'Artwork, cast and upgrades are guesswork until it is matched.',
      action: data.can_manage
        ? { label: 'Find the right title', onClick: () => setFixing(true) }
        : undefined,
    });
  }
  if (best && q.meets === false) {
    issues.push({
      key: 'quality',
      tone: 'warn',
      title: q.reasons?.find((r) => !r.ok)?.text ?? `Below your ${q.cutoff_label} target`,
      detail:
        'Looking for an upgrade puts it on the wishlist. A better copy replaces this file when one turns up.',
      action: data.identity.tmdb_id
        ? { label: 'Look for an upgrade', onClick: () => void upgrade(), busy }
        : undefined,
    });
  }
  if (extra && extra.files > 0) {
    issues.push({
      key: 'copies',
      tone: 'info',
      title: `${plural(extra.files, 'extra copy', 'extra copies')} of this movie`,
      detail: `${formatBytes(extra.size_bytes) || 'No space'} held by copies you don’t need${
        data.copies.spans_drives ? ', on different drives' : ''
      }.`,
      action: data.can_manage
        ? { label: 'Keep the best…', onClick: () => setCopies(true) }
        : undefined,
    });
  }
  if (drifted.length) {
    issues.push({
      key: 'naming',
      tone: 'info',
      title: `${plural(drifted.length, 'file')} not named by your template`,
      detail: drifted[0]?.naming?.expected_name
        ? `Would become “${drifted[0].naming.expected_name}”.`
        : undefined,
      action: data.can_manage
        ? {
            label: drifted.length === 1 ? 'Rename' : 'Rename all',
            onClick: () =>
              drifted.length === 1 && drifted[0]
                ? onRename(drifted[0])
                : void renameMany(drifted, 'movie', reload),
          }
        : undefined,
    });
  }

  // the page's library line: what the summary card used to say, in one row.
  // on the page it sits in the hero (a slot the page hands over); in a test or
  // an older shell with no slot it leads the panel instead.
  const line = (
    <div className={s.libLine} data-card="have">
      {best ? (
        <>
          <ResBadge label={best.resolution_label} size="lg" />
          <span className={s.libFact}>
            {plural(data.files.length, 'copy', 'copies')} · {formatBytes(data.summary.size_bytes)}
            {data.copies.roots.length > 1 ? ` on ${data.copies.roots.length} drives` : ''}
          </span>
          <span className={s.libFact} data-tone={q.meets === false ? 'warn' : 'ok'}>
            {q.meets === false
              ? (q.reasons?.find((r) => !r.ok)?.text ?? `Below your ${q.cutoff_label} target`)
              : `Meets your ${q.cutoff_label} target`}
          </span>
        </>
      ) : (
        <span className={s.libFact}>No files on record for this movie.</span>
      )}
      <span className={s.libDim}>{q.profile_name} profile</span>
      {data.summary.added_at ? (
        <span className={s.libDim}>Added {dateLabel(data.summary.added_at)}</span>
      ) : null}
      {!data.identity.tmdb_id ? (
        <Chip tone="warn">Not matched</Chip>
      ) : (
        <span className={s.libDim}>
          TMDB {data.identity.tmdb_id}
          {data.identity.imdb_id ? ` · IMDb ${data.identity.imdb_id}` : ''}
        </span>
      )}
      {data.identity.fixed ? <Chip tone="ok">Fixed by you</Chip> : null}
      {data.can_manage ? (
        <span className={s.libLinks}>
          {b.openManage ? (
            <button type="button" className={s.linkBtn} onClick={() => b.openManage?.()}>
              Quality and download settings
            </button>
          ) : null}
          <button type="button" className={s.linkBtn} onClick={() => setFixing(true)}>
            {data.identity.tmdb_id ? 'Fix match' : 'Find the right title'}
          </button>
          {data.identity.fixed ? (
            <button type="button" className={s.linkBtn} onClick={onRelease}>
              Use the server’s match again
            </button>
          ) : null}
        </span>
      ) : null}
    </div>
  );

  return (
    <div className={s.tab} data-layout="page">
      {heroSlot ? createPortal(line, heroSlot) : line}

      <Attention issues={issues} />

      <section>
        <div className={s.sectionHead}>
          <h3 className={s.sectionTitle}>
            Files <span className={s.count}>{data.files.length}</span>
          </h3>
          {b.manualSearch ? (
            <button
              type="button"
              className={q.meets === false ? s.btnPrimary : s.btn}
              onClick={() => b.manualSearch?.('movie')}
            >
              {q.meets === false ? 'Find an upgrade' : 'Search releases'}
            </button>
          ) : null}
        </div>
        <FileCards files={data.files} actions={actions} label="File" />
      </section>

      {fixing ? (
        <FixMatchDialog
          kind="movie"
          id={data.id}
          title={data.title}
          year={data.year}
          onClose={() => setFixing(false)}
          onFixed={() => {
            setFixing(false);
            b.reloadPage?.();
            reload();
          }}
        />
      ) : null}
      {copies ? (
        <CopiesDialog
          kind="movie"
          id={data.id}
          recycle={data.recycle}
          onClose={() => setCopies(false)}
          onDone={() => {
            setCopies(false);
            reload();
          }}
        />
      ) : null}
    </div>
  );
}

/* ── show ─────────────────────────────────────────────────────────────────── */

function wishOf(showId: number, season: LibrarySeason, ep: LibraryEpisode): WishEpisode {
  return {
    season_number: season.season_number,
    episode_number: ep.episode_number,
    title: ep.title,
    air_date: ep.air_date,
    still_url: ep.has_still ? `/api/video/poster/episode/${ep.id}` : null,
    season_poster_url:
      season.has_poster && season.id != null
        ? `/api/video/poster/season/${season.id}`
        : `/api/video/poster/show/${showId}`,
  };
}

function ShowView({
  data,
  reload,
  page,
}: {
  data: ShowLibrary;
  reload: () => void;
  /** where the page wants each part; without one it all renders in place */
  page: PageSlots;
}) {
  const { fixing, setFixing, onRelease } = useIdentity('show', data.id, reload);
  const [copies, setCopies] = useState(false);
  const [consolidating, setConsolidating] = useState(false);
  const [job, setJob] = useState<ConsolidateState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const sm = data.summary;
  const aired = sm.owned + sm.missing;
  const q = data.quality;
  const tmdb = data.identity.tmdb_id;

  // a move started earlier (or from another tab) is still this show's news
  useEffect(() => {
    if (!data.can_manage || sm.folders.length < 2) return;
    let live = true;
    fetchConsolidateState(data.id)
      .then((state) => live && setJob(state.known && state.running ? state : null))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [data.id, data.can_manage, sm.folders.length]);

  const wish = useCallback(
    async (episodes: WishEpisode[], what: string) => {
      if (!tmdb || !episodes.length) return;
      try {
        await wishlistEpisodes({ tmdb_id: tmdb, title: data.title, library_id: data.id }, episodes);
        toast(`${plural(episodes.length, 'episode')} on the wishlist and searching. ${what}`);
      } catch (e) {
        toast((e as Error).message || 'Couldn’t start the search', 'error');
      }
    },
    [tmdb, data.title, data.id],
  );

  const below = useMemo(
    () =>
      data.seasons.flatMap((season) =>
        season.episodes
          .filter((ep) => ep.files.length && ep.files.every((f) => f.below_cutoff))
          .map((ep) => wishOf(data.id, season, ep)),
      ),
    [data],
  );
  const missing = useMemo(
    () =>
      data.seasons
        .filter((season) => season.season_number !== 0)
        .flatMap((season) =>
          season.episodes
            .filter((ep) => !ep.has_file && ep.aired)
            .map((ep) => wishOf(data.id, season, ep)),
        ),
    [data],
  );

  const run = async (key: string, episodes: WishEpisode[], what: string) => {
    setBusy(key);
    await wish(episodes, what);
    setBusy(null);
  };

  // the page's own episode list hands over what is ticked there, and the
  // library data goes back so each of its rows can show its file
  const [pickedIds, setPickedIds] = useState<number[]>([]);
  useEffect(() => {
    const onPick = (event: Event) => {
      const detail = (event as CustomEvent<{ showId?: number; episodeIds?: number[] }>).detail;
      if (detail?.showId === data.id) setPickedIds(detail.episodeIds ?? []);
    };
    document.addEventListener(SELECTION_EVENT, onPick);
    return () => document.removeEventListener(SELECTION_EVENT, onPick);
  }, [data.id]);
  useEffect(() => {
    bridge().onLibrary?.(data);
  }, [data]);
  const picked = useMemo(() => {
    const want = new Set(pickedIds);
    return data.seasons.flatMap((season) =>
      season.episodes
        .filter((ep) => want.has(ep.id) && ep.files.length)
        .map((ep) => ({ season, ep })),
    );
  }, [data, pickedIds]);
  const clearPicked = () => {
    setPickedIds([]);
    bridge().clearSelection?.();
  };
  const pickedFiles = picked.flatMap(({ ep }) => ep.files);
  const upgradePicked = async () => {
    setBusy('bulk');
    await wish(
      picked.map(({ season, ep }) => wishOf(data.id, season, ep)),
      'A better copy replaces the file when it lands.',
    );
    setBusy(null);
    clearPicked();
  };
  const renamePicked = async () => {
    // the names were only checked for a season that was opened, so every file
    // is offered and the server leaves the ones already named right alone
    const ok = await confirm({
      title: `Rename ${plural(pickedFiles.length, 'file')} to your template?`,
      message:
        'Each file is renamed where it sits, its subtitles and sidecars move with it, and nothing is overwritten. A file already named by your template, or one SoulSync can’t reach, is left as it is.',
      confirmText: 'Rename',
    });
    if (!ok) return;
    setBusy('bulk');
    try {
      const res = await bulkFiles(
        'rename',
        pickedFiles.map((f) => f.id),
        'show',
      );
      toast(
        res.failed
          ? `Renamed ${res.done}, ${res.failed} couldn’t be renamed`
          : res.done
            ? `Renamed ${plural(res.done, 'file')}`
            : 'Every file was already named by your template',
        res.failed ? 'warning' : 'success',
      );
    } catch (e) {
      toast((e as Error).message || 'Rename failed', 'error');
    }
    setBusy(null);
    clearPicked();
    reload();
  };
  const deletePicked = async () => {
    const ok = await confirm({
      title: data.recycle
        ? `Move ${plural(pickedFiles.length, 'file')} to the recycle bin?`
        : `Delete ${plural(pickedFiles.length, 'file')} for good?`,
      message: `Every file of the ${plural(picked.length, 'episode')} you picked ${data.recycle ? 'moves to the recycle bin, where it can be restored until it expires' : 'is deleted permanently. Recycling is turned off in your library settings'}. Those episodes will show as missing. A file only your media server can see is left alone.`,
      confirmText: data.recycle ? 'Move to recycle bin' : 'Delete',
      destructive: true,
    });
    if (!ok) return;
    setBusy('bulk');
    try {
      const res = await bulkFiles(
        'delete',
        pickedFiles.map((f) => f.id),
        'show',
      );
      toast(
        res.failed
          ? `Removed ${res.done}, ${res.failed} couldn’t be removed`
          : `Removed ${plural(res.done, 'file')}`,
        res.failed ? 'warning' : 'success',
      );
    } catch (e) {
      toast((e as Error).message || 'Delete failed', 'error');
    }
    setBusy(null);
    clearPicked();
    reload();
    bridge().reloadPage?.();
  };

  // grabs that are running or stuck (the page's old second block)
  const [acq, setAcq] = useState<AcquisitionState | null>(null);
  useEffect(() => {
    let live = true;
    fetchAcquisition('show', data.id)
      .then((state) => live && setAcq(state))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [data]);
  const stuck = acq?.counts?.failed ?? 0;
  const moving = (acq?.counts?.queued ?? 0) + (acq?.counts?.downloading ?? 0);

  const extra = data.extra_copies;
  const issues: Issue[] = [];
  if (stuck) {
    issues.push({
      key: 'stuck',
      tone: 'warn',
      title: `${plural(stuck, 'download')} stuck`,
      detail:
        'The last tries failed. A season’s menu has Clear failures, which searches every source again.',
    });
  }
  if (moving) {
    issues.push({
      key: 'moving',
      tone: 'info',
      title: `${plural(moving, 'episode')} downloading now`,
      detail: 'Each one shows its progress on its own row below.',
    });
  }
  if (!tmdb) {
    issues.push({
      key: 'match',
      tone: 'warn',
      title: 'SoulSync couldn’t tell which show this is',
      detail: 'Artwork, episodes and upgrades are guesswork until it is matched.',
      action: data.can_manage
        ? { label: 'Find the right title', onClick: () => setFixing(true) }
        : undefined,
    });
  }
  if (missing.length) {
    issues.push({
      key: 'missing',
      tone: 'warn',
      title: `${plural(missing.length, 'aired episode')} missing`,
      detail: 'Getting them puts each on the wishlist and searches now.',
      action: tmdb
        ? {
            label: 'Get missing',
            busy: busy === 'missing',
            onClick: () => void run('missing', missing, 'Each one is grabbed as it turns up.'),
          }
        : undefined,
    });
  }
  if (q.below_cutoff) {
    issues.push({
      key: 'quality',
      tone: 'warn',
      title: `${plural(q.below_cutoff, 'file')} below your ${q.cutoff_label} target`,
      detail:
        'Looking for upgrades puts those episodes on the wishlist. A better copy replaces the file when one turns up.',
      action:
        tmdb && below.length
          ? {
              label: 'Look for upgrades',
              busy: busy === 'quality',
              onClick: () =>
                void run('quality', below, 'A better copy replaces the file when it lands.'),
            }
          : undefined,
    });
  }
  if (extra && extra.files > 0) {
    issues.push({
      key: 'copies',
      tone: 'info',
      title: `${plural(extra.titles, 'episode has', 'episodes have')} more than one copy`,
      detail: `${plural(extra.files, 'extra file')} holding ${formatBytes(extra.size_bytes) || 'no space'}.`,
      action: data.can_manage
        ? { label: 'Keep the best…', onClick: () => setCopies(true) }
        : undefined,
    });
  }
  if (sm.folders.length > 1) {
    issues.push({
      key: 'folders',
      tone: 'info',
      title: job?.running
        ? `Bringing it into one folder: ${job.done ?? 0} of ${job.total ?? 0} moved`
        : `Split across ${sm.folders.length} folders`,
      detail: job?.running
        ? job.current || undefined
        : `${sm.folders
            .slice(0, 3)
            .map((f) => `${f.files} in ${driveLabel(f.root) || f.name || 'a folder'}`)
            .join(', ')}${sm.folders.length > 3 ? ' and more' : ''}.`,
      action: data.can_manage
        ? {
            label: job?.running ? 'Show progress' : 'Bring together…',
            onClick: () => setConsolidating(true),
          }
        : undefined,
    });
  }

  const folders =
    data.can_manage && sm.folders.length ? (
      <section>
        <h3 className={s.sectionTitle}>
          Where it lives <span className={s.count}>{sm.folders.length}</span>
        </h3>
        <ul className={s.folders}>
          {sm.folders.map((fo) => (
            <li key={fo.path ?? ''} className={s.folder}>
              <span className={s.folderIcon} aria-hidden="true" />
              <div className={s.folderBody}>
                <div className={s.folderName}>
                  {driveLabel(fo.root) ? (
                    <span className={s.drive}>{driveLabel(fo.root)}</span>
                  ) : null}
                  {fo.name || folderName(fo.path)}
                  {fo.main && sm.folders.length > 1 ? <Chip tone="muted">Most files</Chip> : null}
                </div>
                <div className={s.path} title={fo.path ?? ''}>
                  {fo.path}
                </div>
              </div>
              <span className={s.folderCount}>
                {plural(fo.files, 'file')}
                {fo.size_bytes ? ` · ${formatBytes(fo.size_bytes)}` : ''}
              </span>
              {fo.path ? (
                <button
                  type="button"
                  className={s.quietBtn}
                  onClick={() => void copyText(fo.path ?? '')}
                >
                  Copy path
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    ) : null;

  // the page's library line: what the summary card says, in one row, for the
  // hero. the episode list is the page's own, so the season list is not drawn.
  const line = (
    <div className={s.libLine} data-card="have">
      <span className={s.libCount}>
        <b>{sm.owned}</b> of {aired || sm.total} aired episodes
      </span>
      <span className={s.libFact}>{formatBytes(sm.size_bytes) || 'no files'}</span>
      {sm.res_mix.length ? (
        <span className={s.libMix}>
          <span
            className={s.mixBar}
            role="img"
            aria-label={sm.res_mix.map((m) => `${m.count} ${m.label}`).join(', ')}
          >
            {sm.res_mix.map((m) => (
              <span
                key={m.label}
                className={s.mixSeg}
                data-tone={resolutionTone(m.label)}
                style={{ flexGrow: m.count }}
              />
            ))}
          </span>
          {sm.res_mix.map((m) => `${m.label} ${m.count}`).join(' · ')}
        </span>
      ) : null}
      <span className={s.libDim}>
        Target {q.cutoff_label} · {q.profile_name} profile
      </span>
      {!tmdb ? <Chip tone="warn">Not matched</Chip> : <span className={s.libDim}>TMDB {tmdb}</span>}
      {data.identity.fixed ? <Chip tone="ok">Fixed by you</Chip> : null}
      {data.can_manage ? (
        <span className={s.libLinks}>
          {bridge().openManage ? (
            <button type="button" className={s.linkBtn} onClick={() => bridge().openManage?.()}>
              Quality and download settings
            </button>
          ) : null}
          <button type="button" className={s.linkBtn} onClick={() => setFixing(true)}>
            {tmdb ? 'Fix match' : 'Find the right title'}
          </button>
          {data.identity.fixed ? (
            <button type="button" className={s.linkBtn} onClick={onRelease}>
              Use the server’s match again
            </button>
          ) : null}
        </span>
      ) : null}
    </div>
  );

  const dialogs = (
    <>
      {fixing ? (
        <FixMatchDialog
          kind="show"
          id={data.id}
          title={data.title}
          year={data.year}
          onClose={() => setFixing(false)}
          onFixed={() => {
            setFixing(false);
            bridge().reloadPage?.();
            reload();
          }}
        />
      ) : null}
      {copies ? (
        <CopiesDialog
          kind="show"
          id={data.id}
          recycle={data.recycle}
          onClose={() => setCopies(false)}
          onDone={() => {
            setCopies(false);
            reload();
          }}
        />
      ) : null}
      {consolidating ? (
        <ConsolidateDialog
          show={data}
          running={job}
          onClose={() => {
            setConsolidating(false);
            fetchConsolidateState(data.id)
              .then((state) => setJob(state.known && state.running ? state : null))
              .catch(() => {});
          }}
          onDone={() => {
            setConsolidating(false);
            setJob(null);
            reload();
          }}
        />
      ) : null}
    </>
  );

  const selectBar = picked.length ? (
    <div className={s.selectBar} data-active="yes">
      <span className={s.selectAll}>
        {plural(picked.length, 'episode')} selected
        <button type="button" className={s.linkBtn} onClick={clearPicked}>
          Clear
        </button>
      </span>
      <div className={s.selectActions}>
        {tmdb ? (
          <button
            type="button"
            className={s.btn}
            disabled={busy === 'bulk'}
            onClick={() => void upgradePicked()}
          >
            Look for upgrades
          </button>
        ) : null}
        <button
          type="button"
          className={s.btn}
          disabled={busy === 'bulk'}
          onClick={() => void renamePicked()}
        >
          Rename to template
        </button>
        <button
          type="button"
          className={s.btnDanger}
          disabled={busy === 'bulk'}
          onClick={() => void deletePicked()}
        >
          Delete
        </button>
      </div>
    </div>
  ) : null;

  return (
    <div className={s.tab} data-layout="page">
      {page.hero ? createPortal(line, page.hero) : line}
      <Attention issues={issues} />
      {page.select && selectBar ? createPortal(selectBar, page.select) : selectBar}
      {page.foot ? createPortal(folders, page.foot) : folders}
      {dialogs}
    </div>
  );
}

/* ── the tab ──────────────────────────────────────────────────────────────── */

export function LibraryTab({
  kind,
  id,
  heroSlot,
  slots,
}: {
  kind: TitleKind;
  id: number;
  /** where the page wants the library line: an element in its hero */
  heroSlot?: HTMLElement | null;
  /** the one-page layout: where the page wants each part (a show needs all three) */
  slots?: PageSlots | null;
}) {
  const [data, setData] = useState<TitleLibrary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  // a show's episodes come back with their files from the database; the
  // disk checks are asked per season by whichever episode is opened
  const load = useCallback(() => {
    const mine = ++seq.current;
    fetchTitleLibrary(kind, id, null)
      .then((res) => {
        if (mine !== seq.current) return;
        setData(res);
        setError(null);
      })
      .catch((e: Error) => {
        if (mine !== seq.current) return;
        setError(e.message || 'Couldn’t read this title’s files');
      });
  }, [kind, id]);

  useEffect(() => {
    setData(null);
    load();
  }, [load]);

  // a change made anywhere on the page (an episode's own cards are their own
  // React root) reaches every reader through the store
  const reload = useCallback(() => invalidate(kind, id), [kind, id]);
  useEffect(
    () =>
      onInvalidate((changedKind, changedId) => {
        if (changedKind === kind && changedId === id) load();
      }),
    [kind, id, load],
  );

  if (error && !data) {
    return (
      <div className={s.tab}>
        <p className={s.empty}>{error}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className={s.tab} aria-busy="true">
        <div className={s.skeleton} />
        <div className={s.skeletonRow} />
        <div className={s.skeletonRow} />
      </div>
    );
  }
  if (data.kind === 'movie') {
    return <MovieView data={data} reload={reload} heroSlot={slots?.hero ?? heroSlot} />;
  }
  return <ShowView data={data} reload={reload} page={slots ?? {}} />;
}
