/**
 * the "your library" tab on a movie or show page: what you own of the title,
 * where every file sits, how good it is against your profile, and the
 * file-level actions (rename to the template, delete to the recycle bin, fix
 * the match). the music side has had this beside an artist's discography; the
 * video side only ever printed a quality line.
 *
 * the page around it is vanilla (video-detail.js). it mounts this through
 * window.VideoLibraryTab, and this hands the acquisition flows it doesn't own
 * (manual search, season grabs, subtitle search, the manage panel) back to the
 * page through window.SoulSyncVideoDetail.
 */

import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';

import { ActionMenu, type ActionMenuItem } from '@/routes/artist-detail/-ui/action-menu';

import type {
  Identity,
  LibraryEpisode,
  LibraryFile,
  LibrarySeason,
  MovieLibrary,
  ShowLibrary,
  TitleKind,
  TitleLibrary,
} from './types';

import {
  deleteFile,
  fetchTitleLibrary,
  releaseIdentity,
  renameFile,
  setSeasonMonitored,
} from './api';
import { FixMatchDialog } from './fix-match-dialog';
import { dateLabel, fileFacts, formatBytes, pad2, resolutionTone, subtitleLabel } from './format';
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
}

declare global {
  interface Window {
    SoulSyncVideoDetail?: VideoDetailBridge;
  }
}

function bridge(): VideoDetailBridge {
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

function Bar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className={s.bar} role="img" aria-label={label} title={label}>
      <span className={s.barFill} style={{ width: `${pct}%` }} />
    </div>
  );
}

function ResMix({ mix }: { mix: { label: string; count: number }[] }) {
  const total = mix.reduce((n, m) => n + m.count, 0);
  if (!total) return null;
  return (
    <div className={s.mix}>
      <div
        className={s.mixBar}
        role="img"
        aria-label={mix.map((m) => `${m.count} ${m.label}`).join(', ')}
      >
        {mix.map((m) => (
          <span
            key={m.label}
            className={s.mixSeg}
            data-tone={resolutionTone(m.label)}
            style={{ flexGrow: m.count }}
          />
        ))}
      </div>
      <div className={s.mixLegend}>
        {mix.map((m) => (
          <span key={m.label} className={s.mixKey}>
            <span className={s.mixDot} data-tone={resolutionTone(m.label)} />
            {m.label} <b>{m.count}</b>
          </span>
        ))}
      </div>
    </div>
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

/* ── one file ─────────────────────────────────────────────────────────────── */

interface FileActions {
  canManage: boolean;
  recycle: boolean;
  onRename: (f: LibraryFile) => void;
  onDelete: (f: LibraryFile) => void;
  onSubtitles?: (f: LibraryFile) => void;
}

function fileMenu(f: LibraryFile, a: FileActions): ActionMenuItem[] {
  const items: ActionMenuItem[] = [];
  const reachable = f.reachable !== false;
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
  if (a.onSubtitles) {
    items.push({ key: 'subs', label: 'Search subtitles', onSelect: () => a.onSubtitles?.(f) });
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

function FileStatus({
  f,
  canManage,
  onRename,
  quiet = {},
}: {
  f: LibraryFile;
  canManage: boolean;
  onRename: (f: LibraryFile) => void;
  quiet?: Quiet;
}) {
  const chips: ReactNode[] = [];
  if (f.reachable === false && !quiet.unreachable) {
    chips.push(
      <Chip
        key="far"
        tone="muted"
        title="The file is on a drive only your media server can see. Its facts come from the server."
      >
        Only your media server can see this file
      </Chip>,
    );
  }
  if (f.subtitles) {
    chips.push(
      f.subtitles.length ? (
        <Chip key="subs" tone="info">
          <span className={s.cc}>CC</span>
          {f.subtitles.map(subtitleLabel).join(', ')}
        </Chip>
      ) : (
        <Chip key="subs" tone="muted">
          No subtitle files beside it
        </Chip>
      ),
    );
  }
  if (f.naming) {
    chips.push(
      f.naming.conforms ? (
        <Chip key="name" tone="ok">
          ✓ Named by your template
        </Chip>
      ) : (
        <span key="name" className={s.rename}>
          <Chip tone="warn" title={f.naming.expected_name}>
            Template name: {f.naming.expected_name ?? 'differs'}
          </Chip>
          {canManage ? (
            <button type="button" className={s.linkBtn} onClick={() => onRename(f)}>
              Rename
            </button>
          ) : null}
        </span>
      ),
    );
  }
  if (f.below_cutoff && !quiet.below) {
    chips.push(
      <Chip key="cut" tone="warn">
        Below your quality target
      </Chip>,
    );
  }
  return chips.length ? <div className={s.chips}>{chips}</div> : null;
}

function FileCard({ f, actions }: { f: LibraryFile; actions: FileActions }) {
  // the quality tag leads when it says more than the badge ("Bluray-1080p");
  // without one the facts line takes the heading, so nothing reads twice
  const named = !!f.quality && f.quality !== f.resolution_label;
  const facts = fileFacts(f).join(' · ');
  return (
    <article className={s.file} data-file-id={f.id}>
      <div className={s.fileHead}>
        <ResBadge label={f.resolution_label} />
        <div className={s.fileTitle}>
          <span className={named ? s.fileQuality : s.fileFactsHead}>
            {named ? f.quality : facts}
          </span>
          {f.best ? <span className={s.best}>Best copy</span> : null}
        </div>
        <MenuButton label="File actions" items={fileMenu(f, actions)} />
      </div>
      {named && facts ? <div className={s.facts}>{facts}</div> : null}
      <div className={s.fileName} title={f.filename}>
        {f.filename}
      </div>
      {f.path ? (
        <div className={s.path} title={f.path}>
          {folderOf(f.path)}
        </div>
      ) : null}
      <FileStatus f={f} canManage={actions.canManage} onRename={actions.onRename} />
    </article>
  );
}

/* ── identity ─────────────────────────────────────────────────────────────── */

function IdentityCard({
  identity,
  canManage,
  onFix,
  onRelease,
}: {
  identity: Identity;
  canManage: boolean;
  onFix: () => void;
  onRelease: () => void;
}) {
  const ids = [
    identity.tmdb_id ? `TMDB ${identity.tmdb_id}` : null,
    identity.imdb_id ? `IMDb ${identity.imdb_id}` : null,
    identity.tvdb_id ? `TVDB ${identity.tvdb_id}` : null,
  ].filter(Boolean);
  const unmatched = !identity.tmdb_id;
  return (
    <section className={s.card}>
      <div className={s.cardHead}>
        <h3 className={s.cardTitle}>Match</h3>
        {identity.fixed ? <Chip tone="ok">Fixed by you</Chip> : null}
        {unmatched ? <Chip tone="warn">Not matched</Chip> : null}
      </div>
      <p className={s.cardText}>
        {unmatched
          ? 'SoulSync couldn’t tell which title this is, so artwork, cast and upgrades are guesswork.'
          : ids.join(' · ')}
      </p>
      {identity.fixed ? (
        <p className={s.note}>Locked: scans keep this match even if your server says otherwise.</p>
      ) : null}
      {canManage ? (
        <div className={s.cardActions}>
          <button type="button" className={s.btn} onClick={onFix}>
            {unmatched ? 'Find the right title' : 'Fix match'}
          </button>
          {identity.fixed ? (
            <button type="button" className={s.linkBtn} onClick={onRelease}>
              Use the server’s match again
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/* ── shared action handlers ───────────────────────────────────────────────── */

function useFileActions(reload: () => void, kind: TitleKind) {
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

/* ── movie ────────────────────────────────────────────────────────────────── */

function MovieView({ data, reload }: { data: MovieLibrary; reload: () => void }) {
  const { onRename, onDelete } = useFileActions(reload, 'movie');
  const { fixing, setFixing, onRelease } = useIdentity('movie', data.id, reload);
  const best = data.files.find((f) => f.best) ?? data.files[0];
  const q = data.quality;
  const actions: FileActions = {
    canManage: data.can_manage,
    recycle: data.recycle,
    onRename,
    onDelete: (f) => onDelete(f, data.recycle, data.files.length === 1),
    onSubtitles: bridge().subtitleSearch
      ? () => bridge().subtitleSearch?.('movie', data.id, data.title, '')
      : undefined,
  };
  const forked = data.copies.spans_drives && data.copies.same_resolution;
  return (
    <div className={s.tab}>
      <div className={s.grid}>
        <section className={s.card} data-card="have">
          <div className={s.cardHead}>
            <h3 className={s.cardTitle}>What you have</h3>
          </div>
          {best ? (
            <div className={s.hero}>
              <ResBadge label={best.resolution_label} size="lg" />
              <div>
                <div className={s.heroLine}>
                  {data.files.length === 1 ? '1 copy' : `${data.files.length} copies`} ·{' '}
                  {formatBytes(data.summary.size_bytes)}
                  {data.copies.roots.length > 1 ? ` on ${data.copies.roots.length} drives` : ''}
                </div>
                <div className={s.heroSub}>
                  {[
                    data.summary.added_at ? `Added ${dateLabel(data.summary.added_at)}` : '',
                    data.summary.watched ? 'Watched' : 'Not watched yet',
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </div>
              </div>
            </div>
          ) : (
            <p className={s.cardText}>No files on record for this movie.</p>
          )}
          <div className={s.quality} data-meets={q.meets ? 'yes' : 'no'}>
            <span className={s.qualityIcon} aria-hidden="true">
              {q.meets ? '✓' : '↑'}
            </span>
            <span>
              {q.meets
                ? `Meets your ${q.cutoff_label} target`
                : (q.reasons?.find((r) => !r.ok)?.text ?? 'Eligible for an upgrade')}
              <span className={s.muted}> · {q.profile_name} profile</span>
            </span>
          </div>
          <div className={s.cardActions}>
            {bridge().manualSearch ? (
              <button
                type="button"
                className={q.meets ? s.btn : s.btnPrimary}
                onClick={() => bridge().manualSearch?.('movie')}
              >
                {q.meets ? 'Search releases' : 'Find an upgrade'}
              </button>
            ) : null}
            {data.can_manage && bridge().openManage ? (
              <button type="button" className={s.linkBtn} onClick={() => bridge().openManage?.()}>
                Quality and download settings
              </button>
            ) : null}
          </div>
        </section>
        <IdentityCard
          identity={data.identity}
          canManage={data.can_manage}
          onFix={() => setFixing(true)}
          onRelease={onRelease}
        />
      </div>
      {forked ? (
        <p className={s.callout}>
          Two copies at the same quality on different drives. One is usually left over from an
          upgrade that couldn’t find the original.
        </p>
      ) : null}
      <h3 className={s.sectionTitle}>
        Files <span className={s.count}>{data.files.length}</span>
      </h3>
      <div className={s.files}>
        {data.files.map((f) => (
          <FileCard key={f.id} f={f} actions={actions} />
        ))}
      </div>
      {fixing ? (
        <FixMatchDialog
          kind="movie"
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
    </div>
  );
}

/* ── show ─────────────────────────────────────────────────────────────────── */

function seasonMenu(season: LibrarySeason, canManage: boolean): ActionMenuItem[] {
  const b = bridge();
  const items: ActionMenuItem[] = [];
  const n = season.season_number;
  if (season.missing && b.grabSeason) {
    items.push({
      key: 'grab',
      label: `Grab ${season.missing} missing`,
      onSelect: () => b.grabSeason?.(n),
    });
  }
  if (b.manualSearch) {
    items.push({
      key: 'search',
      label: 'Manual search',
      onSelect: () => b.manualSearch?.('season', n),
    });
  }
  if (season.missing && b.wishlistSeason) {
    items.push({ key: 'wish', label: 'Wishlist missing', onSelect: () => b.wishlistSeason?.(n) });
  }
  if (canManage && b.clearSeasonFailures) {
    items.push({
      key: 'retry',
      label: 'Clear failures and search again',
      onSelect: () => b.clearSeasonFailures?.(n),
    });
  }
  return items;
}

function EpisodeRow({
  ep,
  season,
  actions,
  quiet,
}: {
  ep: LibraryEpisode;
  season: number;
  actions: Omit<FileActions, 'onSubtitles'>;
  quiet: Quiet;
}) {
  const label = `S${pad2(season)}E${pad2(ep.episode_number)}`;
  const b = bridge();
  const fileActions: FileActions = {
    ...actions,
    onSubtitles: b.subtitleSearch
      ? () =>
          b.subtitleSearch?.('episode', ep.id, ep.title || `Episode ${ep.episode_number}`, label)
      : undefined,
  };
  return (
    <li className={s.ep} data-ep-id={ep.id} data-owned={ep.has_file ? 'yes' : 'no'}>
      <div className={s.epHead}>
        <span className={s.epNum}>{pad2(ep.episode_number)}</span>
        <span className={s.epTitle}>{ep.title || `Episode ${ep.episode_number}`}</span>
        {ep.watched ? (
          <span className={s.watched} title="Watched">
            ✓
          </span>
        ) : null}
        {!ep.has_file ? (
          ep.aired ? (
            <>
              <span className={s.missing}>Missing</span>
              {b.manualSearch ? (
                <button
                  type="button"
                  className={s.linkBtn}
                  onClick={() => b.manualSearch?.('episode', season, ep.episode_number)}
                >
                  Search
                </button>
              ) : null}
            </>
          ) : (
            <span className={s.muted}>
              {ep.air_date ? `Airs ${dateLabel(ep.air_date)}` : 'Not aired yet'}
            </span>
          )
        ) : null}
      </div>
      {ep.files.map((f) => (
        <div key={f.id} className={s.epFile}>
          <ResBadge label={f.resolution_label} />
          <div className={s.epFileBody}>
            <div className={s.facts}>
              {[f.quality && f.quality !== f.resolution_label ? f.quality : '', ...fileFacts(f)]
                .filter(Boolean)
                .join(' · ')}
            </div>
            <div className={s.fileName} title={f.path || f.filename}>
              {f.filename}
            </div>
            <FileStatus
              f={f}
              canManage={actions.canManage}
              onRename={actions.onRename}
              quiet={quiet}
            />
          </div>
          <MenuButton label={`${label} file actions`} items={fileMenu(f, fileActions)} />
        </div>
      ))}
    </li>
  );
}

function SeasonRow({
  season,
  data,
  open,
  loading,
  onToggle,
  reload,
}: {
  season: LibrarySeason;
  data: ShowLibrary;
  open: boolean;
  loading: boolean;
  onToggle: () => void;
  reload: () => void;
}) {
  const { onRename, onDelete } = useFileActions(reload, 'show');
  const [monitored, setMonitored] = useState(season.monitored);
  useEffect(() => setMonitored(season.monitored), [season.monitored]);
  const toggleMonitor = async () => {
    const next = !monitored;
    setMonitored(next);
    try {
      await setSeasonMonitored(data.id, season.season_number, next);
      toast(next ? `${season.title} monitored` : `${season.title} unmonitored`);
    } catch (e) {
      setMonitored(!next);
      toast((e as Error).message || 'Couldn’t change monitoring', 'error');
    }
  };
  const aired = season.owned + season.missing;
  const files = season.episodes.flatMap((e) => e.files);
  const ownedFiles = files.length;
  const checked = files.filter((f) => f.reachable !== undefined);
  const drifted = files.filter((f) => f.reachable && f.naming && !f.naming.conforms);
  const [renaming, setRenaming] = useState(false);
  const renameAll = async () => {
    const ok = await confirm({
      title: `Rename ${drifted.length} files to your template?`,
      message:
        'Each file is renamed where it sits, its subtitles and sidecars move with it, and nothing is overwritten. A file that would collide is skipped.',
      confirmText: `Rename ${drifted.length}`,
    });
    if (!ok) return;
    setRenaming(true);
    let done = 0;
    let failed = 0;
    for (const f of drifted) {
      try {
        // one at a time: each rename touches the same folder listing
        // eslint-disable-next-line no-await-in-loop
        await renameFile(f.id);
        done++;
      } catch {
        failed++;
      }
    }
    setRenaming(false);
    toast(
      failed
        ? `Renamed ${done}, ${failed} couldn’t be renamed`
        : `Renamed ${done} file${done === 1 ? '' : 's'}`,
      failed ? 'warning' : 'success',
    );
    reload();
  };
  const quiet: Quiet = {
    unreachable: checked.length > 0 && checked.every((f) => f.reachable === false),
    below: ownedFiles > 0 && season.below_cutoff === ownedFiles,
  };
  const actions = {
    canManage: data.can_manage,
    recycle: data.recycle,
    onRename,
    onDelete: (f: LibraryFile) => {
      const ep = season.episodes.find((e) => e.files.some((x) => x.id === f.id));
      return onDelete(f, data.recycle, (ep?.files.length ?? 0) <= 1);
    },
  };
  const bodyId = `vlt-season-${data.id}-${season.season_number}`;
  return (
    <li className={s.season} data-open={open ? 'yes' : 'no'} data-season={season.season_number}>
      <div className={s.seasonHead}>
        <button
          type="button"
          className={s.seasonToggle}
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={onToggle}
        >
          <span className={s.chev} aria-hidden="true">
            ›
          </span>
          <span className={s.seasonTitle}>{season.title}</span>
          <span className={s.seasonCount}>
            {season.owned}/{aired || season.total}
          </span>
          <Bar
            value={season.owned}
            max={aired || season.total}
            label={`${season.owned} of ${aired || season.total} aired episodes`}
          />
          <span className={s.seasonSize}>{formatBytes(season.size_bytes) || '—'}</span>
          <span className={s.seasonMix}>
            {season.res_mix.map((m) => (
              <span key={m.label} className={s.mixPill} data-tone={resolutionTone(m.label)}>
                {m.label}
                {season.res_mix.length > 1 ? ` ${m.count}` : ''}
              </span>
            ))}
          </span>
          {season.missing ? <span className={s.missing}>{season.missing} missing</span> : null}
          {season.upcoming ? <span className={s.muted}>{season.upcoming} upcoming</span> : null}
        </button>
        {data.can_manage ? (
          <label
            className={s.switch}
            title={
              monitored
                ? 'Monitored: missing episodes are hunted'
                : 'Unmonitored: SoulSync leaves this season alone'
            }
          >
            <input type="checkbox" checked={monitored} onChange={() => void toggleMonitor()} />
            <span className={s.switchTrack} aria-hidden="true" />
            <span className={s.switchLabel}>Monitored</span>
          </label>
        ) : null}
        <MenuButton label={`${season.title} actions`} items={seasonMenu(season, data.can_manage)} />
      </div>
      {open ? (
        <div className={s.seasonBody} id={bodyId}>
          {loading ? <div className={s.loading}>Checking the files…</div> : null}
          {quiet.unreachable ? (
            <p className={s.note}>
              These files are on a drive only your media server can see, so SoulSync shows what the
              server reports and can’t rename or delete them from here.
            </p>
          ) : null}
          {data.can_manage && drifted.length > 1 ? (
            <p className={s.note}>
              {drifted.length} files here don’t match your naming template.{' '}
              <button
                type="button"
                className={s.linkBtn}
                disabled={renaming}
                onClick={() => void renameAll()}
              >
                {renaming ? 'Renaming…' : 'Rename all'}
              </button>
            </p>
          ) : null}
          {season.below_cutoff ? (
            <p className={s.note}>
              {season.below_cutoff === ownedFiles
                ? 'Every file here is'
                : `${season.below_cutoff} of ${ownedFiles} files are`}{' '}
              below your {data.quality.cutoff_label} target.
            </p>
          ) : null}
          <ol className={s.eps}>
            {season.episodes.map((ep) => (
              <EpisodeRow
                key={ep.id}
                ep={ep}
                season={season.season_number}
                actions={actions}
                quiet={quiet}
              />
            ))}
          </ol>
        </div>
      ) : null}
    </li>
  );
}

function ShowView({
  data,
  reload,
  openSeason,
  setOpenSeason,
  loadingSeason,
}: {
  data: ShowLibrary;
  reload: () => void;
  openSeason: number | null;
  setOpenSeason: (n: number | null) => void;
  loadingSeason: boolean;
}) {
  const { fixing, setFixing, onRelease } = useIdentity('show', data.id, reload);
  const sm = data.summary;
  const aired = sm.owned + sm.missing;
  const q = data.quality;
  return (
    <div className={s.tab}>
      <div className={s.grid}>
        <section className={s.card} data-card="have">
          <div className={s.cardHead}>
            <h3 className={s.cardTitle}>What you have</h3>
          </div>
          <div className={s.hero}>
            <div className={s.bigNum}>
              {sm.owned}
              <span className={s.bigOf}> of {aired || sm.total}</span>
            </div>
            <div>
              <div className={s.heroLine}>
                aired episodes · {formatBytes(sm.size_bytes) || 'no files'}
              </div>
              <div className={s.heroSub}>
                {[
                  sm.missing ? `${sm.missing} missing` : 'Nothing missing',
                  sm.upcoming ? `${sm.upcoming} upcoming` : '',
                  sm.specials_owned ? `${sm.specials_owned} specials` : '',
                  sm.watched_episodes ? `${sm.watched_episodes} watched` : '',
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </div>
            </div>
          </div>
          <Bar
            value={sm.owned}
            max={aired || sm.total}
            label={`${sm.owned} of ${aired || sm.total} aired episodes`}
          />
          <ResMix mix={sm.res_mix} />
          <div className={s.quality} data-meets={q.below_cutoff ? 'no' : 'yes'}>
            <span className={s.qualityIcon} aria-hidden="true">
              {q.below_cutoff ? '↑' : '✓'}
            </span>
            <span>
              {q.below_cutoff
                ? `${q.below_cutoff} file${q.below_cutoff === 1 ? '' : 's'} below your ${q.cutoff_label} target`
                : `Everything meets your ${q.cutoff_label} target`}
              <span className={s.muted}> · {q.profile_name} profile</span>
            </span>
          </div>
          {data.can_manage && bridge().openManage ? (
            <div className={s.cardActions}>
              <button type="button" className={s.linkBtn} onClick={() => bridge().openManage?.()}>
                Quality and download settings
              </button>
            </div>
          ) : null}
        </section>
        <div className={s.stack}>
          <IdentityCard
            identity={data.identity}
            canManage={data.can_manage}
            onFix={() => setFixing(true)}
            onRelease={onRelease}
          />
          {data.can_manage && sm.folders.length ? (
            <section className={s.card}>
              <div className={s.cardHead}>
                <h3 className={s.cardTitle}>Where it lives</h3>
                {sm.folders.length > 1 ? (
                  <Chip tone="warn">Split across {sm.folders.length} folders</Chip>
                ) : null}
              </div>
              <ul className={s.folders}>
                {sm.folders.map((fo) => (
                  <li key={fo.path ?? ''} className={s.folder}>
                    <span className={s.path} title={fo.path ?? ''}>
                      {fo.path}
                    </span>
                    <span className={s.muted}>{fo.files} files</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      </div>
      <h3 className={s.sectionTitle}>
        Seasons <span className={s.count}>{data.seasons.length}</span>
      </h3>
      <ul className={s.seasons}>
        {data.seasons.map((season) => (
          <SeasonRow
            key={season.season_number}
            season={season}
            data={data}
            open={openSeason === season.season_number}
            loading={loadingSeason && openSeason === season.season_number}
            onToggle={() =>
              setOpenSeason(openSeason === season.season_number ? null : season.season_number)
            }
            reload={reload}
          />
        ))}
      </ul>
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
    </div>
  );
}

/* ── the tab ──────────────────────────────────────────────────────────────── */

export function LibraryTab({ kind, id }: { kind: TitleKind; id: number }) {
  const [data, setData] = useState<TitleLibrary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openSeason, setOpenSeason] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  const load = useCallback(
    (season: number | null) => {
      const mine = ++seq.current;
      setLoading(true);
      fetchTitleLibrary(kind, id, kind === 'show' ? season : null)
        .then((res) => {
          if (mine !== seq.current) return;
          setData(res);
          setError(null);
        })
        .catch((e: Error) => {
          if (mine !== seq.current) return;
          setError(e.message || 'Couldn’t read this title’s files');
        })
        .finally(() => {
          if (mine === seq.current) setLoading(false);
        });
    },
    [kind, id],
  );

  useEffect(() => {
    setData(null);
    setOpenSeason(null);
    load(null);
  }, [load]);

  const reload = useCallback(() => load(openSeason), [load, openSeason]);
  const pickSeason = useCallback(
    (n: number | null) => {
      setOpenSeason(n);
      if (n != null) load(n);
    },
    [load],
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
        <div className={s.grid}>
          <div className={s.skeleton} />
          <div className={s.skeleton} />
        </div>
        <div className={s.skeletonRow} />
        <div className={s.skeletonRow} />
      </div>
    );
  }
  if (data.kind === 'movie') return <MovieView data={data} reload={reload} />;
  return (
    <ShowView
      data={data}
      reload={reload}
      openSeason={openSeason}
      setOpenSeason={pickSeason}
      loadingSeason={loading}
    />
  );
}
