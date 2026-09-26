import { useEffect, useId, useState } from 'react';

import type { RecipeForm, RecipeMixCard } from '../-discover.recipes';

import {
  deleteRecipe,
  formFromRecipe,
  RECIPE_FORM_DEFAULTS,
  RECIPE_SCHEDULES,
  recipeFormProblem,
  recipeFromForm,
  saveRecipe,
} from '../-discover.recipes';

/**
 * Build or edit a mix recipe: what it's made of, how much of each source,
 * how long, how often it renews. The server applies the rules (one song per
 * artist, shortfalls redistributed, a reserve for failed downloads).
 */
export function RecipeEditor({
  editing,
  onClose,
  onSaved,
}: {
  /** The recipe being edited; absent for a new one. */
  editing?: RecipeMixCard | null;
  onClose: () => void;
  /** Saved or deleted: the caller refreshes the mixes. */
  onSaved: (message: string) => void;
}) {
  const titleId = useId();
  const [form, setForm] = useState<RecipeForm>(() =>
    editing ? formFromRecipe(editing.recipe) : RECIPE_FORM_DEFAULTS,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const set = <K extends keyof RecipeForm>(key: K, value: RecipeForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const save = () => {
    const problem = recipeFormProblem(form);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError('');
    saveRecipe(recipeFromForm(form), editing?.recipe_id)
      .then((res) => {
        if (res.success === false) throw new Error(res.error || "Couldn't save the mix.");
        onSaved(editing ? `Updated ${form.name.trim()}` : `Built ${form.name.trim()}`);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Couldn't save the mix."))
      .finally(() => setBusy(false));
  };

  const remove = () => {
    if (!editing) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setBusy(true);
    deleteRecipe(editing.recipe_id)
      .then((res) => {
        if (res.success === false) throw new Error(res.error || "Couldn't delete the mix.");
        onSaved(`Deleted ${editing.name}`);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Couldn't delete the mix."))
      .finally(() => setBusy(false));
  };

  const share = (key: 'library' | 'discovery' | 'trending', label: string, hint: string) => (
    <label className="recipe-share">
      <span className="recipe-share-label">
        {label} <span className="recipe-share-value">{form[key]}%</span>
      </span>
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={form[key]}
        aria-label={`${label} share`}
        onChange={(e) => set(key, Number(e.target.value))}
      />
      <span className="recipe-hint">{hint}</span>
    </label>
  );

  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="recipe-modal" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="recipe-modal-header">
          <h2 id={titleId}>{editing ? 'Edit mix' : 'Build a mix'}</h2>
          <p>
            It renews on its own, one song per artist, and keeps spares for downloads that fail.
          </p>
          <button type="button" className="watch-all-close" aria-label="Close" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="recipe-modal-body">
          <label className="recipe-field">
            <span>Name</span>
            <input
              type="text"
              value={form.name}
              maxLength={80}
              placeholder="Late night prog"
              onChange={(e) => set('name', e.target.value)}
            />
          </label>
          <label className="recipe-field">
            <span>Seed artists</span>
            <input
              type="text"
              value={form.seeds}
              placeholder="Tool, Deftones"
              onChange={(e) => set('seeds', e.target.value)}
            />
            <span className="recipe-hint">
              Comma-separated. The mix draws on them and artists like them.
            </span>
          </label>
          <label className="recipe-field">
            <span>Genres</span>
            <input
              type="text"
              value={form.genres}
              placeholder="progressive metal, shoegaze"
              onChange={(e) => set('genres', e.target.value)}
            />
          </label>
          <div className="recipe-row">
            <label className="recipe-field">
              <span>From year</span>
              <input
                type="number"
                inputMode="numeric"
                value={form.yearFrom}
                placeholder="Any"
                onChange={(e) => set('yearFrom', e.target.value)}
              />
            </label>
            <label className="recipe-field">
              <span>To year</span>
              <input
                type="number"
                inputMode="numeric"
                value={form.yearTo}
                placeholder="Any"
                onChange={(e) => set('yearTo', e.target.value)}
              />
            </label>
          </div>
          <fieldset className="recipe-shares">
            <legend>What it's made of</legend>
            {share('library', 'Your library', 'Tracks you already have.')}
            {share('discovery', 'Discovery', 'New tracks by artists like your seeds.')}
            {share('trending', 'Trending', 'The most popular new tracks in the mix.')}
            <span className="recipe-hint">
              A source that comes up short hands its share to the others.
            </span>
          </fieldset>
          <div className="recipe-row">
            <label className="recipe-field">
              <span>Length</span>
              <input
                type="number"
                min={10}
                max={100}
                value={form.length}
                onChange={(e) => set('length', Number(e.target.value) || 10)}
              />
            </label>
            <label className="recipe-field">
              <span>Renews</span>
              <select
                value={form.schedule}
                onChange={(e) => set('schedule', e.target.value as RecipeForm['schedule'])}
              >
                {RECIPE_SCHEDULES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {error ? (
            <p className="recipe-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="recipe-modal-footer">
          {editing ? (
            <button
              type="button"
              className={`watch-all-btn recipe-delete${confirmDelete ? ' armed' : ''}`}
              disabled={busy}
              onClick={remove}
              onBlur={() => setConfirmDelete(false)}
            >
              {confirmDelete ? 'Delete this mix?' : 'Delete'}
            </button>
          ) : null}
          <button type="button" className="watch-all-btn watch-all-btn-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="watch-all-btn watch-all-btn-primary"
            disabled={busy}
            onClick={save}
          >
            {busy ? 'Saving…' : editing ? 'Save' : 'Build it'}
          </button>
        </div>
      </div>
    </div>
  );
}
