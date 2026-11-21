/**
 * Library > Folders, Music and Podcasts (settings-bic phase 4, mockup 03):
 * the download and library folders side by side, the other folders, the
 * extra libraries and the Sample Studio folders, each path with its folder
 * check in words under it. The Audiobooks folders are library-audiobooks.tsx,
 * the Video ones library-video-folders.tsx. Same ids and keys as the card it
 * replaces; a path saves through the store as it is typed, as before.
 *
 * The three M3U options went to Discovery > Playlists (DESIGN 3.3); they are
 * drawn by M3uExportOptions here, on that tab.
 *
 * folder-check.js checks every field marked data-folder-check; the field
 * draws the answer (PathField). A refresh once the values are in.
 */

import { useEffect, useState } from 'react';

import type { SettingsStore } from '../settings-store';

import { Btn, Card, NumberField, Row, SectionLabel, Segmented, Switch, TextField } from '../ui';
import s from '../ui/settings.module.css';
import { audiobooksPath } from './library-audiobooks';
import {
  FolderRows,
  LibPath,
  checkAttrs,
  isDocker,
  refreshChecks,
  shell,
  useLoadedDraft,
  useLoads,
  useSettings,
} from './library-card';
import { text } from './source-panels';

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

/** the out-of-the-box library folder on a bare-metal install fills the
 *  install disk (an 8 GB LXC root hangs) */
export function isInstallDefault(transfer: string) {
  const out = transfer.trim();
  return out === '' || out === '.' || out.startsWith('./') || out.startsWith('.\\');
}

function musicValues(settings: Record<string, Record<string, unknown> | undefined>) {
  const soulseek = settings.soulseek || {};
  return {
    download: text(soulseek.download_path) || './downloads',
    transfer: text(soulseek.transfer_path) || './Transfer',
  };
}

/** Rebuild playlist folders now, and what it said */
function useRebuild() {
  const [rebuild, setRebuild] = useState<{
    busy: boolean;
    text: string;
    tone: 'ok' | 'bad' | '';
    /** nothing to rebuild: say where the switch is (#2112) */
    none?: boolean;
  }>({ busy: false, text: '', tone: '' });
  async function rebuildNow() {
    setRebuild({ busy: true, text: 'Rebuilding playlist folders…', tone: '' });
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
      setRebuild(
        n === 0
          ? {
              // the switch lives on each playlist, not here, and people
              // looked for it in this panel (#2112)
              busy: false,
              tone: 'ok',
              none: true,
              text: 'No playlists have Organize by playlist on yet. Turn it on per playlist: ',
            }
          : {
              busy: false,
              tone: 'ok',
              text: `Rebuilt ${n} playlist folder${n === 1 ? '' : 's'}: ${linked} linked, ${copied} copied, ${removed} stale removed.`,
            },
      );
      shell().showToast?.('Playlist folders rebuilt', 'success');
    } catch (error) {
      setRebuild({
        busy: false,
        tone: 'bad',
        text: 'Rebuild failed: ' + (error instanceof Error ? error.message : String(error)),
      });
      shell().showToast?.('Playlist rebuild failed', 'error');
    }
  }
  return { rebuild, rebuildNow };
}

const MUSIC_CHECK = 'music';

export function MusicFolders({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const soulseek = settings.soulseek || {};
  const library = settings.library || {};
  const playlists = settings.playlists || {};
  const docker = !!settings._environment?.docker;
  const { download, transfer } = musicValues(settings);

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
    refreshChecks('music', 'audiobooks', 'podcasts');
  }, [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  const saveList = (key: string, rows: string[]) =>
    store.set('library', key, rows.map((r) => r.trim()).filter(Boolean));

  // the install default warns on the field it is about (bare metal only)
  const defaultOut = isInstallDefault(transfer);
  const installDefault = loads && !docker && defaultOut;

  const minFree = useLoadedDraft(store, text(soulseek.min_free_disk_gb ?? 5), (raw) => {
    const n = parseFloat(raw);
    store.set('soulseek', 'min_free_disk_gb', Number.isFinite(n) ? n : 0);
  });
  const { rebuild, rebuildNow } = useRebuild();
  const mode = text(playlists.materialize_mode) || 'symlink';

  return (
    <>
      <SectionLabel
        title="Where files go"
        hint="Keep the two side by side, not one inside the other"
        action={
          <span className={s.flow} aria-label="How files flow">
            <b>Download folder</b>
            <span className={s.flowSep}>›</span>
            tagging, artwork, organizing
            <span className={s.flowSep}>›</span>
            <b>Music library</b>
          </span>
        }
      />
      {/* one of these shows, picked by the detected deployment */}
      <div className={s.guides}>
        <p className={s.guide} data-paths-guide="docker" hidden={loads > 0 && !docker}>
          <strong>Docker / Unraid detected:</strong> leave these as the container-internal paths and
          map your real folders in the container config. Wrong values here break downloads.
        </p>
        <p className={s.guide} data-paths-guide="native" hidden={loads > 0 && docker}>
          <strong>Bare metal / Proxmox LXC / systemd:</strong> there is no container, so you&apos;re
          expected to unlock these and point them at your real folders.
        </p>
      </div>
      <Card>
        <Row
          wide
          label="Download folder"
          htmlFor="download-path"
          help="Where new downloads land before processing. Match this to your slskd download folder."
          more={
            <>
              How files flow: the <strong>download folder</strong> (raw downloads land here), then
              post-processing (tagging, artwork, organizing), then the{' '}
              <strong>music library folder</strong> (their final home: point your media server
              here). Keeping the two separate stops half-finished files being scanned.
            </>
          }
        >
          <LibPath
            store={store}
            id="download-path"
            check={checkAttrs(MUSIC_CHECK, 'Download folder', 'input')}
            placeholder="./downloads"
            value={download}
            onChange={(v) => store.set('soulseek', 'download_path', v)}
          />
        </Row>
        <Row
          wide
          label="Music library folder"
          htmlFor="transfer-path"
          help="Your main music library. Finished tracks are moved here, organized into Artist/Album folders."
        >
          <LibPath
            store={store}
            id="transfer-path"
            check={checkAttrs(MUSIC_CHECK, 'Music library folder')}
            placeholder="./Transfer"
            value={transfer}
            onChange={(v) => store.set('soulseek', 'transfer_path', v)}
            warn={
              installDefault
                ? 'Still the install folder default. Every finished download lands on the disk SoulSync is installed on, and on a Proxmox LXC that is often an 8 GB root that fills until the container hangs. Unlock the field and point it at your real music folder.'
                : null
            }
          />
        </Row>
        <Row
          field
          label="Minimum free disk space"
          htmlFor="min-free-disk-gb"
          help="New downloads are refused when the download disk drops below this. 0 turns it off."
          more="Stops a full disk from hanging the whole system."
        >
          <NumberField
            id="min-free-disk-gb"
            min="0"
            step="0.5"
            placeholder="5"
            unit="GB"
            value={minFree.value}
            onChange={(event) => minFree.onChange(event.target.value)}
          />
        </Row>
      </Card>

      <SectionLabel title="Other folders" />
      <Card>
        <Row
          wide
          label="Import folder"
          htmlFor="staging-path"
          help="Files you drop here show up on the Import page."
        >
          <LibPath
            store={store}
            id="staging-path"
            check={checkAttrs(MUSIC_CHECK, 'Import folder', 'input')}
            placeholder="./Staging"
            value={text(settings.import?.staging_path) || './Staging'}
            onChange={(v) => store.set('import', 'staging_path', v || './Staging')}
          />
        </Row>
        <Row
          wide
          label="Music videos folder"
          htmlFor="music-videos-path"
          help="Where music videos are saved."
        >
          <LibPath
            store={store}
            id="music-videos-path"
            check={checkAttrs(MUSIC_CHECK, 'Music videos folder')}
            placeholder="./MusicVideos"
            value={text(library.music_videos_path) || './MusicVideos'}
            onChange={(v) => store.set('library', 'music_videos_path', v || './MusicVideos')}
          />
        </Row>
        <Row
          wide
          label="Playlists folder"
          htmlFor="playlists-materialize-path"
          help="Where Organize by playlist builds playlist folders, as shortcuts into your library."
          more={
            <>
              Songs are never stored twice. Keep this <strong>outside</strong> your music library
              folder so your media server doesn&apos;t scan the same tracks twice. Organize by
              playlist is turned on per playlist: Playlists, a playlist&apos;s ⋯ menu, Settings.
            </>
          }
        >
          <LibPath
            store={store}
            id="playlists-materialize-path"
            check={checkAttrs(MUSIC_CHECK, 'Playlists folder')}
            placeholder="./Playlists"
            value={text(playlists.materialize_path) || './Playlists'}
            onChange={(v) => store.set('playlists', 'materialize_path', v || './Playlists')}
          />
        </Row>
        <Row
          sub
          label="Playlist folder style"
          htmlFor="playlists-materialize-mode"
          help="Symlinks use almost no space. Copies are for USB sticks and players that can't follow links."
          more="Symlinks fall back to copies on their own where the filesystem can't link (Windows without privileges, some network shares, FAT and exFAT drives)."
        >
          <Segmented
            id="playlists-materialize-mode"
            label="Playlist folder style"
            value={mode}
            options={[
              { value: 'symlink', label: 'Symlinks' },
              { value: 'copy', label: 'Copies' },
            ]}
            onChange={(value) => store.set('playlists', 'materialize_mode', value || 'symlink')}
          />
          <Btn
            small
            id="playlists-rebuild-btn"
            busy={rebuild.busy}
            onClick={() => void rebuildNow()}
          >
            {rebuild.busy ? 'Rebuilding…' : 'Rebuild playlist folders now'}
          </Btn>
        </Row>
        <div
          className={s.rowSay}
          id="playlists-rebuild-status"
          data-tone={rebuild.tone || undefined}
          role="status"
          hidden={!rebuild.text}
        >
          {rebuild.text}
          {rebuild.none ? (
            <>
              <a
                href="/sync"
                onClick={(event) => {
                  const go = shell().navigateToPage;
                  if (typeof go !== 'function') return;
                  event.preventDefault();
                  void go('sync');
                }}
              >
                Playlists
              </a>
              , open a playlist&apos;s ⋯ menu, Settings.
            </>
          ) : null}
        </div>
      </Card>

      <SectionLabel
        title="Additional music libraries"
        hint="Extra folders SoulSync should read. Your music library folder is already covered."
      />
      <Card>
        <div className={s.cardPad}>
          <FolderRows
            id="music-paths-list"
            shown={loads > 0}
            rows={music}
            onRows={(rows) => {
              setMusic(rows);
              saveList('music_paths', rows);
            }}
            empty="No paths configured. Add path adds your music folders."
            addLabel="Add path"
            placeholder="/music or C:\Music"
            itemLabel="Extra music folder"
            check={checkAttrs(MUSIC_CHECK, 'Extra music folder', 'existing')}
          />
        </div>
        <p className={s.cardFoot}>
          <strong>You don&apos;t need to repeat it here.</strong> Add a folder only for an{' '}
          <em>extra</em> library (a second collection, an archive drive), or when your media server
          sees the same files at a different path than SoulSync can. Used for tag writing, streaming
          and file detection. <strong>Docker:</strong> mount the extra folder into the SoulSync
          container read-write, then add the <em>container-side</em> path here (like{' '}
          <code>/music2</code>).
        </p>
      </Card>

      <SectionLabel
        title="Sample Studio folders"
        hint="Where Sample Studio saves your chops. The first folder is the default."
      />
      <Card>
        <div className={s.cardPad}>
          <FolderRows
            id="sample-paths-list"
            shown={loads > 0}
            rows={samples}
            onRows={(rows) => {
              setSamples(rows);
              saveList('sample_paths', rows);
            }}
            empty="No sample folders configured. Saved chops fall back to the default folder."
            addLabel="Add path"
            placeholder="/samples or C:\Samples"
            itemLabel="Sample folder"
            tagFor={(index) => (index === 0 ? 'default' : null)}
          />
        </div>
        <p className={s.cardFoot}>
          Chops are organized into Artist/Track folders inside the folder; the save dialog lets you
          pick another one per chop. Keep these <strong>outside</strong> your music library so your
          media server doesn&apos;t index chops as albums. <strong>Docker:</strong> mount the folder
          into the container, then add the <em>container-side</em> path here.
        </p>
      </Card>
    </>
  );
}

export function PodcastFolders({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  return (
    <Card>
      <Row
        wide
        label="Podcasts folder"
        htmlFor="podcasts-path"
        help="Where downloaded podcast episodes are saved and organized."
      >
        <LibPath
          store={store}
          id="podcasts-path"
          check={checkAttrs('podcasts', 'Podcasts folder')}
          placeholder={isDocker(settings) ? '/app/podcasts' : './podcasts'}
          value={podcastsPath(settings)}
          onChange={(v) => {
            // one folder, saved under both keys, as the collector did
            store.set('podcasts', 'download_path', v || './podcasts');
            store.set('library', 'podcasts_path', v || './podcasts');
          }}
        />
      </Row>
    </Card>
  );
}

/**
 * The three M3U path options. They were in Library > Folders; DESIGN 3.3 puts
 * every M3U setting on Discovery > Playlists, so that tab draws these.
 */
export function M3uExportOptions({ store }: { store: SettingsStore }) {
  const settings = useSettings(store);
  const m3u = settings.m3u_export || {};
  return (
    <div data-ui="" data-m3u-paths="" className={s.stack}>
      <Card title="M3U paths" hint="How track paths are written in saved M3U files">
        <Row
          label="Write M3U entries relative to the playlist file"
          htmlFor="m3u-relative-paths"
          help="Entries become ../Artist/Album/01 - Song.flac instead of the full path SoulSync sees."
          more={
            <>
              So a saved M3U still works when the folder is opened from a network share, another
              machine or a sync tool. Tracks that don&apos;t share a folder with the playlist keep
              their full path, and the base path and prefix rewrite below still apply to those.
            </>
          }
        >
          <Switch
            id="m3u-relative-paths"
            checked={m3u.relative_paths === true}
            onChange={(event) => store.set('m3u_export', 'relative_paths', event.target.checked)}
          />
        </Row>
        <Row
          wide
          label="M3U entry base path"
          htmlFor="m3u-entry-base-path"
          help="An optional prefix added to every track path in exported M3U files."
          more={
            <>
              Use this when your media server needs absolute paths (like <code>/mnt/music</code>).
            </>
          }
        >
          <LibPath
            store={store}
            id="m3u-entry-base-path"
            placeholder="like /mnt/music (empty uses paths as stored)"
            value={text(m3u.entry_base_path)}
            onChange={(v) => store.set('m3u_export', 'entry_base_path', v)}
          />
        </Row>
        <Row
          wide
          label="M3U path prefix rewrite"
          htmlFor="m3u-rewrite-from"
          help="Swaps a path prefix on every entry, so a playlist written inside a container plays on another machine."
          more={
            <>
              A playlist written inside a container (<code>/data/media/&hellip;</code>) then plays
              directly on the machine that mounts the same storage elsewhere (
              <code>M:/media/&hellip;</code>). Entries that don&apos;t start with the prefix pass
              through unchanged. Leave both empty to turn it off.
            </>
          }
        >
          <div className={s.pair}>
            <TextField
              full
              mono
              id="m3u-rewrite-from"
              aria-label="Replace this prefix"
              placeholder="replace this prefix, like /data/media"
              value={text(m3u.rewrite_from)}
              onChange={(event) => store.set('m3u_export', 'rewrite_from', event.target.value)}
            />
            <TextField
              full
              mono
              id="m3u-rewrite-to"
              aria-label="With this"
              placeholder="with this, like M:/media"
              value={text(m3u.rewrite_to)}
              onChange={(event) => store.set('m3u_export', 'rewrite_to', event.target.value)}
            />
          </div>
        </Row>
      </Card>
    </div>
  );
}

export { audiobooksPath };
