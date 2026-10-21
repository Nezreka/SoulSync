/**
 * Step: folders. The pipeline strip explains why there are two music
 * folders; every row is checked on the server as it's typed (exists,
 * writable, free space), and where the media server told us its own folder
 * for that library, it's shown next to ours.
 */

import { useEffect, useState } from 'react';

import type { FolderKey, PathCheck } from '../types';

import { checkPaths } from '../api';
import { FOLDER_INFO, folderRows, formatBytes, hasMusic, serverLabel } from '../plan';
import { useOnboarding } from '../store';
import { Chip, s, StepFrame } from '../ui';

export function FoldersStep() {
  const draft = useOnboarding((st) => st.draft);
  const env = useOnboarding((st) => st.env);
  const update = useOnboarding((st) => st.update);
  const rows = folderRows(draft.purposes);
  const [checks, setChecks] = useState<Record<string, PathCheck>>({});
  const key = rows.map((r) => `${r}=${draft.folders[r]}`).join('|');

  // fill empty rows from the environment's defaults once they're known
  useEffect(() => {
    if (!env) return;
    const missing = rows.filter((r) => !draft.folders[r] && env.folders[r]);
    if (missing.length)
      update((d) => {
        for (const r of missing) d.folders[r] = env.folders[r];
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [env, key]);

  useEffect(() => {
    let live = true;
    const t = window.setTimeout(async () => {
      const res = await checkPaths(Object.fromEntries(rows.map((r) => [r, draft.folders[r]])));
      if (live) setChecks(res);
    }, 350);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const server = draft.server;
  const named = server.kind && server.kind !== 'soulsync' ? serverLabel(server.kind) : null;
  const serverPaths: Partial<Record<FolderKey, string[]>> = {
    library: server.musicLibrary ? server.libraryPaths[server.musicLibrary] : undefined,
    movies: server.moviesLibrary ? server.libraryPaths[server.moviesLibrary] : undefined,
    tv: server.tvLibrary ? server.libraryPaths[server.tvLibrary] : undefined,
  };

  const status = (row: FolderKey) => {
    const c = checks[row];
    if (!c) return <Chip tone="busy">Checking</Chip>;
    if (c.state === 'ok')
      return (
        <Chip tone="ok">
          {c.free_bytes != null ? `${formatBytes(c.free_bytes)} free` : 'Ready'}
        </Chip>
      );
    if (c.state === 'create') return <Chip>Created on first use</Chip>;
    return <Chip tone="warn">Needs a look</Chip>;
  };

  return (
    <StepFrame
      step="folders"
      wide
      title="Where files live"
      lead={
        hasMusic(draft.purposes)
          ? 'Downloads land in one folder. SoulSync checks, tags and files them into your library, so your server never sees a half-finished album.'
          : 'Downloads land in one folder, then SoulSync checks and files them where your server looks.'
      }
    >
      {hasMusic(draft.purposes) ? (
        <div
          className={s.pipeline}
          role="img"
          aria-label="Downloads are checked and tagged, then filed into your music library, which your server reads"
        >
          <div className={s.pipeNode}>
            <span>1</span>Downloads
          </div>
          <div className={s.pipeLine} />
          <div className={`${s.pipeNode} ${s.accent}`}>
            <span>2</span>Checked and tagged
          </div>
          <div className={s.pipeLine} />
          <div className={s.pipeNode}>
            <span>3</span>Music library
          </div>
          <div className={s.pipeLine} />
          <div className={s.pipeNode}>
            <span>4</span>
            {named ?? 'SoulSync'}
          </div>
        </div>
      ) : null}

      {env ? (
        <div className={s.callout}>
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M21 8l-9-5-9 5 9 5 9-5zM3 8v8l9 5 9-5V8" />
          </svg>
          <span>
            {env.docker
              ? 'SoulSync is running in Docker, so these are paths inside the container. Your compose file decides which real folders they point to, so most people leave them alone.'
              : "SoulSync is running on its own, so these are real folders on this machine. Point them wherever you like, they're created if they don't exist."}
          </span>
        </div>
      ) : null}

      <div className={s.list}>
        {rows.map((row) => {
          const c = checks[row];
          const theirs = serverPaths[row];
          return (
            <div key={row} className={`${s.listRow} ${s.stack}`}>
              <div className={s.folderRow}>
                <div className={s.grow} style={{ gap: 2 }}>
                  <label htmlFor={`ob-folder-${row}`} style={{ fontSize: 14, fontWeight: 500 }}>
                    {FOLDER_INFO[row].name}
                  </label>
                  <span className={s.sub}>{FOLDER_INFO[row].hint}</span>
                </div>
                <input
                  id={`ob-folder-${row}`}
                  className={`${s.input} ${s.folderInput}`}
                  value={draft.folders[row]}
                  spellCheck={false}
                  placeholder={env?.docker ? '/media/…' : 'Pick a folder'}
                  onChange={(e) => update((d) => void (d.folders[row] = e.target.value))}
                />
                {status(row)}
              </div>
              {c && c.state === 'problem' ? (
                <p className={s.error} role="alert">
                  {c.message}
                </p>
              ) : null}
              {theirs?.length && named ? (
                <p className={s.note}>
                  {named} reads this library from {theirs.join(', ')}.
                  {theirs.includes(draft.folders[row].trim())
                    ? ' Same folder, good.'
                    : ' Make sure that is this same folder, seen from its side.'}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </StepFrame>
  );
}
