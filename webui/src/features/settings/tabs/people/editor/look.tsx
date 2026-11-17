/**
 * Sidebar & look: what shows, never what they may do (#1909, #2157,
 * #2164). The switches only offer parts the draft lets them open, the lock
 * is "Let Maya change it", and the home page only lists pages they can use.
 * Saved through the same calls the old editor used.
 */

import { useId } from 'react';

import { Switch } from '@/components/form/form';

import type { FeaturesAdminPayload, PermMap } from './types';

import { type Model } from './model';
import styles from './person-editor.module.css';

export interface LookState {
  payload: FeaturesAdminPayload | null;
  error: string;
  off: string[];
  locked: boolean;
  offDirty: boolean;
  lockDirty: boolean;
  reset: boolean;
  home: string;
  savedHome: string;
}

export function lookFrom(payload: FeaturesAdminPayload | null, home: string): LookState {
  const off = payload ? (Array.isArray(payload.set_off) ? payload.set_off : payload.off) : [];
  return {
    payload,
    error: '',
    off: [...(off ?? [])],
    locked: !!payload?.locked,
    offDirty: false,
    lockDirty: false,
    reset: false,
    home,
    savedHome: home,
  };
}

/** the features POST body: only the halves the admin touched (#2164) */
export function featuresBody(s: LookState): Record<string, unknown> | null {
  const body: Record<string, unknown> = {};
  if (s.reset) body.reset = true;
  if (s.offDirty) body.off = s.off;
  if (s.lockDirty) body.locked = s.locked;
  return Object.keys(body).length ? body : null;
}

export const lookDirty = (s: LookState) => !!featuresBody(s) || s.home !== s.savedHome;

type HomeOption = { value: string; label: string };
type ShellHome = {
  profileHomeOptions?: (sides: string, pages: string[] | null) => HomeOption[];
};

/** the pages this draft can open, and its sides, for the home menu */
export function pagesFor(model: Model, eff: PermMap): { sides: string; pages: string[] | null } {
  const music = !!eff['music.access'];
  const video = !!eff['movies.access'] || !!eff['tv.access'];
  const sides = music && video ? 'both' : video ? 'video' : 'music';
  const pageRows = model.catalogue.rows.filter((r) => r.pages?.length && r.stored);
  const on = pageRows.filter(
    (r) => eff[r.id] && !(r.needs?.length && !r.needs.some((n) => eff[n])),
  );
  const pages = on.flatMap((r) => r.pages);
  return { sides, pages: on.length === pageRows.length ? null : pages };
}

function homeOptions(model: Model, eff: PermMap): HomeOption[] {
  const { sides, pages } = pagesFor(model, eff);
  const shell = window as unknown as ShellHome;
  if (typeof shell.profileHomeOptions === 'function') {
    try {
      return shell.profileHomeOptions(sides, pages);
    } catch {
      /* fall through to the catalogue's own list */
    }
  }
  return model.catalogue.rows
    .filter((r) => r.pages?.length && eff[r.id])
    .map((r) => ({ value: r.pages[0], label: r.label }));
}

export function SidebarLook({
  model,
  eff,
  name,
  state,
  onChange,
}: {
  model: Model;
  eff: PermMap;
  name: string;
  state: LookState;
  onChange: (next: LookState) => void;
}) {
  const homeId = useId();
  const lockId = useId();
  const lockDesc = useId();
  const p = state.payload;
  const { sides, pages } = pagesFor(model, eff);
  const opens = (pg: string) =>
    (sides === 'both' || (pg.startsWith('video-') ? sides === 'video' : sides === 'music')) &&
    (!pages || pages.includes(pg));
  const features = (p?.features ?? []).filter((f) => {
    if (f.id === 'video') return sides === 'both';
    if (f.group === 'video' && (sides === 'music' || state.off.includes('video'))) return false;
    if (!f.pages.length) return sides !== 'video';
    return f.pages.some(opens);
  });
  const presetNow = p?.presets.find(
    (pr) => pr.off.length === state.off.length && pr.off.every((x) => state.off.includes(x)),
  );
  const following = (state.reset || p?.follows_default || !p?.admin_set) && !state.offDirty;
  const options = homeOptions(model, eff);
  const homeKnown = !state.home || options.some((o) => o.value === state.home);

  const setOff = (off: string[]) => onChange({ ...state, off, offDirty: true });

  return (
    <>
      {state.error ? <p className={styles.warnNote}>{state.error}</p> : null}
      {p ? (
        <>
          <div className={styles.group}>
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowTitle}>Start from</div>
                <div className={styles.rowDesc}>
                  {presetNow?.desc ?? `${state.off.length} parts hidden by hand.`}
                </div>
                <div className={styles.prov}>
                  {following ? (
                    <span className={styles.from}>Follows what new profiles see</span>
                  ) : (
                    <>
                      <span className={styles.mine}>Set for {name}</span>
                      <button
                        type="button"
                        className={styles.rowReset}
                        onClick={() => {
                          const d = p.default ?? { off: [], locked: false };
                          onChange({
                            ...state,
                            off: [...d.off],
                            locked: !!d.locked,
                            reset: true,
                            offDirty: false,
                            lockDirty: false,
                          });
                        }}
                      >
                        Back to the default
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className={styles.rowCtl}>
                <div className={styles.seg} role="group" aria-label="Start from">
                  {p.presets.map((pr) => (
                    <button
                      key={pr.id}
                      type="button"
                      aria-pressed={presetNow?.id === pr.id}
                      onClick={() => setOff([...pr.off])}
                    >
                      {pr.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            {p.own_off && !state.reset ? (
              <p className={styles.rowNote}>
                {state.offDirty
                  ? `${name} picked their own before. Saving replaces it with this.`
                  : state.locked
                    ? `${name} picked their own before. It's ignored while this is locked.`
                    : `${name} picked their own, so that's what shows for them. Changing anything here replaces it.`}
              </p>
            ) : null}
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowTitle} id={lockId}>
                  Let {name} change it
                </div>
                <div className={styles.rowDesc} id={lockDesc}>
                  Off: {name} sees exactly this and Customize goes away for them.
                </div>
              </div>
              <div className={styles.rowCtl}>
                <Switch
                  aria-labelledby={lockId}
                  aria-describedby={lockDesc}
                  checked={!state.locked}
                  onCheckedChange={(on) => onChange({ ...state, locked: !on, lockDirty: true })}
                />
              </div>
            </div>
            <div className={styles.row}>
              <div className={styles.rowText}>
                <label className={styles.rowTitle} htmlFor={homeId}>
                  Opens on
                </label>
                <div className={styles.rowDesc}>Automatic is the first page they can use.</div>
              </div>
              <div className={styles.rowCtl}>
                <select
                  id={homeId}
                  className={styles.select}
                  value={homeKnown ? state.home : ''}
                  onChange={(e) => onChange({ ...state, home: e.target.value })}
                >
                  <option value="">Automatic</option>
                  {options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowTitle}>Theme and accent</div>
                <div className={styles.rowDesc}>Always theirs. Harmless, so never locked.</div>
              </div>
              <div className={styles.rowCtl}>
                <span className={styles.fixed}>Theirs</span>
              </div>
            </div>
          </div>

          {(p.groups ?? []).map((g) => {
            const rows = features.filter((f) => f.group === g.id);
            if (!rows.length) return null;
            return (
              <section key={g.id} aria-label={g.label}>
                <h3 className={styles.sect}>{g.label}</h3>
                <div className={styles.group}>
                  {rows.map((f) => (
                    <FeatureRow
                      key={f.id}
                      label={f.label}
                      desc={f.desc}
                      on={!state.off.includes(f.id)}
                      onChange={(on) =>
                        setOff(on ? state.off.filter((x) => x !== f.id) : [...state.off, f.id])
                      }
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </>
      ) : state.error ? null : (
        <p className={styles.quiet}>Loading what shows for {name}…</p>
      )}
    </>
  );
}

function FeatureRow({
  label,
  desc,
  on,
  onChange,
}: {
  label: string;
  desc: string;
  on: boolean;
  onChange: (on: boolean) => void;
}) {
  const id = useId();
  return (
    <div className={styles.row}>
      <div className={styles.rowText}>
        <div className={styles.rowTitle} id={id}>
          {label}
        </div>
        <div className={styles.rowDesc}>{desc}</div>
      </div>
      <div className={styles.rowCtl}>
        <Switch aria-labelledby={id} checked={on} onCheckedChange={onChange} />
      </div>
    </div>
  );
}
