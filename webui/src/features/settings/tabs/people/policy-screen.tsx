/**
 * Global policy: the most any member can have. The same rail as the
 * editor, minus Sidebar & look (presentation never grants). Each row is a
 * ceiling. Nothing saves until Save policy; before that a dry run says who
 * the change reaches and what each of them loses (PUT ?dry_run=1).
 */

import { useEffect, useMemo, useState } from 'react';

import type { PeopleData } from './data';
import type { PermMap, PermValue } from './types';

import { putPolicy } from './api';
import { CategoryRows, Rail, SaveBar } from './controls';
import { Icon } from './icons';
import { categoriesFor, changedRows, rowsFor, sameValue } from './model';
import css from './people.module.css';
import { useDryRun } from './use-dry-run';

const PANE_SUB: Record<string, string> = {
  general: 'What members may do with their own account, at most.',
  movies: 'What any member may do with movies, at most.',
  tv: 'What any member may do with shows, at most.',
  music: 'What any member may do with music, at most.',
  books: 'What any member may do with audiobooks and podcasts, at most.',
  requests: 'How far any member can go with requests.',
  admin: 'These belong to admins. Members get them only where you allow it here.',
};

export function PolicyScreen({ data, onSaved }: { data: PeopleData; onSaved: () => void }) {
  const { catalogue } = data;
  const categories = useMemo(() => categoriesFor(catalogue, 'P'), [catalogue]);
  const [category, setCategory] = useState(() =>
    categories.some((c) => c.id === 'movies') ? 'movies' : (categories[0]?.id ?? ''),
  );
  const [draft, setDraft] = useState<PermMap>(data.policy);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // a save (or someone else's) starts the draft again from what is saved;
  // a reload that changed nothing here keeps what is being edited
  const savedKey = JSON.stringify(data.policy);
  useEffect(() => setDraft(JSON.parse(savedKey) as PermMap), [savedKey]);

  const changed = useMemo(() => changedRows(data.policy, draft), [data.policy, draft]);
  const dirty = Object.keys(changed).length > 0;
  const dry = useDryRun(dirty ? changed : null, (set) => putPolicy({ set }, true));

  const limited = useMemo(() => {
    const out = new Set<string>();
    for (const c of categories) {
      if (
        rowsFor(catalogue, c.id, 'P').some(
          (r) => r.id in data.policy && !sameValue(data.policy[r.id], catalogue.open_policy[r.id]),
        )
      )
        out.add(c.id);
    }
    return out;
  }, [catalogue, categories, data.policy]);

  const current = categories.find((c) => c.id === category) ?? categories[0];
  const set = (id: string, value: PermValue) => {
    setSaveError(null);
    setDraft((d) => ({ ...d, [id]: value }));
  };
  const save = async () => {
    setSaving(true);
    setSaveError(null);
    const r = await putPolicy({ set: changed }, false);
    setSaving(false);
    if (!r.ok) {
      setSaveError(r.message);
      return;
    }
    window.showToast?.('Policy saved', 'success');
    onSaved();
  };

  if (!current) return null;
  return (
    <div className={css.split}>
      <Rail
        label="Policy categories"
        idPrefix="ppl-pol"
        categories={categories}
        current={current.id}
        marked={limited}
        markTitle="Limited here"
        onSelect={setCategory}
        note="Sidebar and look aren't here: they only change what shows, never what someone may do."
      />
      <div
        id="ppl-pol-pane"
        role="tabpanel"
        aria-labelledby={`ppl-pol-tab-${current.id}`}
        tabIndex={-1}
      >
        <div className={css.note}>
          <Icon name="admin" />
          <span>
            <b>The most any member can ever have.</b> Templates and personal changes stay inside
            these limits, and nothing here can be turned back on for one person. Admins are never
            limited.
          </span>
        </div>
        <div className={css.paneHead}>
          <div>
            <h2 className={css.h2}>{current.label}</h2>
            <p className={css.paneSub}>{PANE_SUB[current.id] ?? current.help}</p>
          </div>
        </div>
        <CategoryRows
          catalogue={catalogue}
          category={current.id}
          layer="P"
          values={draft}
          ceiling={null}
          quality={data.quality}
          onChange={set}
        />
        <SaveBar
          dirty={dirty}
          checking={dry.checking}
          impact={dry.impact}
          catalogue={catalogue}
          quality={data.quality}
          scope="policy"
          error={saveError ?? dry.error}
          saving={saving}
          saveLabel="Save policy"
          onDiscard={() => {
            setSaveError(null);
            setDraft(data.policy);
          }}
          onSave={() => void save()}
        />
      </div>
    </div>
  );
}
