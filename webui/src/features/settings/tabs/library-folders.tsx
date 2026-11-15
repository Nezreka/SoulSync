/**
 * Library > Folders (SETTINGS_PORT.md, step 8a): the media-tabbed card, its
 * Music and Podcasts panels. Same markup, classes and ids.
 *
 * The Audiobooks and Video panels are still vanilla (the audiobook folders and
 * spoken-word routes are settings.js until step 8c; the video panel is
 * video-settings.js until the video pass). They are parked in
 * #library-parked-panels and placed back in the card, in their old order,
 * while it is mounted; the tabs show and hide them like the rest.
 *
 * folder-check.js puts a status line after each checked input's group or row.
 * It listens on the document, so these inputs need nothing but the same data
 * attributes, and a refresh once their values are in. A removed list row takes
 * its status line with it, as before.
 */

import { type ReactNode, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';
import type { SettingsStore } from '../settings-store';

import { AudiobookFoldersPanel, audiobooksPath } from './library-audiobooks';
import {
  isDocker,
  LIBRARY_TAB,
  LibrarySection,
  LockedPath,
  useMediaTabs,
  useParkedPanels,
} from './library-card';
import { OrganizationCard } from './library-organization';
import { ProcessingCards } from './library-processing';
import { helpBits, useHelp } from './parts';
import { NumberField, text } from './source-panels';

const TAB = LIBRARY_TAB;

type ShellTools = Window & {
  SoulSyncFolderCheck?: { refresh: (group: string) => void };
  showToast?: (message: string, type?: string) => void;
};

function refreshChecks(...groups: string[]) {
  const check = (window as ShellTools).SoulSyncFolderCheck;
  if (check) for (const group of groups) check.refresh(group);
}

function useSettings(store: SettingsStore) {
  return useSyncExternalStore(store.subscribe, () => store.getState().settings);
}

export { isDocker };

/** the podcasts folder as loadSettingsData filled it: Docker's own default
 *  replaces the bare './podcasts' */
export function podcastsPath(settings: Record<string, Record<string, unknown> | undefined>) {
  const fallback = isDocker(settings) ? '/app/podcasts' : './podcasts';
  const current = text(settings.podcasts?.download_path) || text(settings.library?.podcasts_path);
  return isDocker(settings) && (!current || current === './podcasts')
    ? fallback
    : current || fallback;
}

/** LIBRARY_SUMMARIES['library-paths'] */
export function pathsSummary(paths: string[]) {
  const named = paths.filter((p) => p.trim());
  if (!named.length) return 'no folders set yet';
  const tail =
    named[0]
      .replace(/[\\/]+$/, '')
      .split(/[\\/]/)
      .pop() || '';
  return named.length === 1 ? tail : `${tail} +${named.length - 1} more`;
}

/** a field with its "i" and help body inside the form-group, the help line last */
function HelpField({
  label,
  help,
  children,
  after,
}: {
  label: ReactNode;
  help: ReactNode;
  children: ReactNode;
  after?: ReactNode;
}) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group">
      {label}
      {children}
      {bits.icon}
      <div className="setting-help-text setting-help-body" hidden={!state.open}>
        {help}
      </div>
      {after}
      {bits.line}
    </div>
  );
}

const ROW_REMOVE_STYLE = {
  padding: '8px 12px',
  color: '#ef5350',
  borderColor: 'rgba(239,83,80,0.3)',
};

const EMPTY_STYLE = { color: 'rgba(255,255,255,0.3)', fontSize: '0.85em', padding: '4px 0' };

/** a list of folders (extra music libraries, Sample Studio folders) */
function FolderList({
  rows,
  setRows,
  empty,
  rowClass,
  inputClass,
  placeholder,
  check,
  defaultTag,
  loaded,
}: {
  /** the settings have loaded: before that the list is empty, not "none" */
  loaded: boolean;
  rows: string[];
  setRows: (rows: string[]) => void;
  empty: string;
  rowClass: string;
  inputClass: string;
  placeholder: string;
  check?: Record<string, string>;
  /** the first row is the default (Sample Studio) */
  defaultTag?: boolean;
}) {
  const list = useRef<HTMLDivElement>(null);
  const [focusLast, setFocusLast] = useState(false);
  useEffect(() => {
    if (!focusLast) return;
    setFocusLast(false);
    const inputs = list.current?.querySelectorAll<HTMLInputElement>(`.${inputClass}`);
    inputs?.[inputs.length - 1]?.focus();
  }, [focusLast]); // eslint-disable-line react-hooks/exhaustive-deps

  function remove(index: number, button: HTMLElement) {
    // folder-check.js's status line for this row goes with it
    const row = button.closest(`.${rowClass}`);
    const status = row?.nextElementSibling;
    if (status && status.classList.contains('folder-check')) status.remove();
    setRows(rows.filter((_, i) => i !== index));
    if (check) setTimeout(() => refreshChecks(check['data-folder-check']), 0);
  }

  return (
    <>
      <div ref={list} id={rowClass === 'music-path-row' ? 'music-paths-list' : 'sample-paths-list'}>
        {!loaded ? null : rows.length === 0 ? (
          <div style={EMPTY_STYLE}>{empty}</div>
        ) : (
          rows.map((value, i) => (
            <div
              key={i}
              className={`form-group ${rowClass}`}
              {...(check ? { 'data-folder-row': '' } : {})}
              style={{ marginBottom: '4px' }}
            >
              <input
                type="text"
                className={inputClass}
                value={value}
                placeholder={placeholder}
                style={{ flex: 1 }}
                {...check}
                onChange={(event) =>
                  setRows(rows.map((r, k) => (k === i ? event.target.value : r)))
                }
              />
              {defaultTag && i === 0 ? (
                <span
                  style={{ color: 'rgba(255,255,255,0.4)', fontSize: '0.8em', padding: '8px 4px' }}
                >
                  default
                </span>
              ) : null}
              <button
                className="test-button"
                style={ROW_REMOVE_STYLE}
                onClick={(event) => remove(i, event.currentTarget)}
              >
                &times;
              </button>
            </div>
          ))
        )}
      </div>
      <div className="form-actions" style={{ marginTop: '8px' }}>
        <button
          className="test-button"
          onClick={() => {
            setRows([...rows, '']);
            setFocusLast(true);
          }}
        >
          + Add Path
        </button>
      </div>
    </>
  );
}

const MUSIC_CHECK = { 'data-folder-check': 'music' };

export function FoldersCard({ store }: SettingsTabProps) {
  const settings = useSettings(store);
  const soulseek = (settings.soulseek || {}) as Record<string, unknown>;
  const library = (settings.library || {}) as Record<string, unknown>;
  const m3u = (settings.m3u_export || {}) as Record<string, unknown>;
  const playlists = (settings.playlists || {}) as Record<string, unknown>;
  const env = (settings._environment || {}) as Record<string, unknown>;
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  const docker = !!env.docker;

  const download = text(soulseek.download_path) || './downloads';
  const transfer = text(soulseek.transfer_path) || './Transfer';
  const podcasts = podcastsPath(settings);

  // the two lists hold their rows here and save the trimmed, non-empty ones
  const [music, setMusic] = useState<string[]>([]);
  const [samples, setSamples] = useState<string[]>([]);
  useEffect(() => {
    if (!loads) return;
    setMusic((library.music_paths as string[] | undefined) || []);
    const saved = (library.sample_paths as string[] | undefined) || [];
    // a config from before the key gets the Docker-aware default row
    setSamples(saved.length ? saved : [isDocker(settings) ? '/app/samples' : './samples']);
    // the folder values are in now, so the live checks can run
    setTimeout(() => refreshChecks('music', 'audiobooks', 'podcasts'), 0);
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  const saveList = (key: string, rows: string[]) =>
    store.set('library', key, rows.map((r) => r.trim()).filter(Boolean));

  // the out-of-the-box library folder on a bare-metal install fills the
  // install disk (an 8 GB LXC root hangs): it warns
  const out = transfer.trim();
  const defaultOut = out === '' || out === '.' || out.startsWith('./') || out.startsWith('.\\');

  // ── media tabs, and the parked vanilla panels between React's own ──
  const { media, tabs } = useMediaTabs('folders', 'Folders media type');
  const musicPanel = useRef<HTMLDivElement>(null);
  const podcastsPanel = useRef<HTMLDivElement>(null);
  useParkedPanels(media, [{ id: 'folders-video-panel', after: podcastsPanel, media: 'video' }]);
  // the vanilla panels' own deployment guides (the video one has a pair):
  // applyPathsEnvironment toggled every guide on the page
  useEffect(() => {
    if (!loads) return;
    const guides = document.querySelectorAll(
      '#folders-audiobooks-panel [data-paths-guide], #folders-video-panel [data-paths-guide]',
    );
    for (const el of guides)
      el.classList.toggle('hidden', (el.getAttribute('data-paths-guide') === 'docker') !== docker);
  }, [loads, docker]);

  const summary = loads
    ? pathsSummary([download, transfer, podcasts, audiobooksPath(settings)])
    : '';
  const fallback = 'Downloads, library & extra folders';

  // ── rebuild playlist folders ──
  const [rebuild, setRebuild] = useState<{ busy: boolean; text: string; color: string }>({
    busy: false,
    text: '',
    color: '',
  });
  async function rebuildNow() {
    setRebuild({ busy: true, text: 'Rebuilding playlist folders…', color: '' });
    try {
      const response = await fetch('/api/playlists/materialize/rebuild', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const data = (await response.json()) as {
        success?: boolean;
        error?: string;
        count?: number;
        results?: Array<{ linked?: number; copied?: number; removed_stale?: number }>;
      };
      if (!response.ok || !data.success) throw new Error(data.error || 'Rebuild failed');
      const n = data.count || 0;
      let linked = 0;
      let copied = 0;
      let removed = 0;
      for (const r of data.results || []) {
        linked += r.linked || 0;
        copied += r.copied || 0;
        removed += r.removed_stale || 0;
      }
      setRebuild({
        busy: false,
        color: '#4caf50',
        text:
          n === 0
            ? 'No "organize by playlist" playlists to rebuild yet.'
            : `Rebuilt ${n} playlist folder${n === 1 ? '' : 's'} — ${linked} linked, ${copied} copied, ${removed} stale removed.`,
      });
      (window as ShellTools).showToast?.('Playlist folders rebuilt', 'success');
    } catch (error) {
      setRebuild({
        busy: false,
        color: '#f44336',
        text: 'Rebuild failed: ' + (error instanceof Error ? error.message : String(error)),
      });
      (window as ShellTools).showToast?.('Playlist rebuild failed', 'error');
    }
  }

  const hint = (children: ReactNode) => <small className="settings-hint">{children}</small>;

  return (
    <>
      <div className="stg-cardgroup" data-stg={TAB}>
        <span className="stg-cardgroup-label">Files &amp; folders</span>
        <span className="stg-cardgroup-sub">Where things live on disk, and how they get named</span>
      </div>
      <LibrarySection title="Folders" hint="Downloads and library locations">
        {tabs}
        <div
          ref={musicPanel}
          id="folders-music-panel"
          role="tabpanel"
          aria-labelledby="folders-music-tab"
          className="stg-media-panel"
          tabIndex={0}
          hidden={media !== 'music'}
        >
          <div className="stg-media-context">
            <strong>Music folders</strong>
            <span
              className={summary ? 'stg-media-summary is-live' : 'stg-media-summary'}
              data-stg-summary="library-paths"
              data-stg-fallback={fallback}
            >
              {summary || fallback}
            </span>
          </div>

          <div className="settings-group" data-stg={TAB}>
            <h3>Folder Paths</h3>
            <div className="setting-help-text" style={{ marginBottom: '4px' }}>
              How files flow: <strong>Download Folder</strong> (raw downloads land here) →
              post-processing (tagging, artwork, organizing) → <strong>Music Library Folder</strong>{' '}
              (their final home — point your media server here). Keeping the two separate stops
              half-finished files being scanned.
            </div>
            {/* one of these shows, picked by the detected deployment */}
            <div
              className={loads && !docker ? 'setting-help-text hidden' : 'setting-help-text'}
              style={{ marginBottom: '4px' }}
              data-paths-guide="docker"
            >
              <strong>Docker / Unraid detected:</strong> leave these as the container-internal paths
              and map your real folders in the container config — wrong values here break downloads.
            </div>
            <div
              className={loads && docker ? 'setting-help-text hidden' : 'setting-help-text'}
              style={{ marginBottom: '4px' }}
              data-paths-guide="native"
            >
              <strong>Bare metal / Proxmox LXC / systemd:</strong> there is no container, so you're
              expected to unlock these and point them at your real folders.
            </div>
            <div
              className={
                loads && !docker && defaultOut ? 'setting-help-text' : 'setting-help-text hidden'
              }
              style={{
                marginBottom: '4px',
                color: '#fca5a5',
                border: '1px solid rgba(239, 68, 68, 0.35)',
                borderRadius: '10px',
                padding: '10px 12px',
                background: 'rgba(239, 68, 68, 0.08)',
              }}
              data-paths-default-warning=""
            >
              ⚠️ <strong>Your Music Library Folder is still the install-folder default.</strong>{' '}
              Every finished download is landing on the disk SoulSync is installed on — on a Proxmox
              LXC that's typically an 8&nbsp;GB root that fills until the container hangs. Unlock
              the field and point it at your real music folder.
            </div>

            <div className="form-group">
              <label>Download Folder (input):</label>
              <LockedPath
                id="download-path"
                check={{
                  ...MUSIC_CHECK,
                  'data-folder-role': 'input',
                  'data-folder-label': 'Download folder',
                }}
                placeholder="./downloads"
                value={download}
                onChange={(v) => store.set('soulseek', 'download_path', v)}
              />
              {hint(
                'Where new downloads land before processing. Match this to your slskd download folder.',
              )}
            </div>
            <div className="form-group">
              <label>Music Library Folder (output):</label>
              <LockedPath
                id="transfer-path"
                check={{ ...MUSIC_CHECK, 'data-folder-label': 'Music library folder' }}
                placeholder="./Transfer"
                value={transfer}
                onChange={(v) => store.set('soulseek', 'transfer_path', v)}
              />
              {hint(
                'Your main music library. Finished tracks are moved here, organized into Artist/Album folders.',
              )}
            </div>
            <div className="form-group">
              <label>Minimum free disk space (GB):</label>
              <NumberField
                store={store}
                id="min-free-disk-gb"
                min="0"
                step="0.5"
                placeholder="5"
                value={text(soulseek.min_free_disk_gb ?? 5)}
                save={(raw) => {
                  const n = parseFloat(raw);
                  store.set('soulseek', 'min_free_disk_gb', Number.isFinite(n) ? n : 0);
                }}
              />
              {hint(
                'New downloads are refused when the download disk drops below this. Stops a full disk from hanging the whole system (0 = off).',
              )}
            </div>
            <div className="form-group">
              <label>Import Folder:</label>
              <LockedPath
                id="staging-path"
                check={{
                  ...MUSIC_CHECK,
                  'data-folder-role': 'input',
                  'data-folder-label': 'Import folder',
                }}
                placeholder="./Staging"
                value={text(settings.import?.staging_path) || './Staging'}
                onChange={(v) => store.set('import', 'staging_path', v || './Staging')}
              />
            </div>
            <div className="form-group">
              <label>Music Videos Dir:</label>
              <LockedPath
                id="music-videos-path"
                check={{ ...MUSIC_CHECK, 'data-folder-label': 'Music videos folder' }}
                placeholder="./MusicVideos"
                value={text(library.music_videos_path) || './MusicVideos'}
                onChange={(v) => store.set('library', 'music_videos_path', v || './MusicVideos')}
              />
            </div>
            <HelpField
              label={<label>Playlists Folder:</label>}
              help={
                <>
                  Where <strong>Organize by playlist</strong> builds playlist folders — as shortcuts
                  into your library, so songs are never stored twice. Keep this{' '}
                  <strong>outside</strong> your music library folder so your media server doesn't
                  scan the same tracks twice.
                </>
              }
            >
              <LockedPath
                id="playlists-materialize-path"
                check={{ ...MUSIC_CHECK, 'data-folder-label': 'Playlists folder' }}
                placeholder="./Playlists"
                value={text(playlists.materialize_path) || './Playlists'}
                onChange={(v) => store.set('playlists', 'materialize_path', v || './Playlists')}
              />
            </HelpField>
            <HelpField
              label={<label>Playlist Folder Style:</label>}
              help="Symlinks use almost no space. Copies are self-contained. Symlinks fall back to copies automatically where the filesystem can't link (Windows without privileges, some network shares, FAT/exFAT drives)."
              after={
                <div style={{ flexBasis: '100%', width: '100%' }}>
                  <button
                    className="test-button"
                    id="playlists-rebuild-btn"
                    disabled={rebuild.busy}
                    onClick={() => void rebuildNow()}
                  >
                    {rebuild.busy ? 'Rebuilding…' : 'Rebuild playlist folders now'}
                  </button>
                  <div
                    className="setting-help-text"
                    id="playlists-rebuild-status"
                    style={{ marginTop: '6px', ...(rebuild.color ? { color: rebuild.color } : {}) }}
                  >
                    {rebuild.text}
                  </div>
                </div>
              }
            >
              <select
                id="playlists-materialize-mode"
                value={text(playlists.materialize_mode) || 'symlink'}
                onChange={(event) =>
                  store.set('playlists', 'materialize_mode', event.target.value || 'symlink')
                }
              >
                <option value="symlink">Symlinks — no extra disk, point to your library</option>
                <option value="copy">
                  Copies — real duplicates (USB sticks / players that can't follow links)
                </option>
              </select>
            </HelpField>
            <HelpField
              label={
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    id="m3u-relative-paths"
                    checked={m3u.relative_paths === true}
                    onChange={(event) =>
                      store.set('m3u_export', 'relative_paths', event.target.checked)
                    }
                  />
                  Write M3U entries relative to the playlist file
                </label>
              }
              help={
                <>
                  Entries become <code>../Artist/Album/01 - Song.flac</code> instead of the full
                  path SoulSync sees, so a saved M3U still works when the folder is opened from a
                  network share, another machine or a sync tool. Tracks that don't share a folder
                  with the playlist keep their full path, and the base path and prefix rewrite below
                  still apply to those.
                </>
              }
            >
              {null}
            </HelpField>
            <HelpField
              label={<label>M3U Entry Base Path:</label>}
              help={
                <>
                  Optional prefix added to every track path in exported M3U files. Use this when
                  your media server needs absolute paths (e.g. <code>/mnt/music</code>).
                </>
              }
            >
              <LockedPath
                id="m3u-entry-base-path"
                placeholder="e.g. /mnt/music (leave empty to use paths as stored)"
                value={text(m3u.entry_base_path)}
                onChange={(v) => store.set('m3u_export', 'entry_base_path', v)}
              />
            </HelpField>
            <HelpField
              label={<label>M3U Path Prefix Rewrite:</label>}
              help={
                <>
                  Hot-swaps a path prefix on every entry when M3Us are generated, so a playlist
                  written inside a container (<code>/data/media/&hellip;</code>) plays directly on
                  the machine that mounts the same storage elsewhere (<code>M:/media/&hellip;</code>
                  ). Entries that don't start with the prefix pass through unchanged. Leave both
                  empty to disable.
                </>
              }
            >
              <div className="path-input-group">
                <input
                  type="text"
                  id="m3u-rewrite-from"
                  placeholder="replace this prefix, e.g. /data/media"
                  value={text(m3u.rewrite_from)}
                  onChange={(event) => store.set('m3u_export', 'rewrite_from', event.target.value)}
                />
                <input
                  type="text"
                  id="m3u-rewrite-to"
                  placeholder="with this, e.g. M:/media"
                  value={text(m3u.rewrite_to)}
                  onChange={(event) => store.set('m3u_export', 'rewrite_to', event.target.value)}
                />
              </div>
            </HelpField>
          </div>
          <div className="settings-group" data-stg={TAB}>
            <h3>Additional Music Libraries</h3>
            <div className="help-text" style={{ marginBottom: '12px' }}>
              Your Music Library Folder above is already covered —{' '}
              <strong>you don't need to repeat it here.</strong> Add entries only for <em>extra</em>{' '}
              libraries SoulSync should read (a second collection, an archive drive), or when your
              media server sees the same files at a different path than SoulSync can. Used for tag
              writing, streaming, and file detection. <strong>Docker users:</strong> mount the extra
              folder(s) into the SoulSync container with read-write access, then add the{' '}
              <em>container-side</em> path here (e.g. <code>/music2</code>).
            </div>
            <FolderList
              loaded={loads > 0}
              rows={music}
              setRows={(rows) => {
                setMusic(rows);
                saveList('music_paths', rows);
              }}
              empty={'No paths configured. Click "Add Path" to add your music folder(s).'}
              rowClass="music-path-row"
              inputClass="music-path-input"
              placeholder="/music or C:\Music"
              check={{
                ...MUSIC_CHECK,
                'data-folder-role': 'existing',
                'data-folder-label': 'Extra music folder',
              }}
            />
          </div>
          <div className="settings-group" data-stg={TAB}>
            <h3>Sample Studio Folders</h3>
            <div className="help-text" style={{ marginBottom: '12px' }}>
              Where <strong>Sample Studio</strong> saves your chops — organized into Artist/Track
              folders inside the destination. The <strong>first folder is the default</strong>; the
              save dialog lets you pick a different one per chop (drums here, melodies there, a
              folder per project). Keep these <strong>outside</strong> your music library so your
              media server doesn't index chops as albums. <strong>Docker users:</strong> mount the
              folder(s) into the SoulSync container, then add the <em>container-side</em> path here.
            </div>
            <FolderList
              loaded={loads > 0}
              rows={samples}
              setRows={(rows) => {
                setSamples(rows);
                saveList('sample_paths', rows);
              }}
              empty="No sample folders configured. Saved chops fall back to the default folder."
              rowClass="sample-path-row"
              inputClass="sample-path-input"
              placeholder="/samples or C:\Samples"
              defaultTag
            />
          </div>
        </div>
        <AudiobookFoldersPanel store={store} hidden={media !== 'audiobooks'} />
        <div
          ref={podcastsPanel}
          id="folders-podcasts-panel"
          role="tabpanel"
          aria-labelledby="folders-podcasts-tab"
          className="stg-media-panel"
          hidden={media !== 'podcasts'}
          tabIndex={0}
        >
          <div className="stg-media-context">
            <strong>Podcast folders</strong>
            <span className="stg-media-summary">Where episodes live</span>
          </div>
          <div className="settings-group" data-stg={TAB}>
            <div className="form-group">
              <label>Podcasts Folder (output):</label>
              <LockedPath
                id="podcasts-path"
                check={{ 'data-folder-check': 'podcasts', 'data-folder-label': 'Podcasts folder' }}
                placeholder={isDocker(settings) ? '/app/podcasts' : './podcasts'}
                value={podcasts}
                onChange={(v) => {
                  // one folder, saved under both keys, as the collector did
                  store.set('podcasts', 'download_path', v || './podcasts');
                  store.set('library', 'podcasts_path', v || './podcasts');
                }}
              />
              {hint('Where downloaded podcast episodes are saved and organized.')}
            </div>
          </div>
        </div>
      </LibrarySection>
    </>
  );
}

/** the Library tab, part by part */
export function LibraryTab(props: SettingsTabProps) {
  if (props.part === 'folders') return <FoldersCard {...props} />;
  if (props.part === 'organization') return <OrganizationCard {...props} />;
  if (props.part === 'processing') return <ProcessingCards {...props} />;
  return null;
}
