/**
 * The controls Global policy and Templates share: the category rail, a
 * category's rows (one control per permission kind), the quality list and
 * the save bar with its impact line. The per-person editor (piece E) has
 * its own sheet; these follow the same mockup.
 */

import { type KeyboardEvent, type ReactNode, useId, useRef } from 'react';

import type {
  Catalogue,
  CatalogueRow,
  Category,
  Count,
  Impact,
  PermMap,
  PermValue,
  QualityOption,
  QualityOptions,
} from './types';

import { Icon } from './icons';
import {
  MODES,
  RATINGS,
  aboveCeiling,
  capLine,
  idOutside,
  impactLine,
  isCount,
  modeLabel,
  needsLine,
  periodWords,
  qualityFor,
  ratingLabel,
  rowById,
  rowsFor,
  str,
} from './model';
import css from './people.module.css';

export type Layer = 'P' | 'T';

// ── small parts ──

export function Switch({
  checked,
  label,
  disabled,
  small,
  warn,
  onChange,
}: {
  checked: boolean;
  label: string;
  disabled?: boolean;
  small?: boolean;
  warn?: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={[css.sw, small ? css.swSmall : '', warn ? css.swWarn : ''].join(' ')}
      onClick={() => onChange(!checked)}
    />
  );
}

export function Seg<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: Array<{ value: T; label: string; disabled?: boolean }>;
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div className={css.seg} role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={css.segB}
          aria-pressed={o.value === value}
          disabled={o.disabled}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function LockLine({ children }: { children: ReactNode }) {
  return (
    <div className={css.prov}>
      <span className={css.provLock}>
        <Icon name="lock" />
        {children}
      </span>
    </div>
  );
}

// ── the rail ──

export function Rail({
  label,
  categories,
  current,
  marked,
  markTitle,
  onSelect,
  note,
  idPrefix,
}: {
  label: string;
  categories: Category[];
  current: string;
  marked: Set<string>;
  markTitle: string;
  onSelect: (id: string) => void;
  note?: ReactNode;
  idPrefix: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const move = (event: KeyboardEvent, index: number) => {
    const keys: Record<string, number> = {
      ArrowDown: 1,
      ArrowRight: 1,
      ArrowUp: -1,
      ArrowLeft: -1,
    };
    let next = -1;
    if (event.key in keys) next = (index + keys[event.key] + categories.length) % categories.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = categories.length - 1;
    if (next < 0) return;
    event.preventDefault();
    const id = categories[next].id;
    onSelect(id);
    refs.current[id]?.focus();
  };
  return (
    <div className={css.rail} role="tablist" aria-label={label} aria-orientation="vertical">
      {categories.map((c, i) => (
        <button
          key={c.id}
          ref={(el) => {
            refs.current[c.id] = el;
          }}
          type="button"
          role="tab"
          id={`${idPrefix}-tab-${c.id}`}
          aria-controls={`${idPrefix}-pane`}
          aria-selected={c.id === current}
          tabIndex={c.id === current ? 0 : -1}
          className={css.railB}
          onClick={() => onSelect(c.id)}
          onKeyDown={(e) => move(e, i)}
        >
          <Icon name={c.id} />
          {c.label}
          {marked.has(c.id) ? <span className={css.mk} title={markTitle} /> : null}
          {marked.has(c.id) ? <span className={css.srOnly}>{markTitle}</span> : null}
        </button>
      ))}
      {note ? (
        <>
          <div className={css.railSep} />
          <p className={css.railNote}>{note}</p>
        </>
      ) : null}
    </div>
  );
}

// ── one row's control ──

const COUNT_N = [0, 1, 2, 3, 5, 10, 15, 20, 30, 50, 100];
const COUNT_DAYS = [1, 7, 30];

function CountControl({
  row,
  value,
  disabled,
  onChange,
}: {
  row: CatalogueRow;
  value: Count;
  disabled?: boolean;
  onChange: (v: Count) => void;
}) {
  const ns = COUNT_N.includes(value.n) ? COUNT_N : [...COUNT_N, value.n].sort((a, b) => a - b);
  const days = COUNT_DAYS.includes(value.days)
    ? COUNT_DAYS
    : [...COUNT_DAYS, value.days].sort((a, b) => a - b);
  return (
    <>
      <select
        className={css.sel}
        aria-label={`${row.label}: how many`}
        value={value.n}
        disabled={disabled}
        onChange={(e) => onChange({ ...value, n: Number(e.target.value) })}
      >
        {ns.map((n) => (
          <option key={n} value={n}>
            {n === 0 ? 'No limit' : `${n} ${n === 1 ? 'ask' : 'asks'}`}
          </option>
        ))}
      </select>
      {value.n ? (
        <select
          className={css.sel}
          aria-label={`${row.label}: per`}
          value={value.days}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, days: Number(e.target.value) })}
        >
          {days.map((d) => (
            <option key={d} value={d}>
              {d === 1 ? 'per day' : d === 7 ? 'per week' : d === 30 ? 'per month' : periodWords(d)}
            </option>
          ))}
        </select>
      ) : null}
    </>
  );
}

const MODE_HELP: Record<string, string> = {
  download: 'Download right away: it starts as soon as they pick it.',
  ask: 'Ask first: what they pick lands in your Requests.',
  none: "Can't request: they can look, but not ask.",
};

const POLICY_TITLE: Partial<Record<string, (row: CatalogueRow) => string>> = {
  mode: (row) => `${row.label}, at most`,
  rating: (row) =>
    row.id.startsWith('tv.') ? 'Highest TV rating anyone gets' : 'Highest rating anyone gets',
  count: () => 'Request limit, at most',
};

function policyHelp(row: CatalogueRow): string {
  switch (row.kind) {
    case 'mode':
      return 'Ask first means you approve every one, even for people whose template says download.';
    case 'rating':
      return row.id.startsWith('tv.')
        ? 'Same as movies keeps shows at the movie limit for every member.'
        : 'Titles above it, or with no rating, stay out of sight for every member.';
    case 'count':
      return 'No template or person can go above this. An album, a movie or a season is one ask.';
    case 'restrict':
      return 'On: left out for every member, whatever their template says.';
    case 'bool':
      return `${row.help} Off: only admins get it.`;
    default:
      return row.help;
  }
}

function RowControl({
  row,
  value,
  ceiling,
  layer,
  disabled,
  onChange,
}: {
  row: CatalogueRow;
  value: PermValue;
  ceiling: PermValue | undefined;
  layer: Layer;
  disabled: boolean;
  onChange: (v: PermValue) => void;
}) {
  const capped = layer === 'T' && ceiling !== undefined && aboveCeiling(row, value, ceiling);
  switch (row.kind) {
    case 'bool':
    case 'restrict': {
      const forced = row.kind === 'restrict' && layer === 'T' && ceiling === true;
      const shown = row.kind === 'bool' ? value === true && !capped : value === true || forced;
      return (
        <Switch
          checked={shown}
          label={row.label}
          disabled={disabled || capped || forced}
          onChange={(next) => onChange(next)}
        />
      );
    }
    case 'mode': {
      const options = MODES.filter((m) => row.values.includes(m)).map((m) => ({
        value: m,
        label: modeLabel(row, m, layer),
        disabled:
          disabled ||
          (layer === 'T' && ceiling !== undefined && aboveCeiling(row, m, ceiling) && m !== value),
      }));
      return (
        <Seg
          label={row.label}
          options={options}
          value={str(value) as (typeof MODES)[number]}
          onChange={(m) => onChange(m)}
        />
      );
    }
    case 'rating': {
      const tv = row.id.startsWith('tv.');
      const values = tv ? ['follow', ...RATINGS] : RATINGS;
      return (
        <select
          className={css.sel}
          aria-label={row.label}
          value={str(value ?? 'any')}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        >
          {values.map((r) => (
            <option
              key={r}
              value={r}
              disabled={
                layer === 'T' &&
                ceiling !== undefined &&
                r !== value &&
                aboveCeiling(row, r, ceiling)
              }
            >
              {ratingLabel(r, tv)}
            </option>
          ))}
        </select>
      );
    }
    case 'count':
      return (
        <CountControl
          row={row}
          value={isCount(value) ? value : { n: 0, days: 7 }}
          disabled={disabled}
          onChange={onChange}
        />
      );
    default:
      return null;
  }
}

// ── quality ──

function ids(value: PermValue, options: QualityOption[]): number[] {
  if (value === '*') return options.map((o) => o.id);
  return Array.isArray(value) ? (value.filter((v) => typeof v === 'number') as number[]) : [];
}

function tagClass(tag: string) {
  return tag === '4k' ? css.tagGold : css.tagInfo;
}

function QualityGroup({
  setRow,
  choiceRow,
  flagRow,
  values,
  ceiling,
  layer,
  quality,
  disabled,
  onChange,
}: {
  setRow: CatalogueRow;
  choiceRow?: CatalogueRow;
  flagRow?: CatalogueRow;
  values: PermMap;
  ceiling: PermMap | null;
  layer: Layer;
  quality: QualityOptions | null;
  disabled: boolean;
  onChange: (id: string, v: PermValue) => void;
}) {
  const options = qualityFor(setRow, quality);
  const value = values[setRow.id];
  const chosen = ids(value, options);
  const ceil = ceiling?.[setRow.id];
  const flags = flagRow ? values[flagRow.id] : undefined;
  const flagList = Array.isArray(flags) ? flags : [];
  const ceilFlags = flagRow && ceiling ? ceiling[flagRow.id] : undefined;
  const policyAsks = (o: QualityOption) =>
    Array.isArray(ceilFlags) &&
    (ceilFlags.includes(o.id) || (o.tags ?? []).some((t) => ceilFlags.includes(t)));

  const toggle = (id: number, on: boolean) => {
    const next = on ? [...new Set([...chosen, id])] : chosen.filter((x) => x !== id);
    const all = options.every((o) => next.includes(o.id));
    onChange(setRow.id, all ? '*' : options.filter((o) => next.includes(o.id)).map((o) => o.id));
  };
  const flag = (id: number, on: boolean) => {
    if (!flagRow) return;
    const next = on ? [...new Set([...flagList, id])] : flagList.filter((x) => x !== id);
    onChange(flagRow.id, next);
  };
  const choice = choiceRow ? values[choiceRow.id] : null;
  const outsideOf = (o: QualityOption) =>
    layer === 'T' && ceil !== undefined && idOutside(ceil, o.id);
  const allowed = options.filter((o) => chosen.includes(o.id) && !outsideOf(o));

  if (!options.length) {
    return (
      <div className={css.row}>
        <div>
          <div className={css.rowT}>{setRow.label}</div>
          <div className={css.rowD}>No quality profiles for this kind of title yet.</div>
        </div>
      </div>
    );
  }
  return (
    <>
      <div className={css.qlist}>
        {options.map((o) => {
          const outside = outsideOf(o);
          const on = chosen.includes(o.id) && !outside;
          const asks = policyAsks(o);
          return (
            <div className={css.q} key={o.id}>
              <button
                type="button"
                role="checkbox"
                className={css.cb}
                aria-checked={on}
                aria-label={o.name}
                disabled={disabled || outside}
                onClick={() => toggle(o.id, !on)}
              />
              <div>
                <div className={css.qn}>
                  {o.name}
                  {(o.tags ?? []).map((t) => (
                    <span key={t} className={`${css.tag} ${tagClass(t)}`}>
                      {t === '4k' ? '4K' : t.charAt(0).toUpperCase() + t.slice(1)}
                    </span>
                  ))}
                  {o.is_default || (o.id === 0 && o.name === 'Default') ? (
                    <span className={`${css.tag} ${css.tagAccent}`}>Default</span>
                  ) : null}
                </div>
                {outside ? <LockLine>Global policy: admins only</LockLine> : null}
              </div>
              {flagRow && on ? (
                <span className={css.qAsk}>
                  {asks ? 'Global policy asks' : 'Needs my OK'}
                  <Switch
                    small
                    warn
                    checked={asks || flagList.includes(o.id)}
                    label={`${o.name} needs your OK`}
                    disabled={disabled || asks}
                    onChange={(next) => flag(o.id, next)}
                  />
                </span>
              ) : !on && layer === 'P' ? (
                <span className={css.t2}>Admins only</span>
              ) : (
                <span />
              )}
            </div>
          );
        })}
      </div>
      {choiceRow ? (
        <div className={css.qFoot}>
          <label>
            {layer === 'P' ? 'Members start on' : 'Starts on'}
            <select
              className={css.sel}
              aria-label={choiceRow.label}
              value={choice == null ? '' : str(choice)}
              disabled={disabled}
              onChange={(e) =>
                onChange(choiceRow.id, e.target.value === '' ? null : Number(e.target.value))
              }
            >
              <option value="">Server default</option>
              {allowed.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
          <span className={`${css.t2} ${css.wrap}`}>
            {allowed.length === 0
              ? 'Nothing ticked: they can still ask, and get the server default.'
              : allowed.length === 1
                ? 'With one allowed there is no menu.'
                : `With ${allowed.length} allowed they get a short menu of just these.`}
          </span>
        </div>
      ) : null}
    </>
  );
}

// ── a category's rows ──

function RowShell({
  title,
  help,
  dim,
  lock,
  children,
}: {
  title: string;
  help?: string;
  dim?: boolean;
  lock?: string | null;
  children?: ReactNode;
}) {
  return (
    <div className={`${css.row} ${dim ? css.dim : ''}`}>
      <div>
        <div className={css.rowT}>{title}</div>
        {help ? <div className={css.rowD}>{help}</div> : null}
        {lock ? <LockLine>{lock}</LockLine> : null}
      </div>
      {children ? <div className={css.rowCtl}>{children}</div> : null}
    </div>
  );
}

export function CategoryRows({
  catalogue,
  category,
  layer,
  values,
  ceiling,
  quality,
  onChange,
}: {
  catalogue: Catalogue;
  category: string;
  layer: Layer;
  values: PermMap;
  ceiling: PermMap | null;
  quality: QualityOptions | null;
  onChange: (id: string, v: PermValue) => void;
}) {
  const byId = rowById(catalogue);
  const rows = rowsFor(catalogue, category, layer);
  const plainRows: CatalogueRow[] = [];
  const groups = new Map<string, CatalogueRow[]>();
  const qualitySets: CatalogueRow[] = [];
  for (const row of rows) {
    if (row.kind === 'set') qualitySets.push(row);
    else if (row.kind === 'choice' || row.kind === 'set_flag') continue;
    else if (row.group) groups.set(row.group, [...(groups.get(row.group) ?? []), row]);
    else plainRows.push(row);
  }

  const renderRow = (row: CatalogueRow) => {
    if (row.kind === 'fixed') {
      const always =
        row.id === 'admin.everything' ? 'Admins only, always' : 'Always on, for everyone';
      return <RowShell key={row.id} title={row.label} lock={always} dim />;
    }
    const value = values[row.id];
    const ceil = ceiling?.[row.id];
    const needs = layer === 'T' ? needsLine(row, values, byId) : null;
    const capped = layer === 'T' && ceil !== undefined && aboveCeiling(row, value, ceil);
    const forced = layer === 'T' && row.kind === 'restrict' && ceil === true;
    const title = layer === 'P' ? (POLICY_TITLE[row.kind]?.(row) ?? row.label) : row.label;
    const help =
      layer === 'P'
        ? policyHelp(row)
        : row.kind === 'mode'
          ? (MODE_HELP[str(value)] ?? row.help)
          : row.help;
    const lock = needs ?? (capped || forced ? capLine(row, ceil as PermValue, quality) : null);
    return (
      <RowShell
        key={row.id}
        title={title}
        help={help}
        dim={!!needs || capped || forced}
        lock={lock}
      >
        <RowControl
          row={row}
          value={value}
          ceiling={ceil}
          layer={layer}
          disabled={!!needs}
          onChange={(v) => onChange(row.id, v)}
        />
      </RowShell>
    );
  };

  const sections: ReactNode[] = [];
  if (plainRows.length) {
    sections.push(
      <div className={css.group} key="main">
        {plainRows.map(renderRow)}
      </div>,
    );
  }
  for (const setRow of qualitySets) {
    const choiceRow = rows.find((r) => r.kind === 'choice' && r.set_row === setRow.id);
    const flagRow = rows.find((r) => r.kind === 'set_flag' && r.media === setRow.media);
    const needs = layer === 'T' ? needsLine(setRow, values, byId) : null;
    sections.push(
      <div key={setRow.id}>
        <div className={css.sect}>{layer === 'P' ? 'Quality members may pick' : 'Quality'}</div>
        <div className={`${css.group} ${needs ? css.dim : ''}`}>
          {needs ? <RowShell title={setRow.label} lock={needs} dim /> : null}
          <QualityGroup
            setRow={setRow}
            choiceRow={choiceRow}
            flagRow={layer === 'T' ? flagRow : undefined}
            values={values}
            ceiling={ceiling}
            layer={layer}
            quality={quality}
            disabled={!!needs}
            onChange={onChange}
          />
        </div>
        {layer === 'P' && flagRow ? (
          <PolicyFlags
            row={flagRow}
            value={values[flagRow.id]}
            onChange={(v) => onChange(flagRow.id, v)}
          />
        ) : null}
      </div>,
    );
  }
  for (const [group, list] of groups) {
    const sharedNote =
      list.some((r) => r.also_in.length) && category !== 'movies'
        ? ', the same as under Movies'
        : '';
    sections.push(
      <div key={group}>
        <div className={css.sect}>
          {group}
          {sharedNote}
        </div>
        <div className={css.group}>{list.map(renderRow)}</div>
      </div>,
    );
  }
  return <>{sections}</>;
}

/** the policy's "always needs your OK": 4K for anyone allowed it */
function PolicyFlags({
  row,
  value,
  onChange,
}: {
  row: CatalogueRow;
  value: PermValue;
  onChange: (v: PermValue) => void;
}) {
  const list = Array.isArray(value) ? value : [];
  const on = list.includes('4k');
  return (
    <>
      <div className={css.sect}>Always needs your OK</div>
      <div className={css.group}>
        <RowShell
          title="4K for anyone allowed it"
          help="A profile marked 4K always comes to you first, whatever someone's template says."
        >
          <Switch
            checked={on}
            label={`${row.label}: 4K always asks`}
            onChange={(next) =>
              onChange(
                next ? [...list.filter((x) => x !== '4k'), '4k'] : list.filter((x) => x !== '4k'),
              )
            }
          />
        </RowShell>
      </div>
    </>
  );
}

// ── the save bar ──

export function SaveBar({
  dirty,
  checking,
  impact,
  catalogue,
  quality,
  scope,
  error,
  saving,
  saveLabel,
  onDiscard,
  onSave,
}: {
  dirty: boolean;
  checking: boolean;
  impact: Impact | null;
  catalogue: Catalogue;
  quality: QualityOptions | null;
  scope: 'policy' | 'template';
  error: string | null;
  saving: boolean;
  saveLabel: string;
  onDiscard: () => void;
  onSave: () => void;
}) {
  const id = useId();
  if (!dirty) {
    return (
      <div className={css.srOnly} aria-live="polite" id={id}>
        {''}
      </div>
    );
  }
  const line = impact ? impactLine(impact, catalogue, quality, scope) : null;
  return (
    <div className={css.impact}>
      <span className={css.dotw} aria-hidden="true" />
      <span className={css.impactText} aria-live="polite" id={id}>
        {error ? (
          <span className={css.error}>{error}</span>
        ) : checking || !line ? (
          'Working out who this reaches...'
        ) : (
          <>
            {line.lead}
            {line.people.map((p) => (
              <span key={p.name}>
                {' '}
                <b>{p.name}</b>: {p.text}.
              </span>
            ))}
            {line.more ? ` And ${line.more} more.` : null}
            {line.tail ? ` ${line.tail}` : null}
          </>
        )}
      </span>
      <span className={css.impactActs}>
        <button
          type="button"
          className={`${css.btn} ${css.secondary}`}
          onClick={onDiscard}
          disabled={saving}
        >
          Discard
        </button>
        <button
          type="button"
          className={`${css.btn} ${css.primary}`}
          onClick={onSave}
          disabled={saving || checking || !!error}
        >
          {saving ? 'Saving...' : saveLabel}
        </button>
      </span>
    </div>
  );
}
