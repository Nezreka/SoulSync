/**
 * customize home: a panel beside the page that changes it as you go.
 * every row can be turned on or off, moved up or down (buttons, so a remote
 * can do it too), and shown in a style it suits. genre rows can be added
 * from what's in the library. it saves for this screen, or for every screen
 * of the profile.
 */

import { useEffect, useState } from 'react';

import {
  genreRowId,
  moveRow,
  rowDef,
  type HomeLayout,
  type LayoutCatalog,
  type RowStyle,
} from '../-lib/home-layout';
import { ChevronDownIcon } from './icons';
import { styles as s } from './kit';
import { inPlayer } from './trailer';

export function HomeEditor({
  layout,
  catalog,
  scope,
  movieGenres,
  showGenres,
  onChange,
  onSave,
  onReset,
  onClose,
  title = 'Customize home',
  noBillboard,
}: {
  /** the panel's heading: "Customize home", "Customize Movies" */
  title?: string;
  /** a page with no billboard to switch */
  noBillboard?: boolean;
  layout: HomeLayout;
  catalog: LayoutCatalog | undefined;
  /** where the layout on show came from: this screen's own, the shared one, or the default */
  scope: 'device' | 'all' | null;
  movieGenres: string[];
  showGenres: string[];
  onChange: (layout: HomeLayout) => void;
  onSave: (where: 'device' | 'all') => Promise<void>;
  onReset: (what: 'device' | 'all') => Promise<void>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [adding, setAdding] = useState('');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopImmediatePropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  const set = (index: number, patch: Partial<HomeLayout['rows'][number]>) =>
    onChange({
      ...layout,
      rows: layout.rows.map((r, i) => (i === index ? { ...r, ...patch } : r)),
    });

  const run = async (label: string, job: () => Promise<void>) => {
    setBusy(true);
    try {
      await job();
      setSaved(label);
    } catch {
      setSaved('Could not save. Try again in a moment.');
    } finally {
      setBusy(false);
    }
  };

  const taken = new Set(layout.rows.map((r) => r.id));
  const genreOptions = [
    ...movieGenres.map((g) => ({ id: genreRowId('movie', g), label: `${g} movies` })),
    ...showGenres.map((g) => ({ id: genreRowId('show', g), label: `${g} shows` })),
  ].filter((o) => !taken.has(o.id));

  return inPlayer(
    <aside className={s.editor} role="dialog" aria-modal="false" aria-label={title}>
      <div className={s.editorHead}>
        <div>
          <h2 className={s.editorTitle}>{title}</h2>
          <p className={s.editorSub}>
            {scope === 'device'
              ? 'This screen has its own layout.'
              : scope === 'all'
                ? 'This screen uses your shared layout.'
                : 'This is the default layout.'}{' '}
            Changes show as you make them.
          </p>
        </div>
        <button type="button" className={s.btn} onClick={onClose} data-nav>
          Done
        </button>
      </div>

      <div className={s.editorBody}>
        {noBillboard ? null : (
          <label className={s.editorRow} data-on={layout.billboard ? 'true' : undefined}>
            <input
              type="checkbox"
              checked={layout.billboard}
              onChange={(e) => onChange({ ...layout, billboard: e.target.checked })}
              data-nav
            />
            <span className={s.editorRowText}>
              <span className={s.editorRowName}>Featured billboard</span>
              <span className={s.editorRowHint}>The big banner of what's new at the top</span>
            </span>
          </label>
        )}

        {layout.rows.map((row, i) => {
          const def = rowDef(catalog, row.id);
          if (!def) return null;
          return (
            <div key={row.id} className={s.editorRow} data-on={row.on ? 'true' : undefined}>
              <input
                type="checkbox"
                checked={row.on}
                onChange={(e) => set(i, { on: e.target.checked })}
                aria-label={`Show ${def.label}`}
                data-nav
              />
              <span className={s.editorRowText}>
                <span className={s.editorRowName}>{def.label}</span>
                <span className={s.editorRowHint}>{def.hint}</span>
              </span>
              {def.styles.length > 1 ? (
                <span className={s.editorSelect}>
                  <select
                    value={row.style}
                    onChange={(e) => set(i, { style: e.target.value as RowStyle })}
                    aria-label={`Style of ${def.label}`}
                    disabled={!row.on}
                    data-nav
                  >
                    {def.styles.map((st) => (
                      <option key={st} value={st}>
                        {catalog?.styles.find((x) => x.id === st)?.label ?? st}
                      </option>
                    ))}
                  </select>
                  <ChevronDownIcon />
                </span>
              ) : null}
              <span className={s.editorMove}>
                <button
                  type="button"
                  onClick={() => onChange(moveRow(layout, i, -1))}
                  disabled={i === 0}
                  aria-label={`Move ${def.label} up`}
                  data-nav
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={() => onChange(moveRow(layout, i, 1))}
                  disabled={i === layout.rows.length - 1}
                  aria-label={`Move ${def.label} down`}
                  data-nav
                >
                  ↓
                </button>
              </span>
              {row.id.startsWith('genre:') ? (
                <button
                  type="button"
                  className={s.editorRemove}
                  onClick={() =>
                    onChange({ ...layout, rows: layout.rows.filter((_, j) => j !== i) })
                  }
                  aria-label={`Remove ${def.label}`}
                  data-nav
                >
                  ×
                </button>
              ) : null}
            </div>
          );
        })}

        {genreOptions.length ? (
          <div className={s.editorAdd}>
            <span className={s.editorSelect}>
              <select
                value={adding}
                onChange={(e) => setAdding(e.target.value)}
                aria-label="Add a genre row"
                data-nav
              >
                <option value="">Add a genre row…</option>
                {genreOptions.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </select>
              <ChevronDownIcon />
            </span>
            <button
              type="button"
              className={s.btn}
              disabled={!adding}
              onClick={() => {
                onChange({
                  ...layout,
                  rows: [{ id: adding, style: 'standard', on: true }, ...layout.rows],
                });
                setAdding('');
              }}
              data-nav
            >
              Add to the top
            </button>
          </div>
        ) : null}
      </div>

      <div className={s.editorFoot}>
        {saved ? (
          <span className={s.editorSaved} role="status">
            {saved}
          </span>
        ) : null}
        <div className={s.editorButtons}>
          <button
            type="button"
            className={s.btnPrimary}
            disabled={busy}
            onClick={() => void run('Saved for this screen', () => onSave('device'))}
            data-nav
          >
            Save for this screen
          </button>
          <button
            type="button"
            className={s.btn}
            disabled={busy}
            onClick={() => void run('Saved for all your screens', () => onSave('all'))}
            data-nav
          >
            Save for all my screens
          </button>
        </div>
        <div className={s.editorButtons}>
          {scope === 'device' ? (
            <button
              type="button"
              className={s.moreLink}
              disabled={busy}
              onClick={() =>
                void run('This screen uses your shared layout again', () => onReset('device'))
              }
              data-nav
            >
              Use my shared layout here
            </button>
          ) : null}
          <button
            type="button"
            className={s.moreLink}
            disabled={busy}
            onClick={() => void run('Back to the default on every screen', () => onReset('all'))}
            data-nav
          >
            Reset to default
          </button>
        </div>
      </div>
    </aside>,
  );
}
