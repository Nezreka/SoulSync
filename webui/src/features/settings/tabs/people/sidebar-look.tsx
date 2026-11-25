/**
 * A template's Sidebar & look: what shows in the sidebar of the people who
 * follow it, out of what the template lets them use, and whether they may
 * change it. Presentation only: it never grants anything. This is where
 * "What new profiles see" went; the migration copied that setting into
 * every template's sidebar, so nothing was lost.
 */

import type { Catalogue, Feature, FeatureCatalogue, PermMap, Sidebar, Template } from './types';

import { Seg, Switch, LockLine } from './controls';
import { Icon } from './icons';
import css from './people.module.css';

/** whether a template lets its people use a part at all */
export function featureUsable(feature: Feature, perms: PermMap, catalogue: Catalogue): boolean {
  if (feature.id === 'video') return perms['movies.access'] === true || perms['tv.access'] === true;
  if (feature.id === 'music_videos')
    return perms['music.access'] === true && perms['music.videos'] === true;
  const owners = new Map<string, string>();
  for (const row of catalogue.rows) for (const page of row.pages) owners.set(page, row.id);
  const rows = catalogue.rows;
  const on = (id: string) => {
    if (perms[id] !== true) return false;
    const needs = rows.find((r) => r.id === id)?.needs ?? [];
    return !needs.length || needs.some((n) => perms[n] === true);
  };
  const owned = (feature.pages ?? []).map((p) => owners.get(p)).filter(Boolean) as string[];
  return !owned.length || owned.some(on);
}

export function SidebarLook({
  template,
  perms,
  catalogue,
  features,
  error,
  value,
  onChange,
}: {
  template: Template;
  perms: PermMap;
  catalogue: Catalogue;
  features: FeatureCatalogue | null;
  error: string | null;
  value: Sidebar;
  onChange: (next: Sidebar) => void;
}) {
  const note = (
    <div className={`${css.note} ${css.noteInfo}`}>
      <Icon name="info" />
      <span>
        What shows in their sidebar, out of what {template.name} allows.{' '}
        <b>It never grants anything.</b> This replaces &quot;What new profiles see&quot;: every
        template starts with its own sidebar.
      </span>
    </div>
  );
  if (error) {
    return (
      <>
        {note}
        <p className={css.error}>{error}</p>
      </>
    );
  }
  if (!features) {
    return (
      <>
        {note}
        <p className={css.t2}>Loading the parts of SoulSync...</p>
      </>
    );
  }
  const off = new Set(value.off);
  const preset = features.presets.find(
    (p) => p.off.length === off.size && p.off.every((id) => off.has(id)),
  );
  const startOptions = features.presets
    .slice()
    // Everything first, as the mockup has it
    .sort((a, b) => Number(b.id === 'full') - Number(a.id === 'full'))
    .map((p) => ({ value: p.id, label: p.label }));
  if (!preset) startOptions.push({ value: 'custom', label: 'Your own mix' });
  const toggle = (id: string, show: boolean) => {
    const next = show ? value.off.filter((x) => x !== id) : [...value.off, id];
    onChange({ ...value, off: features.features.map((f) => f.id).filter((f) => next.includes(f)) });
  };
  const groups = features.groups.length ? features.groups : [{ id: '', label: 'Parts' }];

  return (
    <>
      {note}
      <div className={css.group}>
        <div className={css.row}>
          <div>
            <div className={css.rowT}>Start from</div>
            <div className={css.rowD}>
              {preset?.desc ?? 'Your own mix of parts. Pick one to start over.'}
            </div>
          </div>
          <div className={css.rowCtl}>
            <Seg
              label="Start from"
              options={startOptions.map((o) => ({ ...o, disabled: o.value === 'custom' }))}
              value={preset?.id ?? 'custom'}
              onChange={(id) => {
                const p = features.presets.find((x) => x.id === id);
                if (p) onChange({ ...value, off: [...p.off] });
              }}
            />
          </div>
        </div>
        <div className={css.row}>
          <div>
            <div className={css.rowT}>Let them change it</div>
            <div className={css.rowD}>
              Off: they see exactly this and Customize goes away for them.
            </div>
          </div>
          <div className={css.rowCtl}>
            <Switch
              checked={!value.locked}
              label="Let them change it"
              onChange={(next) => onChange({ ...value, locked: !next })}
            />
          </div>
        </div>
      </div>
      {groups.map((g) => {
        const list = features.features.filter(
          (f) => (f.group ?? '') === g.id || (!g.id && !f.group),
        );
        if (!list.length) return null;
        return (
          <div key={g.id || 'all'}>
            <div className={css.sect}>{g.label}</div>
            <div className={css.group}>
              {list.map((f) => {
                const usable = featureUsable(f, perms, catalogue);
                return (
                  <div key={f.id} className={`${css.row} ${usable ? '' : css.dim}`}>
                    <div>
                      <div className={css.rowT}>{f.label}</div>
                      {f.desc ? <div className={css.rowD}>{f.desc}</div> : null}
                      {usable ? null : (
                        <LockLine>Not part of {template.name}, so it never shows.</LockLine>
                      )}
                    </div>
                    <div className={css.rowCtl}>
                      <Switch
                        checked={usable && !off.has(f.id)}
                        label={`Show ${f.label}`}
                        disabled={!usable}
                        onChange={(next) => toggle(f.id, next)}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </>
  );
}
