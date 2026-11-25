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
import { useEffect, useState, useSyncExternalStore } from 'react';

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

const STEPS = ['Name', 'Movies', 'TV', 'People', 'Sidebar'] as const;

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
        body.sidebar_order = order;
        body.sidebar_hidden = gone;
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

  const libraryStep = (kind: Kind) => {
    const mine = libs.filter((l) => l.kind === kind);
    const main = mine.find((l) => l.isDefault);
    const folders = (data.folders?.[kind] ?? []).filter((f) => f.folder);
    const loose = (data.folders?.[kind] ?? []).find((f) => !f.folder);
    const noun = kind === 'movie' ? 'films' : 'shows';
    return (
      <>
        {mine.map((lib) => {
          const inIt = folders.filter(
            (f) => (where[`${kind}|${f.folder}`] ?? main?.key) === lib.key,
          );
          return (
            <section key={lib.key} className={s.library} aria-label={lib.name}>
              <div className={s.libraryHead}>
                <input
                  className={s.field}
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
                  <span className={s.note}>The main one</span>
                ) : (
                  <button
                    type="button"
                    className={k.btn}
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
              {inIt.length ? (
                inIt.map((f) => (
                  <div key={f.folder} className={s.folder}>
                    <div>
                      <div className={s.folderPath} title={f.path}>
                        {f.path}
                      </div>
                      <div className={s.note}>
                        {f.titles.toLocaleString()} {noun}
                      </div>
                      {f.sample.length ? (
                        <div className={s.posters} aria-hidden="true">
                          {f.sample.map((id) => (
                            <img key={id} src={posterSet(kind, id).src} alt="" loading="lazy" />
                          ))}
                        </div>
                      ) : null}
                    </div>
                    {mine.length > 1 ? (
                      <select
                        className={s.pick}
                        value={lib.key}
                        aria-label={`Library for ${f.path}`}
                        onChange={(e) =>
                          setWhere((w) => ({ ...w, [`${kind}|${f.folder}`]: e.target.value }))
                        }
                        data-nav
                      >
                        {mine.map((o) => (
                          <option key={o.key} value={o.key}>
                            {o.name || 'Unnamed'}
                          </option>
                        ))}
                      </select>
                    ) : null}
                  </div>
                ))
              ) : (
                <div className={s.note}>
                  {lib.isDefault
                    ? 'Every folder is in another library.'
                    : 'No folders yet. Pick this library on a folder to move it here.'}
                </div>
              )}
              {lib.isDefault && loose ? (
                <div className={s.note}>
                  Plus {loose.titles.toLocaleString()} {noun} that sit outside these folders.
                </div>
              ) : null}
            </section>
          );
        })}
        <div>
          <button
            type="button"
            className={k.btn}
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
            data-nav
          >
            Add a library
          </button>
        </div>
        {!folders.length ? (
          <div className={s.note}>
            No {kind === 'movie' ? 'movie' : 'TV'} folders are set up in SoulSync yet (Settings,
            Folders).
          </div>
        ) : null}
      </>
    );
  };

  const people = (data.profiles ?? []).filter((p) => !p.is_admin);
  const places = NAV.map((e) => ({ id: e.to, label: e.label, group: e.group }));
  const ordered = [
    ...order.filter((id) => places.some((p) => p.id === id)),
    ...places.map((p) => p.id).filter((id) => !order.includes(id)),
  ];

  return inPlayer(
    <div className={s.veil} role="dialog" aria-modal="true" aria-label="Player setup">
      <div className={s.sheet}>
        <ol className={s.steps}>
          {STEPS.map((label, i) => (
            <li
              key={label}
              className={s.step}
              data-on={i === step ? 'true' : undefined}
              data-done={i < step ? 'true' : undefined}
            >
              {label}
            </li>
          ))}
        </ol>

        {step === 0 ? (
          <div className={s.body}>
            <h1 className={s.title}>{firstTime ? 'Set up your player' : 'Player setup'}</h1>
            <p className={s.lead}>
              A few choices for everyone on this server: what it is called, how your folders are
              grouped into libraries, who sees which, and the sidebar. Every one has a default, so
              you can change only what you care about.
            </p>
            <label className={s.note} htmlFor="player-server-name">
              What should this server be called?
            </label>
            <input
              id="player-server-name"
              className={s.field}
              value={name}
              maxLength={60}
              placeholder="SoulSync"
              onChange={(e) => setName(e.target.value)}
              data-nav
            />
          </div>
        ) : null}

        {step === 1 ? (
          <div className={s.body}>
            <h1 className={s.title}>Movies</h1>
            <p className={s.lead}>
              These are the folders SoulSync keeps films in. They are all one library unless you
              make more. A library you add gets its own place in the sidebar and its own page.
            </p>
            {libraryStep('movie')}
          </div>
        ) : null}

        {step === 2 ? (
          <div className={s.body}>
            <h1 className={s.title}>TV</h1>
            <p className={s.lead}>The same for shows.</p>
            {libraryStep('show')}
          </div>
        ) : null}

        {step === 3 ? (
          <div className={s.body}>
            <h1 className={s.title}>Who sees what</h1>
            <p className={s.lead}>
              Untick a library for someone and it is gone for them everywhere: lists, search, and
              the titles themselves. Admins always see everything. Age ratings are set per person in
              Settings, People.
            </p>
            {people.length ? (
              <table className={s.grid}>
                <thead>
                  <tr>
                    <th scope="col">Library</th>
                    {people.map((p) => (
                      <th key={p.id} scope="col">
                        {p.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {libs.map((lib) => (
                    <tr key={lib.key}>
                      <td>{lib.name || 'Unnamed'}</td>
                      {people.map((p) => {
                        const off = (hidden[lib.key] ?? []).includes(p.id);
                        return (
                          <td key={p.id}>
                            <input
                              type="checkbox"
                              checked={!off}
                              aria-label={`${p.name} can see ${lib.name}`}
                              onChange={(e) =>
                                setHidden((h) => {
                                  const set = new Set(h[lib.key] ?? []);
                                  if (e.target.checked) set.delete(p.id);
                                  else set.add(p.id);
                                  return { ...h, [lib.key]: [...set] };
                                })
                              }
                              data-nav
                            />
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className={s.note}>
                There is nobody but admins on this server yet. Add people in Settings, People.
              </div>
            )}
          </div>
        ) : null}

        {step === 4 ? (
          <div className={s.body}>
            <h1 className={s.title}>Sidebar</h1>
            <p className={s.lead}>
              The order everyone starts from, and the places nobody on this server gets. Each person
              can still arrange their own in their settings.
            </p>
            {NAV_GROUPS.map((group) => {
              const inGroup = ordered.filter(
                (id) => places.find((p) => p.id === id)?.group === group.id,
              );
              return (
                <div key={group.id} role="group" aria-label={group.label ?? 'Main'}>
                  {group.label ? <div className={s.note}>{group.label}</div> : null}
                  {inGroup.map((id, i) => {
                    const place = places.find((p) => p.id === id)!;
                    const move = (stepBy: -1 | 1) => {
                      const next = moveNav(inGroup, id, stepBy);
                      let n = 0;
                      setOrder(ordered.map((x) => (inGroup.includes(x) ? next[n++] : x)));
                    };
                    return (
                      <div key={id} className={s.place}>
                        <input
                          type="checkbox"
                          checked={!gone.includes(id)}
                          disabled={id === '/player'}
                          aria-label={`${place.label} is in the sidebar`}
                          onChange={(e) =>
                            setGone((g) =>
                              e.target.checked ? g.filter((x) => x !== id) : [...g, id],
                            )
                          }
                          data-nav
                        />
                        <span>{place.label}</span>
                        <span className={k.editorMove}>
                          <button
                            type="button"
                            onClick={() => move(-1)}
                            disabled={i === 0}
                            aria-label={`Move ${place.label} up`}
                            data-nav
                          >
                            ↑
                          </button>
                          <button
                            type="button"
                            onClick={() => move(1)}
                            disabled={i === inGroup.length - 1}
                            aria-label={`Move ${place.label} down`}
                            data-nav
                          >
                            ↓
                          </button>
                        </span>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        ) : null}

        <div className={s.foot}>
          {error ? (
            <span className={s.error} role="alert">
              {error}
            </span>
          ) : null}
          <span className={s.grow} />
          {step === 0 && firstTime ? (
            <button
              type="button"
              className={k.btn}
              disabled={busy}
              onClick={() => void save(true)}
              data-nav
            >
              Use the defaults
            </button>
          ) : null}
          {!firstTime ? (
            <button type="button" className={k.btn} onClick={closePlayerSetup} data-nav>
              Close
            </button>
          ) : null}
          {step > 0 ? (
            <button type="button" className={k.btn} onClick={() => setStep(step - 1)} data-nav>
              Back
            </button>
          ) : null}
          {step < STEPS.length - 1 ? (
            <button
              type="button"
              className={k.btnPrimary}
              onClick={() => setStep(step + 1)}
              data-nav
            >
              Next
            </button>
          ) : null}
          {step === STEPS.length - 1 || !firstTime ? (
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
      </div>
    </div>,
  );
}
