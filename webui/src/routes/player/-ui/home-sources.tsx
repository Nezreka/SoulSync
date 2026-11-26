/**
 * where home videos come from, set up from the player (player setup's home
 * videos step): the media server's home video libraries, each one "Add it"
 * (it is switched on to scan and read at once), and folders on disk, each
 * checked as it is typed. the same lists as Settings, Connections (Home
 * videos libraries) and Library, Folders (Home videos folders).
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { refusalText } from '@/app/api-client';

import {
  checkHomeFolder,
  homeSourcesQuery,
  putHomeSources,
  type HomeSources,
} from '../-lib/home-videos';
import { CamcorderIcon } from './icons';
import { styles as k } from './kit';
import s from './setup.module.css';

const SERVER_NAME = { plex: 'Plex', jellyfin: 'Jellyfin' } as const;

function scanWords(src: HomeSources): string {
  const scan = src.scan;
  if (scan.state !== 'running') return '';
  const found = scan.found ?? 0;
  return `${scan.phase ? scan.phase.charAt(0).toUpperCase() + scan.phase.slice(1) : 'Reading'}… ${
    found ? `${found.toLocaleString()} found` : ''
  }`.trim();
}

/** a folder's count; clips the server also gives are said, not hidden */
export function folderWords(f: { videos: number; shared?: number; shared_with?: string | null }) {
  const plural = (n: number) => `${n.toLocaleString()} ${n === 1 ? 'video' : 'videos'}`;
  const shared = f.shared ?? 0;
  if (!shared) return plural(f.videos);
  const where = f.shared_with ? `in ${f.shared_with}` : 'from your server';
  if (!f.videos) return `${plural(shared)}, all already ${where}, so counted there`;
  return `${plural(f.videos + shared)} · ${shared.toLocaleString()} already ${where}`;
}

export function HomeSourcesPanel({ onChanged }: { onChanged?: () => void }) {
  const queryClient = useQueryClient();
  const sources = useQuery({
    ...homeSourcesQuery(),
    // while a scan runs, ask again until it is done
    refetchInterval: (q) => (q.state.data?.scan.state === 'running' ? 1500 : false),
  });
  const src = sources.data;
  const running = src?.scan.state === 'running';
  const was = useRef(running);
  const told = useRef(onChanged);
  told.current = onChanged;
  useEffect(() => {
    if (was.current && !running) {
      told.current?.();
      void queryClient.invalidateQueries({ queryKey: ['player', 'home'] });
      void queryClient.invalidateQueries({ queryKey: ['player', 'libraries'] });
    }
    was.current = running;
  }, [running, queryClient]);

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const save = async (what: string, body: { home_more?: string[]; folders?: string[] }) => {
    setBusy(what);
    setError(null);
    try {
      const next = await putHomeSources(body);
      queryClient.setQueryData(homeSourcesQuery().queryKey, next);
      told.current?.();
    } catch (e) {
      setError(await refusalText(e, 'Couldn’t save that. Try again in a moment.'));
    } finally {
      setBusy(null);
    }
  };

  if (sources.isLoading)
    return (
      <div className={s.homeSources} aria-busy="true">
        <div className={s.homeCard} data-ghost="true" />
      </div>
    );
  if (!src) return <p className={s.muted}>Couldn’t read where home videos come from.</p>;

  const server = src.server ? SERVER_NAME[src.server] : null;
  const on = src.libraries.filter((l) => l.on).map((l) => l.title);

  return (
    <div className={s.homeSources}>
      {server ? (
        <section aria-label={`${server} home video libraries`} className={s.homeGroup}>
          <div className={s.stepGroup}>On {server}</div>
          {src.libraries.length ? (
            src.libraries.map((lib) => {
              const reading = running && src.scan.only === lib.root;
              return (
                <div key={lib.title} className={s.homeCard} data-on={lib.on ? 'true' : undefined}>
                  <span className={s.homeIcon} aria-hidden="true">
                    <CamcorderIcon />
                  </span>
                  <span className={s.homeText}>
                    <b>
                      {server} has {lib.title}
                    </b>
                    <span className={s.muted}>
                      {lib.on
                        ? reading
                          ? scanWords(src)
                          : `Added · ${(lib.videos ?? 0).toLocaleString()} ${lib.videos === 1 ? 'video' : 'videos'}`
                        : lib.count != null
                          ? `${lib.count.toLocaleString()} ${lib.count === 1 ? 'video' : 'videos'}`
                          : 'A home videos library'}
                    </span>
                    {reading ? (
                      <span className={s.homeBar} aria-hidden="true">
                        <i />
                      </span>
                    ) : null}
                  </span>
                  {lib.on ? (
                    <button
                      type="button"
                      className={s.ghost}
                      disabled={!!busy}
                      onClick={() =>
                        void save(lib.title, { home_more: on.filter((t) => t !== lib.title) })
                      }
                      data-nav
                    >
                      Remove
                    </button>
                  ) : (
                    <button
                      type="button"
                      className={k.btnPrimary}
                      disabled={!!busy}
                      onClick={() => void save(lib.title, { home_more: [...on, lib.title] })}
                      data-nav
                    >
                      {busy === lib.title ? 'Adding…' : 'Add it'}
                    </button>
                  )}
                </div>
              );
            })
          ) : (
            <p className={s.muted}>
              {src.error ??
                (src.server === 'plex'
                  ? 'Plex has no home video library. In Plex, a library of the “Other videos” type is one.'
                  : 'Jellyfin has no home video library. In Jellyfin, a “Home videos and photos” library is one.')}
            </p>
          )}
        </section>
      ) : null}

      <section aria-label="Folders on disk" className={s.homeGroup}>
        <div className={s.stepGroup}>Folders on disk</div>
        {src.folders.map((f) => {
          const reading = running && src.scan.only === f.root;
          return (
            <div key={f.root} className={s.homeCard} data-on="true">
              <span className={s.homeIcon} aria-hidden="true">
                <CamcorderIcon />
              </span>
              <span className={s.homeText}>
                <b title={f.path}>{f.path}</b>
                <span className={s.muted}>{reading ? scanWords(src) : folderWords(f)}</span>
                {reading ? (
                  <span className={s.homeBar} aria-hidden="true">
                    <i />
                  </span>
                ) : null}
              </span>
              <button
                type="button"
                className={s.ghost}
                disabled={!!busy}
                aria-label={`Remove ${f.path}`}
                onClick={() =>
                  void save(f.path, {
                    folders: src.folders.map((x) => x.path).filter((p) => p !== f.path),
                  })
                }
                data-nav
              >
                Remove
              </button>
            </div>
          );
        })}
        <AddFolder
          busy={busy === 'folder'}
          onAdd={(path) => save('folder', { folders: [...src.folders.map((x) => x.path), path] })}
        />
      </section>

      {running && !src.scan.only ? (
        <p className={s.muted} role="status">
          {scanWords(src)}
        </p>
      ) : null}
      {error ? (
        <p className={s.error} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function AddFolder({ busy, onAdd }: { busy: boolean; onAdd: (path: string) => Promise<void> }) {
  const [path, setPath] = useState('');
  const [check, setCheck] = useState<{ ok: boolean; message: string } | null>(null);
  useEffect(() => {
    const text = path.trim();
    if (!text) {
      setCheck(null);
      return;
    }
    let live = true;
    const t = window.setTimeout(() => {
      checkHomeFolder(text)
        .then((r) => live && setCheck(r))
        .catch(() => live && setCheck(null));
    }, 450);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [path]);
  const ready = !!path.trim() && check?.ok === true && !busy;
  return (
    <form
      className={s.homeAdd}
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready) return;
        void onAdd(path.trim()).then(() => {
          setPath('');
          setCheck(null);
        });
      }}
    >
      <label className={s.homeAddField}>
        <span className={s.muted}>Add a folder</span>
        <input
          value={path}
          placeholder="/media/family-videos"
          aria-label="A folder of home videos"
          spellCheck={false}
          onChange={(e) => setPath(e.target.value)}
          data-nav
        />
        {check ? (
          <span className={s.homeCheck} data-tone={check.ok ? 'ok' : 'problem'} role="status">
            {check.message}
          </span>
        ) : null}
      </label>
      <button type="submit" className={k.btnPrimary} disabled={!ready} data-nav>
        {busy ? 'Adding…' : 'Add'}
      </button>
    </form>
  );
}
