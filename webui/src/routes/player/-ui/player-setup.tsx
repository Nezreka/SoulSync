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
 *   people     which libraries each person can see
 *   sidebar    the order everyone starts from, and places nobody here gets
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';

import { apiClient } from '@/app/api-client';

import { posterSet } from '../-lib/images';
import { moveNav } from '../-lib/nav-order';
import { styles as k } from './kit';
import { NAV, NAV_GROUPS } from './sections';
import s from './setup.module.css';
import { inPlayer } from './trailer';

type Kind = 'movie' | 'show';

interface SetupFolder {
  folder: string;
  path: string;
  titles: number;
  sample: number[];
  library_id: number;
}
interface SetupLibrary {
  id: number;
  kind: Kind;
  name: string;
  is_default: boolean;
  folders: string[];
}
interface SetupPayload {
  done: boolean;
  server_name: string;
  is_admin: boolean;
  sidebar_order?: string[];
  sidebar_hidden?: string[];
  libraries?: SetupLibrary[];
  folders?: Record<Kind, SetupFolder[]>;
  hidden?: Record<string, number[]>;
  profiles?: { id: number; name: string; is_admin: boolean }[];
}

/** a library in the draft: a saved one has an id, a new one only its key */
interface DraftLibrary {
  key: string;
  id: number | null;
  kind: Kind;
  name: string;
  isDefault: boolean;
}

export const playerSetupQuery = () => ({
  queryKey: ['player', 'setup'] as const,
  queryFn: async () => await apiClient.get('player/setup').json<SetupPayload>(),
  staleTime: 60_000,
});

// ── opened from anywhere (Settings, the users page) ───────────────────────

let wanted = false;
const listeners = new Set<() => void>();
const tell = () => listeners.forEach((fn) => fn());
export function openPlayerSetup() {
  wanted = true;
  tell();
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

function PlayerSetup({ data, firstTime }: { data: SetupPayload; firstTime: boolean }) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);
  const [name, setName] = useState(data.server_name);
  const [libs, setLibs] = useState<DraftLibrary[]>(() =>
    (data.libraries ?? []).map((l) => ({
      key: `id:${l.id}`,
      id: l.id,
      kind: l.kind,
      name: l.name,
      isDefault: l.is_default,
    })),
  );
  // folder -> the library (by key) it is in
  const [where, setWhere] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const kind of ['movie', 'show'] as const)
      for (const f of data.folders?.[kind] ?? [])
        if (f.folder) out[`${kind}|${f.folder}`] = `id:${f.library_id}`;
    return out;
  });
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
        body.libraries = libs.map((l) => ({
          ...(l.id !== null ? { id: l.id } : {}),
          kind: l.kind,
          name: l.name,
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
      ]);
      closePlayerSetup();
    } catch (e) {
      let message = 'Could not save. Try again in a moment.';
      try {
        const res = (e as { response?: Response }).response;
        const body = res ? ((await res.json()) as { error?: string; message?: string }) : null;
        message = body?.message ?? body?.error ?? message;
      } catch {
        // the plain message stands
      }
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  const moveFolder = (kind: Kind, folder: string, toKey: string | null) => {
    let target = toKey;
    if (!target) {
      // dropped on New library: a library made for it, named after the folder
      target = `new:${Date.now()}`;
      const base = folder.split(/[\\/]/).filter(Boolean).pop() || 'New library';
      const name = base.charAt(0).toUpperCase() + base.slice(1);
      setLibs((all) => [...all, { key: target!, id: null, kind, name, isDefault: false }]);
    }
    setWhere((w) => ({ ...w, [`${kind}|${folder}`]: target! }));
  };

  // a folder being dragged, and the card it is over
  const dragging = useRef<{ kind: Kind; folder: string } | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const libraryStep = (kind: Kind) => {
    const mine = libs.filter((l) => l.kind === kind);
    const main = mine.find((l) => l.isDefault);
    const folders = (data.folders?.[kind] ?? []).filter((f) => f.folder);
    const loose = (data.folders?.[kind] ?? []).find((f) => !f.folder);
    const noun = kind === 'movie' ? 'films' : 'shows';
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
    return (
      <>
        <div className={s.board}>
          {mine.map((lib) => {
            const inIt = folders.filter(
              (f) => (where[`${kind}|${f.folder}`] ?? main?.key) === lib.key,
            );
            const total =
              inIt.reduce((n, f) => n + f.titles, 0) + (lib.isDefault ? (loose?.titles ?? 0) : 0);
            return (
              <section
                key={lib.key}
                className={s.lib}
                data-over={over === lib.key ? 'true' : undefined}
                aria-label={lib.name || 'Unnamed library'}
                {...dropProps(lib.key)}
              >
                <div className={s.libHead}>
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
                  {lib.isDefault ? (
                    <span className={s.chip} data-tone="accent">
                      Main
                    </span>
                  ) : (
                    <button
                      type="button"
                      className={s.textBtn}
                      onClick={() => {
                        setLibs((all) => all.filter((x) => x.key !== lib.key));
                        setWhere((w) =>
                          Object.fromEntries(Object.entries(w).filter(([, v]) => v !== lib.key)),
                        );
                      }}
                      data-nav
                    >
                      Remove
                    </button>
                  )}
                </div>
                <div className={s.libCount}>
                  {total.toLocaleString()} {noun}
                </div>
                {inIt.map((f) => (
                  <div
                    key={f.folder}
                    className={s.folder}
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
                    <span className={s.stack} aria-hidden="true">
                      {f.sample.slice(0, 3).map((id) => (
                        <img
                          key={id}
                          src={posterSet(kind, id).src}
                          alt=""
                          loading="lazy"
                          onError={(e) => {
                            e.currentTarget.hidden = true;
                          }}
                        />
                      ))}
                    </span>
                    <span className={s.folderText}>
                      <span className={s.folderPath} title={f.path}>
                        {f.path}
                      </span>
                      <span className={s.muted}>
                        {f.titles.toLocaleString()} {noun}
                      </span>
                    </span>
                    {/* the same move without a mouse: a remote, a keyboard, a phone */}
                    <span className={s.break} aria-hidden="true" />
                    <select
                      className={s.move}
                      value={lib.key}
                      aria-label={`Library for ${f.path}`}
                      onChange={(e) =>
                        e.target.value === '+'
                          ? moveFolder(kind, f.folder, null)
                          : setWhere((w) => ({ ...w, [`${kind}|${f.folder}`]: e.target.value }))
                      }
                      data-nav
                    >
                      {mine.map((o) => (
                        <option key={o.key} value={o.key}>
                          {o.name || 'Unnamed'}
                        </option>
                      ))}
                      <option value="+">New library…</option>
                    </select>
                  </div>
                ))}
                {!inIt.length ? (
                  <div className={s.dropHint}>
                    {lib.isDefault ? 'Every folder is in another library' : 'Drop folders here'}
                  </div>
                ) : null}
                {lib.isDefault && loose ? (
                  <div className={s.muted}>
                    Plus {loose.titles.toLocaleString()} {noun} outside these folders
                  </div>
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
                  name: kind === 'movie' ? 'New movie library' : 'New TV library',
                  isDefault: false,
                },
              ])
            }
            {...dropProps(null)}
            data-nav
          >
            <span className={s.plus} aria-hidden="true">
              +
            </span>
            Add a library
            <span className={s.muted}>or drop a folder here</span>
          </button>
        </div>
        {!folders.length ? (
          <p className={s.muted}>
            No {kind === 'movie' ? 'movie' : 'TV'} folders are set up in SoulSync yet (Settings,
            Folders).
          </p>
        ) : (
          <p className={s.muted}>
            Drag a folder onto another library, or pick one from its menu. A library you add gets
            its own page in the sidebar.
          </p>
        )}
      </>
    );
  };

  const people = (data.profiles ?? []).filter((p) => !p.is_admin);
  // every page, and after Movies and TV the libraries made here: one that
  // isn't saved yet goes by its draft key here, and by its place in the list in the save
  const places = NAV.flatMap((e) => [
    { id: e.to, label: e.label, group: e.group, lib: null as DraftLibrary | null },
    ...libs.flatMap((l) =>
      !l.isDefault && e.to === (l.kind === 'movie' ? '/player/movies' : '/player/tv')
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

  const counts = (['movie', 'show'] as const).map((kind) => {
    const all = data.folders?.[kind] ?? [];
    return {
      kind,
      titles: all.reduce((n, f) => n + f.titles, 0),
      folders: all.filter((f) => f.folder).length,
    };
  });
  const madeHere = libs.filter((l) => !l.isDefault).length;
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
            <span className={s.mark} aria-hidden="true" />
            Player setup
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
                  data-nav
                />
                <span className={s.muted}>Your server’s name</span>
              </label>
              <div className={s.found} aria-label="What SoulSync found">
                {counts.map((c) => (
                  <div key={c.kind}>
                    <b>{c.titles.toLocaleString()}</b>
                    <span>
                      {c.kind === 'movie' ? 'films' : 'shows'} in {c.folders}{' '}
                      {c.folders === 1 ? 'folder' : 'folders'}
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
                              {lib.kind === 'movie' ? 'Movies' : 'TV'}
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
            </div>
          ) : null}

          {step === 4 ? (
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
                Next
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
              <span className={s.mark} aria-hidden="true" />
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
