/**
 * The Automations card (dash-card data-card="automations") — the live and
 * upcoming view of the engine, beside the Sync band. Its rows are everything
 * /api/automations runs EXCEPT the playlist pipelines (those are the Sync
 * band; showing them twice would recreate the duplication the band merge
 * removed): watchlist scans, wishlist processing, backups, notifications.
 *
 * Each row: name, its trigger in words, last-run outcome (red with the error
 * text on failure), the next-fire countdown, and a hover Run button —
 * /api/automations/<id>/run is the CORRECT run path here (these are real
 * automations, not pipelines; the Sync band's Run deliberately bypasses it).
 *
 * The footer is Boulder's quick-settings strip: the two performance switches
 * (Reduce effects / Max performance) wired to init.js's own appliers — the
 * exact functions the Settings checkboxes call, so body classes, canvas
 * loops, and localStorage all behave identically. Max performance overrides
 * Reduce effects, so the latter locks while the former is on (mirroring
 * _syncMaxPerfDependentToggles).
 *
 * Same fetch discipline as the band: mount + focus + after a Run + minute
 * tick for countdowns; no steady-state poller.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  AutomationApiRow,
  AutomationCardRow,
  AutomationProgressState,
} from '../-dash.automations';

import { automationCardRows } from '../-dash.automations';

function useAutomationsCard() {
  const [rows, setRows] = useState<AutomationApiRow[] | null>(null);
  const [progress, setProgress] = useState<Record<string, AutomationProgressState>>({});
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [busyId, setBusyId] = useState<number | string | null>(null);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    if (document.body.classList.contains('app-locked')) return;
    try {
      const [listRes, progressRes] = await Promise.all([
        fetch('/api/automations'),
        fetch('/api/automations/progress'),
      ]);
      if (!mountedRef.current) return;
      if (listRes.ok) {
        const data = (await listRes.json()) as AutomationApiRow[];
        if (Array.isArray(data)) setRows(data);
      }
      if (progressRes.ok) {
        const live = (await progressRes.json()) as Record<string, AutomationProgressState>;
        if (live && typeof live === 'object') setProgress(live);
      }
      setNowMs(Date.now());
    } catch {
      // keep the previous rows
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    const onFocus = () => void load();
    window.addEventListener('focus', onFocus);
    const tick = window.setInterval(() => {
      if (mountedRef.current) setNowMs(Date.now());
    }, 60_000);
    return () => {
      mountedRef.current = false;
      window.removeEventListener('focus', onFocus);
      window.clearInterval(tick);
    };
  }, [load]);

  const view = useMemo(
    () => automationCardRows(rows ?? [], nowMs, progress),
    [rows, nowMs, progress],
  );

  // While an automation runs its phase/progress must move — a short loop
  // that exists ONLY while a running row is present (the band's idiom).
  const anyRunning = view.some((r) => r.running);
  useEffect(() => {
    if (!anyRunning) return;
    const h = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(h);
  }, [anyRunning, load]);

  const runNow = useCallback(
    async (row: AutomationCardRow) => {
      setBusyId(row.id);
      try {
        const res = await fetch(`/api/automations/${row.id}/run`, { method: 'POST' });
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        if (!res.ok) window.showToast?.(data.error || `Could not run ${row.name}`, 'error');
        else window.showToast?.(`${row.name} started`, 'success');
      } catch {
        window.showToast?.(`Could not run ${row.name}`, 'error');
      } finally {
        if (mountedRef.current) setBusyId(null);
        setTimeout(() => {
          if (mountedRef.current) void load();
        }, 1500);
      }
    },
    [load],
  );

  /** Pause/resume — the page's own toggle route; the engine reschedules on
   *  enable and cancels timers on disable server-side. */
  const toggle = useCallback(
    async (row: AutomationCardRow) => {
      setBusyId(row.id);
      try {
        const res = await fetch(`/api/automations/${row.id}/toggle`, { method: 'POST' });
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        if (!res.ok || data.error) {
          window.showToast?.(data.error || `Could not toggle ${row.name}`, 'error');
        } else {
          window.showToast?.(row.enabled ? `${row.name} paused` : `${row.name} resumed`, 'success');
        }
      } catch {
        window.showToast?.(`Could not toggle ${row.name}`, 'error');
      } finally {
        if (mountedRef.current) setBusyId(null);
        void load();
      }
    },
    [load],
  );

  return { loaded: rows !== null, view, busyId, runNow, toggle };
}

/** The Settings page's exact preset palette (index.html #accent-preset). */
const ACCENT_PRESETS: Array<{ hex: string; name: string }> = [
  { hex: '#1db954', name: 'Spotify Green' },
  { hex: '#1d8ab9', name: 'Ocean Blue' },
  { hex: '#a78bfa', name: 'Purple' },
  { hex: '#8b5cf6', name: 'Boulder Purple' },
  { hex: '#f59e0b', name: 'Sunset Orange' },
  { hex: '#f43f5e', name: 'Rose' },
  { hex: '#14b8a6', name: 'Teal' },
];

function QuickSettings() {
  const [reduce, setReduce] = useState(
    () => localStorage.getItem('soulsync-reduce-effects') === '1',
  );
  const [maxPerf, setMaxPerf] = useState(
    () => localStorage.getItem('soulsync-max-performance') === '1',
  );
  const [accent, setAccent] = useState(() => localStorage.getItem('soulsync-accent') || '#1db954');

  /** Apply instantly via init.js's own applier, then persist to the server
   *  config (partial POST — the handler merges key-by-key). Without the
   *  server write, bootstrapServerAppearanceSettings would revert the color
   *  on the next load. Persist failures (e.g. a non-admin profile — the
   *  endpoint is admin-only) stay quiet: the color still applies for the
   *  session, which is honest feedback on its own. */
  const pickAccent = (hex: string, isCustom: boolean) => {
    setAccent(hex);
    window.applyAccentColor?.(hex);
    void fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ui_appearance: isCustom
          ? { accent_preset: 'custom', accent_color: hex }
          : { accent_preset: hex },
      }),
    }).catch(() => undefined);
  };

  const [particles, setParticles] = useState(
    () => localStorage.getItem('soulsync-particles') !== 'false',
  );
  const [orbs, setOrbs] = useState(() => localStorage.getItem('soulsync-worker-orbs') !== 'false');

  const toggleReduce = () => {
    const next = !reduce;
    setReduce(next);
    window.applyReduceEffects?.(next);
  };
  const toggleMaxPerf = () => {
    const next = !maxPerf;
    setMaxPerf(next);
    window.applyMaxPerformance?.(next);
  };
  const toggleParticles = () => {
    const next = !particles;
    setParticles(next);
    window.applyParticlesSetting?.(next);
  };
  const toggleOrbs = () => {
    const next = !orbs;
    setOrbs(next);
    window.applyWorkerOrbsSetting?.(next);
  };

  return (
    <div className="dash-quick-settings">
      <span className="dash-quick-settings-label">Quick settings</span>
      <button
        type="button"
        className={reduce && !maxPerf ? 'dash-qs-toggle dash-qs-toggle--on' : 'dash-qs-toggle'}
        disabled={maxPerf}
        title="Calms the ambient effects (glows, particles, worker orbs) on this device"
        onClick={toggleReduce}
      >
        <span className="dash-qs-knob"></span>
        Reduce effects
      </button>
      <button
        type="button"
        className={maxPerf ? 'dash-qs-toggle dash-qs-toggle--on' : 'dash-qs-toggle'}
        title="The low-power switch for no-GPU setups — kills every animation and canvas loop on this device"
        onClick={toggleMaxPerf}
      >
        <span className="dash-qs-knob"></span>
        Max performance
      </button>
      <button
        type="button"
        className={particles && !maxPerf ? 'dash-qs-toggle dash-qs-toggle--on' : 'dash-qs-toggle'}
        disabled={maxPerf}
        title="The ambient page particles on this device"
        onClick={toggleParticles}
      >
        <span className="dash-qs-knob"></span>
        Particles
      </button>
      <button
        type="button"
        className={orbs && !maxPerf ? 'dash-qs-toggle dash-qs-toggle--on' : 'dash-qs-toggle'}
        disabled={maxPerf}
        title="The header's enrichment worker orbs on this device"
        onClick={toggleOrbs}
      >
        <span className="dash-qs-knob"></span>
        Worker orbs
      </button>
      <span className="dash-qs-accent">
        {ACCENT_PRESETS.map((preset) => (
          <button
            key={preset.hex}
            type="button"
            className={
              accent.toLowerCase() === preset.hex
                ? 'dash-qs-swatch dash-qs-swatch--active'
                : 'dash-qs-swatch'
            }
            style={{ background: preset.hex }}
            title={preset.name}
            onClick={() => pickAccent(preset.hex, false)}
          />
        ))}
        {/* Native color input styled as the eighth swatch — live-applies
            while dragging (input), persists on commit (change). */}
        <input
          type="color"
          className={
            ACCENT_PRESETS.some((p) => p.hex === accent.toLowerCase())
              ? 'dash-qs-swatch dash-qs-swatch--custom'
              : 'dash-qs-swatch dash-qs-swatch--custom dash-qs-swatch--active'
          }
          value={accent}
          title="Custom color"
          onInput={(event) => {
            const hex = (event.target as HTMLInputElement).value;
            setAccent(hex);
            window.applyAccentColor?.(hex);
          }}
          onChange={(event) => pickAccent((event.target as HTMLInputElement).value, true)}
        />
      </span>
    </div>
  );
}

export function AutomationsCard() {
  const { loaded, view, busyId, runNow, toggle } = useAutomationsCard();

  return (
    <article className="dash-card" data-card="automations">
      <header className="dash-card__head">
        <h3 className="dash-card__title">Automations</h3>
        <p className="dash-card__sub">What the engine runs next.</p>
        <button
          type="button"
          className="autosync-manage-btn"
          onClick={() => void window.navigateToPage?.('automations')}
        >
          All →
        </button>
      </header>
      <div className="dash-card__body">
        <div className="dash-autom-rows">
          {!loaded ? null : view.length === 0 ? (
            <div className="autosync-empty">
              <strong>No automations yet</strong>
              <span>
                Automations run the machinery on triggers you set — scans, wishlist processing,
                backups, notifications.
              </span>
              <button
                type="button"
                className="autosync-empty-cta"
                onClick={() => void window.navigateToPage?.('automations')}
              >
                Create one
              </button>
            </div>
          ) : (
            view.map((row) => (
              <div
                key={row.id}
                className={[
                  'dash-autom-row',
                  row.enabled ? '' : 'dash-autom-row--off',
                  row.running ? 'dash-autom-row--live' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                role="button"
                title="Run history"
                onClick={() =>
                  window.showAutomationHistory?.(Number(row.id), row.name, row.actionType)
                }
              >
                <span
                  className={
                    row.running
                      ? 'dash-autom-dot dash-autom-dot--live'
                      : !row.lastRun
                        ? 'dash-autom-dot'
                        : row.lastRun.ok
                          ? 'dash-autom-dot dash-autom-dot--ok'
                          : 'dash-autom-dot dash-autom-dot--err'
                  }
                  title={
                    row.running
                      ? row.running.phase
                      : !row.lastRun
                        ? 'No runs yet'
                        : row.lastRun.ok
                          ? `Last run ${row.lastRun.ago} · ${row.runCount} runs`
                          : `Last run failed: ${row.lastRun.error}`
                  }
                ></span>
                <span className="dash-autom-main">
                  <span className="dash-autom-name" title={row.name}>
                    {row.name}
                  </span>
                  {row.running ? (
                    <span className="dash-autom-meta dash-autom-phase" title={row.running.phase}>
                      {row.running.phase}
                    </span>
                  ) : (
                    <span className="dash-autom-meta">
                      <span>{row.trigger}</span>
                      {row.lastRun ? (
                        <span
                          className={
                            row.lastRun.ok
                              ? 'dash-autom-last'
                              : 'dash-autom-last dash-autom-last--bad'
                          }
                        >
                          {row.lastRun.ok ? row.lastRun.ago : `⚠ ${row.lastRun.ago}`}
                        </span>
                      ) : null}
                    </span>
                  )}
                </span>
                <span className="dash-autom-when">
                  {row.running ? (
                    <span className="dash-autom-live-pill">
                      {row.running.progress > 0 ? `${row.running.progress}%` : 'running'}
                    </span>
                  ) : row.enabled ? (
                    row.nextRun ? (
                      <span className="dash-autom-next">{row.nextRun}</span>
                    ) : null
                  ) : (
                    <span className="dash-autom-paused">paused</span>
                  )}
                </span>
                <span className="dash-autom-actions" onClick={(e) => e.stopPropagation()}>
                  {!row.running ? (
                    <>
                      <button
                        type="button"
                        className="dash-autom-run"
                        disabled={busyId !== null}
                        title={row.enabled ? 'Pause this automation' : 'Resume this automation'}
                        onClick={() => void toggle(row)}
                      >
                        {busyId === row.id ? '…' : row.enabled ? '⏸' : '▶'}
                      </button>
                      {row.enabled ? (
                        <button
                          type="button"
                          className="dash-autom-run"
                          disabled={busyId !== null}
                          title="Run this automation now"
                          onClick={() => void runNow(row)}
                        >
                          {busyId === row.id ? '…' : 'Run'}
                        </button>
                      ) : null}
                    </>
                  ) : null}
                </span>
              </div>
            ))
          )}
        </div>
        <QuickSettings />
      </div>
    </article>
  );
}
