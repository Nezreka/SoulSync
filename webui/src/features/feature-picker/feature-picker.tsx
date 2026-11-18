/**
 * "Make SoulSync yours" (#1909). One place for every profile to pick which
 * parts of SoulSync show up. Each switch saves on its own, the sidebar
 * changes behind the dialog as you go, nothing to confirm.
 *
 * The list, the presets and the rules are the server's (core/ui_features.py).
 * This only draws them and skips what the profile isn't allowed to open
 * anyway, so nobody gets a switch for a page they can't reach.
 *
 * An admin can lock a profile's choice (#2157). Locked, there's no way in
 * (#2164): the sidebar and Settings drop Customize and a stray call opens
 * nothing. A lock that lands while it's open turns it read only; the server
 * refuses the save anyway.
 */

import { Dialog } from '@base-ui/react/dialog';
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';

import type { FeatureDef, FeaturesPayload, FeaturesSave } from '@/shell/features';

import { DialogFrame } from '@/components/dialog';
import { Switch } from '@/components/form/form';
import { isFeaturesLocked } from '@/platform/features';

import styles from './feature-picker.module.css';
import { FeatureIcon } from './icons';

/** the pages no choice can hide, shown so nobody worries they'll lose them */
const ALWAYS = ['Search', 'Library', 'Watchlist', 'Wishlist', 'Downloads', 'Settings'];

type ShellGlobals = {
  isPageAllowed?: (page: string) => boolean;
  profileAllowedSides?: () => string;
  _currentProfileName?: string;
};

/** a feature this profile could open. no pages = lives inside other pages.
 *  only a fallback: the server sends `offered` and that wins. */
export function reachable(feature: FeatureDef, g: ShellGlobals = window as ShellGlobals): boolean {
  const sides = typeof g.profileAllowedSides === 'function' ? g.profileAllowedSides() : 'both';
  // a video-only profile can't hide its only side (#2157)
  if (feature.id === 'video' && sides === 'video') return false;
  if (feature.id === 'video' || feature.group === 'video') {
    if (sides === 'music') return false;
  }
  if (!feature.pages.length || typeof g.isPageAllowed !== 'function') return true;
  return feature.pages.some((page) => g.isPageAllowed?.(page));
}

export function FeaturePicker() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<FeaturesPayload | null>(null);
  const [off, setOff] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // saves go out one at a time, so a fast double flip can't land backwards
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  const load = useCallback(async () => {
    setError('');
    const payload = await window.SoulSyncFeatures?.load();
    if (payload) {
      setData(payload);
      setOff(payload.off);
    } else {
      setError("Couldn't load your choices. Try again in a moment.");
    }
  }, []);

  useEffect(() => {
    window.openFeaturePicker = () => {
      if (isFeaturesLocked()) return;
      setOpen(true);
      void load();
    };
    return () => {
      delete window.openFeaturePicker;
    };
  }, [load]);

  const save = useCallback(
    (body: FeaturesSave, optimistic: string[]) => {
      const before = off;
      setOff(optimistic);
      setError('');
      setBusy(true);
      queue.current = queue.current
        .then(async () => {
          const api = window.SoulSyncFeatures;
          if (!api) throw new Error('not ready');
          const payload = await api.save(body);
          setData(payload);
          setOff(payload.off);
        })
        .catch(() => {
          setOff(before);
          setError("That didn't save. Check your connection and try again.");
        })
        .finally(() => setBusy(false));
    },
    [off],
  );

  const locked = !!data?.locked;

  const toggle = (id: string, on: boolean) => {
    if (locked) return;
    const next = on ? off.filter((x) => x !== id) : [...off, id];
    save({ off: next }, next);
  };

  const pauseBackground = async (feature: string, paused: boolean) => {
    setError('');
    try {
      const res = await fetch('/api/profiles/me/features/background', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ feature, paused }),
      });
      const body = (await res.json().catch(() => null)) as {
        success?: boolean;
        background?: FeaturesPayload['background'];
      } | null;
      if (!res.ok || !body?.success) throw new Error('save');
      setData((prev) => (prev ? { ...prev, background: body.background } : prev));
    } catch {
      setError("That didn't save. Check your connection and try again.");
    }
  };

  const pickPreset = (id: string) => {
    if (locked) return;
    const preset = data?.presets.find((p) => p.id === id);
    if (preset) save({ preset: id }, preset.off);
  };

  const resetToDefault = () => {
    if (!locked) save({ reset: true }, off);
  };

  // the video side's own switches mean nothing while the whole side is off
  const offered = data?.offered;
  const features = (data?.features ?? []).filter(
    (f) =>
      (offered ? offered.includes(f.id) : reachable(f)) &&
      !(f.group === 'video' && off.includes('video')),
  );
  const presetNow =
    data?.presets.find((p) => p.off.length === off.length && p.off.every((x) => off.includes(x)))
      ?.id ?? 'custom';
  const shown = features.filter((f) => !off.includes(f.id)).length;
  const name = (window as ShellGlobals)._currentProfileName;

  return (
    <DialogFrame
      open={open}
      onOpenChange={setOpen}
      aria-label="Make SoulSync yours"
      viewportClassName={styles.viewport}
      className={styles.popup}
    >
      <div className={styles.head}>
        <div>
          <Dialog.Title className={styles.title}>Make SoulSync yours</Dialog.Title>
          <Dialog.Description className={styles.lead}>
            {locked
              ? 'An admin set what this profile shows. Ask them if you want something changed.'
              : 'Show only what you use. Hidden parts keep working in the background and come back with one switch.'}
          </Dialog.Description>
        </div>
        <Dialog.Close className={styles.close} aria-label="Close">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          >
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </Dialog.Close>
      </div>

      <div className={styles.body}>
        <div className={styles.presets} role="radiogroup" aria-label="Start from">
          {(data?.presets ?? []).map((p) => (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={presetNow === p.id}
              aria-disabled={locked || undefined}
              disabled={locked}
              className={styles.preset}
              onClick={() => pickPreset(p.id)}
            >
              <span className={styles.presetTitle}>{p.label}</span>
              <span className={styles.presetDesc}>{p.desc}</span>
            </button>
          ))}
        </div>

        <div className={styles.always}>
          <span className={styles.groupLabel}>Always here</span>
          <div className={styles.chips}>
            {ALWAYS.map((label) => (
              <span key={label} className={styles.chip}>
                {label}
              </span>
            ))}
          </div>
        </div>

        {(data?.groups ?? []).map((group) => {
          const rows = features.filter((f) => f.group === group.id);
          if (!rows.length) return null;
          return (
            <section key={group.id} className={styles.group} aria-label={group.label}>
              <h3 className={styles.groupLabel}>{group.label}</h3>
              <div className={styles.rows}>
                {rows.map((f) => {
                  const on = !off.includes(f.id);
                  const labelId = `fp-${f.id}`;
                  const bg = data?.background?.[f.id];
                  return (
                    <Fragment key={f.id}>
                      <label className={styles.row} data-on={on || undefined}>
                        <span className={styles.icon} aria-hidden="true">
                          <FeatureIcon id={f.id} />
                        </span>
                        <span className={styles.text}>
                          <span className={styles.rowTitle} id={labelId}>
                            {f.label}
                          </span>
                          <span className={styles.rowDesc}>{f.desc}</span>
                        </span>
                        <Switch
                          aria-labelledby={labelId}
                          checked={on}
                          disabled={locked}
                          onCheckedChange={(next) => toggle(f.id, next)}
                        />
                      </label>
                      {/* jobs are install-wide (#1909): an admin can stop them
                        once nobody uses the part */}
                      {!on && bg ? (
                        bg.everyone_hid ? (
                          <label className={styles.subRow}>
                            <span className={styles.subText} id={`${labelId}-bg`}>
                              Also pause its background jobs
                              <span className={styles.rowDesc}>
                                Hidden for every profile. Turning it back on for anyone resumes
                                them.
                              </span>
                            </span>
                            <Switch
                              aria-labelledby={`${labelId}-bg`}
                              checked={bg.paused}
                              onCheckedChange={(next) => void pauseBackground(f.id, next)}
                            />
                          </label>
                        ) : (
                          <p className={styles.subNote}>
                            Other profiles still use {f.label.toLowerCase()}, so its background jobs
                            keep running.
                          </p>
                        )
                      ) : null}
                    </Fragment>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>

      <div className={styles.foot}>
        <span className={styles.status} role="status" aria-live="polite">
          {error ? (
            <span className={styles.error}>{error}</span>
          ) : busy ? (
            'Saving…'
          ) : data && locked ? (
            `${shown} of ${features.length} extras on${name ? ` for ${name}` : ''}. Set by an admin.`
          ) : data ? (
            `${shown} of ${features.length} extras on${name ? ` for ${name}` : ''}. Each profile picks its own.`
          ) : (
            'Loading…'
          )}
        </span>
        <span className={styles.actions}>
          {/* back to what an admin set for new profiles, everything if nothing */}
          {data && !locked && !data.follows_default ? (
            <button type="button" className={styles.reset} onClick={resetToDefault} disabled={busy}>
              Reset to default
            </button>
          ) : null}
          <Dialog.Close className={styles.done}>Done</Dialog.Close>
        </span>
      </div>
    </DialogFrame>
  );
}
