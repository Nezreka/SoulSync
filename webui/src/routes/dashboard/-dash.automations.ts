/**
 * Pure core for the dashboard's Automations card — the live/upcoming view of
 * /api/automations, beside the Sync band.
 *
 * Playlist-pipeline and personalized-pipeline automations are EXCLUDED here:
 * those are the Sync band's rows, and showing them twice would recreate the
 * duplication the band merge just removed. This card is everything else the
 * engine runs — watchlist scans, wishlist processing, backups, notifications.
 *
 * Trigger labels reuse the Auto-Sync formatters where the vocabulary
 * overlaps (intervalLabel / weeklyLabel); the timed variants follow
 * automation_engine.py's trigger map (:460 — schedule, daily_time,
 * weekly_time, monthly_time) and everything else renders as an event.
 */

import { intervalLabel, nextRunText, parseDbUtc, weeklyLabel } from './-dash.autosync';
import { relativeTime } from './-dash.library';

export interface AutomationApiRow {
  id: number | string;
  name?: string;
  enabled?: number | boolean;
  trigger_type?: string;
  trigger_config?: Record<string, unknown> | string | null;
  action_type?: string;
  last_run?: string | null;
  next_run?: string | null;
  run_count?: number;
  last_error?: string | null;
  is_system?: number | boolean;
  [key: string]: unknown;
}

/** The Sync band's rows — never duplicated here. */
const SYNCBAND_ACTIONS = new Set(['playlist_pipeline', 'personalized_pipeline']);

/** This dashboard is the MUSIC side; the shared engine also runs the video
 *  side's automations, whose action types all carry the video_ prefix
 *  (core/automation/handlers/registration.py) — they belong to the video
 *  dashboard, not here (Boulder's report). */
function isVideoAction(actionType: string): boolean {
  return actionType.startsWith('video_');
}

function config(row: AutomationApiRow): Record<string, unknown> {
  const raw = row.trigger_config;
  if (raw && typeof raw === 'object') return raw as Record<string, unknown>;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return {};
    }
  }
  return {};
}

/** autoSyncHoursFromTrigger's unit math (auto-sync.js:52). */
function hoursFrom(cfg: Record<string, unknown>): number {
  const interval = parseInt(String(cfg.interval), 10) || 0;
  const unit = String(cfg.unit || 'hours');
  if (!interval) return 0;
  if (unit === 'minutes') return Math.max(1, Math.round(interval / 60));
  if (unit === 'days') return interval * 24;
  if (unit === 'weeks') return interval * 168;
  return interval;
}

function humanizeEvent(name: string): string {
  return name.replace(/[_.]/g, ' ').trim();
}

export function triggerLabel(row: AutomationApiRow): string {
  const cfg = config(row);
  const type = row.trigger_type || '';
  if (type === 'schedule') {
    const hours = hoursFrom(cfg);
    return hours ? intervalLabel(hours) : 'Scheduled';
  }
  if (type === 'daily_time') return `Daily @ ${String(cfg.time || '00:00')}`;
  if (type === 'weekly_time') {
    return weeklyLabel(
      String(cfg.time || '00:00'),
      Array.isArray(cfg.days) ? (cfg.days as string[]) : [],
    );
  }
  if (type === 'monthly_time') {
    return `Monthly · day ${String(cfg.day ?? 1)} @ ${String(cfg.time || '00:00')}`;
  }
  const event = String(cfg.event || cfg.event_type || type || 'event');
  return `On ${humanizeEvent(event)}`;
}

export interface AutomationProgressState {
  status?: string;
  phase?: string;
  progress?: number;
  [key: string]: unknown;
}

export interface AutomationCardRow {
  id: number | string;
  name: string;
  /** showAutomationHistory needs it (the shared run-history modal). */
  actionType: string;
  trigger: string;
  enabled: boolean;
  nextRun: string | null;
  lastRun: { ago: string; ok: boolean; error: string | null } | null;
  /** Live state from /api/automations/progress while a run is in flight. */
  running: { phase: string; progress: number } | null;
  runCount: number;
}

export function automationCardRows(
  rows: AutomationApiRow[],
  nowMs: number,
  progress: Record<string, AutomationProgressState> = {},
): AutomationCardRow[] {
  const out: AutomationCardRow[] = [];
  for (const row of rows || []) {
    const actionType = String(row.action_type || '');
    if (SYNCBAND_ACTIONS.has(actionType) || isVideoAction(actionType)) continue;
    const enabled = row.enabled !== false && row.enabled !== 0;
    const live = progress[String(row.id)];
    out.push({
      id: row.id,
      name: String(row.name || `Automation #${row.id}`),
      actionType: String(row.action_type || ''),
      trigger: triggerLabel(row),
      enabled,
      running:
        live?.status === 'running'
          ? {
              phase: String(live.phase || 'Running...'),
              progress: Math.max(0, Math.min(100, Number(live.progress) || 0)),
            }
          : null,
      nextRun: enabled ? nextRunText(row.next_run, nowMs) : null,
      lastRun: row.last_run
        ? {
            ago: relativeTime(
              // relativeTime parses via Date — pre-resolve the db-UTC stamp.
              new Date(parseDbUtc(String(row.last_run))).toISOString(),
              nowMs,
            ),
            ok: !row.last_error,
            error: row.last_error ? String(row.last_error) : null,
          }
        : null,
      runCount: Number(row.run_count) || 0,
    });
  }

  // Running first; then soonest-to-fire by raw stamp (not display buckets);
  // no next_run (event-triggered) after timed ones; disabled last. Stable
  // within groups.
  const stamp = (row: AutomationApiRow) => {
    if (!row.next_run) return Infinity;
    const t = parseDbUtc(String(row.next_run));
    return Number.isFinite(t) ? t : Infinity;
  };
  const byId = new Map((rows || []).map((r) => [r.id, r]));
  return out
    .map((r, i) => ({ r, i }))
    .sort((a, b) => {
      if (!!a.r.running !== !!b.r.running) return a.r.running ? -1 : 1;
      if (a.r.enabled !== b.r.enabled) return a.r.enabled ? -1 : 1;
      const sa = stamp(byId.get(a.r.id)!);
      const sb = stamp(byId.get(b.r.id)!);
      if (sa !== sb) return sa - sb;
      return a.i - b.i;
    })
    .map(({ r }) => r);
}
