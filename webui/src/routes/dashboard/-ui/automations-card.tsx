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

import type { AutomationApiRow, AutomationCardRow } from '../-dash.automations';

import { automationCardRows } from '../-dash.automations';

function useAutomationsCard() {
  const [rows, setRows] = useState<AutomationApiRow[] | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [busyId, setBusyId] = useState<number | string | null>(null);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    if (document.body.classList.contains('app-locked')) return;
    try {
      const res = await fetch('/api/automations');
      if (!res.ok) return;
      const data = (await res.json()) as AutomationApiRow[];
      if (!Array.isArray(data) || !mountedRef.current) return;
      setRows(data);
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

  const view = useMemo(() => automationCardRows(rows ?? [], nowMs), [rows, nowMs]);

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

  return { loaded: rows !== null, view, busyId, runNow };
}

function QuickSettings() {
  const [reduce, setReduce] = useState(
    () => localStorage.getItem('soulsync-reduce-effects') === '1',
  );
  const [maxPerf, setMaxPerf] = useState(
    () => localStorage.getItem('soulsync-max-performance') === '1',
  );

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
    </div>
  );
}

export function AutomationsCard() {
  const { loaded, view, busyId, runNow } = useAutomationsCard();

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
                className={row.enabled ? 'dash-autom-row' : 'dash-autom-row dash-autom-row--off'}
              >
                <span
                  className={
                    !row.lastRun
                      ? 'dash-autom-dot'
                      : row.lastRun.ok
                        ? 'dash-autom-dot dash-autom-dot--ok'
                        : 'dash-autom-dot dash-autom-dot--err'
                  }
                  title={
                    !row.lastRun
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
                  <span className="dash-autom-meta">
                    <span>{row.trigger}</span>
                    {row.lastRun ? (
                      <span
                        className={
                          row.lastRun.ok ? 'dash-autom-last' : 'dash-autom-last dash-autom-last--bad'
                        }
                      >
                        {row.lastRun.ok ? row.lastRun.ago : `⚠ ${row.lastRun.ago}`}
                      </span>
                    ) : null}
                  </span>
                </span>
                <span className="dash-autom-when">
                  {row.enabled ? (
                    row.nextRun ? (
                      <span className="dash-autom-next">{row.nextRun}</span>
                    ) : null
                  ) : (
                    <span className="dash-autom-paused">paused</span>
                  )}
                </span>
                <button
                  type="button"
                  className="dash-autom-run"
                  disabled={busyId !== null}
                  title="Run this automation now"
                  onClick={() => void runNow(row)}
                >
                  {busyId === row.id ? '…' : 'Run'}
                </button>
              </div>
            ))
          )}
        </div>
        <QuickSettings />
      </div>
    </article>
  );
}
