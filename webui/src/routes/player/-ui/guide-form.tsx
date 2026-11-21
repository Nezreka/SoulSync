/**
 * make a channel (or change one): what goes on it, said in plain terms,
 * with what it would air counted as you go. movies, tv or one show start to
 * finish; genres, decades, studios and networks, a film series, a lowest
 * score, only what's new, only what's fine for kids; shuffled or in order.
 * an admin can make it for everyone on the server; anyone can make their own.
 */

import { useMemo, useState, type ReactNode } from 'react';

import type { VideoCatalog } from '../-player.types';

import { EMPTY_SPEC, specPool, type ChannelSpec, type SavedChannel } from '../-lib/channels';
import { guideEdits } from '../-player.api';
import f from './guide-form.module.css';
import { Sheet } from './sheet';
import sh from './sheet.module.css';

const DECADES = [1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020];
const NEW_DAYS: [number | null, string][] = [
  [null, 'Anytime'],
  [7, 'This week'],
  [30, 'This month'],
  [90, 'Last 3 months'],
  [365, 'This year'],
];
const SCORES: [number | null, string][] = [
  [null, 'Any'],
  [6, '6+'],
  [7, '7+'],
  [8, '8+'],
];

function Chips<T extends string | number>({
  options,
  picked,
  onToggle,
  label,
}: {
  options: readonly { value: T; label: string }[];
  picked: readonly T[];
  onToggle: (value: T) => void;
  label: string;
}) {
  return (
    <div className={f.chips} role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          className={f.chip}
          aria-pressed={picked.includes(o.value)}
          onClick={() => onToggle(o.value)}
          data-nav
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className={f.field}>
      <div className={f.fieldHead}>
        <span className={f.fieldLabel}>{label}</span>
        {hint ? <span className={f.fieldHint}>{hint}</span> : null}
      </div>
      {children}
    </div>
  );
}

function toggle<T>(list: readonly T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

export function ChannelForm({
  catalog,
  editing,
  canServer,
  onSaved,
  onCancel,
}: {
  catalog: VideoCatalog;
  /** a channel being changed; absent: a new one */
  editing?: SavedChannel | null;
  /** an admin: may make it for everyone */
  canServer: boolean;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [spec, setSpec] = useState<ChannelSpec>(editing?.spec ?? EMPTY_SPEC);
  const [scope, setScope] = useState<'mine' | 'server'>(editing?.scope ?? 'mine');
  const [mode, setMode] = useState<'library' | 'show'>(editing?.spec.show ? 'show' : 'library');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (next: Partial<ChannelSpec>) => setSpec((s) => ({ ...s, ...next }));

  const options = useMemo(() => {
    const count = (xs: (string | null | undefined)[]) => {
      const m = new Map<string, number>();
      for (const x of xs) if (x) m.set(x, (m.get(x) ?? 0) + 1);
      return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
    };
    return {
      genres: count([...catalog.movies, ...catalog.shows].flatMap((t) => t.genres)).slice(0, 24),
      studios: count(catalog.movies.map((m) => m.studio)).slice(0, 18),
      networks: count(catalog.shows.map((x) => x.network)).slice(0, 18),
      collections: count(catalog.movies.map((m) => m.collection ?? null)).filter(Boolean),
      shows: [...catalog.shows].sort((a, b) => a.title.localeCompare(b.title)),
    };
  }, [catalog]);

  // what it would air, counted as you choose (episodes are the catalog's sample per show)
  const now = useMemo(() => Date.now(), []);
  const airs = useMemo(() => {
    if (mode === 'show') return null;
    const pool = specPool({ ...spec, show: null }, catalog, now);
    const movies = pool.filter((p) => p.kind === 'movie').length;
    const shows = new Set(pool.filter((p) => p.kind === 'episode').map((p) => p.href)).size;
    return { movies, shows, total: pool.length };
  }, [spec, catalog, mode, now]);

  const save = async () => {
    const clean: ChannelSpec =
      mode === 'show'
        ? { ...EMPTY_SPEC, name: spec.name, show: spec.show, kinds: [], order: spec.order }
        : { ...spec, show: null };
    if (!clean.name.trim()) return setError('Give it a name.');
    if (mode === 'show' && !clean.show) return setError('Pick a show.');
    if (mode === 'library' && !clean.kinds.length) return setError('Pick movies, TV or both.');
    setBusy(true);
    setError(null);
    try {
      if (editing) await guideEdits.edit(editing.id, clean);
      else await guideEdits.add(scope, clean);
      onSaved();
    } catch {
      setError("Couldn't save that channel. Try again.");
      setBusy(false);
    }
  };

  const tooFew = airs && airs.total < 3;
  return (
    <Sheet
      wide
      title={editing ? `Change ${editing.spec.name}` : 'Make a channel'}
      subtitle="It airs around the clock in the guide, made from your library."
      onClose={onCancel}
      footer={
        <>
          <span className={f.count} role="status">
            {mode === 'show'
              ? spec.show
                ? 'Every episode, in order, around the clock'
                : 'Pick a show'
              : airs
                ? tooFew
                  ? 'Too little matches yet: loosen it a little'
                  : `${airs.movies ? `${airs.movies} movies` : ''}${airs.movies && airs.shows ? ' and ' : ''}${airs.shows ? `${airs.shows} shows` : ''} match`
                : ''}
          </span>
          <button type="button" className={sh.btn} onClick={onCancel} data-nav>
            Cancel
          </button>
          <button
            type="button"
            className={sh.btnPrimary}
            disabled={busy || !!tooFew}
            onClick={() => void save()}
            data-nav
          >
            {busy ? 'Saving…' : editing ? 'Save' : 'Make it'}
          </button>
        </>
      }
    >
      <div className={f.form}>
        <label className={sh.field}>
          Name
          <input
            className={sh.input}
            value={spec.name}
            maxLength={40}
            placeholder="Saturday matinee"
            onChange={(e) => set({ name: e.target.value })}
          />
        </label>

        <Field label="What it airs">
          <Chips
            label="What it airs"
            options={[
              { value: 'library', label: 'From the library' },
              { value: 'show', label: 'One show, start to finish' },
            ]}
            picked={[mode]}
            onToggle={(v) => setMode(v as 'library' | 'show')}
          />
        </Field>

        {mode === 'show' ? (
          <>
            <Field label="The show">
              <select
                className={sh.input}
                value={spec.show ?? ''}
                onChange={(e) => {
                  const id = Number(e.target.value) || null;
                  const title = options.shows.find((x) => x.id === id)?.title;
                  set({ show: id, name: spec.name || (title ? `${title} marathon` : '') });
                }}
                aria-label="The show"
              >
                <option value="">Pick a show…</option>
                {options.shows.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.title}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Order">
              <Chips
                label="Order"
                options={[
                  { value: 'in_order', label: 'In order' },
                  { value: 'shuffle', label: 'Shuffled' },
                ]}
                picked={[spec.order]}
                onToggle={(v) => set({ order: v as ChannelSpec['order'] })}
              />
            </Field>
          </>
        ) : (
          <>
            <Field label="Movies or TV">
              <Chips
                label="Movies or TV"
                options={[
                  { value: 'movies', label: 'Movies' },
                  { value: 'tv', label: 'TV' },
                ]}
                picked={spec.kinds}
                onToggle={(v) => set({ kinds: toggle(spec.kinds, v as 'movies' | 'tv') })}
              />
            </Field>
            <Field label="Genres" hint="any of these">
              <Chips
                label="Genres"
                options={options.genres.map((g) => ({ value: g, label: g }))}
                picked={spec.genres}
                onToggle={(v) => set({ genres: toggle(spec.genres, v) })}
              />
            </Field>
            <Field label="Decades" hint="any of these">
              <Chips
                label="Decades"
                options={DECADES.map((d) => ({ value: d, label: `${d}s` }))}
                picked={spec.decades}
                onToggle={(v) => set({ decades: toggle(spec.decades, v) })}
              />
            </Field>
            {spec.kinds.includes('movies') && options.studios.length ? (
              <Field label="Studios" hint="movies from any of these">
                <Chips
                  label="Studios"
                  options={options.studios.map((x) => ({ value: x, label: x }))}
                  picked={spec.studios}
                  onToggle={(v) => set({ studios: toggle(spec.studios, v) })}
                />
              </Field>
            ) : null}
            {spec.kinds.includes('tv') && options.networks.length ? (
              <Field label="Networks" hint="shows from any of these">
                <Chips
                  label="Networks"
                  options={options.networks.map((x) => ({ value: x, label: x }))}
                  picked={spec.networks}
                  onToggle={(v) => set({ networks: toggle(spec.networks, v) })}
                />
              </Field>
            ) : null}
            {spec.kinds.includes('movies') && options.collections.length ? (
              <Field label="A film series">
                <select
                  className={sh.input}
                  value={spec.collection ?? ''}
                  onChange={(e) => set({ collection: e.target.value || null })}
                  aria-label="A film series"
                >
                  <option value="">Any</option>
                  {options.collections.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
            <div className={f.row}>
              <Field label="Lowest score">
                <Chips
                  label="Lowest score"
                  options={SCORES.map(([v, l]) => ({ value: String(v), label: l }))}
                  picked={[String(spec.min_score)]}
                  onToggle={(v) => set({ min_score: v === 'null' ? null : Number(v) })}
                />
              </Field>
              <Field label="Added">
                <Chips
                  label="Added"
                  options={NEW_DAYS.map(([v, l]) => ({ value: String(v), label: l }))}
                  picked={[String(spec.added_days)]}
                  onToggle={(v) => set({ added_days: v === 'null' ? null : Number(v) })}
                />
              </Field>
            </div>
            <div className={f.row}>
              <Field label="Order">
                <Chips
                  label="Order"
                  options={[
                    { value: 'shuffle', label: 'Shuffled' },
                    { value: 'in_order', label: 'In order' },
                  ]}
                  picked={[spec.order]}
                  onToggle={(v) => set({ order: v as ChannelSpec['order'] })}
                />
              </Field>
              <Field label="Kids">
                <Chips
                  label="Kids"
                  options={[{ value: 'kids', label: 'Only what’s fine for kids' }]}
                  picked={spec.kids ? ['kids'] : []}
                  onToggle={() => set({ kids: !spec.kids })}
                />
              </Field>
            </div>
          </>
        )}

        {canServer && !editing ? (
          <Field label="Who gets it">
            <Chips
              label="Who gets it"
              options={[
                { value: 'mine', label: 'Just me' },
                { value: 'server', label: 'Everyone on this server' },
              ]}
              picked={[scope]}
              onToggle={(v) => setScope(v as 'mine' | 'server')}
            />
          </Field>
        ) : null}
        {error ? <p className={sh.error}>{error}</p> : null}
      </div>
    </Sheet>
  );
}
