import { useEffect, useRef, useState } from 'react';

export const AUTOMATION_PROGRESS_EVENT = 'ss:automation-progress';

export interface AutomationLogLine {
  text: string;
  type?: string;
}

export interface AutomationRunState {
  status?: 'running' | 'finished' | 'error' | string;
  progress?: number;
  phase?: string;
  log?: AutomationLogLine[];
}

/** automation id -> its in-flight run state. */
export type AutomationProgressMap = Record<number, AutomationRunState>;

/**
 * How long a finished panel stays on screen before collapsing.
 * 30s, matching _autoProgressHideTimers in stats-automations.js.
 */
export const PROGRESS_HIDE_MS = 30_000;

function normalise(raw: unknown): AutomationProgressMap {
  if (!raw || typeof raw !== 'object') return {};
  const out: AutomationProgressMap = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const id = Number.parseInt(key, 10);
    // The socket keys these by id-as-string. A non-numeric key — including
    // the `error` of a failed catch-up response — is not an automation and
    // must not become a NaN entry.
    if (Number.isNaN(id) || !value || typeof value !== 'object') continue;
    out[id] = value as AutomationRunState;
  }
  return out;
}

/**
 * Live automation run progress.
 *
 * The socket frame is emitted app-wide by core.js and mirrored onto the
 * window as ss:automation-progress. Frames are MERGED rather than replacing
 * the map: the server sends only the automations it has news about, so
 * replacing would make a still-running automation's panel vanish whenever an
 * unrelated one reported.
 *
 * `seed` hydrates from /api/automations/progress so a page opened mid-run
 * shows the run already in flight instead of waiting for the next frame.
 */
export function useAutomationProgress(seed?: unknown): AutomationProgressMap {
  // The seed arrives as the raw /api/automations/progress body, so it goes
  // through the same normaliser as a socket frame — it carries an `error` key
  // on failure and string ids on success.
  const [progress, setProgress] = useState<AutomationProgressMap>(() => normalise(seed));

  // Applied ONCE, guarded by a ref rather than by the effect's dependency.
  //
  // `seed` is an object, so depending on its identity re-runs this on every
  // render for any caller that does not memoise it — each run calling
  // setProgress, which renders again, forever. React Query's `data` happens to
  // be referentially stable, so the page never looped; a plain object literal
  // hung the test worker outright. A catch-up is a one-shot anyway.
  const hasSeeded = useRef(false);
  useEffect(() => {
    if (hasSeeded.current) return;
    const seeded = normalise(seed);
    if (Object.keys(seeded).length === 0) return;
    hasSeeded.current = true;
    // Existing live state wins: a socket frame that landed while the catch-up
    // request was in flight is newer than the catch-up.
    setProgress((prev) => ({ ...seeded, ...prev }));
  }, [seed]);

  useEffect(() => {
    const onFrame = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      const incoming = normalise(detail);
      if (Object.keys(incoming).length === 0) return;
      setProgress((prev) => ({ ...prev, ...incoming }));
    };
    window.addEventListener(AUTOMATION_PROGRESS_EVENT, onFrame);
    return () => window.removeEventListener(AUTOMATION_PROGRESS_EVENT, onFrame);
  }, []);

  return progress;
}

/** Whether a run state should still be showing its panel. */
export function isRunning(state: AutomationRunState | undefined): boolean {
  return state?.status === 'running';
}

export function isFinished(state: AutomationRunState | undefined): boolean {
  return state?.status === 'finished' || state?.status === 'error';
}

// ── shared 1s tick ──────────────────────────────────────────────────────────
//
// The "Next: in 4m" countdowns need re-rendering once a second. The vanilla
// page did that with ONE module-level interval rewriting every
// `.auto-next-run` node; giving each card its own interval would run N timers
// and re-render N components per second for the same result.
//
// One interval, shared by every subscriber, started on the first and stopped
// with the last — so a page with no scheduled automations runs no timer at all.

let tickHandle: ReturnType<typeof setInterval> | null = null;
const tickListeners = new Set<() => void>();

function subscribeToTick(listener: () => void): () => void {
  tickListeners.add(listener);
  if (tickHandle === null) {
    tickHandle = setInterval(() => {
      for (const fn of tickListeners) fn();
    }, 1000);
  }
  return () => {
    tickListeners.delete(listener);
    if (tickListeners.size === 0 && tickHandle !== null) {
      clearInterval(tickHandle);
      tickHandle = null;
    }
  };
}

/** Re-render once a second, off a single interval shared by all callers. */
export function useSecondTick(): void {
  const [, force] = useState(0);
  useEffect(() => subscribeToTick(() => force((n) => n + 1)), []);
}

/** Test-only: how many timers are live. Exposed to pin the "one interval" claim. */
export function _tickDiagnostics(): { running: boolean; listeners: number } {
  return { running: tickHandle !== null, listeners: tickListeners.size };
}
