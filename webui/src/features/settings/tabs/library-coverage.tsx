/**
 * Settings > Library > Folders > Music: "Your media server's folders".
 *
 * SoulSync reads music straight from disk, from its library folders. This
 * lists every music folder the linked media server keeps, says whether
 * SoulSync scans it, and how much of it really opens (a sample of each
 * folder, core/library/coverage.py). For a folder it doesn't scan it says
 * why in plain words, with the one thing to do:
 *
 *   not mounted      mount the drive into SoulSync (a compose line in Docker)
 *   not a library    "Add as a library folder", the same save as the
 *                    Additional Music Libraries list (onAddLibraryFolder)
 *   another name     found on its own; when it isn't, "SoulSync sees it
 *                    somewhere else" takes a typed folder, tries it, saves it
 *                    as an override
 *
 * Self-contained: its own fetches, no settings store. The host passes how to
 * add a library folder, so the settings overhaul can place it as it is.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import styles from './library-coverage.module.css';

export type CoverageExample = {
  server_path: string;
  local_path: string | null;
  reason: string;
  why: string;
};

export type CoverageFolder = {
  library: string;
  server_folder: string;
  tracks: number;
  local_folder: string | null;
  found_by: 'override' | 'same' | 'learned' | 'suffix' | null;
  broken_override?: string | null;
  status: 'covered' | 'partly_covered' | 'not_library' | 'not_mounted';
  library_folder?: string | null;
  library_parts?: string[];
  volume_example?: string;
  sampled: number;
  readable: number;
  readable_pct: number | null;
  reasons: Record<string, number>;
  examples: CoverageExample[];
  reachable_tracks: number;
};

export type CoverageResult = {
  success?: boolean;
  server: string;
  message?: string;
  folders_from?: 'server' | 'inferred';
  relative_paths?: boolean;
  total_tracks: number;
  outside_folders?: number;
  reachable_tracks?: number;
  reachable_pct: number | null;
  docker?: boolean;
  folders: CoverageFolder[];
};

export type PathOverride = {
  id: number;
  server_kind: string;
  server_prefix: string;
  local_prefix: string;
  enabled: boolean;
  verified_at: string | null;
  verify: { sampled?: number; readable?: number; readable_pct?: number | null };
};

type PathCheck = {
  exists: boolean;
  is_dir: boolean;
  readable: boolean;
  entries: number;
  sample: string[];
};

const SERVER_NAMES: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  navidrome: 'Navidrome',
  emby: 'Emby',
};

export function serverName(kind: string) {
  return SERVER_NAMES[kind] || (kind ? kind[0].toUpperCase() + kind.slice(1) : 'Your media server');
}

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
  });
  const data = (await response.json().catch(() => ({}))) as T & { error?: string };
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

const n = (value: number) => value.toLocaleString();

/** the one-line verdict for a folder, in plain words */
export function folderVerdict(folder: CoverageFolder): string {
  const seen = folder.local_folder;
  switch (folder.status) {
    case 'covered':
      return folder.found_by === 'same'
        ? 'SoulSync sees this folder under the same name, and scans it.'
        : `SoulSync sees this folder as ${seen}, and scans it.`;
    case 'partly_covered':
      return `SoulSync sees this folder as ${seen}, but only scans part of it: ${(folder.library_parts || []).join(', ')}.`;
    case 'not_library':
      return `SoulSync sees this folder as ${seen}, but it isn't one of its library folders, so nothing in it is scanned.`;
    default:
      return folder.broken_override
        ? `Your override says SoulSync sees this as ${folder.broken_override}, but that folder isn't there.`
        : "SoulSync can't see this folder. The drive or share it's on has to be mounted into SoulSync first.";
  }
}

function tone(folder: CoverageFolder) {
  if (folder.status === 'covered' && (folder.readable_pct ?? 0) >= 90) return 'good';
  if (folder.status === 'not_mounted') return 'bad';
  return 'warn';
}

/** a typed folder, checked as you type */
function PathField({
  value,
  onChange,
  id,
}: {
  value: string;
  onChange: (v: string) => void;
  id: string;
}) {
  const [check, setCheck] = useState<PathCheck | null>(null);
  const seq = useRef(0);
  useEffect(() => {
    const path = value.trim();
    if (!path) {
      setCheck(null);
      return;
    }
    const mine = ++seq.current;
    const timer = setTimeout(() => {
      getJson<PathCheck>('/api/library/coverage/check-path', {
        method: 'POST',
        body: JSON.stringify({ path }),
      })
        .then((data) => {
          if (mine === seq.current) setCheck(data);
        })
        .catch(() => {
          if (mine === seq.current) setCheck(null);
        });
    }, 350);
    return () => clearTimeout(timer);
  }, [value]);
  let line = '';
  let state = '';
  if (check) {
    if (!check.exists) {
      line = 'Not there inside SoulSync';
      state = 'bad';
    } else if (!check.is_dir) {
      line = "That's a file, not a folder";
      state = 'bad';
    } else if (!check.readable) {
      line = "There, but SoulSync isn't allowed to read it";
      state = 'bad';
    } else {
      const shown = check.sample.slice(0, 3).join(', ');
      line = `Found, ${check.entries >= 1000 ? '1,000+' : n(check.entries)} items${shown ? `: ${shown}` : ''}`;
      state = 'good';
    }
  }
  return (
    <>
      <input
        id={id}
        type="text"
        className={styles.pathInput}
        value={value}
        placeholder="/music"
        aria-label="The folder as SoulSync sees it"
        onChange={(event) => onChange(event.target.value)}
      />
      {line ? (
        <div className={styles.pathCheck} data-tone={state} role="status">
          {line}
        </div>
      ) : null}
    </>
  );
}

function FolderRow({
  folder,
  server,
  docker,
  onAddLibraryFolder,
  onSaved,
}: {
  folder: CoverageFolder;
  server: string;
  docker: boolean;
  onAddLibraryFolder?: (path: string) => void;
  onSaved: () => void;
}) {
  const [elsewhere, setElsewhere] = useState(false);
  const [typed, setTyped] = useState('');
  const [trial, setTrial] = useState<CoverageFolder | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [added, setAdded] = useState(false);
  const shown = trial || folder;
  const rowId = `cov-${folder.server_folder.replace(/[^A-Za-z0-9]+/g, '-')}`;

  async function tryIt() {
    setBusy(true);
    setNote('');
    try {
      const data = await getJson<{ folder: CoverageFolder }>('/api/library/coverage/folder', {
        method: 'POST',
        body: JSON.stringify({
          server_folder: folder.server_folder,
          local_folder: typed.trim(),
          library: folder.library,
        }),
      });
      setTrial(data.folder);
    } catch (error) {
      setNote(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setNote('');
    try {
      await getJson('/api/library/path-mappings', {
        method: 'POST',
        body: JSON.stringify({
          server_prefix: folder.server_folder,
          local_prefix: typed.trim(),
          this_server: true,
          media_type: 'music',
        }),
      });
      setElsewhere(false);
      setTrial(null);
      onSaved();
    } catch (error) {
      setNote(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  const addable =
    shown.status === 'not_library' && shown.local_folder && onAddLibraryFolder && !added;

  return (
    <li className={styles.folder} data-tone={tone(shown)} data-status={shown.status}>
      <div className={styles.folderHead}>
        <div className={styles.folderName}>
          {folder.library ? <span className={styles.libraryName}>{folder.library}</span> : null}
          <code>{folder.server_folder}</code>
        </div>
        <div className={styles.folderShare}>
          {shown.readable_pct == null ? '' : `${Math.round(shown.readable_pct)}%`}
          <span>{n(folder.tracks)} tracks</span>
        </div>
      </div>
      <p className={styles.verdict}>{folderVerdict(shown)}</p>
      {shown.sampled ? (
        <p className={styles.sampleLine}>
          {shown.readable} of {shown.sampled} sampled files open in SoulSync's library.
        </p>
      ) : null}
      {shown.status === 'not_mounted' && docker && shown.volume_example ? (
        <div className={styles.howTo}>
          In your compose file, under the SoulSync service's <code>volumes:</code>
          <pre>{shown.volume_example}</pre>
          then recreate the container and add the right-hand folder as a library folder.
        </div>
      ) : null}
      {added ? (
        <p className={styles.sampleLine}>
          Added. It is scanned on the next library scan; check again after that.
        </p>
      ) : null}
      {shown.examples.length ? (
        <details className={styles.examples}>
          <summary>Why some files failed</summary>
          <ul>
            {shown.examples.map((ex) => (
              <li key={ex.server_path}>
                <code>{ex.local_path || ex.server_path}</code>
                <span>{ex.why}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <div className={styles.actions}>
        {addable ? (
          <button
            className="test-button"
            onClick={() => {
              onAddLibraryFolder?.(shown.local_folder as string);
              setAdded(true);
            }}
          >
            Add as a library folder
          </button>
        ) : null}
        {shown.status !== 'covered' || trial ? (
          <button
            className="test-button"
            aria-expanded={elsewhere}
            aria-controls={`${rowId}-elsewhere`}
            onClick={() => setElsewhere(!elsewhere)}
          >
            SoulSync sees it somewhere else
          </button>
        ) : null}
      </div>
      {elsewhere ? (
        <div className={styles.elsewhere} id={`${rowId}-elsewhere`}>
          <label htmlFor={`${rowId}-input`}>
            {serverName(server)} calls it <code>{folder.server_folder}</code>. SoulSync sees this
            folder as:
          </label>
          <PathField id={`${rowId}-input`} value={typed} onChange={setTyped} />
          <div className={styles.actions}>
            <button
              className="test-button"
              disabled={busy || !typed.trim()}
              onClick={() => void tryIt()}
            >
              Try it
            </button>
            <button
              className="test-button"
              disabled={busy || !typed.trim() || !trial || !trial.readable}
              onClick={() => void save()}
            >
              Save
            </button>
          </div>
          {note ? (
            <div className={styles.pathCheck} data-tone="bad" role="alert">
              {note}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function Overrides({ rows, onRemoved }: { rows: PathOverride[]; onRemoved: () => void }) {
  if (!rows.length) return null;
  async function remove(id: number) {
    await getJson(`/api/library/path-mappings/${id}`, { method: 'DELETE' }).catch(() => null);
    onRemoved();
  }
  return (
    <div className={styles.overrides}>
      <h4>Folder overrides</h4>
      <p className="setting-help-text">
        Used before SoulSync's own guessing. They only rename a folder, they never give SoulSync
        access to one.
      </p>
      <ul>
        {rows.map((row) => (
          <li key={row.id}>
            <code>{row.server_prefix}</code>
            <span aria-hidden="true">→</span>
            <code>{row.local_prefix}</code>
            {row.verify?.sampled ? (
              <span className={styles.sampleLine}>
                {row.verify.readable} of {row.verify.sampled} opened
              </span>
            ) : null}
            <button
              className={styles.remove}
              aria-label={`Remove the override for ${row.server_prefix}`}
              onClick={() => void remove(row.id)}
            >
              &times;
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ServerFoldersSection({
  onAddLibraryFolder,
  stg,
  bare = false,
}: {
  /** add a folder to SoulSync's library folders (library.music_paths) */
  onAddLibraryFolder?: (path: string) => void;
  /** the settings tab this sits on, for the page's search and filters */
  stg?: string;
  /** no group box or heading of its own: the host draws them (the
   *  settings overhaul's SectionLabel + Card) */
  bare?: boolean;
}) {
  const [data, setData] = useState<CoverageResult | null>(null);
  const [overrides, setOverrides] = useState<PathOverride[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true);
    setError('');
    try {
      const [coverage, maps] = await Promise.all([
        getJson<CoverageResult>(`/api/library/coverage${refresh ? '?refresh=1' : ''}`),
        getJson<{ mappings: PathOverride[] }>('/api/library/path-mappings?media_type=music'),
      ]);
      setData(coverage);
      setOverrides(maps.mappings || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const name = serverName(data?.server || '');
  const pct = data?.reachable_pct;

  return (
    <div className={bare ? undefined : 'settings-group'} id="library-coverage" data-stg={stg}>
      {bare ? null : <h3>Your media server's folders</h3>}
      <div
        className={bare ? styles.intro : 'help-text'}
        style={bare ? undefined : { marginBottom: '12px' }}
      >
        SoulSync reads your music straight from disk, from its library folders. This compares the
        folders your media server keeps music in with the ones SoulSync can see. The names don't
        have to match, but the drives do have to be mounted into SoulSync.
      </div>
      {error ? (
        <div className={styles.pathCheck} data-tone="bad" role="alert">
          {error}
        </div>
      ) : null}
      {data && !data.server ? <p className="setting-help-text">{data.message}</p> : null}
      {data && data.server ? (
        <>
          <div
            className={styles.overall}
            data-tone={pct == null ? '' : pct >= 90 ? 'good' : pct >= 50 ? 'warn' : 'bad'}
          >
            <strong>{pct == null ? '–' : `${Math.round(pct)}%`}</strong>
            <span>
              {pct == null
                ? `${name} hasn't reported any tracks yet. Run a library scan first.`
                : `of ${name}'s music SoulSync can read directly: about ${n(data.reachable_tracks || 0)} of ${n(data.total_tracks)} tracks, from a sample of each folder.`}
            </span>
          </div>
          {data.relative_paths ? (
            <p className="setting-help-text">
              {name} sends paths that start at the artist folder, so they can't be matched to a
              folder. In Navidrome, turn on Report Real Path for the SoulSync user.
            </p>
          ) : null}
          {data.folders_from === 'inferred' && data.folders.length ? (
            <p className="setting-help-text">
              {name} doesn't say which folders it uses, so these were worked out from its track
              paths.
            </p>
          ) : null}
          {data.outside_folders ? (
            <p className="setting-help-text">
              {n(data.outside_folders)} tracks sit outside every folder {name} lists.
            </p>
          ) : null}
          <ul className={styles.folders}>
            {data.folders.map((folder) => (
              <FolderRow
                key={folder.server_folder}
                folder={folder}
                server={data.server}
                docker={!!data.docker}
                onAddLibraryFolder={onAddLibraryFolder}
                onSaved={() => void load(true)}
              />
            ))}
          </ul>
        </>
      ) : null}
      <Overrides rows={overrides} onRemoved={() => void load(true)} />
      <div className="form-actions" style={{ marginTop: '8px' }}>
        <button className="test-button" disabled={loading} onClick={() => void load(true)}>
          {loading ? 'Checking…' : 'Check again'}
        </button>
      </div>
    </div>
  );
}
