/**
 * One row per permission, drawn from the catalogue. Every row says where
 * its value comes from (From Teen / Changed for Maya), the policy's lock
 * when it caps the row, and what it needs when it can't matter yet.
 */

import { useId, type ReactNode } from 'react';

import { Switch } from '@/components/form/form';

import type { CatalogueRow, Count, IdList, QualityOption, Value } from './types';

import { Icon } from './icons';
import {
  ALL,
  FOLLOW,
  MODES,
  RATINGS,
  type Draft,
  type Model,
  type Resolved,
  ceilingBites,
  daysWord,
  dependsWords,
  fullyLocked,
  lockWords,
  modeHelp,
  modeLabel,
  qualityOptionsFor,
  ratingWord,
  same,
  str,
  valueWords,
} from './model';
import styles from './person-editor.module.css';

export interface EditorCtx {
  model: Model;
  draft: Draft;
  resolved: Resolved;
  /** the person's name */
  name: string;
  /** the template's name, or "their own settings" */
  tplName: string;
  legacy: boolean;
  set: (id: string, value: Value) => void;
  reset: (ids: string[]) => void;
  onOpenPolicy?: () => void;
}

/** the effective value against what the template or change asks for */
export function isCapped(ctx: EditorCtx, row: CatalogueRow): boolean {
  if (row.kind === 'choice') return false;
  let want = ctx.resolved.raw[row.id];
  if (row.id === 'tv.max_rating' && want === FOLLOW)
    want = ctx.resolved.effective['movies.max_rating'];
  return !same(ctx.resolved.effective[row.id], want);
}

export function PolicyLock({ ctx, text }: { ctx: EditorCtx; text: string }) {
  return (
    <span className={styles.lock}>
      <Icon name="lock" />
      <span>
        {text}{' '}
        {ctx.onOpenPolicy ? (
          <button type="button" className={styles.inlineLink} onClick={ctx.onOpenPolicy}>
            Change it there
          </button>
        ) : (
          'Change it in Global policy.'
        )}
      </span>
    </span>
  );
}

/** "From Teen" or "Changed for Maya · Teen: Ask first · Reset" */
export function Provenance({
  ctx,
  ids,
  label,
  was,
}: {
  ctx: EditorCtx;
  /** the rows this marker covers; the first one's template value is shown */
  ids: string[];
  label: string;
  was?: string;
}) {
  const changed = ids.filter((id) => id in ctx.draft.overrides);
  if (!changed.length) {
    return (
      <span className={styles.from} data-prov="inherited">
        {ctx.legacy ? 'Their own setting' : `From ${ctx.tplName}`}
      </span>
    );
  }
  const row = ctx.model.rowsById.get(ids[0])!;
  const base = ctx.resolved.base[ids[0]];
  return (
    <>
      <span className={styles.mine} data-prov="changed">
        Changed for {ctx.name}
      </span>
      <span className={styles.was}>
        {ctx.legacy ? 'Was' : ctx.tplName}: {was ?? valueWords(ctx.model, row, base)}
      </span>
      <button
        type="button"
        className={styles.rowReset}
        onClick={() => ctx.reset(changed)}
        aria-label={`Reset ${label} to ${ctx.legacy ? 'their own setting' : ctx.tplName}`}
      >
        <Icon name="undo" />
        Reset
      </button>
    </>
  );
}

function ModeControl({
  ctx,
  row,
  labelId,
  disabled,
}: {
  ctx: EditorCtx;
  row: CatalogueRow;
  labelId: string;
  disabled: boolean;
}) {
  const value = ctx.resolved.effective[row.id];
  const ceiling = str(ctx.model.policy[row.id]);
  const order = row.values.length ? row.values : MODES;
  return (
    <div className={styles.seg} role="group" aria-labelledby={labelId}>
      {[...order].reverse().map((v) => {
        const above = order.indexOf(v) > order.indexOf(ceiling);
        return (
          <button
            key={v}
            type="button"
            aria-pressed={value === v}
            disabled={disabled || above}
            title={above ? lockWords(ctx.model, row) : undefined}
            onClick={() => ctx.set(row.id, v)}
          >
            {modeLabel(row, v)}
          </button>
        );
      })}
    </div>
  );
}

function RatingControl({
  ctx,
  row,
  labelId,
  disabled,
}: {
  ctx: EditorCtx;
  row: CatalogueRow;
  labelId: string;
  disabled: boolean;
}) {
  const raw = ctx.resolved.raw[row.id];
  const eff = ctx.resolved.effective[row.id];
  const shown = raw === FOLLOW ? FOLLOW : eff;
  let ceiling = ctx.model.policy[row.id];
  if (ceiling === FOLLOW) ceiling = ctx.model.policy['movies.max_rating'];
  const movies = ctx.resolved.effective['movies.max_rating'];
  const options = [...RATINGS].reverse();
  return (
    <select
      className={styles.select}
      aria-labelledby={labelId}
      value={str(shown)}
      disabled={disabled}
      onChange={(e) => ctx.set(row.id, e.target.value)}
    >
      {row.id === 'tv.max_rating' ? (
        <option value={FOLLOW}>{ratingWord(row, FOLLOW, movies)}</option>
      ) : null}
      {options.map((r) => (
        <option key={r} value={r} disabled={RATINGS.indexOf(r) > RATINGS.indexOf(str(ceiling))}>
          {ratingWord(row, r)}
        </option>
      ))}
    </select>
  );
}

const COUNT_STEPS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30, 50, 100];
const DAY_STEPS = [1, 7, 30];

function CountControl({
  ctx,
  row,
  labelId,
  disabled,
}: {
  ctx: EditorCtx;
  row: CatalogueRow;
  labelId: string;
  disabled: boolean;
}) {
  const v = (ctx.resolved.effective[row.id] as Count) ?? { n: 0, days: 7 };
  const ceiling = ctx.model.policy[row.id] as Count;
  const ns = COUNT_STEPS.includes(v.n) ? COUNT_STEPS : [...COUNT_STEPS, v.n].sort((a, b) => a - b);
  const days = DAY_STEPS.includes(v.days)
    ? DAY_STEPS
    : [...DAY_STEPS, v.days].sort((a, b) => a - b);
  const tooMany = (n: number, d: number) =>
    !!ceiling?.n && (!n || n / d > ceiling.n / ceiling.days);
  return (
    <span className={styles.pair}>
      <select
        className={styles.select}
        aria-labelledby={labelId}
        value={String(v.n)}
        disabled={disabled}
        onChange={(e) => ctx.set(row.id, { n: Number(e.target.value), days: v.days })}
      >
        {ns.map((n) => (
          <option key={n} value={n} disabled={tooMany(n, v.days)}>
            {n === 0 ? 'No limit' : n}
          </option>
        ))}
      </select>
      {v.n ? (
        <select
          className={styles.select}
          aria-label={`${row.label}: per`}
          value={String(v.days)}
          disabled={disabled}
          onChange={(e) => ctx.set(row.id, { n: v.n, days: Number(e.target.value) })}
        >
          {days.map((d) => (
            <option key={d} value={d} disabled={tooMany(v.n, d)}>
              per {daysWord(d)}
            </option>
          ))}
        </select>
      ) : null}
    </span>
  );
}

/** one permission row, any plain kind */
export function PermRow({
  ctx,
  row,
  shared,
}: {
  ctx: EditorCtx;
  row: CatalogueRow;
  shared?: string;
}) {
  const labelId = useId();
  const descId = useId();
  const eff = ctx.resolved.effective;
  const dep = dependsWords(ctx.model, row, eff);
  const locked = fullyLocked(ctx.model, row);
  const capped = isCapped(ctx, row);
  const changed = row.id in ctx.draft.overrides;
  const bites = ceilingBites(ctx.model, row);
  const disabled = !!dep || locked;

  if (row.kind === 'fixed') {
    return (
      <div className={styles.row} data-row={row.id}>
        <div className={styles.rowText}>
          <div className={styles.rowTitle}>{row.label}</div>
          <div className={styles.rowDesc}>{row.help}</div>
        </div>
        <div className={styles.rowCtl}>
          <span className={styles.fixed}>{row.default ? 'Always' : 'Admins only'}</span>
        </div>
      </div>
    );
  }

  let control: ReactNode = null;
  if (row.kind === 'bool' || row.kind === 'restrict') {
    control = (
      <Switch
        aria-labelledby={labelId}
        aria-describedby={descId}
        checked={!!eff[row.id]}
        disabled={disabled}
        onCheckedChange={(on) => ctx.set(row.id, on)}
      />
    );
  } else if (row.kind === 'mode') {
    control = <ModeControl ctx={ctx} row={row} labelId={labelId} disabled={!!dep} />;
  } else if (row.kind === 'rating') {
    control = <RatingControl ctx={ctx} row={row} labelId={labelId} disabled={!!dep} />;
  } else if (row.kind === 'count') {
    control = <CountControl ctx={ctx} row={row} labelId={labelId} disabled={!!dep} />;
  }

  return (
    <div
      className={styles.row}
      data-row={row.id}
      data-changed={changed || undefined}
      data-capped={(locked && !changed) || undefined}
      data-dep={dep ? true : undefined}
    >
      <div className={styles.rowText}>
        <div className={styles.rowTitle} id={labelId}>
          {row.label}
        </div>
        <div className={styles.rowDesc} id={descId}>
          {dep ||
            (row.kind === 'mode'
              ? modeHelp(row, eff[row.id])
              : shared
                ? `${row.help} ${shared}`
                : row.help)}
        </div>
        {dep ? null : (
          <div className={styles.prov}>
            {locked && !changed ? null : <Provenance ctx={ctx} ids={[row.id]} label={row.label} />}
            {bites ? <PolicyLock ctx={ctx} text={lockWords(ctx.model, row)} /> : null}
            {changed && capped ? (
              <span className={styles.inert}>
                The policy holds this change back for now. Lifting it brings it back.
              </span>
            ) : null}
          </div>
        )}
      </div>
      <div className={styles.rowCtl}>{control}</div>
    </div>
  );
}

/* ── quality ──────────────────────────────────────────────────────────── */

const PREFIX: Record<string, string> = { movie: 'movies', tv: 'tv', music: 'music' };

function tagFor(o: QualityOption) {
  if (o.tags?.includes('4k')) return <span className={styles.tag4k}>4K</span>;
  if (o.tags?.includes('extended')) return <span className={styles.tagExt}>Extended</span>;
  return null;
}

/** the allowed list, the default ("Starts here") and Needs my OK, as one block */
export function QualityBlock({ ctx, media }: { ctx: EditorCtx; media: 'movie' | 'tv' | 'music' }) {
  const p = PREFIX[media];
  const setRow = ctx.model.rowsById.get(`${p}.quality`);
  const askRow = ctx.model.rowsById.get(`${p}.quality_ask`);
  const choiceRow = ctx.model.rowsById.get(`${p}.quality_default`);
  const headId = useId();
  if (!setRow) return null;
  const opts = qualityOptionsFor(ctx.model, media);
  const { raw, effective } = ctx.resolved;
  const dep = dependsWords(ctx.model, setRow, effective);
  const policySet = ctx.model.policy[setRow.id];
  const policyAsk = (askRow ? ctx.model.policy[askRow.id] : []) as IdList;
  const capped = (o: QualityOption) =>
    policySet !== ALL && !((policySet as IdList) ?? []).includes(o.id);
  const allowed = (o: QualityOption) =>
    effective[setRow.id] === ALL || ((effective[setRow.id] as IdList) ?? []).includes(o.id);
  const rawAsk = ((askRow ? raw[askRow.id] : []) as IdList) ?? [];
  const effAsk = ((askRow ? effective[askRow.id] : []) as IdList) ?? [];
  const asks = (o: QualityOption) =>
    effAsk.includes(o.id) || (effAsk.includes('4k') && !!o.tags?.includes('4k'));
  const forced = (o: QualityOption) =>
    policyAsk.includes(o.id) || (policyAsk.includes('4k') && !!o.tags?.includes('4k'));
  const pick = choiceRow ? effective[choiceRow.id] : null;
  const ids = [setRow.id, choiceRow?.id, askRow?.id].filter(Boolean) as string[];

  const toggleAllowed = (o: QualityOption, on: boolean) => {
    const cur = raw[setRow.id];
    const list: IdList =
      cur === ALL ? opts.filter((x) => !capped(x)).map((x) => x.id) : [...((cur as IdList) ?? [])];
    const next = on ? [...list, o.id] : list.filter((x) => x !== o.id);
    ctx.set(setRow.id, next);
  };
  const toggleAsk = (token: number | string, on: boolean) => {
    if (!askRow) return;
    const next = on ? [...rawAsk, token] : rawAsk.filter((x) => x !== token);
    ctx.set(askRow.id, next);
  };

  const allowedOpts = opts.filter(allowed);
  const has4k = opts.some((o) => o.tags?.includes('4k'));
  const kind = media === 'movie' ? 'movie' : media === 'tv' ? 'show' : 'music';
  let foot: ReactNode;
  if (dep) foot = dep;
  else if (!allowedOpts.length)
    foot = `Nothing allowed, so ${ctx.name} can't ask for ${kind === 'music' ? 'music' : `${kind}s`} at all.`;
  else if (allowedOpts.length === 1)
    foot = (
      <>
        {ctx.name}&apos;s {kind} {kind === 'music' ? 'adds' : 'requests'} always use{' '}
        <b>{allowedOpts[0].name}</b>. Seerr apps and automatic adds can&apos;t pick anything else.
      </>
    );
  else {
    const askNames = allowedOpts.filter(asks).map((o) => o.name);
    foot = (
      <>
        {allowedOpts.length} allowed, so {ctx.name} picks from a short menu.
        {pick == null ? ' Without a pick, it starts on the usual default.' : ''}
        {askNames.length
          ? ` ${askNames.join(' and ')} always ${askNames.length > 1 ? 'come' : 'comes'} to you first.`
          : ''}
      </>
    );
  }

  return (
    <section className={styles.group} aria-labelledby={headId} data-quality={media}>
      <div
        className={styles.row}
        data-row={setRow.id}
        data-changed={ids.some((id) => id in ctx.draft.overrides) || undefined}
        data-full=""
      >
        <div className={styles.rowText}>
          <div className={styles.rowTitle} id={headId}>
            Allowed profiles
          </div>
          <div className={styles.rowDesc}>
            {dep || `${setRow.help} With one left, ${ctx.name} won't see a quality menu at all.`}
          </div>
          {dep ? null : (
            <div className={styles.prov}>
              <Provenance ctx={ctx} ids={ids} label="Allowed profiles" />
              {raw[setRow.id] !== ALL ? (
                <button
                  type="button"
                  className={styles.rowReset}
                  onClick={() => ctx.set(setRow.id, ALL)}
                >
                  Allow all, later ones too
                </button>
              ) : null}
            </div>
          )}
        </div>
      </div>
      <ul className={styles.qlist} aria-labelledby={headId}>
        {opts.map((o) => {
          const isCapped = capped(o);
          const on = allowed(o);
          const isPick = pick === o.id;
          return (
            <li key={o.id} className={styles.q} data-q={o.id}>
              <input
                type="checkbox"
                className={styles.cb}
                aria-label={o.name}
                checked={on}
                disabled={!!dep || isCapped}
                onChange={(e) => toggleAllowed(o, e.target.checked)}
              />
              <div className={styles.qText}>
                <div className={styles.qn}>
                  {o.name}
                  {tagFor(o)}
                  {isPick ? <span className={styles.tagDef}>Starts here</span> : null}
                </div>
                {isCapped ? (
                  <div className={styles.qd}>
                    <PolicyLock ctx={ctx} text="Global policy keeps this for admins." />
                  </div>
                ) : on && !isPick && choiceRow && !dep ? (
                  <button
                    type="button"
                    className={styles.startHere}
                    onClick={() => ctx.set(choiceRow.id, o.id)}
                  >
                    Start here
                  </button>
                ) : null}
              </div>
              {askRow && !isCapped ? (
                <AskSwitch
                  name={o.name}
                  checked={asks(o)}
                  disabled={!!dep || !on || forced(o)}
                  forced={forced(o)}
                  onChange={(next) => toggleAsk(o.id, next)}
                />
              ) : (
                <span />
              )}
            </li>
          );
        })}
      </ul>
      {askRow && has4k && !dep ? (
        <div className={styles.qToken}>
          <span>Every 4K profile needs my OK, later ones too</span>
          <AskSwitch
            name="every 4K profile"
            checked={effAsk.includes('4k')}
            disabled={policyAsk.includes('4k')}
            forced={policyAsk.includes('4k')}
            onChange={(next) => toggleAsk('4k', next)}
          />
        </div>
      ) : null}
      <div className={styles.qFoot}>
        {allowedOpts.length === 1 && !dep ? (
          <Icon name="check" className={styles.qFootIcon} />
        ) : null}
        <span>{foot}</span>
      </div>
    </section>
  );
}

function AskSwitch({
  name,
  checked,
  disabled,
  forced,
  onChange,
}: {
  name: string;
  checked: boolean;
  disabled: boolean;
  forced: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <span className={styles.qAsk} data-forced={forced || undefined}>
      <span aria-hidden="true">{forced ? 'Always asks' : 'Needs my OK'}</span>
      <Switch
        className={styles.askSwitch}
        aria-label={`${name} needs my OK`}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
      />
    </span>
  );
}
