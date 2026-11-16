/**
 * The Folders card's Video panel (SETTINGS_PORT.md, video pass V2a): where
 * downloads arrive, the Movies, TV, Anime and YouTube libraries, the optional
 * 4K ones, More libraries (#2029), your library's folders (the drive map),
 * the folders your server doesn't list, and the Docker volume lines. Was
 * video-settings.js. The folders are the video downloads config
 * (video-downloads.ts), saved whole on each change as before; More libraries
 * and the drive map have their own endpoints.
 *
 * A typed path saves when it is left, as it did (useCommit); the Docker lines
 * and the summary follow it while it is typed.
 */

import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { useCommit } from './commit';
import { videoDownloads, useVideoDownloads, type DownloadsConfig } from './video-downloads';

type Shell = Window & {
  showToast?: (message: string, type?: string) => void;
  showConfirmDialog?: (o: {
    title: string;
    message: string;
    confirmText?: string;
    cancelText?: string;
    destructive?: boolean;
  }) => Promise<boolean>;
  SoulSyncFolderCheck?: {
    refresh: (group: string) => void;
    environment?: () => Promise<{ docker?: boolean } | null>;
  };
};
const shell = () => window as Shell;
const toast = (message: string, type: string) => shell().showToast?.(message, type);
const refreshChecks = () => setTimeout(() => shell().SoulSyncFolderCheck?.refresh('video'), 0);
const TAB = 'library';

const str = (v: unknown) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');

/** settings.js LIBRARY_SUMMARIES['library-video-folders'] */
export function videoFoldersSummary(c: DownloadsConfig | null): string {
  if (!c) return '';
  const set = ['movies_path', 'tv_path', 'youtube_path'].filter((k) => str(c[k]).trim()).length;
  const extra = [
    ...((c.movies_additional_paths as string[] | undefined) || []),
    ...((c.tv_additional_paths as string[] | undefined) || []),
  ].filter((p) => String(p).trim()).length;
  const primary = set ? `${set} of 3 libraries set` : 'no primary libraries set';
  return extra ? `${primary} · ${extra} additional` : set ? primary : 'no libraries set yet';
}

/** a folder behind its lock, typed into the config and saved when left */
function VideoPath({
  id,
  field,
  placeholder,
  label,
  role,
  aria,
  openRef,
}: {
  id: string;
  field: string;
  placeholder: string;
  label: string;
  role?: string;
  aria?: string;
  openRef?: { current: (() => void) | null };
}) {
  const { config } = useVideoDownloads();
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);
  const commit = useCommit<HTMLInputElement>(() => void videoDownloads.save());
  if (openRef)
    openRef.current = () => {
      setOpen(true);
      setTimeout(() => input.current?.focus(), 0);
    };
  return (
    <div className="path-input-group">
      <input
        type="text"
        id={id}
        placeholder={placeholder}
        readOnly={!open}
        aria-label={aria}
        data-folder-check="video"
        data-folder-role={role}
        data-folder-label={label}
        ref={(el) => {
          input.current = el;
          commit(el);
        }}
        value={str(config?.[field])}
        onChange={(event) => videoDownloads.edit({ [field]: event.target.value })}
      />{' '}
      <button
        type="button"
        className={open ? 'browse-button' : 'browse-button locked'}
        onClick={() => {
          setOpen(!open);
          if (!open) setTimeout(() => input.current?.focus(), 0);
        }}
      >
        {open ? 'Lock' : 'Unlock'}
      </button>
    </div>
  );
}

/** the extra folders of one kind: rows of their own, saved when left */
function ExtraPaths({ kind }: { kind: 'movies' | 'tv' }) {
  const { config, loads } = useVideoDownloads();
  const key = `${kind}_additional_paths`;
  const rows = (config?.[key] as string[] | undefined) || [];
  const list = useRef<HTMLDivElement>(null);
  const [focusLast, setFocusLast] = useState(false);
  const commitAll = useCommit<HTMLDivElement>(() => void videoDownloads.save());
  // a key per row that stays with it: removing one row must take that row's
  // element (and the status line folder-check.js put after it), not the last
  const ids = useRef<{ loads: number; list: number[]; next: number }>({
    loads: -1,
    list: [],
    next: 0,
  });
  if (ids.current.loads !== loads) ids.current = { loads, list: [], next: 0 };
  while (ids.current.list.length < rows.length) ids.current.list.push(ids.current.next++);
  if (ids.current.list.length > rows.length) ids.current.list.length = rows.length;

  useEffect(() => {
    if (!focusLast) return;
    setFocusLast(false);
    const inputs = list.current?.querySelectorAll('input');
    inputs?.[inputs.length - 1]?.focus();
  }, [focusLast]);

  const set = (next: string[]) => videoDownloads.edit({ [key]: next });
  return (
    <>
      <div
        id={`video-${kind}-additional-paths`}
        data-video-extra-kind={kind}
        data-loaded={loads ? 'true' : undefined}
        ref={(el) => {
          list.current = el;
          commitAll(el);
        }}
      >
        {rows.map((value, i) => (
          <div
            key={`${loads}-${ids.current.list[i]}`}
            data-folder-row=""
            style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '8px' }}
          >
            <input
              type="text"
              value={value}
              placeholder={kind === 'movies' ? '/media/movies2' : '/media/tv2'}
              aria-label={
                kind === 'movies' ? 'Additional movie library path' : 'Additional TV library path'
              }
              data-folder-check="video"
              data-folder-role="existing"
              data-folder-label={kind === 'movies' ? 'Movie folder' : 'TV folder'}
              style={{ flex: 1, minWidth: 0 }}
              onChange={(event) => set(rows.map((r, j) => (j === i ? event.target.value : r)))}
            />
            <button
              type="button"
              className="test-button"
              aria-label="Remove library path"
              onClick={(event) => {
                // folder-check.js's status line for this row goes with it
                const status = event.currentTarget.closest('[data-folder-row]')?.nextElementSibling;
                if (status && status.classList.contains('folder-check')) status.remove();
                ids.current.list.splice(i, 1);
                void videoDownloads.change({ [key]: rows.filter((_, j) => j !== i) });
                refreshChecks();
              }}
            >
              Remove
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        className="test-button"
        data-video-add-path={kind}
        onClick={() => {
          if (!loads) return;
          set([...rows, '']);
          setFocusLast(true);
          refreshChecks();
        }}
      >
        + Add folder
      </button>
    </>
  );
}

// ── more libraries (#2029) ──

const LIBS_URL = '/api/video/libraries/folders';
const LIB_KIND: Record<string, string> = { movie: 'Movies', show: 'TV' };
const LIB_SOURCES: Array<[string, string]> = [
  ['soulseek', 'Soulseek'],
  ['usenet', 'Usenet'],
  ['torrent', 'Torrent'],
  ['extto', 'EXT.to'],
];

interface Library {
  id?: number | string;
  kind?: string;
  name?: string;
  builtin?: string;
  enabled?: boolean;
  usable?: boolean;
  is_default?: boolean;
  path?: string;
  rules?: { genres?: string[]; keywords?: string[] };
  quality_profile_id?: number;
  options?: {
    sources?: string[];
    indexer_ids?: number[];
    torrent_category?: string;
    usenet_category?: string;
    auto_search?: boolean;
    upgrades?: boolean;
  };
}
interface Indexer {
  id: number;
  name: string;
  protocol: string;
}

/** the Prowlarr indexers, asked for the first time a library's box opens */
let indexers: Indexer[] | null = null;
const indexerListeners = new Set<() => void>();
function loadIndexers() {
  if (indexers !== null) return;
  indexers = [];
  fetch('/api/prowlarr/indexers', { headers: { Accept: 'application/json' } })
    .then((r) => (r.ok ? r.json() : null))
    .then(
      (
        d: {
          success?: boolean;
          indexers?: Array<{ id: number; name?: string; protocol?: string }>;
        } | null,
      ) => {
        indexers = ((d && d.success && d.indexers) || []).map((i) => ({
          id: Number(i.id),
          name: i.name || `Indexer ${i.id}`,
          protocol: i.protocol || '',
        }));
        for (const l of indexerListeners) l();
      },
    )
    .catch(() => {
      indexers = [];
    });
}

const terms = (list?: string[]) => (list || []).join(', ');
const splitTerms = (v: string) =>
  v
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);

function LibraryRow({
  lib,
  profiles,
  reload,
  cancelNew,
}: {
  lib: Library;
  profiles: Array<{ id: number; name: string }>;
  reload: () => void;
  cancelNew: () => void;
}) {
  const row = useRef<HTMLDivElement>(null);
  const isNew = !lib.id;
  const builtin = lib.builtin;
  const rules = lib.rules || {};
  const options = lib.options || {};
  const [order, setOrder] = useState<string[]>(options.sources || []);
  const [, setIndexersVersion] = useState(0);
  useEffect(() => {
    const l = () => setIndexersVersion((n) => n + 1);
    indexerListeners.add(l);
    return () => {
      indexerListeners.delete(l);
    };
  }, []);
  const idxIds = options.indexer_ids || [];

  const state =
    builtin === '4k' && !lib.enabled
      ? 'Off: switch it on above'
      : !lib.usable
        ? lib.enabled
          ? 'No folder yet'
          : 'Switched off'
        : lib.is_default
          ? `Default for new ${lib.kind === 'movie' ? 'movies' : 'shows'}`
          : '';

  /** settings.js libraryBody: what the row says now */
  function body() {
    const r = row.current!;
    const val = (sel: string) => (r.querySelector<HTMLInputElement>(sel)?.value || '').trim();
    const out: Record<string, unknown> = {
      name: val('.video-library-name'),
      rules: {
        genres: splitTerms(val('.video-library-genres')),
        keywords: splitTerms(val('.video-library-keywords')),
      },
      quality_profile_id: parseInt(val('.video-library-profile'), 10) || 0,
      sources: order,
      torrent_category: val('.video-library-tcat'),
      usenet_category: val('.video-library-ucat'),
    };
    const auto = r.querySelector<HTMLInputElement>('.video-library-auto');
    const upg = r.querySelector<HTMLInputElement>('.video-library-upgrades');
    if (auto) out.auto_search = auto.checked;
    if (upg) out.upgrades = upg.checked;
    // the indexers: what's ticked once the list loaded, else what it had
    out.indexer_ids = r.querySelector('.video-library-idx')
      ? [...r.querySelectorAll<HTMLInputElement>('.video-library-idx:checked')].map((c) =>
          Number(c.value),
        )
      : idxIds;
    if (!builtin) {
      out.path = val('.video-library-path');
      const on = r.querySelector<HTMLInputElement>('.video-library-enabled');
      out.enabled = on ? on.checked : true;
    }
    const pick = r.querySelector<HTMLSelectElement>('.video-library-kind-pick');
    if (pick) out.kind = pick.value;
    return out;
  }

  async function request(url: string, method: string, payload: unknown, done: string) {
    try {
      const r = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: payload ? JSON.stringify(payload) : undefined,
      });
      const j = (await r.json()) as { success?: boolean; error?: string };
      if (!r.ok || j.success === false) {
        toast(j?.error || 'Could not save the library', 'error');
        return;
      }
      toast(done, 'success');
      reload();
    } catch {
      toast('Could not save the library', 'error');
    }
  }

  async function act(action: string) {
    if (action === 'save') {
      const payload = body();
      if (lib.id) return request(`${LIBS_URL}/${lib.id}`, 'PUT', payload, 'Library saved');
      return request(LIBS_URL, 'POST', payload, 'Library added');
    }
    if (action === 'default')
      return request(`${LIBS_URL}/${lib.id}/default`, 'POST', null, 'Default library changed');
    if (!lib.id) return cancelNew();
    const name =
      row.current?.querySelector<HTMLInputElement>('.video-library-name')?.value || 'this library';
    const go = () => void request(`${LIBS_URL}/${lib.id}`, 'DELETE', null, 'Library removed');
    const confirm = shell().showConfirmDialog;
    // without the shell the folder and files stay anyway, so it just goes
    if (!confirm) return go();
    const ok = await confirm({
      title: `Remove ${name}?`,
      message: `Titles picked for ${name} go back to the rules and the default. Its folder and files stay where they are.`,
      confirmText: 'Remove',
      cancelText: 'Keep it',
      destructive: true,
    });
    if (ok) go();
  }

  return (
    <div
      className="video-library-row"
      data-library-id={str(lib.id)}
      data-kind={lib.kind || 'movie'}
      data-builtin={builtin || undefined}
      ref={row}
    >
      <div className="video-library-head">
        <input
          type="text"
          className="video-library-name"
          defaultValue={lib.name || ''}
          placeholder="Library name, e.g. Stand-Up"
          aria-label="Library name"
          maxLength={80}
        />
        {isNew ? (
          <select
            className="video-library-kind-pick"
            aria-label="What the library holds"
            defaultValue="movie"
          >
            <option value="movie">Movies</option>
            <option value="show">TV</option>
          </select>
        ) : (
          <span className="video-library-kind">{LIB_KIND[lib.kind || ''] || lib.kind}</span>
        )}
        {state ? (
          <span
            className={
              lib.is_default && lib.usable
                ? 'video-library-state is-default'
                : 'video-library-state'
            }
          >
            {state}
          </span>
        ) : null}
      </div>
      {builtin ? (
        <div className="video-library-known">
          {lib.path || 'No folder set'} <span>(set above)</span>
        </div>
      ) : (
        <input
          type="text"
          className="video-library-path"
          defaultValue={lib.path || ''}
          placeholder="/media/stand-up"
          aria-label="Library folder"
          data-folder-check="video"
          data-folder-label={`${lib.name || 'New'} library`}
        />
      )}
      <div className="video-library-rules">
        <input
          type="text"
          className="video-library-genres"
          list="video-library-genre-list"
          defaultValue={terms(rules.genres)}
          placeholder="Genres, e.g. Documentary, Family"
          aria-label="Genres that send a title here"
        />
        <input
          type="text"
          className="video-library-keywords"
          defaultValue={terms(rules.keywords)}
          placeholder="Keywords, e.g. stand-up"
          aria-label="Keywords that send a title here"
        />
      </div>
      <label className="video-library-field">
        Quality profile{' '}
        <select
          className="video-library-profile"
          aria-label="Quality profile for titles in this library"
          defaultValue={String(lib.quality_profile_id || 0)}
        >
          <option value="0">
            {lib.kind === 'show' ? 'Default for new shows' : 'Default for new movies'}
          </option>
          {profiles
            .filter((p) => p.id)
            .map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
        </select>
      </label>
      <div
        className="video-library-field video-library-sources"
        role="group"
        aria-label="Download from, in order"
      >
        Download from{' '}
        {LIB_SOURCES.map(([key, name]) => {
          const at = order.indexOf(key);
          return (
            <button
              key={key}
              type="button"
              className={at >= 0 ? 'video-library-src is-on' : 'video-library-src'}
              data-library-src={key}
              aria-pressed={at >= 0 ? 'true' : 'false'}
              onClick={() => setOrder(at >= 0 ? order.filter((k) => k !== key) : [...order, key])}
            >
              {at >= 0 ? `${at + 1} ` : ''}
              {name}
            </button>
          );
        })}
        <span className="video-library-hint">{order.length ? '' : 'the global order'}</span>
      </div>
      <details
        className="video-library-indexers"
        data-library-idx-ids={idxIds.join(',')}
        onToggle={(event) => {
          if ((event.currentTarget as HTMLDetailsElement).open) loadIndexers();
        }}
      >
        <summary>Indexers: {idxIds.length ? `${idxIds.length} picked` : 'every one'}</summary>
        <div className="video-library-idx-list">
          {indexers === null || (indexers.length === 0 && !indexersLoaded()) ? (
            <em className="video-library-hint">Loading…</em>
          ) : !indexers.length ? (
            <em className="video-library-hint">
              No Prowlarr indexers (Settings → Indexers &amp; Downloaders).
            </em>
          ) : (
            indexers.map((i) => (
              <label key={i.id} className="checkbox-label">
                <input
                  type="checkbox"
                  className="video-library-idx"
                  value={i.id}
                  defaultChecked={idxIds.includes(i.id)}
                />{' '}
                {i.name}
                {i.protocol ? (
                  <>
                    {' '}
                    <span className="video-library-hint">{i.protocol}</span>
                  </>
                ) : null}
              </label>
            ))
          )}
        </div>
      </details>
      <div className="video-library-rules">
        <input
          type="text"
          className="video-library-tcat"
          defaultValue={options.torrent_category || ''}
          placeholder="Torrent category / label"
          aria-label="Torrent client category or label"
          maxLength={64}
        />
        <input
          type="text"
          className="video-library-ucat"
          defaultValue={options.usenet_category || ''}
          placeholder="Usenet category"
          aria-label="Usenet client category"
          maxLength={64}
        />
      </div>
      <div className="video-library-field">
        <label
          className="checkbox-label"
          title="The wishlist and RSS sync hunt its titles; off = only when you search"
        >
          <input
            type="checkbox"
            className="video-library-auto"
            defaultChecked={options.auto_search !== false}
          />{' '}
          Search automatically
        </label>
        <label
          className="checkbox-label"
          title="Titles you have keep upgrading toward their profile's cutoff"
        >
          <input
            type="checkbox"
            className="video-library-upgrades"
            defaultChecked={options.upgrades !== false}
          />{' '}
          Upgrade what you have
        </label>
      </div>
      <div className="video-library-actions">
        {builtin ? null : (
          <label className="checkbox-label">
            <input
              type="checkbox"
              className="video-library-enabled"
              defaultChecked={lib.enabled !== false}
            />{' '}
            Switched on
          </label>
        )}
        <button
          type="button"
          className="test-button"
          data-library-action="save"
          onClick={() => void act('save')}
        >
          {isNew ? 'Add' : 'Save'}
        </button>
        {!isNew && lib.usable && !lib.is_default ? (
          <button
            type="button"
            className="test-button"
            data-library-action="default"
            onClick={() => void act('default')}
          >
            Make default
          </button>
        ) : null}
        {isNew || !builtin ? (
          <button
            type="button"
            className="test-button"
            data-library-action="remove"
            onClick={() => void act('remove')}
          >
            {isNew ? 'Cancel' : 'Remove'}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** the list came back (an empty one included) */
let indexersDone = false;
/** for tests: ask Prowlarr again */
export function resetLibraryIndexers() {
  indexers = null;
  indexersDone = false;
}
indexerListeners.add(() => {
  indexersDone = true;
});
const indexersLoaded = () => indexersDone;

function VideoLibraries({ loads }: { loads: number }) {
  const [list, setList] = useState<Library[] | null>(null);
  const [genres, setGenres] = useState<string[]>([]);
  const [profiles, setProfiles] = useState<Array<{ id: number; name: string }>>([]);
  const [adding, setAdding] = useState(false);
  const [version, setVersion] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  async function load() {
    const get = (url: string) =>
      fetch(url, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);
    const [libs, qp] = (await Promise.all([
      get(LIBS_URL),
      get('/api/video/downloads/quality/profiles'),
    ])) as [
      { libraries?: Library[]; genres?: string[] } | null,
      { profiles?: Array<{ id: number; name: string }> } | null,
    ];
    setProfiles((qp && qp.profiles) || []);
    if (libs && libs.libraries) {
      setList(libs.libraries);
      setGenres(libs.genres || []);
      setAdding(false);
      setVersion((n) => n + 1);
      refreshChecks();
    }
  }

  useEffect(() => {
    if (loads) void load();
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!adding) return;
    listRef.current
      ?.querySelector<HTMLInputElement>(
        '.video-library-row[data-library-id=""] .video-library-name',
      )
      ?.focus();
    refreshChecks();
  }, [adding]);

  return (
    <>
      <div id="video-libraries-list" className="video-libraries" ref={listRef}>
        {(list || []).map((lib) => (
          <LibraryRow
            key={`${lib.id}-${version}`}
            lib={lib}
            profiles={profiles}
            reload={() => void load()}
            cancelNew={() => setAdding(false)}
          />
        ))}
        {adding ? (
          <LibraryRow
            key={`new-${version}`}
            lib={{ kind: 'movie', enabled: true }}
            profiles={profiles}
            reload={() => void load()}
            cancelNew={() => setAdding(false)}
          />
        ) : null}
      </div>
      <datalist id="video-library-genre-list">
        {genres.map((g) => (
          <option key={g} value={g} />
        ))}
      </datalist>
      <button
        type="button"
        className="test-button"
        id="video-library-add"
        onClick={() => setAdding(true)}
      >
        + Add library
      </button>
    </>
  );
}

// ── your library's folders (the drive map) ──

const DRIVES_URL = '/api/video/libraries/drives';
const KIND_LABEL: Record<string, string> = { movie: 'Movies', episode: 'TV' };

interface Drive {
  server: string;
  kind?: string;
  library?: string;
  files?: number;
  auto?: boolean;
  local?: string;
  role?: string;
}
type DriveStatus = { text: string; cls: string };

/** settings.js drivePrefix: one folder of a drive reached, where its
 *  neighbours are (never past the drive itself) */
export function drivePrefix(server: string, local: string) {
  const s = String(server).split('/').filter(Boolean);
  const l = String(local).split(/[\\/]/);
  let n = 0;
  const keep = s.length > 2 ? 2 : 1;
  while (
    n < s.length - keep &&
    n < l.length - 1 &&
    s[s.length - 1 - n].toLowerCase() === String(l[l.length - 1 - n]).toLowerCase()
  )
    n++;
  if (!n) return null;
  const sep = local.includes('\\') ? '\\' : '/';
  return {
    server: `/${s.slice(0, s.length - n).join('/')}`,
    local: l.slice(0, l.length - n).join(sep),
    sep,
  };
}

function DriveMap({ loads }: { loads: number }) {
  const [drives, setDrives] = useState<Drive[] | null>(null);
  const [local, setLocal] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<Record<string, DriveStatus>>({});
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const localNow = useRef(local);
  localNow.current = local;

  function render(folders: Drive[]) {
    // a drive's folders side by side, so filling one fills its neighbours
    const sorted = [...folders].sort((a, b) => String(a.server).localeCompare(String(b.server)));
    setDrives(sorted);
    const values: Record<string, string> = {};
    for (const d of sorted) if (!d.auto) values[d.server] = d.local || '';
    setLocal(values);
    localNow.current = values;
    setStatus({});
    for (const d of sorted) if (!d.auto) void check(d.server, sorted);
  }

  async function check(server: string, all: Drive[] = drives || []) {
    const value = (localNow.current[server] || '').trim();
    if (!value) {
      setStatus((s) => ({
        ...s,
        [server]: { text: 'Not reached yet: works the way it does today', cls: '' },
      }));
      return;
    }
    setStatus((s) => ({ ...s, [server]: { text: 'Checking…', cls: '' } }));
    try {
      const r = await fetch(`${DRIVES_URL}/check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server, local: value }),
      });
      const res = (await r.json()) as { ok?: boolean; message?: string };
      if ((localNow.current[server] || '').trim() !== value) return; // typed on meanwhile
      setStatus((s) => ({
        ...s,
        [server]: {
          text: `${res.ok ? '✓ ' : '✗ '}${res.message || ''}`,
          cls: res.ok ? 'is-ok' : 'is-bad',
        },
      }));
      if (res.ok) fillSiblings(server, value, all);
    } catch {
      setStatus((s) => ({ ...s, [server]: { text: 'The check failed', cls: 'is-bad' } }));
    }
  }

  /** one folder of a drive checked out: its neighbours are filled in and
   *  checked too, never over what was typed */
  function fillSiblings(server: string, value: string, all: Drive[]) {
    const pre = drivePrefix(server, value);
    if (!pre) return;
    for (const other of all) {
      if (other.auto || other.server === server || (localNow.current[other.server] || '').trim())
        continue;
      if (!other.server.startsWith(`${pre.server}/`)) continue;
      const filled =
        pre.local +
        pre.sep +
        other.server
          .slice(pre.server.length + 1)
          .split('/')
          .join(pre.sep);
      localNow.current = { ...localNow.current, [other.server]: filled };
      setLocal(localNow.current);
      void check(other.server, all);
    }
  }

  useEffect(() => {
    if (!loads) return;
    fetch(DRIVES_URL, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { folders?: Drive[] } | null) => {
        if (d) render(d.folders || []);
      })
      .catch(() => {});
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    if (!drives) return;
    const map = drives.map((d) => ({
      server: d.server,
      kind: d.kind || null,
      local: d.auto ? d.local || '' : (localNow.current[d.server] || '').trim(),
    }));
    try {
      const r = await fetch(DRIVES_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ map }),
      });
      const j = (await r.json()) as { error?: string; map?: unknown[]; folders?: Drive[] };
      if (!r.ok) {
        toast(j.error || 'Could not save the folders', 'error');
        return;
      }
      toast(`Folders saved: ${(j.map || []).length} reached`, 'success');
      render(j.folders || []);
    } catch {
      toast('Could not save the folders', 'error');
    }
  }

  return (
    <>
      <div id="video-drive-map" className="video-drive-map">
        {drives === null ? null : !drives.length ? (
          <div className="setting-help-text">
            No library folders yet: pick your libraries and scan first.
          </div>
        ) : (
          drives.map((d) => {
            const meta = [
              d.library,
              KIND_LABEL[d.kind || ''] || 'Drive',
              d.files ? `${Number(d.files).toLocaleString()} files` : 'no files',
            ]
              .filter(Boolean)
              .join(' · ');
            const head = (
              <div className="video-drive-server">
                <code title={d.server}>{d.server}</code>
                <span className="video-drive-count">{meta}</span>
              </div>
            );
            if (d.auto)
              return (
                <div
                  key={d.server}
                  className="video-drive-row is-auto"
                  data-server={d.server}
                  data-kind={d.kind || ''}
                  data-local={d.local}
                >
                  {head}
                  <div className="video-drive-known">{d.local}</div>
                  <div className="video-drive-status is-ok">✓ {d.role || 'Already reached'}</div>
                </div>
              );
            // the example is the drive's own path on a share
            const parts = String(d.server).split('/').filter(Boolean);
            const example = `\\\\nas\\${(parts.length > 1 ? parts.slice(1) : parts).join('\\')}`;
            const st = status[d.server];
            return (
              <div
                key={d.server}
                className="video-drive-row"
                data-server={d.server}
                data-kind={d.kind || ''}
              >
                {head}
                <input
                  type="text"
                  className="video-drive-local"
                  value={local[d.server] || ''}
                  placeholder={`Where SoulSync reaches it, e.g. ${example}`}
                  aria-label={`Where SoulSync reaches ${d.server}`}
                  onChange={(event) => {
                    const v = event.target.value;
                    localNow.current = { ...localNow.current, [d.server]: v };
                    setLocal(localNow.current);
                    clearTimeout(timers.current[d.server]);
                    timers.current[d.server] = setTimeout(() => void check(d.server), 600);
                  }}
                />
                <div
                  className={st?.cls ? `video-drive-status ${st.cls}` : 'video-drive-status'}
                  aria-live="polite"
                >
                  {st?.text || ''}
                </div>
              </div>
            );
          })
        )}
      </div>
      <button
        type="button"
        className="test-button"
        id="video-drive-map-save"
        onClick={() => void save()}
      >
        Save folders
      </button>
    </>
  );
}

// ── the Docker lines ──

/** settings.js renderVideoCompose: every video folder set on the panel as
 *  the compose volume line it needs */
export function composeLines(paths: string[]) {
  const seen = new Set<string>();
  const lines = paths
    .map((p) => p.trim().replace(/[\\/]+$/, ''))
    .filter((p) => p && !seen.has(p) && (seen.add(p), true))
    .map(
      (p) =>
        `      - /path/on/your/machine/${p.split(/[\\/]/).filter(Boolean).pop() || 'folder'}:${p}`,
    );
  return lines.length ? `    volumes:\n${lines.join('\n')}` : 'Set a folder above to see its line.';
}

function Compose({ panel }: { panel: { current: HTMLElement | null } }) {
  // redrawn with each change to the folders, so the lines read them
  useVideoDownloads();
  const [docker, setDocker] = useState(false);
  // empty until the first load, as the page had it
  const [text, setText] = useState('');
  useEffect(() => {
    void shell()
      .SoulSyncFolderCheck?.environment?.()
      .then((env) => setDocker(!!env?.docker));
  }, []);
  // the lines read the folders as they are on the panel, typed or not
  useLayoutEffect(() => {
    const el = panel.current;
    if (!el) return;
    const read = () =>
      setText(
        composeLines(
          [...el.querySelectorAll<HTMLInputElement>('[data-folder-check="video"]')]
            .filter((input) => !input.closest('[data-folder-optional][hidden]'))
            .map((input) => input.value),
        ),
      );
    if (videoDownloads.get().loads) read();
    el.addEventListener('input', read);
    return () => el.removeEventListener('input', read);
  });
  return (
    <div className="settings-group" data-stg={TAB} data-video-compose="" hidden={!docker}>
      <h3>Docker volumes</h3>
      <div className="setting-help-text">
        In Docker every folder above is a path inside the container. Each one needs a line in your
        compose file mapping a real folder to it, read-write so renames, upgrades and the recycle
        bin work. These are the lines for what's set here: swap the left side for the folder on your
        machine, then recreate the container.
      </div>
      <pre className="video-compose-lines" id="video-compose-lines">
        {text}
      </pre>
      <button
        type="button"
        className="test-button"
        id="video-compose-copy"
        onClick={() => {
          if (!navigator.clipboard) return toast('Select the lines and copy them', 'info');
          navigator.clipboard
            .writeText(text)
            .then(() => toast('Compose lines copied', 'success'))
            .catch(() => toast('Select the lines and copy them', 'info'));
        }}
      >
        Copy lines
      </button>
    </div>
  );
}

// ── the panel ──

function Help({ children }: { children: ReactNode }) {
  return <div className="setting-help-text">{children}</div>;
}

export function VideoFoldersPanel({
  hidden,
  docker,
  settingsLoaded = false,
}: {
  hidden: boolean;
  /** null until the settings load: both guides show */
  docker: boolean | null;
  /** the music settings have loaded: the page's summaries went live then */
  settingsLoaded?: boolean;
}) {
  const { config, status, loads } = useVideoDownloads();
  // an object ref, not a callback: a fresh callback each render is detached
  // and attached again after the Docker lines' effect runs, which saw null
  const panel = useRef<HTMLDivElement | null>(null);
  const busy = status === 'loading' || status === 'failed';
  const open4k = {
    movies: useRef<(() => void) | null>(null),
    tv: useRef<(() => void) | null>(null),
  };

  // after each load the folder checks look again
  useEffect(() => {
    if (loads) refreshChecks();
  }, [loads]);

  // while it loads (or failed to) every control is off but Retry, and back
  // as it was after, as loadSharedLibrary did it
  useLayoutEffect(() => {
    const el = panel.current;
    if (!el || !busy) return;
    const off = [
      ...el.querySelectorAll<HTMLInputElement>(
        'input, select, textarea, button:not(.stg-library-retry)',
      ),
    ].filter((c) => !c.disabled);
    for (const c of off) c.disabled = true;
    return () => {
      for (const c of off) c.disabled = false;
    };
  }, [busy]);

  // what the page's summary said once the settings had loaded: read off the
  // panel's fields, empty or not
  const summary = settingsLoaded ? videoFoldersSummary(config ?? {}) : '';
  const fallback = 'Movies, TV & YouTube libraries';
  const fourK = (kind: 'movies' | 'tv') => !!config?.[`${kind}_4k_enabled`];

  function toggle4k(kind: 'movies' | 'tv', on: boolean) {
    void videoDownloads.change({ [`${kind}_4k_enabled`]: on });
    refreshChecks();
    // nothing to protect yet: open the lock so the folder can be typed
    if (on && !str(config?.[`${kind}_4k_path`]).trim()) open4k[kind].current?.();
  }

  const lib = (id: string, field: string, placeholder: string, label: string) => (
    <VideoPath id={id} field={field} placeholder={placeholder} label={label} />
  );

  return (
    <div
      id="folders-video-panel"
      role="tabpanel"
      aria-labelledby="folders-video-tab"
      className="stg-media-panel"
      hidden={hidden}
      tabIndex={0}
      aria-busy={status === 'idle' ? undefined : busy ? 'true' : 'false'}
      ref={panel}
    >
      {busy ? (
        <p className="stg-library-load-status settings-hint" role="status">
          {status === 'loading' ? (
            'Loading saved video settings...'
          ) : (
            <>
              Could not load saved video settings.{' '}
              <button
                type="button"
                className="stg-library-retry"
                onClick={() => void videoDownloads.load()}
              >
                Retry
              </button>
            </>
          )}
        </p>
      ) : null}
      <div className="stg-media-context">
        <strong>Video folders</strong>
        <span
          className={summary ? 'stg-media-summary is-live' : 'stg-media-summary'}
          data-stg-summary="library-video-folders"
          data-stg-fallback={fallback}
        >
          {summary || fallback}
        </span>
      </div>

      <div className="settings-group" data-stg={TAB}>
        <h3>Where downloads arrive</h3>
        <Help>
          Files land in the download folder first, then get renamed and moved into the right library
          below. Each folder gets a live check under it: whether SoulSync can write there and how
          much room is left. Video folders are separate from your Music folders.
        </Help>
        <div
          className={docker === false ? 'setting-help-text hidden' : 'setting-help-text'}
          data-paths-guide="docker"
        >
          <strong>Docker / Unraid detected:</strong> these start on the container paths your compose
          file maps. Leave them and map your real folders in the container config. Unlock one only
          if you mapped it somewhere else.
        </div>
        <div
          className={docker === true ? 'setting-help-text hidden' : 'setting-help-text'}
          data-paths-guide="native"
        >
          <strong>Bare metal / Proxmox LXC / systemd:</strong> there is no container, so unlock each
          folder and point it at your real one.
        </div>
        <div className="form-group">
          <label htmlFor="video-download-path">Download folder (input, shared with Music):</label>
          <VideoPath
            id="video-download-path"
            field="download_path"
            placeholder="./downloads"
            role="input"
            label="Download folder"
          />
          <Help>
            Point this at the same folder as your slskd / torrent / usenet client. It's the same
            setting as the Music side's Download Folder, so it's locked here too: change it and
            Music changes with it.
          </Help>
        </div>
      </div>

      <div className="settings-group" data-stg={TAB}>
        <h3>Libraries</h3>
        <Help>
          Where finished downloads are filed. Point your Plex / Jellyfin / Emby libraries at the
          same folders.
        </Help>
        <div className="form-group">
          <label htmlFor="video-movies-path">🎬 Movies:</label>
          {lib('video-movies-path', 'movies_path', '/media/movies', 'Movies library')}
        </div>
        <div className="form-group">
          <label htmlFor="video-tv-path">📺 TV shows:</label>
          {lib('video-tv-path', 'tv_path', '/media/tv', 'TV library')}
        </div>
        <div className="form-group">
          <label htmlFor="video-anime-path">🍥 Anime (optional):</label>
          {lib('video-anime-path', 'anime_path', '/media/anime', 'Anime library')}
          <Help>
            New episodes of anime shows go here instead of the TV folder: shows in a TV library
            ticked Anime (Settings &gt; Connections), shows set to Anime by hand, and shows not in
            your library yet that TMDB suggests are anime (an admin can change that on the show's
            page or when approving). Leave empty to keep anime with TV.
          </Help>
        </div>
        <div className="form-group">
          <label htmlFor="video-youtube-path">▶️ YouTube:</label>
          {lib('video-youtube-path', 'youtube_path', '/media/youtube', 'YouTube library')}
        </div>
      </div>

      <div className="settings-group" data-stg={TAB}>
        <h3>4K libraries (optional)</h3>
        <Help>
          Keep 4K copies in their own folders, like a separate Movies 4K library in Plex. Off until
          you turn one on. A title files here when its quality profile is set to save to the 4K
          library (Settings &gt; Quality). Titles you already have stay where they are, and their
          upgrades land beside the copy you have.
        </Help>
        {(
          [
            [
              'movies',
              'Separate 4K movies folder',
              '/media/movies-4k',
              '4K movies folder',
              'Movies 4K library',
            ],
            ['tv', 'Separate 4K TV folder', '/media/tv-4k', '4K TV folder', 'TV 4K library'],
          ] as const
        ).map(([kind, words, placeholder, aria, label]) => (
          <div className="form-group" key={kind}>
            <label className="checkbox-label">
              <input
                type="checkbox"
                id={`video-${kind}-4k-enabled`}
                data-video-4k-toggle={kind}
                checked={fourK(kind)}
                onChange={(event) => toggle4k(kind, event.target.checked)}
              />{' '}
              {words}
            </label>
            <div
              className="video-4k-path"
              data-folder-optional=""
              data-video-4k-row={kind}
              hidden={!fourK(kind)}
            >
              <VideoPath
                id={`video-${kind}-4k-path`}
                field={`${kind}_4k_path`}
                placeholder={placeholder}
                aria={aria}
                label={label}
                openRef={open4k[kind]}
              />
            </div>
            {kind === 'tv' ? (
              <Help>Anime keeps its own folder when one is set, whatever the profile.</Help>
            ) : null}
          </div>
        ))}
      </div>

      <div className="settings-group" data-stg={TAB}>
        <h3>More libraries</h3>
        <Help>
          Your own movie and TV libraries, each with its own folder, like a Stand-Up, Documentaries
          or Kids library in Plex. A new title files into the library picked for it (in its Get or
          Manual search window), else the 4K one when its quality profile says so, else the first
          library whose rules match its genres or TMDB keywords, else the default library for its
          kind. Titles you already have stay where they are. The main and 4K libraries are here too:
          their folders are the ones above.
        </Help>
        <VideoLibraries loads={loads} />
      </div>

      <div className="settings-group" data-stg={TAB}>
        <h3>Your library's folders</h3>
        <Help>
          Every folder your Plex / Jellyfin libraries read, Movies or TV. The ones SoulSync already
          reaches are ticked. Tell it where it reaches the rest and every file in them is found,
          renamed to your templates, recycled on delete and kept track of. New downloads still go to
          the folders above. Leave one blank and it works the way it does today.
        </Help>
        <DriveMap loads={loads} />

        <h4 className="video-extra-heading">Folders your server doesn't list</h4>
        <Help>
          A collection SoulSync should manage that your server doesn't read. New downloads never go
          here.
        </Help>
        <div className="form-group">
          <label>Movie folders:</label>
          <ExtraPaths kind="movies" />
        </div>
        <div className="form-group">
          <label>TV folders:</label>
          <ExtraPaths kind="tv" />
        </div>
      </div>

      <Compose panel={panel} />
    </div>
  );
}
