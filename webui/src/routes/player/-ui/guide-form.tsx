/** make a channel: a rule over the library (kinds, genre, decade, kids) */

import { useState, type FormEvent } from 'react';

import type { ChannelRule } from '../-lib/channels';

import { styles as s } from './kit';

export function ChannelForm({
  genres,
  onSave,
  onCancel,
}: {
  genres: string[];
  onSave: (rule: ChannelRule) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [movies, setMovies] = useState(true);
  const [tv, setTv] = useState(true);
  const [genre, setGenre] = useState('');
  const [decade, setDecade] = useState('');
  const [kids, setKids] = useState(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const kinds = [...(movies ? (['movies'] as const) : []), ...(tv ? (['tv'] as const) : [])];
    if (!name.trim() || !kinds.length) return;
    onSave({
      id: `${Date.now().toString(36)}`,
      name: name.trim().slice(0, 40),
      kinds,
      genre: genre || null,
      decade: decade ? Number(decade) : null,
      kidsOnly: kids,
    });
  };
  return (
    <form className={s.guideDetail} onSubmit={submit} aria-label="Make a channel">
      <div className={s.fieldRow}>
        <label className={s.field}>
          Name
          <input
            className={s.fieldInput}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            data-nav
          />
        </label>
        <label className={s.check}>
          <input
            type="checkbox"
            checked={movies}
            onChange={(e) => setMovies(e.target.checked)}
            data-nav
          />{' '}
          Movies
        </label>
        <label className={s.check}>
          <input type="checkbox" checked={tv} onChange={(e) => setTv(e.target.checked)} data-nav />{' '}
          TV
        </label>
        <label className={s.field}>
          Genre
          <select
            className={s.fieldInput}
            value={genre}
            onChange={(e) => setGenre(e.target.value)}
            data-nav
          >
            <option value="">Any</option>
            {genres.map((g) => (
              <option key={g}>{g}</option>
            ))}
          </select>
        </label>
        <label className={s.field}>
          Decade
          <select
            className={s.fieldInput}
            value={decade}
            onChange={(e) => setDecade(e.target.value)}
            data-nav
          >
            <option value="">Any</option>
            {[1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020].map((d) => (
              <option key={d} value={d}>
                {d}s
              </option>
            ))}
          </select>
        </label>
        <label className={s.check}>
          <input
            type="checkbox"
            checked={kids}
            onChange={(e) => setKids(e.target.checked)}
            data-nav
          />{' '}
          Kids only
        </label>
      </div>
      <div className={s.heroActions}>
        <button type="button" className={s.btn} onClick={onCancel} data-nav>
          Cancel
        </button>
        <button type="submit" className={s.btnPrimary} data-nav>
          Add channel
        </button>
      </div>
      <span className={s.notYet}>Your channels are kept on this device.</span>
    </form>
  );
}
