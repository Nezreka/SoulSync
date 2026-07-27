import { useEffect, useState } from 'react';

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
    // The socket keys these by id-as-string; a non-numeric key is not an
    // automation and must not become NaN in the map.
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
export function useAutomationProgress(seed?: AutomationProgressMap): AutomationProgressMap {
  const [progress, setProgress] = useState<AutomationProgressMap>(() => seed ?? {});

  useEffect(() => {
    if (seed && Object.keys(seed).length > 0) {
      setProgress((prev) => ({ ...seed, ...prev }));
    }
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
