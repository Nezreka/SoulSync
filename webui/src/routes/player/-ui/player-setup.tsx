/**
 * player setup: what an admin decides once for the whole server
 * (core/player/setup.py, core/player/libraries.py).
 *
 * it opens by itself the first time an admin lands on the player, and again
 * from Settings whenever they want. everything in it has a default that is
 * the player as it always was, so "Use the defaults" on the first step is a
 * whole setup.
 *
 *   name       what the server is called in the player and the app
 *   movies     every movie folder, grouped into libraries you can name
 *   tv         the same for shows
 *   home       home videos: the server's home video libraries and folders on
 *              disk, added right here, then grouped like the others
 *   people     which libraries each person can see
 *   sidebar    the order everyone starts from, and places nobody here gets
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';

import { apiClient, refusalText } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

import { posterSet } from '../-lib/images';
import { moveNav } from '../-lib/nav-order';
import { HomeSourcesPanel } from './home-sources';
import { styles as k } from './kit';
import { NAV, NAV_GROUPS } from './sections';
import s from './setup.module.css';
import { inPlayer } from './trailer';

type Kind = 'movie' | 'show' | 'home';

interface SetupFolder {
  folder: string;
  path: string;
  titles: number;
  sample: number[];
  library_id: number | null;
  /** home: the folder's or server library's name, and where it is */
  label?: string;
  source?: 'disk' | 'plex' | 'jellyfin';
  /** home: the folder its clips live in, so it shows on its drive */
  where?: string | null;
}
interface SetupLibrary {
  id: number;
  kind: Kind;
  name: string;
  is_default: boolean;
  in_guide?: boolean;
  folders: string[];
}
interface SetupPayload {
  done: boolean;
  server_name: string;
  is_admin: boolean;
  sidebar_order?: string[];
  sidebar_hidden?: string[];
  libraries?: SetupLibrary[];
  folders?: Partial<Record<Kind, SetupFolder[]>>;
  hidden?: Record<string, number[]>;
  home_editors?: 'admins' | 'everyone';
  profiles?: { id: number; name: string; is_admin: boolean }[];
}

/** a library in the draft: a saved one has an id, a new one only its key */
interface DraftLibrary {
  key: string;
  id: number | null;
  kind: Kind;
  name: string;
  isDefault: boolean;
  inGuide: boolean;
}

export const playerSetupQuery = () => ({
  queryKey: ['player', 'setup'] as const,
  queryFn: async () => await apiClient.get('player/setup').json<SetupPayload>(),
  staleTime: 60_000,
});

// ── opened from anywhere (Settings, the users page) ───────────────────────

let wanted = false;
/** the step it opens on: 0 the welcome, 1 the movie libraries */
let wantedStep = 0;
const listeners = new Set<() => void>();
const tell = () => listeners.forEach((fn) => fn());
export function openPlayerSetup(step = 0) {
  wanted = true;
  wantedStep = typeof step === 'number' ? step : 0;
  tell();
}
/** straight to the libraries, to add, remove, rename or regroup them later */
export function openLibrarySetup() {
  openPlayerSetup(1);
}
function closePlayerSetup() {
  wanted = false;
  tell();
}
const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => void listeners.delete(fn);
};

const STEPS = [
  { label: 'Welcome', group: '' },
  { label: 'Movies', group: 'Libraries' },
  { label: 'TV', group: 'Libraries' },
  { label: 'Home videos', group: 'Libraries' },
  { label: 'Who sees what', group: '' },
  { label: 'Sidebar', group: '' },
  { label: 'Done', group: '' },
] as const;

/** mounts in the player shell: shows setup when it is due or asked for */
export function PlayerSetupHost() {
  const setup = useQuery(playerSetupQuery());
  const asked = useSyncExternalStore(subscribe, () => wanted);
  const data = setup.data;
  if (!data?.is_admin) return null;
  if (!asked && data.done) return null;
  if (!data.libraries || !data.folders) return null;
  return <PlayerSetup data={data} firstTime={!data.done} />;
}

/**
 * a folder's path as where it lives (the share's server, or the drive) and
 * the folders down to it, so a long path breaks between folder names, never
 * inside one. "\\192.168.86.36\easystore1\PLEX\MOVIES" is
 * { root: "192.168.86.36", parts: ["easystore1", "PLEX", "MOVIES"] }
 */
export function splitFolderPath(path: string): { root: string; parts: string[] } {
  const raw = String(path || '').trim();
  const bits = raw.split(/[\\/]+/).filter(Boolean);
  if (!bits.length) return { root: '', parts: [] };
  // a network share (\\server\share or //server/share): the server is the root
  if (/^(\\\\|\/\/)/.test(raw)) return { root: bits[0], parts: bits.slice(1) };
  // a drive letter (H:\Music): the drive
  if (/^[A-Za-z]:$/.test(bits[0])) return { root: bits[0], parts: bits.slice(1) };
  // a unix path (/mnt/media/movies): no root of its own
  return { root: '', parts: bits };
}

/** each library's colour, in the order the kind's libraries are listed */
const LIB_COLORS = [
  'hsl(158 66% 51%)',
  'hsl(228 100% 74%)',
  'hsl(37 100% 64%)',
  'hsl(334 100% 72%)',
  'hsl(195 100% 69%)',
  'hsl(276 100% 77%)',
];

/** a library named after a folder: "4K MOVIES" is "4K", "Kids Movies" is "Kids" */
export function libraryNameFor(path: string, kind: Kind): string {
  const last = splitFolderPath(path).parts.at(-1) ?? '';
  const noise =
    kind === 'movie'
      ? /\b(movies?|films?)\b/gi
      : kind === 'show'
        ? /\b(tv|shows?|series)\b/gi
        : /\b(home|videos?|clips?)\b/gi;
  const left = last.replace(noise, ' ').replace(/\s+/g, ' ').trim();
  const base = left || last || 'New library';
  return base === base.toUpperCase() && base.length > 3
    ? base.charAt(0) + base.slice(1).toLowerCase()
    : base.charAt(0).toUpperCase() + base.slice(1);
}

const IDEAS: { re: RegExp; name: string; kinds: Kind[] }[] = [
  { re: /\b4k\b|\buhd\b|2160/i, name: '4K', kinds: ['movie', 'show'] },
  { re: /\bkids?\b|family|children/i, name: 'Kids', kinds: ['movie', 'show'] },
  { re: /anime/i, name: 'Anime', kinds: ['movie', 'show'] },
  { re: /documentar/i, name: 'Documentaries', kinds: ['movie', 'show'] },
];

/** libraries worth offering, read off folder names: a folder called 4K
 *  MOVIES could be its own 4K library. none that already exist by name */
/** a movie or tv folder that is really home videos ("cam" is a theater
 * recording, not a home video) */
export const HOME_LIKE = /\bhome ?(videos?|movies)\b|camcorder|family videos?/i;

export function librarySuggestions(
  kind: Kind,
  folders: { folder: string; path: string }[],
  existing: string[],
): { name: string; folders: string[]; labels: string[] }[] {
  const have = new Set(existing.map((n) => n.trim().toLowerCase()));
  const out: { name: string; folders: string[]; labels: string[] }[] = [];
  for (const idea of IDEAS) {
    if (!idea.kinds.includes(kind) || have.has(idea.name.toLowerCase())) continue;
    const hits = folders.filter((f) => idea.re.test(splitFolderPath(f.path).parts.at(-1) ?? ''));
    if (hits.length)
      out.push({
        name: idea.name,
        folders: hits.map((f) => f.folder),
        labels: hits.map((f) => splitFolderPath(f.path).parts.at(-1) ?? f.path),
      });
  }
  return out;
}

/** the SoulSync mark with the boot screen's light passing over it */
function Mark({ size = 'sm' }: { size?: 'sm' | 'md' | 'hero' }) {
  const src = appURL('/static/soulsync-mark.png');
  return (
    <span className={s.logo} data-size={size} aria-hidden="true">
      {size === 'hero' ? <span className={s.logoAura} /> : null}
      <img src={src} alt="" />
      <span
        className={s.logoSheen}
        style={{ WebkitMaskImage: `url("${src}")`, maskImage: `url("${src}")` }}
      />
    </span>
  );
}

/** each folder's library, by draft key */
function placesOf(data: SetupPayload): Record<string, string> {
  const out: Record<string, string> = {};
  for (const kind of ['movie', 'show', 'home'] as const)
    for (const f of data.folders?.[kind] ?? [])
      if (f.folder && f.library_id != null) out[`${kind}|${f.folder}`] = `id:${f.library_id}`;
  return out;
}

function PlayerSetup({ data, firstTime }: { data: SetupPayload; firstTime: boolean }) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState(() => (firstTime ? 0 : wantedStep));
  const [name, setName] = useState(data.server_name);
  const [libs, setLibs] = useState<DraftLibrary[]>(() =>
    (data.libraries ?? []).map((l) => ({
      key: `id:${l.id}`,
      id: l.id,
      kind: l.kind,
      name: l.name,
      isDefault: l.is_default,
      inGuide: l.in_guide ?? l.kind !== 'home',
    })),
  );
  // folder -> the library (by key) it is in
  const [where, setWhere] = useState<Record<string, string>>(() => placesOf(data));
  // a home library or folder that turned up while setup is open (a scan
  // started from the home videos step) joins the draft; nothing typed is lost
  useEffect(() => {
    setLibs((all) => {
      const have = new Set(all.map((l) => l.id));
      const fresh = (data.libraries ?? []).filter((l) => !have.has(l.id));
      if (!fresh.length) return all;
      return [
        ...all,
        ...fresh.map((l) => ({
          key: `id:${l.id}`,
          id: l.id,
          kind: l.kind,
          name: l.name,
          isDefault: l.is_default,
          inGuide: l.in_guide ?? l.kind !== 'home',
        })),
      ];
    });
    setWhere((w) => ({ ...placesOf(data), ...w }));
  }, [data]);
  // library key -> the profiles it is hidden from
  const [hidden, setHidden] = useState<Record<string, number[]>>(() => {
    const out: Record<string, number[]> = {};
    for (const [id, pids] of Object.entries(data.hidden ?? {})) out[`id:${id}`] = pids;
    return out;
  });
  const [order, setOrder] = useState<string[]>(data.sidebar_order ?? []);
  const [gone, setGone] = useState<string[]>(data.sidebar_hidden ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (firstTime) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closePlayerSetup();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [firstTime]);

  const [homeEditors, setHomeEditors] = useState(data.home_editors ?? 'admins');

  const save = async (defaultsOnly = false) => {
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { done: true };
      if (!defaultsOnly) {
        body.server_name = name;
        // a library not saved yet is named by its place in the list; one
        // removed again after being arranged is dropped
        const sent = (ids: string[]) =>
          ids.flatMap((id) => {
            if (!id.startsWith('draft:')) return [id];
            const at = libs.findIndex((l) => l.key === id.slice(6) && l.id === null);
            return at >= 0 ? [`lib:new:${at}`] : [];
          });
        body.sidebar_order = sent(order);
        body.sidebar_hidden = sent(gone);
        body.home_editors = homeEditors;
        body.libraries = libs.map((l) => ({
          ...(l.id !== null ? { id: l.id } : {}),
          kind: l.kind,
          name: l.name,
          in_guide: l.inGuide,
          folders: l.isDefault
            ? []
            : Object.entries(where)
                .filter(([key, lib]) => lib === l.key && key.startsWith(`${l.kind}|`))
                .map(([key]) => key.slice(l.kind.length + 1)),
        }));
        body.hidden = Object.fromEntries(
          libs.map((l, i) => [l.id !== null ? String(l.id) : `new:${i}`, hidden[l.key] ?? []]),
        );
      }
      await apiClient.put('player/setup', { json: body });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['player', 'setup'] }),
        queryClient.invalidateQueries({ queryKey: ['player', 'libraries'] }),
        queryClient.invalidateQueries({ queryKey: ['player', 'video-catalog'] }),
        queryClient.invalidateQueries({ queryKey: ['player', 'settings'] }),
        queryClient.invalidateQueries({ queryKey: ['player', 'home'] }),
      ]);
      closePlayerSetup();
    } catch (e) {
      setError(await refusalText(e, 'Could not save. Try again in a moment.'));
    } finally {
      setBusy(false);
    }
  };

  const moveFolder = (kind: Kind, folder: string, toKey: string | null, name?: string) => {
    let target = toKey;
    if (!target) {
      // its own library, named after the folder
      target = `new:${Date.now()}:${Math.random().toString(36).slice(2, 6)}`;
      const path = data.folders?.[kind]?.find((f) => f.folder === folder)?.path ?? folder;
      const libName = name ?? libraryNameFor(path, kind);
      setLibs((all) => [
        ...all,
        { key: target!, id: null, kind, name: libName, isDefault: false, inGuide: kind !== 'home' },
      ]);
    }
    setWhere((w) => ({ ...w, [`${kind}|${folder}`]: target! }));
  };

  // a folder being dragged, and the library card it is over
  const dragging = useRef<{ kind: Kind; folder: string } | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [hideHint, setHideHint] = useState<Record<string, boolean>>({});

  const libraryStep = (kind: Kind) => {
    const mine = libs.filter((l) => l.kind === kind);
    const main = mine.find((l) => l.isDefault);
    const folders = (data.folders?.[kind] ?? []).filter((f) => f.folder);
    const loose = (data.folders?.[kind] ?? []).find((f) => !f.folder);
    const noun = kind === 'movie' ? 'films' : kind === 'show' ? 'shows' : 'videos';
    // a folder's picture: a title's poster, or a home video's own frame
    const shot = (id: number) =>
      kind === 'home' ? appURL(`/api/player/home/thumb/${id}`) : posterSet(kind, id).src;
    // a home root reads as where it is: the server, or the folder's drive
    // a server's home library sits on the drive its clips live on, beside the
    // other folders there; only when that isn't known does it sit under the server
    const whereIs = (f: SetupFolder) =>
      kind === 'home' && f.source && f.source !== 'disk'
        ? f.where
          ? splitFolderPath(f.where)
          : { root: f.source === 'plex' ? 'Plex' : 'Jellyfin', parts: [f.label ?? f.path] }
        : splitFolderPath(f.path);
    const libOf = (f: SetupFolder) => where[`${kind}|${f.folder}`] ?? main?.key ?? '';
    const colorOf = (key: string) =>
      LIB_COLORS[
        Math.max(
          0,
          mine.findIndex((l) => l.key === key),
        ) % LIB_COLORS.length
      ];
    const dropProps = (key: string | null) => ({
      onDragOver: (e: React.DragEvent) => {
        if (dragging.current?.kind !== kind) return;
        e.preventDefault();
        setOver(key ?? 'new');
      },
      onDragLeave: () => setOver((o) => (o === (key ?? 'new') ? null : o)),
      onDrop: (e: React.DragEvent) => {
        e.preventDefault();
        setOver(null);
        const d = dragging.current;
        dragging.current = null;
        if (d && d.kind === kind) moveFolder(kind, d.folder, key);
      },
    });
    // suggestions read off the folder names, for folders still in the main one
    const ideas = librarySuggestions(
      kind,
      folders
        .filter((f) => libOf(f) === main?.key)
        .map((f) => ({ folder: f.folder, path: f.path })),
      mine.map((l) => l.name),
    );
    const homeLike =
      kind === 'home'
        ? []
        : folders
            .map((f) => splitFolderPath(f.path).parts.at(-1) ?? '')
            .filter((name) => HOME_LIKE.test(name));
    const acceptIdeas = () => {
      for (const idea of ideas) {
        const key = `new:${Date.now()}:${Math.random().toString(36).slice(2, 6)}`;
        setLibs((all) => [
          ...all,
          { key, id: null, kind, name: idea.name, isDefault: false, inGuide: kind !== 'home' },
        ]);
        setWhere((w) => {
          const next = { ...w };
          for (const folder of idea.folders) next[`${kind}|${folder}`] = key;
          return next;
        });
      }
    };
    // home videos on the same drives, shown there so the drive reads whole;
    // they are set up on their own step
    const homeStep = STEPS.findIndex((x) => x.label === 'Home videos');
    const homeOn = (root: string) =>
      kind === 'home'
        ? []
        : (data.folders?.home ?? []).filter(
            (f) => f.titles && f.where && splitFolderPath(f.where).root === root,
          );
    // the folders grouped by where they live, then by the folders above them
    const byRoot = new Map<string, Map<string, SetupFolder[]>>();
    for (const f of folders) {
      const { root, parts } = whereIs(f);
      const group = parts.slice(0, -1).join('\u0000');
      if (!byRoot.has(root)) byRoot.set(root, new Map());
      const groups = byRoot.get(root)!;
      groups.set(group, [...(groups.get(group) ?? []), f]);
    }
    return (
      <>
        <div className={s.libCards}>
          {mine.map((lib) => {
            const inIt = folders.filter((f) => libOf(f) === lib.key);
            const total =
              inIt.reduce((n, f) => n + f.titles, 0) + (lib.isDefault ? (loose?.titles ?? 0) : 0);
            const shots = inIt.flatMap((f) => f.sample).slice(0, 4);
            const color = colorOf(lib.key);
            const guide = toggle(lib.inGuide, `${lib.name || 'This library'} is in the guide`, () =>
              setLibs((all) =>
                all.map((x) => (x.key === lib.key ? { ...x, inGuide: !x.inGuide } : x)),
              ),
            );
            return (
              <section
                key={lib.key}
                className={s.libCard}
                data-over={over === lib.key ? 'true' : undefined}
                aria-label={lib.name || 'Unnamed library'}
                {...dropProps(lib.key)}
              >
                <span className={s.libEdge} style={{ background: color }} aria-hidden="true" />
                {shots.length ? (
                  <div className={s.collage} aria-hidden="true">
                    {shots.map((id) => (
                      <img
                        key={id}
                        src={shot(id)}
                        alt=""
                        loading="lazy"
                        onError={(e) => {
                          e.currentTarget.style.visibility = 'hidden';
                        }}
                      />
                    ))}
                  </div>
                ) : (
                  <div className={s.collageEmpty}>
                    {inIt.length ? 'Nothing in it yet' : 'No folders yet'}
                  </div>
                )}
                <div className={s.libMeta}>
                  <span className={s.libDot} style={{ background: color }} aria-hidden="true" />
                  {lib.isDefault && lib.kind !== 'home' ? (
                    <b className={s.libTitle}>{lib.name}</b>
                  ) : (
                    <input
                      className={s.libName}
                      value={lib.name}
                      maxLength={60}
                      aria-label="Library name"
                      onChange={(e) =>
                        setLibs((all) =>
                          all.map((x) => (x.key === lib.key ? { ...x, name: e.target.value } : x)),
                        )
                      }
                      data-nav
                    />
                  )}
                  <span className={s.libCount}>
                    {total.toLocaleString()} {noun}
                  </span>
                </div>
                <div className={s.libGuide}>
                  {guide}
                  <span>Show in the guide</span>
                </div>
                {!lib.isDefault ? (
                  <button
                    type="button"
                    className={s.libRemove}
                    aria-label={`Remove ${lib.name || 'this library'}`}
                    title="Remove (its folders go back to the main one)"
                    onClick={() => {
                      setLibs((all) => all.filter((x) => x.key !== lib.key));
                      setWhere((w) =>
                        Object.fromEntries(Object.entries(w).filter(([, v]) => v !== lib.key)),
                      );
                    }}
                    data-nav
                  >
                    ×
                  </button>
                ) : null}
              </section>
            );
          })}
          <button
            type="button"
            className={s.newLib}
            aria-label="Add a library"
            data-over={over === 'new' ? 'true' : undefined}
            onClick={() =>
              setLibs((all) => [
                ...all,
                {
                  key: `new:${Date.now()}`,
                  id: null,
                  kind,
                  name:
                    kind === 'movie'
                      ? 'New movie library'
                      : kind === 'show'
                        ? 'New TV library'
                        : 'New home video library',
                  isDefault: false,
                  inGuide: kind !== 'home',
                },
              ])
            }
            {...dropProps(null)}
            data-nav
          >
            <span className={s.plus} aria-hidden="true">
              +
            </span>
            New library
            <span className={s.muted}>or drop a folder here</span>
          </button>
        </div>

        {kind !== 'home' && homeLike.length ? (
          <div className={s.ideas} role="note">
            <p>
              <b>{homeLike.join(', ')}</b> {homeLike.length === 1 ? 'looks' : 'look'} like home
              videos. They get their own library, without movie or show lookups.
            </p>
            <button
              type="button"
              className={k.btnPrimary}
              onClick={() => setStep(STEPS.findIndex((x) => x.label === 'Home videos'))}
              data-nav
            >
              Set up in Home videos
            </button>
          </div>
        ) : null}
        {ideas.length && !hideHint[kind] ? (
          <div className={s.ideas} role="note">
            <p>
              From the folder names:{' '}
              {ideas.map((idea, i) => (
                <span key={idea.name}>
                  {i ? '; ' : ''}
                  <b>{idea.labels.join(', ')}</b> could be its own <b>{idea.name}</b> library
                </span>
              ))}
              .
            </p>
            <button type="button" className={k.btnPrimary} onClick={acceptIdeas} data-nav>
              {ideas.length === 1 ? 'Make it' : 'Make them'}
            </button>
            <button
              type="button"
              className={s.ghost}
              onClick={() => setHideHint((h) => ({ ...h, [kind]: true }))}
              data-nav
            >
              Not now
            </button>
          </div>
        ) : null}

        {folders.length ? (
          <div className={s.map}>
            {[...byRoot.entries()].map(([root, groups]) => {
              const all = [...groups.values()].flat();
              return (
                <section key={root || 'local'} aria-label={root || 'This machine'}>
                  <div className={s.mapRoot}>
                    <b>{root || 'This machine'}</b>
                    <span>
                      {all.length} {all.length === 1 ? 'folder' : 'folders'} ·{' '}
                      {all.reduce((n, f) => n + f.titles, 0).toLocaleString()} {noun}
                    </span>
                  </div>
                  {[...groups.entries()].map(([group, list]) => (
                    <div key={group || 'top'}>
                      {group ? (
                        <div className={s.mapGroup}>
                          {group.split('\u0000').map((part, i) => (
                            <span key={`${i}-${part}`}>
                              {i ? <em aria-hidden="true">›</em> : null}
                              {part}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      {list.map((f) => {
                        const key = libOf(f);
                        const parts = whereIs(f).parts;
                        return (
                          <div
                            key={f.folder}
                            className={s.mapRow}
                            title={f.path}
                            draggable
                            onDragStart={(e) => {
                              dragging.current = { kind, folder: f.folder };
                              e.dataTransfer.effectAllowed = 'move';
                              e.dataTransfer.setData('text/plain', f.folder);
                            }}
                            onDragEnd={() => {
                              dragging.current = null;
                              setOver(null);
                            }}
                          >
                            <span
                              className={s.rowEdge}
                              style={{ background: colorOf(key) }}
                              aria-hidden="true"
                            />
                            {f.sample.length ? (
                              <span className={s.fan} aria-hidden="true">
                                {f.sample.slice(0, 3).map((id) => (
                                  <img
                                    key={id}
                                    src={shot(id)}
                                    alt=""
                                    loading="lazy"
                                    onError={(e) => {
                                      e.currentTarget.style.visibility = 'hidden';
                                    }}
                                  />
                                ))}
                              </span>
                            ) : (
                              <span className={s.fanEmpty} aria-hidden="true">
                                empty
                              </span>
                            )}
                            <span className={s.rowText}>
                              <b>{parts.at(-1) ?? f.path}</b>
                              <span className={f.titles ? s.muted : s.mutedFaint}>
                                {f.titles.toLocaleString()} {noun}
                              </span>
                            </span>
                            <span className={s.pillWrap}>
                              <span
                                className={s.pillDot}
                                style={{ background: colorOf(key) }}
                                aria-hidden="true"
                              />
                              <select
                                className={s.pill}
                                value={key}
                                aria-label={`Library for ${f.path}`}
                                onChange={(e) =>
                                  e.target.value === '+'
                                    ? moveFolder(kind, f.folder, null)
                                    : setWhere((w) => ({
                                        ...w,
                                        [`${kind}|${f.folder}`]: e.target.value,
                                      }))
                                }
                                data-nav
                              >
                                {mine.map((o) => (
                                  <option key={o.key} value={o.key}>
                                    {o.name || 'Unnamed'}
                                  </option>
                                ))}
                                <option value="+">Its own library…</option>
                              </select>
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  ))}
                  {homeOn(root).map((f) => (
                    <button
                      key={`home-${f.folder}`}
                      type="button"
                      className={clsx(s.mapRow, s.mapHome)}
                      title={f.where ?? f.path}
                      onClick={() => setStep(homeStep)}
                      data-nav
                    >
                      <span className={s.fanEmpty} aria-hidden="true">
                        home
                      </span>
                      <span className={s.rowText}>
                        <b>{splitFolderPath(f.where ?? '').parts.at(-1) ?? f.label}</b>
                        <span className={s.muted}>
                          {f.titles.toLocaleString()} home {f.titles === 1 ? 'video' : 'videos'} ·
                          set up in Home videos
                        </span>
                      </span>
                      <span className={s.mapHomeGo} aria-hidden="true">
                        ›
                      </span>
                    </button>
                  ))}
                </section>
              );
            })}
          </div>
        ) : kind === 'home' ? null : (
          <p className={s.muted}>
            No {kind === 'movie' ? 'movie' : 'TV'} folders are set up in SoulSync yet (Settings,
            Folders).
          </p>
        )}
        {loose ? (
          <p className={s.muted}>
            Plus {loose.titles.toLocaleString()} {noun} outside these folders, which stay in{' '}
            {main?.name ?? 'the main library'}.
          </p>
        ) : null}
        <p className={s.muted}>
          Empty folders count too: what lands there later goes to that folder&apos;s library.
          Nothing on disk moves.
        </p>
      </>
    );
  };

  const people = (data.profiles ?? []).filter((p) => !p.is_admin);
  // every page, and after Movies and TV the libraries made here: one that
  // isn't saved yet goes by its draft key here, and by its place in the list in the save
  const places = NAV.flatMap((e) => [
    { id: e.to, label: e.label, group: e.group, lib: null as DraftLibrary | null },
    ...libs.flatMap((l) =>
      (!l.isDefault || l.kind === 'home') &&
      e.to === (l.kind === 'movie' ? '/player/movies' : '/player/tv')
        ? [
            {
              id: l.id !== null ? `lib:${l.id}` : `draft:${l.key}`,
              label: l.name.trim() || 'New library',
              group: e.group,
              lib: l,
            },
          ]
        : [],
    ),
  ]);
  const ordered = [
    ...order.filter((id) => places.some((p) => p.id === id)),
    ...places.map((p) => p.id).filter((id) => !order.includes(id)),
  ];

  // the live preview: whose sidebar it shows
  const [as, setAs] = useState<number | null>(null);
  const previewPlaces = useMemo(() => {
    const isOff = (lib: DraftLibrary | null) =>
      lib !== null && as !== null && (hidden[lib.key] ?? []).includes(as);
    const mainOf = (id: string) =>
      libs.find(
        (l) => l.isDefault && (l.kind === 'movie' ? '/player/movies' : '/player/tv') === id,
      ) ?? null;
    return ordered
      .map((id) => places.find((p) => p.id === id)!)
      .filter((p) => p && !gone.includes(p.id) && !isOff(p.lib) && !isOff(mainOf(p.id)));
  }, [ordered, places, gone, hidden, as, libs]);

  // sidebar drag: the place being dragged and the one it is over
  const draggingPlace = useRef<string | null>(null);
  const [overPlace, setOverPlace] = useState<string | null>(null);
  const dropPlace = (target: string) => {
    const from = draggingPlace.current;
    draggingPlace.current = null;
    setOverPlace(null);
    if (!from || from === target || from === '/player') return;
    const groupOf = (id: string) => places.find((p) => p.id === id)?.group;
    if (groupOf(from) !== groupOf(target)) return;
    const next = ordered.filter((id) => id !== from);
    next.splice(next.indexOf(target), 0, from);
    setOrder(next);
  };

  const counts = (['movie', 'show', 'home'] as const).map((kind) => {
    const all = data.folders?.[kind] ?? [];
    return {
      kind,
      titles: all.reduce((n, f) => n + f.titles, 0),
      folders: all.filter((f) => f.folder).length,
    };
  });
  const madeHere = libs.filter((l) => !l.isDefault || l.kind === 'home').length;
  const keptFrom = Object.values(hidden).reduce((n, list) => n + list.length, 0);
  const last = STEPS.length - 1;

  const toggle = (on: boolean, label: string, onClick: () => void, extra?: React.ReactNode) => (
    <button
      type="button"
      className={s.toggle}
      aria-pressed={on}
      aria-label={label}
      onClick={onClick}
      data-nav
    >
      <span className={s.switch} aria-hidden="true" />
      {extra}
    </button>
  );

  return inPlayer(
    <div className={s.veil} role="dialog" aria-modal="true" aria-label="Player setup">
      <div className={s.stage}>
        <nav className={s.rail} aria-label="Setup steps">
          <div className={s.brand}>
            <Mark size="md" />
            <span>
              {name.trim() || 'SoulSync'}
              <small>Player setup</small>
            </span>
          </div>
          <div className={s.progress} aria-hidden="true">
            <span style={{ width: `${Math.round((step / (STEPS.length - 1)) * 100)}%` }} />
          </div>
          <ol className={s.steps}>
            {STEPS.map((st, i) => (
              <li key={st.label}>
                {st.group && STEPS[i - 1]?.group !== st.group ? (
                  <div className={s.stepGroup}>{st.group}</div>
                ) : null}
                <button
                  type="button"
                  className={s.step}
                  aria-current={i === step ? 'step' : undefined}
                  data-done={i < step ? 'true' : undefined}
                  onClick={() => setStep(i)}
                  data-nav
                >
                  <span className={s.dot} aria-hidden="true">
                    {i < step ? '✓' : i + 1}
                  </span>
                  {st.label}
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <main className={s.main}>
          {step === 0 ? (
            <div className={s.body}>
              <Mark size="hero" />
              <div className={s.eyebrow}>{firstTime ? 'Welcome' : 'Player setup'}</div>
              <h1 className={s.title}>
                {firstTime ? 'Let’s set up your player' : 'Your player’s setup'}
              </h1>
              <p className={s.lead}>
                A few minutes, once, for everyone on this server. Every step has a default, so skip
                anything you don’t care about and change it later from the player’s settings.
              </p>
              <label className={s.name} htmlFor="player-server-name">
                <input
                  id="player-server-name"
                  value={name}
                  maxLength={60}
                  placeholder="SoulSync"
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') setStep(1);
                  }}
                  data-nav
                />
                <span className={s.muted}>Your server’s name</span>
              </label>
              <div className={s.found} aria-label="What SoulSync found">
                {counts
                  .filter((c) => c.kind !== 'home' || c.folders)
                  .map((c) => (
                    <div key={c.kind}>
                      <b>{c.titles.toLocaleString()}</b>
                      <span>
                        {c.kind === 'movie' ? 'films' : c.kind === 'show' ? 'shows' : 'home videos'}{' '}
                        in {c.folders} {c.folders === 1 ? 'folder' : 'folders'}
                      </span>
                    </div>
                  ))}
                <div>
                  <b>{people.length}</b>
                  <span>{people.length === 1 ? 'person' : 'people'} besides the admins</span>
                </div>
              </div>
            </div>
          ) : null}

          {step === 1 || step === 2 ? (
            <div className={s.body}>
              <div className={s.eyebrow}>Libraries · {step === 1 ? 'Movies' : 'TV'}</div>
              <h1 className={s.title}>
                How should {step === 1 ? 'movies' : 'TV shows'} be grouped?
              </h1>
              <p className={s.lead}>
                These are the folders SoulSync keeps {step === 1 ? 'films' : 'shows'} in. They start
                as one library. Split some off (4K, Kids, Anime) and each gets its own page.
              </p>
              {libraryStep(step === 1 ? 'movie' : 'show')}
            </div>
          ) : null}

          {step === 3 ? (
            <div className={s.body}>
              <div className={s.eyebrow}>Libraries · Home videos</div>
              <h1 className={s.title}>Your home videos</h1>
              <p className={s.lead}>
                Clips from your phone or camera. SoulSync never looks them up or renames them: it
                names and dates them from the clip itself, and each folder becomes an album.
              </p>
              <HomeSourcesPanel
                onChanged={() =>
                  void queryClient.invalidateQueries({ queryKey: ['player', 'setup'] })
                }
              />
              {libs.some((l) => l.kind === 'home') ? (
                <>
                  <div className={s.stepGroup}>Libraries</div>
                  {libraryStep('home')}
                  <label className={s.homeEditors}>
                    {toggle(
                      homeEditors === 'everyone',
                      'Everyone who can see a home video can rename it',
                      () => setHomeEditors((v) => (v === 'everyone' ? 'admins' : 'everyone')),
                    )}
                    <span>
                      Let everyone who can see them rename, re-date and pick pictures (otherwise
                      only admins)
                    </span>
                  </label>
                </>
              ) : null}
            </div>
          ) : null}

          {step === 4 ? (
            <div className={s.body}>
              <div className={s.eyebrow}>People</div>
              <h1 className={s.title}>Who sees what</h1>
              <p className={s.lead}>
                Switch a library off for someone and it is gone for them everywhere: pages, search,
                the guide, home rows and links. Admins always see everything. Age ratings stay per
                person in Settings, People.
              </p>
              {people.length ? (
                <div className={s.people}>
                  {people.map((p) => (
                    <section key={p.id} className={s.person} aria-label={p.name}>
                      <div className={s.personHead}>
                        <span className={s.avatar} aria-hidden="true">
                          {p.name.charAt(0).toUpperCase()}
                        </span>
                        <b>{p.name}</b>
                      </div>
                      {libs.map((lib) => {
                        const off = (hidden[lib.key] ?? []).includes(p.id);
                        return (
                          <div
                            key={lib.key}
                            className={s.personRow}
                            data-off={off ? 'true' : undefined}
                          >
                            {toggle(!off, `${p.name} can see ${lib.name}`, () =>
                              setHidden((h) => {
                                const set = new Set(h[lib.key] ?? []);
                                if (off) set.delete(p.id);
                                else set.add(p.id);
                                return { ...h, [lib.key]: [...set] };
                              }),
                            )}
                            <span className={s.personLib}>{lib.name || 'Unnamed'}</span>
                            <span className={s.muted}>
                              {lib.kind === 'movie'
                                ? 'Movies'
                                : lib.kind === 'show'
                                  ? 'TV'
                                  : 'Home videos'}
                            </span>
                          </div>
                        );
                      })}
                    </section>
                  ))}
                </div>
              ) : (
                <p className={s.muted}>
                  There is nobody but admins on this server yet. Add people in Settings, People.
                </p>
              )}
              {people.length && libs.some((l) => l.kind === 'home') ? (
                <p className={s.note} role="note">
                  Home videos are your family’s own clips. You may want to switch them off for
                  guests or anyone outside the house.
                </p>
              ) : null}
            </div>
          ) : null}

          {step === 5 ? (
            <div className={s.body}>
              <div className={s.eyebrow}>Sidebar</div>
              <h1 className={s.title}>Arrange the sidebar</h1>
              <p className={s.lead}>
                Drag to reorder, switch off what nobody here uses. This is where everyone starts;
                each person can arrange their own afterwards.
              </p>
              <div className={s.order}>
                {NAV_GROUPS.map((group) => {
                  const inGroup = ordered.filter(
                    (id) => places.find((p) => p.id === id)?.group === group.id,
                  );
                  return (
                    <div key={group.id} role="group" aria-label={group.label ?? 'Main'}>
                      {group.label ? <div className={s.stepGroup}>{group.label}</div> : null}
                      {inGroup.map((id, i) => {
                        const place = places.find((p) => p.id === id)!;
                        const fixed = id === '/player';
                        const off = gone.includes(id);
                        const move = (stepBy: -1 | 1) => {
                          const next = moveNav(inGroup, id, stepBy);
                          let n = 0;
                          setOrder(ordered.map((x) => (inGroup.includes(x) ? next[n++] : x)));
                        };
                        return (
                          <div
                            key={id}
                            className={s.place}
                            data-off={off ? 'true' : undefined}
                            data-over={overPlace === id ? 'true' : undefined}
                            draggable={!fixed}
                            onDragStart={(e) => {
                              draggingPlace.current = id;
                              e.dataTransfer.effectAllowed = 'move';
                              e.dataTransfer.setData('text/plain', id);
                            }}
                            onDragOver={(e) => {
                              if (!draggingPlace.current) return;
                              e.preventDefault();
                              setOverPlace(id);
                            }}
                            onDrop={(e) => {
                              e.preventDefault();
                              dropPlace(id);
                            }}
                            onDragEnd={() => {
                              draggingPlace.current = null;
                              setOverPlace(null);
                            }}
                          >
                            <span className={s.grip} aria-hidden="true">
                              ⋮⋮
                            </span>
                            <span className={s.placeLabel}>{place.label}</span>
                            {place.lib ? (
                              <span className={s.chip} data-tone="accent">
                                Library
                              </span>
                            ) : null}
                            <span className={s.grow} />
                            <span className={s.arrows}>
                              <button
                                type="button"
                                onClick={() => move(-1)}
                                disabled={i === 0 || fixed}
                                aria-label={`Move ${place.label} up`}
                                data-nav
                              >
                                ↑
                              </button>
                              <button
                                type="button"
                                onClick={() => move(1)}
                                disabled={i === inGroup.length - 1 || fixed}
                                aria-label={`Move ${place.label} down`}
                                data-nav
                              >
                                ↓
                              </button>
                            </span>
                            {fixed ? (
                              <span className={s.muted}>Always there</span>
                            ) : (
                              toggle(!off, `${place.label} is in the sidebar`, () =>
                                setGone((g) => (off ? g.filter((x) => x !== id) : [...g, id])),
                              )
                            )}
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {step === last ? (
            <div className={s.body}>
              <Mark size="hero" />
              <div className={s.eyebrow}>All set</div>
              <h1 className={s.title}>{(name.trim() || 'SoulSync') + ' is ready'}</h1>
              <p className={s.lead}>
                Here is what you set up. The preview shows the sidebar each person gets. You can
                come back to any of this from the player’s settings, under Player setup.
              </p>
              <div className={s.found}>
                <div>
                  <b>{libs.length}</b>
                  <span>
                    libraries, {madeHere} with {madeHere === 1 ? 'its' : 'their'} own page
                  </span>
                </div>
                <div>
                  <b>{keptFrom}</b>
                  <span>
                    {keptFrom === 1 ? 'library kept from someone' : 'libraries kept from someone'}
                  </span>
                </div>
                <div>
                  <b>{gone.length}</b>
                  <span>{gone.length === 1 ? 'place' : 'places'} off for everyone</span>
                </div>
              </div>
              {/* pairing stays in Settings, Devices: it is open whether or not
                  setup ran, so this only points the way */}
              <div className={s.next}>
                <div className={s.stepGroup}>Next, your devices</div>
                <a className={s.nextCard} href={appURL('/settings?tab=devices')} data-nav>
                  <b>The SoulSync app</b>
                  <span>Phones, tablets and TVs pair with a one-time code or a QR.</span>
                </a>
                <a className={s.nextCard} href={appURL('/settings?tab=devices')} data-nav>
                  <b>Any music app</b>
                  <span>Symfonium, Feishin and other Subsonic apps play one profile’s music.</span>
                </a>
              </div>
            </div>
          ) : null}

          <div className={s.foot}>
            {error ? (
              <span className={s.error} role="alert">
                {error}
              </span>
            ) : (
              <span className={s.muted}>
                Step {step + 1} of {STEPS.length}
              </span>
            )}
            <span className={s.grow} />
            {firstTime && step < last ? (
              <button
                type="button"
                className={s.ghost}
                disabled={busy}
                onClick={() => void save(true)}
                data-nav
              >
                Use the defaults
              </button>
            ) : null}
            {!firstTime ? (
              <button type="button" className={s.ghost} onClick={closePlayerSetup} data-nav>
                Close
              </button>
            ) : null}
            {step > 0 ? (
              <button type="button" className={k.btn} onClick={() => setStep(step - 1)} data-nav>
                Back
              </button>
            ) : null}
            {step < last ? (
              <button
                type="button"
                className={k.btnPrimary}
                onClick={() => setStep(step + 1)}
                data-nav
              >
                Next: {STEPS[step + 1].label}
              </button>
            ) : null}
            {step === last || !firstTime ? (
              <button
                type="button"
                className={k.btnPrimary}
                disabled={busy}
                onClick={() => void save()}
                data-nav
              >
                {busy ? 'Saving…' : firstTime ? 'Finish' : 'Save'}
              </button>
            ) : null}
          </div>
        </main>

        <aside className={s.preview} aria-label="Live preview">
          <div className={s.stepGroup}>Live preview</div>
          <div className={s.who} role="group" aria-label="Preview as">
            <button type="button" aria-pressed={as === null} onClick={() => setAs(null)} data-nav>
              You
            </button>
            {people.map((p) => (
              <button
                key={p.id}
                type="button"
                aria-pressed={as === p.id}
                onClick={() => setAs(p.id)}
                data-nav
              >
                {p.name}
              </button>
            ))}
          </div>
          <div className={s.mini}>
            <div className={s.miniName}>
              <Mark />
              {name.trim() || 'SoulSync'}
            </div>
            {NAV_GROUPS.map((group) => {
              const items = previewPlaces.filter((p) => p.group === group.id);
              if (!items.length) return null;
              return (
                <div key={group.id}>
                  {group.label ? <div className={s.miniGroup}>{group.label}</div> : null}
                  {items.map((p) => (
                    <div key={p.id} className={s.miniItem} data-lib={p.lib ? 'true' : undefined}>
                      {p.label}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
          <p className={s.muted}>Updates as you change things.</p>
        </aside>
      </div>
    </div>,
  );
}
