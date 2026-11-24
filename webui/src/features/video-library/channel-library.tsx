/**
 * a followed YouTube channel as a library you manage, on its page.
 *
 * the show page's library manager works on media_files rows; a channel's
 * downloads never become those, so the channel page showed which videos
 * were downloaded and nothing more. this is the same idea built from the
 * download ledger (api/video/youtube.py /youtube/library/<channel>):
 *
 * - the hero line: how many videos are on disk, how big, in what quality,
 *   what retention keeps
 * - what needs attention: files gone from disk, wished videos that keep
 *   failing or were removed from youtube, what retention removes next
 * - your files: each with its date, quality, size and folder, to delete
 *   one, some or all (through the recycle bin, with undo), or delete and
 *   let it download again
 * - where it lives: the channel's folder
 *
 * admin only (file paths and deletes): for anyone else the route says no
 * and this draws nothing.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';

import type { PageSlots } from './library-tab';

import { formatBytes, resolutionTone } from './format';
import {
  Attention,
  Chip,
  confirm,
  copyText,
  type Issue,
  refreshPage,
  ResBadge,
  toast,
} from './library-tab';
import s from './video-library.module.css';

export interface ChannelFile {
  history_id: number;
  video_id: string;
  title: string;
  published: string;
  downloaded_at: string;
  path: string;
  folder: string | null;
  size: number | null;
  exists: boolean;
  quality: string | null;
}

interface AttentionItem {
  kind: 'missing' | 'failing' | 'unavailable' | 'retention' | 'paused';
  count: number;
  video_ids: string[];
  words: string;
}

export interface ChannelLibraryData {
  files: ChannelFile[];
  count: number;
  size: number;
  folder: string | null;
  quality: { label: string; count: number }[];
  waiting: number;
  retention: { policy: string; words: string; due: number };
  attention: AttentionItem[];
}

interface UndoItem {
  history_id: number;
  trash_path: string;
}

interface Removed extends ChannelLibraryData {
  removed: number;
  freed: number;
  failed: string[];
  undo: UndoItem[];
}

async function send<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      Accept: 'application/json',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = (await r.json().catch(() => null)) as (T & { error?: string }) | null;
  if (!r.ok || !data || data.error) throw new Error(data?.error || `Request failed (${r.status})`);
  return data;
}

const base = (id: string) => `/api/video/youtube/library/${encodeURIComponent(id)}`;
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
// the newest few, then the rest on request: a channel with hundreds of
// files would push the page's own video list off the screen
const SHOWN = 6;

/** the video's youtube thumbnail, through the app's image proxy (hotlinks blank) */
function thumb(videoId: string): string {
  return `/api/video/img?u=${encodeURIComponent(`https://i.ytimg.com/vi/${videoId}/mqdefault.jpg`)}`;
}

/** the file's own name, without the folder every row shares */
function fileName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

declare global {
  interface Window {
    VideoYoutube?: {
      openChannelSettings?: (id: string, title: string, kind: 'channel' | 'playlist') => void;
    };
  }
}

function openSettings(id: string, title: string) {
  window.VideoYoutube?.openChannelSettings?.(id, title, 'channel');
}

export function ChannelLibrary({
  channelId,
  title,
  slots,
}: {
  channelId: string;
  title: string;
  slots?: PageSlots | null;
}) {
  const [data, setData] = useState<ChannelLibraryData | null>(null);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [find, setFind] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [undo, setUndo] = useState<{ items: UndoItem[]; words: string } | null>(null);

  const load = useCallback(() => {
    send<ChannelLibraryData>(base(channelId))
      .then((d) => {
        setData(d);
        setDenied(false);
      })
      .catch(() => setDenied(true));
  }, [channelId]);
  useEffect(load, [load]);

  // an undo offer lasts a little while, then the delete is just done
  useEffect(() => {
    if (!undo) return;
    const t = window.setTimeout(() => setUndo(null), 20_000);
    return () => window.clearTimeout(t);
  }, [undo]);

  const remove = useCallback(
    async (videoIds: string[] | 'all', forget = false) => {
      const n = videoIds === 'all' ? (data?.count ?? 0) : videoIds.length;
      if (!n) return;
      const ok = await confirm({
        title: forget
          ? `Delete ${plural(n, 'video')} and get ${n === 1 ? 'it' : 'them'} again?`
          : `Delete ${plural(n, 'video')}?`,
        message: forget
          ? 'The files go to the recycle bin, and the next download run fetches them fresh.'
          : "The files go to the recycle bin, so you can put them back. SoulSync remembers these videos and won't download them again.",
        confirmText: forget ? 'Delete and get again' : 'Delete',
        destructive: true,
      });
      if (!ok) return;
      setBusy(true);
      try {
        const out = await send<Removed>(`${base(channelId)}/delete`, {
          ...(videoIds === 'all' ? { all: true } : { video_ids: videoIds }),
          forget,
        });
        setData(out);
        setPicked(new Set());
        const words = `Deleted ${plural(out.removed, 'video')}${out.freed ? ` · freed ${formatBytes(out.freed)}` : ''}`;
        if (out.undo.length) setUndo({ items: out.undo, words });
        else toast(words);
        if (out.failed.length)
          toast(`${plural(out.failed.length, 'file')} couldn't be removed`, 'error');
        refreshPage();
      } catch (e) {
        toast(e instanceof Error ? e.message : "Couldn't delete those", 'error');
      } finally {
        setBusy(false);
      }
    },
    [channelId, data?.count],
  );

  const clean = useCallback(async () => {
    setBusy(true);
    try {
      const out = await send<Removed>(`${base(channelId)}/clean`, {});
      setData(out);
      const words = out.removed
        ? `Removed ${plural(out.removed, 'old video')}${out.freed ? ` · freed ${formatBytes(out.freed)}` : ''}`
        : 'Nothing to remove: every video is inside the retention window';
      if (out.undo.length) setUndo({ items: out.undo, words });
      else toast(words);
      refreshPage();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't clean up", 'error');
    } finally {
      setBusy(false);
    }
  }, [channelId]);

  const putBack = useCallback(async () => {
    if (!undo) return;
    setBusy(true);
    try {
      const out = await send<ChannelLibraryData & { restored: number }>(`${base(channelId)}/undo`, {
        undo: undo.items,
      });
      setData(out);
      setUndo(null);
      toast(`Put back ${plural(out.restored, 'video')}`);
      refreshPage();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't put them back", 'error');
    } finally {
      setBusy(false);
    }
  }, [channelId, undo]);

  const files = useMemo(() => {
    const all = data?.files ?? [];
    const q = find.trim().toLowerCase();
    return q ? all.filter((f) => f.title.toLowerCase().includes(q)) : all;
  }, [data, find]);

  if (denied || !data) return null;
  if (!data.files.length && !data.waiting && !data.attention.length) {
    // nothing downloaded yet and nothing waiting: the page's video list says enough
    return null;
  }

  const issues: Issue[] = data.attention.map((a) => {
    const n = a.count;
    if (a.kind === 'missing')
      return {
        key: 'missing',
        tone: 'warn',
        title: `${plural(n, 'video')} ${a.words}`,
        detail: 'The file was moved or deleted outside SoulSync.',
        action: {
          label: 'Get them again',
          busy,
          onClick: () => void remove(a.video_ids, true),
        },
      };
    if (a.kind === 'failing')
      return {
        key: 'failing',
        tone: 'warn',
        title: `${plural(n, 'wished video')} ${a.words}`,
        detail: 'SoulSync keeps trying, waiting longer each time.',
      };
    if (a.kind === 'unavailable')
      return {
        key: 'unavailable',
        tone: 'info',
        title: `${plural(n, 'wished video')} ${a.words}`,
        detail: "They can't be downloaded any more.",
      };
    if (a.kind === 'retention')
      return {
        key: 'retention',
        tone: 'info',
        title: `${plural(n, 'video')} ${a.words}`,
        detail: `${data.retention.words}. The nightly clean removes them, or do it now.`,
        action: { label: 'Clean now', busy, onClick: () => void clean() },
      };
    return {
      key: 'paused',
      tone: 'info',
      title: a.words,
      action: { label: 'Settings', onClick: () => openSettings(channelId, title) },
    };
  });

  const line = (
    <div className={s.libLine} data-card="have">
      <span className={s.libCount}>
        <b>{data.count}</b> {data.count === 1 ? 'video' : 'videos'} on disk
      </span>
      <span className={s.libFact}>{data.size ? formatBytes(data.size) : 'no files'}</span>
      {data.quality.length ? (
        <span className={s.libMix}>
          <span
            className={s.mixBar}
            role="img"
            aria-label={data.quality.map((m) => `${m.count} ${m.label}`).join(', ')}
          >
            {data.quality.map((m) => (
              <span
                key={m.label}
                className={s.mixSeg}
                data-tone={resolutionTone(m.label)}
                style={{ flexGrow: m.count }}
              />
            ))}
          </span>
          {data.quality.map((m) => `${m.label} ${m.count}`).join(' · ')}
        </span>
      ) : null}
      {data.waiting ? <span className={s.libDim}>{data.waiting} waiting to download</span> : null}
      <span className={s.libDim}>{data.retention.words}</span>
      <span className={s.libLinks}>
        <button type="button" className={s.linkBtn} onClick={() => openSettings(channelId, title)}>
          Quality, retention and download settings
        </button>
      </span>
    </div>
  );

  const shown = showAll || find.trim() ? files : files.slice(0, SHOWN);
  // youtube grabs rarely record a quality: no column of dashes
  const hasQuality = data.files.some((f) => f.quality);
  const pickedIds = [...picked];
  const allPicked = shown.length > 0 && shown.every((f) => picked.has(f.video_id));

  const list = data.files.length ? (
    <section aria-label="Your files">
      <div className={s.sectionHead}>
        <h3 className={s.sectionTitle}>
          Your files <span className={s.count}>{data.files.length}</span>
        </h3>
        <div className={s.chanTools}>
          {data.files.length > 8 ? (
            <input
              type="search"
              className={s.chanFind}
              placeholder="Find a video"
              aria-label="Find a video in your files"
              value={find}
              onChange={(e) => setFind(e.target.value)}
            />
          ) : null}
          {pickedIds.length ? (
            <>
              <button
                type="button"
                className={s.btn}
                disabled={busy}
                onClick={() => void remove(pickedIds)}
              >
                Delete {plural(pickedIds.length, 'video')}
              </button>
              <button
                type="button"
                className={s.quietBtn}
                disabled={busy}
                onClick={() => void remove(pickedIds, true)}
              >
                Delete and get again
              </button>
              <button type="button" className={s.quietBtn} onClick={() => setPicked(new Set())}>
                Clear
              </button>
            </>
          ) : (
            <button
              type="button"
              className={s.quietBtn}
              disabled={busy || !data.count}
              onClick={() => void remove('all')}
            >
              Delete all
            </button>
          )}
        </div>
      </div>
      <ul className={s.chanFiles} data-quality={hasQuality ? 'true' : undefined}>
        <li className={s.chanFileHead}>
          <input
            type="checkbox"
            aria-label="Pick every video shown"
            checked={allPicked}
            onChange={() =>
              setPicked((prev) => {
                const next = new Set(prev);
                for (const f of shown) {
                  if (allPicked) next.delete(f.video_id);
                  else next.add(f.video_id);
                }
                return next;
              })
            }
          />
          <span aria-hidden="true" />
          <span>Video</span>
          {hasQuality ? <span>Quality</span> : null}
          <span>Size</span>
          <span aria-hidden="true" />
        </li>
        {shown.map((f) => (
          <li key={f.history_id} className={s.chanFile} data-gone={f.exists ? undefined : 'true'}>
            <input
              type="checkbox"
              aria-label={`Pick ${f.title}`}
              checked={picked.has(f.video_id)}
              onChange={() =>
                setPicked((prev) => {
                  const next = new Set(prev);
                  if (next.has(f.video_id)) next.delete(f.video_id);
                  else next.add(f.video_id);
                  return next;
                })
              }
            />
            <img
              className={s.chanThumb}
              src={thumb(f.video_id)}
              alt=""
              loading="lazy"
              onError={(e) => {
                e.currentTarget.style.visibility = 'hidden';
              }}
            />
            <div className={s.chanFileMain}>
              <div className={s.chanFileTitle} title={f.title}>
                {f.title}
              </div>
              <div className={s.chanFileSub}>
                {f.published ? <span>{f.published}</span> : null}
                {f.exists ? (
                  <span className={s.path} title={f.path}>
                    {fileName(f.path)}
                  </span>
                ) : (
                  <Chip tone="warn">Gone from disk</Chip>
                )}
              </div>
            </div>
            {hasQuality ? (
              <span>
                {f.quality ? <ResBadge label={f.quality} /> : <span className={s.libDim}>—</span>}
              </span>
            ) : null}
            <span className={s.chanFileSize}>{f.size != null ? formatBytes(f.size) : '—'}</span>
            <span className={s.chanFileActions}>
              {f.exists ? (
                <button
                  type="button"
                  className={s.quietBtn}
                  onClick={() => void copyText(f.path)}
                  aria-label={`Copy the path of ${f.title}`}
                >
                  Copy path
                </button>
              ) : null}
              <button
                type="button"
                className={s.quietBtn}
                disabled={busy}
                onClick={() => void remove([f.video_id])}
                aria-label={`Delete ${f.title}`}
              >
                Delete
              </button>
            </span>
          </li>
        ))}
      </ul>
      {files.length > SHOWN && !find.trim() ? (
        <button type="button" className={s.chanMore} onClick={() => setShowAll((v) => !v)}>
          {showAll ? 'Show the newest only' : `Show all ${files.length} files`}
        </button>
      ) : null}
    </section>
  ) : null;

  const folder = data.folder ? (
    <section>
      <div className={s.sectionHead}>
        <h3 className={s.sectionTitle}>Where it lives</h3>
      </div>
      <ul className={s.folders}>
        <li className={s.folder}>
          <span className={s.folderIcon} aria-hidden="true" />
          <div className={s.folderBody}>
            <div className={s.folderName}>{title}</div>
            <div className={s.path} title={data.folder}>
              {data.folder}
            </div>
          </div>
          <span className={s.folderCount}>
            {plural(data.count, 'file')}
            {data.size ? ` · ${formatBytes(data.size)}` : ''}
          </span>
          <button
            type="button"
            className={s.quietBtn}
            onClick={() => void copyText(data.folder ?? '')}
          >
            Copy path
          </button>
        </li>
      </ul>
    </section>
  ) : null;

  return (
    <div className={s.tab} data-layout="page" data-channel-library="">
      {slots?.hero ? createPortal(line, slots.hero) : line}
      <Attention issues={issues} />
      {list}
      {slots?.foot ? createPortal(folder, slots.foot) : folder}
      {undo ? (
        <div className={s.chanUndo} role="status">
          <span>{undo.words}</span>
          <button type="button" className={s.btn} disabled={busy} onClick={() => void putBack()}>
            Undo
          </button>
        </div>
      ) : null}
    </div>
  );
}
