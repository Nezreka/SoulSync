/**
 * Library > Folders, Video (settings-bic phase 4): where downloads arrive
 * (with the video minimum free disk space beside the download folder, DESIGN
 * 3.3), the Movies, TV, Anime and YouTube libraries, the optional 4K ones,
 * More libraries (#2029), your library's folders (the drive map), the extra
 * folders, and the Docker volume lines. The folders are the
 * video downloads config (video-downloads.ts), saved whole on each change as
 * before; More libraries and the drive map have their own endpoints.
 *
 * A typed path saves when it is left, as it did (useCommit); the Docker lines
 * and the hero follow it while it is typed.
 *
 * More libraries: a library's fields are its own until its Save. Saving,
 * making default or removing one reloads the list, and only that library's
 * card is drawn again: what is typed into another card stays (the old list
 * drew every card again and dropped it, settings-bic AUDIT 32).
 */

import { type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

import {
  Btn,
  Card,
  NumberField,
  PathField,
  Pre,
  Row,
  SectionLabel,
  StatusWords,
  Switch,
  Tag,
} from '../ui';
import s from '../ui/settings.module.css';
import { useCommit } from './commit';
import {
  FolderRows,
  checkAttrs,
  folderChecks,
  refreshChecks,
  shell,
  toast,
  useBusyGate,
} from './library-card';
import { videoDownloads, useVideoDownloads, type DownloadsConfig } from './video-downloads';
import { useVideoOrganization, videoOrganization } from './video-organization';
import { useVideoServer, videoServer } from './video-server';

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
  role?: 'input' | 'existing';
  aria?: string;
  openRef?: { current: (() => void) | null };
}) {
  const { config } = useVideoDownloads();
  const [locked, setLocked] = useState(true);
  const commit = useCommit<HTMLInputElement>(() => void videoDownloads.save());
  if (openRef)
    openRef.current = () => {
      setLocked(false);
      setTimeout(() => document.getElementById(id)?.focus(), 0);
    };
  return (
    <PathField
      id={id}
      value={str(config?.[field])}
      onChange={(value) => videoDownloads.edit({ [field]: value })}
      placeholder={placeholder}
      locked={locked}
      onLock={(next) => {
        setLocked(next);
        if (!next) setTimeout(() => document.getElementById(id)?.focus(), 0);
      }}
      onCheck={(answer) => folderChecks.set(id, answer)}
      inputProps={{ ...checkAttrs('video', label, role), 'aria-label': aria, ref: commit }}
    />
  );
}

/** the extra folders of one kind: rows of their own, saved when left */
function ExtraPaths({ kind }: { kind: 'movies' | 'tv' }) {
  const { config, loads } = useVideoDownloads();
  const key = `${kind}_additional_paths`;
  const rows = (config?.[key] as string[] | undefined) || [];
  const commitAll = useCommit<HTMLDivElement>(() => void videoDownloads.save());
  return (
    <FolderRows
      id={`video-${kind}-additional-paths`}
      listAttrs={{ 'data-video-extra-kind': kind, 'data-loaded': loads ? 'true' : undefined }}
      listRef={commitAll}
      rows={rows}
      onRows={(next) => videoDownloads.edit({ [key]: next })}
      // removing saves at once, as before
      onRemove={(next) => void videoDownloads.change({ [key]: next })}
      onAdd={(next) => {
        if (loads) videoDownloads.edit({ [key]: next });
      }}
      placeholder={kind === 'movies' ? '/media/movies2' : '/media/tv2'}
      itemLabel={kind === 'movies' ? 'Additional movie library path' : 'Additional TV library path'}
      check={checkAttrs('video', kind === 'movies' ? 'Movie folder' : 'TV folder', 'existing')}
      addAttrs={{ 'data-video-add-path': kind }}
    />
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

/** a field of a library card: the parts' look, its own class for the hooks */
function LibInput({
  className,
  ...rest
}: { className: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <span className={`${s.field} ${s.fieldFull}`}>
      <input type="text" {...rest} className={`${s.input} ${className}`} />
    </span>
  );
}

function LibSelect({
  className,
  children,
  ...rest
}: { className: string; children: ReactNode } & React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={`${s.field} ${s.fieldSelect}`}>
      <select {...rest} className={`${s.select} ${className}`}>
        {children}
      </select>
    </span>
  );
}

function LibraryRow({
  lib,
  profiles,
  reload,
  cancelNew,
}: {
  lib: Library;
  profiles: Array<{ id: number; name: string }>;
  /** reload the list; `drawAgain` is this card, whose fields show what the server has */
  reload: (drawAgain?: string) => void;
  cancelNew: () => void;
}) {
  const row = useRef<HTMLDivElement>(null);
  const isNew = !lib.id;
  const builtin = lib.builtin;
  const rules = lib.rules || {};
  const options = lib.options || {};
  const [open, setOpen] = useState(isNew);
  const bodyId = useId();
  const [order, setOrder] = useState<string[]>(options.sources || []);
  // the folder is the one field drawn by the parts' PathField (its check
  // line): it keeps what is typed here, as the others keep theirs
  const [path, setPath] = useState(lib.path || '');
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

  /** settings.js libraryBody: what the card says now */
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
      reload(str(lib.id));
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

  const kindWord = lib.kind === 'show' ? 'shows' : 'movies';
  return (
    <div
      className="video-library-row"
      data-library-id={str(lib.id)}
      data-kind={lib.kind || 'movie'}
      data-builtin={builtin || undefined}
      ref={row}
    >
      <div className={`video-library-head ${s.libHead}`}>
        <LibInput
          className="video-library-name"
          defaultValue={lib.name || ''}
          placeholder="Library name, like Stand-Up"
          aria-label="Library name"
          maxLength={80}
        />
        {isNew ? (
          <LibSelect
            className="video-library-kind-pick"
            aria-label="What the library holds"
            defaultValue="movie"
          >
            <option value="movie">Movies</option>
            <option value="show">TV</option>
          </LibSelect>
        ) : (
          <Tag>
            <span className="video-library-kind">{LIB_KIND[lib.kind || ''] || lib.kind}</span>
          </Tag>
        )}
        {state ? (
          <StatusWords tone={lib.is_default && lib.usable ? 'ok' : lib.usable ? 'idle' : 'warn'}>
            <span
              className={
                lib.is_default && lib.usable
                  ? 'video-library-state is-default'
                  : 'video-library-state'
              }
            >
              {state}
            </span>
          </StatusWords>
        ) : null}
        <Btn
          kind="ghost"
          small
          icon={open ? 'up' : 'down'}
          aria-expanded={open}
          aria-controls={bodyId}
          aria-label={`${open ? 'Close' : 'Open'} ${lib.name || 'the new library'}`}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? 'Close' : 'Edit'}
        </Btn>
      </div>
      {builtin ? (
        <div className="video-library-known">
          {lib.path || 'No folder set'} <span>(set above)</span>
        </div>
      ) : null}
      <div className={s.libBody} id={bodyId} hidden={!open}>
        {builtin ? null : (
          <label className={s.libField}>
            <span>Folder</span>
            <PathField
              value={path}
              onChange={setPath}
              placeholder="/media/stand-up"
              inputClass="video-library-path"
              inputProps={{
                'aria-label': 'Library folder',
                ...checkAttrs('video', `${lib.name || 'New'} library`),
              }}
            />
          </label>
        )}
        <div className={s.libGrid}>
          <label className={s.libField}>
            <span>Genres that send a title here</span>
            <LibInput
              className="video-library-genres"
              list="video-library-genre-list"
              defaultValue={terms(rules.genres)}
              placeholder="Genres, like Documentary, Family"
              aria-label="Genres that send a title here"
            />
          </label>
          <label className={s.libField}>
            <span>Keywords that send a title here</span>
            <LibInput
              className="video-library-keywords"
              defaultValue={terms(rules.keywords)}
              placeholder="Keywords, like stand-up"
              aria-label="Keywords that send a title here"
            />
          </label>
          <label className={s.libField}>
            <span>Quality profile</span>
            <LibSelect
              className="video-library-profile"
              aria-label="Quality profile for titles in this library"
              defaultValue={String(lib.quality_profile_id || 0)}
            >
              <option value="0">{`Default for new ${kindWord}`}</option>
              {profiles
                .filter((p) => p.id)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </LibSelect>
          </label>
          <div className={s.libField} role="group" aria-label="Download from, in order">
            <span>Download from, in the order picked</span>
            <div className={s.libSources}>
              {LIB_SOURCES.map(([key, name]) => {
                const at = order.indexOf(key);
                return (
                  <button
                    key={key}
                    type="button"
                    className={
                      at >= 0
                        ? `video-library-src is-on ${s.chip} ${s.chipOn}`
                        : `video-library-src ${s.chip}`
                    }
                    data-library-src={key}
                    aria-pressed={at >= 0 ? 'true' : 'false'}
                    onClick={() =>
                      setOrder(at >= 0 ? order.filter((k) => k !== key) : [...order, key])
                    }
                  >
                    {at >= 0 ? `${at + 1} ` : ''}
                    {name}
                  </button>
                );
              })}
              <span className="video-library-hint">{order.length ? '' : 'the global order'}</span>
            </div>
          </div>
          <label className={s.libField}>
            <span>Torrent client category or label</span>
            <LibInput
              className="video-library-tcat"
              defaultValue={options.torrent_category || ''}
              placeholder="Torrent category / label"
              aria-label="Torrent client category or label"
              maxLength={64}
            />
          </label>
          <label className={s.libField}>
            <span>Usenet client category</span>
            <LibInput
              className="video-library-ucat"
              defaultValue={options.usenet_category || ''}
              placeholder="Usenet category"
              aria-label="Usenet client category"
              maxLength={64}
            />
          </label>
        </div>
        <details
          className={`video-library-indexers ${s.libIndexers}`}
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
                No Prowlarr indexers (Downloads, Sources, Indexers).
              </em>
            ) : (
              indexers.map((i) => (
                <label key={i.id} className={s.libCheck}>
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
        <div className={s.libChecks}>
          <label className={s.libCheck}>
            <Switch
              aria-label="Search automatically"
              className="video-library-auto"
              defaultChecked={options.auto_search !== false}
            />
            <span>
              Search automatically
              <small>The wishlist and RSS sync hunt its titles; off, only when you search</small>
            </span>
          </label>
          <label className={s.libCheck}>
            <Switch
              aria-label="Upgrade what you have"
              className="video-library-upgrades"
              defaultChecked={options.upgrades !== false}
            />
            <span>
              Upgrade what you have
              <small>Titles you have keep upgrading toward their profile&apos;s cutoff</small>
            </span>
          </label>
          {builtin ? null : (
            <label className={s.libCheck}>
              <Switch
                aria-label="Switched on"
                className="video-library-enabled"
                defaultChecked={lib.enabled !== false}
              />
              <span>Switched on</span>
            </label>
          )}
        </div>
        <div className={`video-library-actions ${s.libActions}`}>
          <Btn kind="primary" small data-library-action="save" onClick={() => void act('save')}>
            {isNew ? 'Add' : 'Save'}
          </Btn>
          {!isNew && lib.usable && !lib.is_default ? (
            <Btn small data-library-action="default" onClick={() => void act('default')}>
              Make default
            </Btn>
          ) : null}
          {isNew || !builtin ? (
            <Btn
              kind={isNew ? 'ghost' : 'danger'}
              small
              data-library-action="remove"
              onClick={() => void act('remove')}
            >
              {isNew ? 'Cancel' : 'Remove'}
            </Btn>
          ) : null}
        </div>
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
  // the cards drawn again after a load: only the one that was acted on
  // (and a new one, which comes back with an id)
  const [drawn, setDrawn] = useState<Record<string, number>>({});
  const [added, setAdded] = useState(0);
  // a fresh load (the settings loaded again, Retry) draws every card again,
  // with the list it brought: in the same render, so no card is drawn twice
  const [generation, setGeneration] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  async function load(drawAgain?: string) {
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
      if (drawAgain === undefined) setGeneration((n) => n + 1);
      else if (drawAgain === '') {
        // the new card was saved: it comes back with an id
        setAdding(false);
        setAdded((n) => n + 1);
      } else if (drawAgain) setDrawn((d) => ({ ...d, [drawAgain]: (d[drawAgain] ?? 0) + 1 }));
      refreshChecks('video');
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
    refreshChecks('video');
  }, [adding]);

  return (
    <>
      <div id="video-libraries-list" className={`video-libraries ${s.libList}`} ref={listRef}>
        {(list || []).map((lib) => (
          <LibraryRow
            key={`${lib.id}-${generation}-${drawn[str(lib.id)] ?? 0}`}
            lib={lib}
            profiles={profiles}
            reload={(drawAgain) => void load(drawAgain)}
            cancelNew={() => setAdding(false)}
          />
        ))}
        {adding ? (
          <LibraryRow
            key={`new-${added}`}
            lib={{ kind: 'movie', enabled: true }}
            profiles={profiles}
            reload={(drawAgain) => void load(drawAgain)}
            cancelNew={() => setAdding(false)}
          />
        ) : null}
      </div>
      <datalist id="video-library-genre-list">
        {genres.map((g) => (
          <option key={g} value={g} />
        ))}
      </datalist>
      <div>
        <Btn small icon="plus" id="video-library-add" onClick={() => setAdding(true)}>
          Add library
        </Btn>
      </div>
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
type DriveStatus = { text: string; tone: 'ok' | 'bad' | 'idle' | 'busy' };

/** settings.js drivePrefix: one folder of a drive reached, where its
 *  neighbours are (never past the drive itself) */
export function drivePrefix(server: string, local: string) {
  const parts = String(server).split('/').filter(Boolean);
  const l = String(local).split(/[\\/]/);
  let n = 0;
  const keep = parts.length > 2 ? 2 : 1;
  while (
    n < parts.length - keep &&
    n < l.length - 1 &&
    parts[parts.length - 1 - n].toLowerCase() === String(l[l.length - 1 - n]).toLowerCase()
  )
    n++;
  if (!n) return null;
  const sep = local.includes('\\') ? '\\' : '/';
  return {
    server: `/${parts.slice(0, parts.length - n).join('/')}`,
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
      setStatus((st) => ({
        ...st,
        [server]: { text: 'Not reached yet: works the way it does today', tone: 'idle' },
      }));
      return;
    }
    setStatus((st) => ({ ...st, [server]: { text: 'Checking…', tone: 'busy' } }));
    try {
      const r = await fetch(`${DRIVES_URL}/check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server, local: value }),
      });
      const res = (await r.json()) as { ok?: boolean; message?: string };
      if ((localNow.current[server] || '').trim() !== value) return; // typed on meanwhile
      setStatus((st) => ({
        ...st,
        [server]: {
          text: res.message || (res.ok ? 'Reached' : 'Not reached'),
          tone: res.ok ? 'ok' : 'bad',
        },
      }));
      if (res.ok) fillSiblings(server, value, all);
    } catch {
      setStatus((st) => ({ ...st, [server]: { text: 'The check failed', tone: 'bad' } }));
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
      <div id="video-drive-map" className={s.driveMap}>
        {drives === null ? null : !drives.length ? (
          <p className={s.listEmpty}>No library folders yet: pick your libraries and scan first.</p>
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
              <div className={s.driveServer}>
                <code title={d.server}>{d.server}</code>
                <span>{meta}</span>
              </div>
            );
            if (d.auto)
              return (
                <div
                  key={d.server}
                  className={s.driveRow}
                  data-server={d.server}
                  data-kind={d.kind || ''}
                  data-local={d.local}
                  data-auto=""
                >
                  {head}
                  <div className={s.driveLocal}>
                    <div className={s.driveKnown}>{d.local}</div>
                    <StatusWords tone="ok">{d.role || 'Already reached'}</StatusWords>
                  </div>
                </div>
              );
            // the example is the drive's own path on a share
            const parts = String(d.server).split('/').filter(Boolean);
            const example = `\\\\nas\\${(parts.length > 1 ? parts.slice(1) : parts).join('\\')}`;
            const st = status[d.server];
            return (
              <div
                key={d.server}
                className={s.driveRow}
                data-server={d.server}
                data-kind={d.kind || ''}
              >
                {head}
                <div className={s.driveLocal}>
                  <span className={`${s.field} ${s.fieldFull}`}>
                    <input
                      type="text"
                      className={`${s.input} ${s.inputMono} video-drive-local`}
                      value={local[d.server] || ''}
                      placeholder={`Where SoulSync reaches it, like ${example}`}
                      aria-label={`Where SoulSync reaches ${d.server}`}
                      onChange={(event) => {
                        const v = event.target.value;
                        localNow.current = { ...localNow.current, [d.server]: v };
                        setLocal(localNow.current);
                        clearTimeout(timers.current[d.server]);
                        timers.current[d.server] = setTimeout(() => void check(d.server), 600);
                      }}
                    />
                  </span>
                  <div className={s.check} data-tone={st?.tone} aria-live="polite">
                    {st?.text || ''}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
      <div>
        <Btn small id="video-drive-map-save" onClick={() => void save()}>
          Save folders
        </Btn>
      </div>
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
  const [lines, setLines] = useState('');
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
      setLines(
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
    <div data-video-compose="" hidden={!docker}>
      <SectionLabel
        title="Docker volumes"
        hint="The compose lines for the folders set here"
        action={
          <Btn
            small
            icon="copy"
            id="video-compose-copy"
            onClick={() => {
              if (!navigator.clipboard) return toast('Select the lines and copy them', 'info');
              navigator.clipboard
                .writeText(lines)
                .then(() => toast('Compose lines copied', 'success'))
                .catch(() => toast('Select the lines and copy them', 'info'));
            }}
          >
            Copy lines
          </Btn>
        }
      />
      <Card>
        <div className={s.cardPad}>
          <Pre id="video-compose-lines">{lines}</Pre>
        </div>
        <p className={s.cardFoot}>
          In Docker every folder above is a path inside the container. Each needs a line in your
          compose file mapping a real folder to it, read-write so renames, upgrades and the recycle
          bin work. Swap the left side for the folder on your machine, then recreate the container.
        </p>
      </Card>
    </div>
  );
}

// ── the import folder (#2129) ──

/** its own endpoint: the Import page reads and flips the same settings */
export const IMPORT_FOLDER_URL = '/api/video/import/folder/settings';

/** loadImportFolder / saveImportFolder: loaded with each load of the
 *  downloads config, saved (both fields, read off the page) when one changes */
function ImportFolder() {
  const { loads } = useVideoDownloads();
  const [path, setPath] = useState('');
  const [auto, setAuto] = useState(false);
  const [locked, setLocked] = useState(true);
  const pathEl = useRef<HTMLInputElement | null>(null);
  const autoEl = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!loads) return;
    let live = true;
    void (async () => {
      try {
        const r = await fetch(IMPORT_FOLDER_URL, { headers: { Accept: 'application/json' } });
        const d = r.ok ? ((await r.json()) as { path?: string; auto?: boolean } | null) : null;
        if (!live || !d) return;
        setPath(d.path || '');
        setAuto(!!d.auto);
        shell().SoulSyncFolderCheck?.refresh('video');
      } catch {
        // as before: a failed load leaves the fields as they are
      }
    })();
    return () => {
      live = false;
    };
  }, [loads]);

  async function save() {
    try {
      const r = await fetch(IMPORT_FOLDER_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          path: pathEl.current ? pathEl.current.value : '',
          auto: !!autoEl.current?.checked,
        }),
      });
      if (!r.ok) throw new Error('save');
      toast('Import folder saved', 'success');
    } catch {
      toast('Couldn’t save the import folder', 'error');
    }
  }
  const commitPath = useCommit<HTMLInputElement>(() => void save());
  const commitAuto = useCommit<HTMLInputElement>(() => void save());

  return (
    <>
      <Row
        wide
        label="Import folder"
        htmlFor="video-import-folder-path"
        help="Optional. Movie or episode files dropped here show on the Import page with the title SoulSync found."
        more="They're filed into your libraries with your naming template, exactly like a download. Keep it apart from the download folder."
      >
        <PathField
          id="video-import-folder-path"
          value={path}
          onChange={setPath}
          placeholder="/media/import"
          locked={locked}
          onLock={(next) => {
            setLocked(next);
            if (!next) setTimeout(() => pathEl.current?.focus(), 0);
          }}
          onCheck={(answer) => folderChecks.set('video-import-folder-path', answer)}
          inputProps={{
            ...checkAttrs('video', 'Import folder', 'input'),
            ref: (el: HTMLInputElement | null) => {
              pathEl.current = el;
              commitPath(el);
            },
          }}
        />
      </Row>
      <Row
        sub
        label="Import sure matches automatically"
        htmlFor="video-import-folder-auto"
        help="A sure match is imported on its own once it has finished copying."
      >
        <Switch
          id="video-import-folder-auto"
          ref={(el: HTMLInputElement | null) => {
            autoEl.current = el;
            commitAuto(el);
          }}
          checked={auto}
          onChange={(event) => setAuto(event.target.checked)}
        />
      </Row>
    </>
  );
}

/** the video minimum free disk space: a key of the video organization, saved when left */
function VideoMinFree() {
  const { form } = useVideoOrganization();
  const commit = useCommit<HTMLInputElement>(() => void videoOrganization.save());
  return (
    <Row
      field
      label="Minimum free disk space"
      htmlFor="vo-min-free"
      help="New grabs are refused, and the wishlist pauses, when the target drive has less free space than this. 0 turns it off."
    >
      <NumberField
        id="vo-min-free"
        min="0"
        step="1"
        placeholder="0"
        unit="GB"
        ref={commit}
        value={String(form.min_free_disk_gb ?? '')}
        onChange={(event) => videoOrganization.edit({ min_free_disk_gb: event.target.value })}
      />
    </Row>
  );
}

function Retry({ status, onRetry }: { status: string; onRetry: () => void }) {
  if (status !== 'loading' && status !== 'failed') return null;
  return (
    <p className={s.loadNote} role="status">
      {status === 'loading' ? (
        'Loading saved video settings...'
      ) : (
        <>
          Could not load saved video settings.{' '}
          <button type="button" className="stg-library-retry" onClick={onRetry}>
            Retry
          </button>
        </>
      )}
    </p>
  );
}

export function VideoFolders({ docker }: { docker: boolean | null }) {
  const { config, status, loads } = useVideoDownloads();
  // an object ref, not a callback: a fresh callback each render is detached
  // and attached again after the Docker lines' effect runs, which saw null
  const panel = useRef<HTMLDivElement | null>(null);
  const busy = status === 'loading' || status === 'failed';
  useBusyGate(panel, busy);
  const open4k = {
    movies: useRef<(() => void) | null>(null),
    tv: useRef<(() => void) | null>(null),
  };

  // after each load the folder checks look again
  useEffect(() => {
    if (loads) refreshChecks('video');
  }, [loads]);
  // the extra folders' hint follows the setup: a media server or Standalone
  const { active } = useVideoServer();
  useEffect(() => {
    if (loads) videoServer.ensureServer();
  }, [loads]);

  const fourK = (kind: 'movies' | 'tv') => !!config?.[`${kind}_4k_enabled`];
  function toggle4k(kind: 'movies' | 'tv', on: boolean) {
    void videoDownloads.change({ [`${kind}_4k_enabled`]: on });
    refreshChecks('video');
    // nothing to protect yet: open the lock so the folder can be typed
    if (on && !str(config?.[`${kind}_4k_path`]).trim()) open4k[kind].current?.();
  }

  // a library folder left empty once the settings loaded: said on its row
  const unset = (field: string) => status === 'loaded' && !str(config?.[field]).trim();
  const lib = (id: string, field: string, placeholder: string, label: string) => (
    <VideoPath id={id} field={field} placeholder={placeholder} label={label} />
  );

  return (
    <div
      id="folders-video-panel"
      ref={panel}
      aria-busy={status === 'idle' ? undefined : busy ? 'true' : 'false'}
    >
      <Retry status={status} onRetry={() => void videoDownloads.load()} />
      <SectionLabel
        title="Where downloads arrive"
        hint="Files land here first, then are renamed and moved into a library below"
      />
      <div className={s.guides}>
        <p className={s.guide} data-paths-guide="docker" hidden={docker === false}>
          <strong>Docker / Unraid detected:</strong> these start on the container paths your compose
          file maps. Leave them and map your real folders in the container config. Unlock one only
          if you mapped it somewhere else.
        </p>
        <p className={s.guide} data-paths-guide="native" hidden={docker === true}>
          <strong>Bare metal / Proxmox LXC / systemd:</strong> there is no container, so unlock each
          folder and point it at your real one.
        </p>
      </div>
      <Card>
        <Row
          wide
          label="Download folder, shared with Music"
          htmlFor="video-download-path"
          help="Point this at the same folder as your slskd, torrent and usenet clients."
          more="It is the same setting as the Music side's download folder, so it is locked here too: change it and Music changes with it. Each folder gets a live check under it: whether SoulSync can write there and how much room is left."
        >
          <VideoPath
            id="video-download-path"
            field="download_path"
            placeholder="./downloads"
            role="input"
            label="Download folder"
          />
        </Row>
        <VideoMinFree />
        <ImportFolder />
      </Card>

      <SectionLabel
        title="Libraries"
        hint="Where finished downloads are filed. Point Plex, Jellyfin or Emby at the same folders"
      />
      <Card>
        <Row
          wide
          label="Movies"
          htmlFor="video-movies-path"
          warn={unset('movies_path') ? 'No movies folder is set yet.' : undefined}
        >
          {lib('video-movies-path', 'movies_path', '/media/movies', 'Movies library')}
        </Row>
        <Row
          wide
          label="TV shows"
          htmlFor="video-tv-path"
          warn={unset('tv_path') ? 'No TV folder is set yet.' : undefined}
        >
          {lib('video-tv-path', 'tv_path', '/media/tv', 'TV library')}
        </Row>
        <Row
          wide
          label="Anime"
          htmlFor="video-anime-path"
          help="Optional. New anime episodes go here instead of the TV folder. Empty keeps anime with TV."
          more="Shows in a TV library ticked Anime (Connections), shows set to Anime by hand, and shows not in your library yet that TMDB suggests are anime (an admin can change that on the show's page or when approving)."
        >
          {lib('video-anime-path', 'anime_path', '/media/anime', 'Anime library')}
        </Row>
        <Row wide label="YouTube" htmlFor="video-youtube-path">
          {lib('video-youtube-path', 'youtube_path', '/media/youtube', 'YouTube library')}
        </Row>
      </Card>

      <SectionLabel
        title="4K libraries"
        hint="Optional. 4K copies in their own folders, like a separate Movies 4K library in Plex"
      />
      <Card>
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
          <div key={kind}>
            <Row
              label={words}
              htmlFor={`video-${kind}-4k-enabled`}
              help={
                kind === 'movies'
                  ? 'A title files here when its quality profile saves to the 4K library (Quality). Titles you have stay where they are.'
                  : 'Anime keeps its own folder when one is set, whatever the profile.'
              }
            >
              <Switch
                id={`video-${kind}-4k-enabled`}
                data-video-4k-toggle={kind}
                checked={fourK(kind)}
                onChange={(event) => toggle4k(kind, event.target.checked)}
              />
            </Row>
            <div
              className={s.fourKPath}
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
          </div>
        ))}
      </Card>

      <SectionLabel
        title="More libraries"
        hint="Your own movie and TV libraries, each with its own folder (Stand-Up, Documentaries, Kids)"
      />
      <Card>
        <div className={s.cardPad}>
          <VideoLibraries loads={loads} />
        </div>
        <p className={s.cardFoot}>
          A new title files into the library picked for it (in its Get or Manual search window),
          else the 4K one when its quality profile says so, else the first library whose rules match
          its genres or TMDB keywords, else the default library for its kind. Titles you have stay
          where they are. The main and 4K libraries are here too; their folders are the ones above.
          Each library saves with its own Save.
        </p>
      </Card>

      <SectionLabel
        title="Your library's folders"
        hint="Every folder your Plex or Jellyfin libraries read, and where SoulSync reaches it"
      />
      <Card>
        <div className={s.cardPad}>
          <DriveMap loads={loads} />
        </div>
        <p className={s.cardFoot}>
          The ones SoulSync already reaches are ticked. Tell it where it reaches the rest and every
          file in them is found, renamed to your templates, recycled on delete and kept track of.
          New downloads still go to the folders above. Leave one blank and it works the way it does
          today.
        </p>
      </Card>

      <SectionLabel
        title="Extra folders"
        hint={
          active === 'soulsync'
            ? 'More movie and TV folders for SoulSync to scan, like a second drive. New downloads never go here.'
            : "Folders SoulSync should also manage that the libraries it scans from your server don't include. New downloads never go here."
        }
      />
      <Card>
        <Row stack label="Movie folders">
          <ExtraPaths kind="movies" />
        </Row>
        <Row stack label="TV folders">
          <ExtraPaths kind="tv" />
        </Row>
      </Card>

      <Compose panel={panel} />
    </div>
  );
}

/** the old panel's name, for the tests that render the folders on their own */
export function VideoFoldersPanel({
  hidden,
  docker,
}: {
  hidden: boolean;
  docker: boolean | null;
  settingsLoaded?: boolean;
}) {
  return (
    <div data-ui="" hidden={hidden}>
      <VideoFolders docker={docker} />
    </div>
  );
}
