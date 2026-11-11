import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { AutomationSchedule } from '../-automations.format';
import type { Automation } from '../-automations.types';

import { automationRunsQueryOptions, updateAutomationTrigger } from '../-automations.api';
import {
  automationMeta,
  automationSchedule,
  displayName,
  formatAction,
  formatInterval,
  formatTrigger,
  resultFigures,
  timeAgo,
} from '../-automations.format';
import { formatNotify } from '../-automations.icons';
import {
  type AutomationRunState,
  isFinished,
  isRunning,
  PROGRESS_HIDE_MS,
  useSecondTick,
} from '../-automations.progress';
import { AutomationArt } from './automation-art';

export interface AutomationCardHandlers {
  onRun?: (a: Automation) => void;
  onToggle?: (a: Automation) => void;
  onEdit?: (a: Automation) => void;
  onDuplicate?: (a: Automation) => void;
  onAssignGroup?: (a: Automation, event: React.MouseEvent) => void;
  onDelete?: (a: Automation) => void;
  onShowHistory?: (a: Automation) => void;
  /** Refetch after a row edited something the server owns (the schedule). */
  onRefresh?: () => void;
  /** Label for a trigger/action type not in the static maps, from /blocks. */
  blockLabel?: (type: string) => string | undefined;
  /**
   * The side's master switch is off. Not a handler, but it rides this same
   * bag because the section spreads it straight through to every row.
   */
  paused?: boolean;
}

interface Props extends AutomationCardHandlers {
  automation: Automation;
  /** Live run state for this automation, if one is in flight or just ended. */
  progress?: AutomationRunState;
  /** draggable + drag handlers, empty for system automations. */
  dragProps?: Record<string, unknown>;
  /** This row is the one being dragged. */
  isDragging?: boolean;
  /** The row is opened to its detail. Uncontrolled when left out. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

/**
 * The run's phase and streaming log, inside an opened row.
 *
 * A finished panel lingers for 30s then collapses, matching the vanilla hide
 * timer: long enough to read the result, short enough not to accumulate.
 */
function ProgressPanel({ state }: { state: AutomationRunState }) {
  const [hidden, setHidden] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const done = isFinished(state);

  useEffect(() => {
    if (!done) {
      // A re-run inside the hide window must bring the panel back.
      setHidden(false);
      return;
    }
    const id = setTimeout(() => setHidden(true), PROGRESS_HIDE_MS);
    return () => clearTimeout(id);
  }, [done, state.status]);

  const lines = state.log ?? [];
  useEffect(() => {
    // Follow the tail as lines arrive, as the vanilla renderer did.
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines.length]);

  const classes = [
    'automation-output',
    'automx-log',
    hidden ? '' : 'visible',
    done ? 'finished' : '',
    state.status === 'error' ? 'error' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes}>
      <div className="auto-progress-phase">{state.phase ?? ''}</div>
      <div className="auto-progress-log" ref={logRef}>
        {lines.map((line, i) => (
          <div key={i} className={`auto-log-line ${line.type || 'info'}`}>
            {line.text}
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * The schedule state, as one phrase. Only a live countdown mounts the shared
 * second tick, so a page of switched-off rows re-renders nothing.
 */
function ScheduleLabel({ schedule }: { schedule: AutomationSchedule }) {
  return (
    <span className={`automx-next ${schedule.state}`}>
      {schedule.state === 'waiting' ? schedule.label.replace(/^Next /, '') : schedule.label}
    </span>
  );
}

function TickingScheduleLabel({ schedule }: { schedule: AutomationSchedule }) {
  useSecondTick();
  return <ScheduleLabel schedule={schedule} />;
}

// ── The cadence editor ───────────────────────────────────────────────────────

const UNITS = ['minutes', 'hours', 'days'] as const;
type IntervalUnit = (typeof UNITS)[number];

/**
 * Editable trigger types, and why the list stops where it does.
 *
 * `schedule` is an interval and `daily_time` is a wall-clock time: one control
 * each, so they can be changed in place. `weekly_time` needs a day
 * multi-select and `monthly_time` a day-of-month picker; putting either here
 * would be a second builder growing inside a row. Those lead to the real
 * builder, which already does them properly.
 */
function isEditableTrigger(type: string | null | undefined): boolean {
  return type === 'schedule' || type === 'daily_time';
}

/**
 * The When block, editable in place.
 *
 * The whole existing trigger_config is spread into the PUT: `daily_time` also
 * carries `tz`, and sending only `time` would silently re-home a schedule to
 * the server default. The API nulls `next_run` whenever the trigger shape
 * changes, so the scheduler re-arms rather than firing on the old timestamp.
 */
function TriggerCadence({
  automation: a,
  label,
  onSaved,
}: {
  automation: Automation;
  label: string;
  onSaved?: () => void;
}) {
  const config = (a.trigger_config ?? {}) as Record<string, unknown>;
  const daily = a.trigger_type === 'daily_time';

  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [interval, setInterval] = useState(1);
  const [unit, setUnit] = useState<IntervalUnit>('hours');
  const [time, setTime] = useState('00:00');

  // Reset from the server copy every time the editor opens, so an abandoned
  // edit never becomes the starting point of the next one.
  useEffect(() => {
    if (editing) return;
    const raw = Number.parseInt(String(config.interval ?? 1), 10);
    setInterval(Number.isFinite(raw) && raw > 0 ? raw : 1);
    setUnit(
      (UNITS as readonly string[]).includes(String(config.unit))
        ? (config.unit as IntervalUnit)
        : 'hours',
    );
    setTime(typeof config.time === 'string' && config.time ? config.time : '00:00');
    // config is a fresh object identity each render; the values are the input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, config.interval, config.unit, config.time]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const next = daily
        ? { ...config, time }
        : { ...config, interval: Math.max(1, interval), unit };
      await updateAutomationTrigger(a.id, next);
      window.showToast?.(
        daily
          ? `${a.name} now runs daily at ${time}`
          : `${a.name} now runs ${formatInterval(interval, unit).replace(/^Every/, 'every')}`,
        'success',
      );
      setEditing(false);
      onSaved?.();
    } catch {
      window.showToast?.('Could not change the schedule', 'error');
    } finally {
      setSaving(false);
    }
  }, [a.id, a.name, config, daily, interval, onSaved, time, unit]);

  if (!editing) {
    return (
      <button
        type="button"
        className="automx-block automx-block-edit"
        title="Change when this runs"
        onClick={() => setEditing(true)}
      >
        <small>When</small>
        <b>{label}</b>
      </button>
    );
  }

  return (
    <span
      className="automx-block automx-cadence"
      // User rows are draggable, and a drag begun inside a field would move
      // the row instead of selecting the text. dragstart bubbles from the
      // field, so cancelling it here keeps the inputs usable.
      onDragStart={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }}
    >
      <small>When</small>
      <span className="automx-cadence-fields">
        {daily ? (
          <input
            type="time"
            aria-label="Time of day"
            value={time}
            onChange={(event) => setTime(event.target.value)}
          />
        ) : (
          <>
            <input
              type="number"
              min="1"
              step="1"
              aria-label="Interval"
              value={String(interval)}
              onChange={(event) =>
                setInterval(Math.max(1, Number.parseInt(event.target.value, 10) || 1))
              }
            />
            <select
              aria-label="Interval unit"
              value={unit}
              onChange={(event) => setUnit(event.target.value as IntervalUnit)}
            >
              {UNITS.map((u) => (
                <option value={u} key={u}>
                  {u}
                </option>
              ))}
            </select>
          </>
        )}
        <button
          type="button"
          className="automx-cap automx-cap-primary"
          disabled={saving}
          onClick={() => void save()}
        >
          {saving ? '…' : 'Save'}
        </button>
        <button type="button" className="automx-cap" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </span>
    </span>
  );
}

const RUN_WORDS: Record<string, string> = {
  completed: 'Completed',
  complete: 'Completed',
  error: 'Failed',
  skipped: 'Skipped',
};

function seconds(value: number | null | undefined): string {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n) || n < 1) return '<1s';
  if (n < 90) return `${Math.round(n)}s`;
  return `${Math.round(n / 60)}m`;
}

/** The last few stored runs. Fetched when the row opens, not before. */
function LastRuns({ automation: a, now }: { automation: Automation; now: number }) {
  const runs = useQuery(automationRunsQueryOptions(a.id));
  const list = runs.data?.history ?? [];
  return (
    <div className="automx-runs">
      <h4>Last runs</h4>
      {list.length === 0 ? (
        <p className="automx-runs-none">{runs.isPending ? 'Loading…' : 'No runs kept yet.'}</p>
      ) : (
        list.map((run) => {
          const status = String(run.status ?? '');
          const words = RUN_WORDS[status] ?? status;
          const said = run.summary && run.summary !== status ? run.summary : words;
          return (
            <div className="automx-run" key={run.id}>
              <i className={`automx-run-dot ${status}`} aria-hidden="true" />
              <span className="automx-run-what" title={said}>
                {said}
              </span>
              <span className="automx-run-took">{seconds(run.duration_seconds)}</span>
              <span className="automx-run-when">
                {run.started_at ? timeAgo(run.started_at, now) : ''}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}

/**
 * One automation, as a row that opens.
 *
 * What it replaced: a tile per automation. Eighty of them made a wall, and a
 * tile gave the same weight to the one fact a person reads (when is it next,
 * did it work) as to five look-alike icon buttons. A row keeps the two facts
 * and the two actions used daily, the switch and Run. Everything else is one
 * click in: what it does as When, Do and Then, what the last run counted, the
 * last few runs, and the rest of the actions by name.
 *
 * `data-id`, `data-trigger-type` and `data-action-type` stay on the root, and
 * the run log keeps its `.automation-output` / `.auto-progress-*` classes.
 */
export function AutomationCard({
  automation: a,
  blockLabel,
  progress,
  dragProps,
  isDragging,
  paused = false,
  open: openProp,
  onOpenChange,
  ...on
}: Props) {
  const [openLocal, setOpenLocal] = useState(false);
  const open = openProp ?? openLocal;
  const setOpen = (next: boolean) => {
    setOpenLocal(next);
    onOpenChange?.(next);
  };

  const enabled = a.enabled === true || a.enabled === 1;
  const isSystem = a.is_system === true || a.is_system === 1;
  const running = isRunning(progress);
  const now = Date.now();
  const meta = automationMeta(a, now, paused);
  const schedule = automationSchedule(a, now, paused, running);
  // A run in flight outranks the last error: while a new one is going the row
  // should read as busy, not broken.
  const errored = Boolean(meta.error) && !running;
  const thenItems = a.then_actions ?? [];
  const delay = (a.action_config as { delay?: number } | null)?.delay ?? 0;

  const name = displayName(a);
  const triggerLabel = formatTrigger(a.trigger_type, a.trigger_config, blockLabel);
  const actionLabel = a.playlist?.name
    ? `Sync ${a.playlist.name}`
    : formatAction(a.action_type, blockLabel);
  // 2% so a run that has just started still shows an edge rather than nothing.
  const rawPercent = Number(progress?.progress);
  const percent = running
    ? Math.max(2, Math.min(100, Number.isFinite(rawPercent) ? rawPercent : 0))
    : 0;

  const last = running
    ? { text: progress?.phase || 'Running', tone: 'run' }
    : meta.error
      ? { text: `Failed: ${meta.error}`, tone: 'bad' }
      : meta.result
        ? { text: meta.result.text, tone: meta.result.kind === 'skipped' ? 'quiet' : '' }
        : null;

  const figures = resultFigures(a.last_result);
  const stop = (event: React.SyntheticEvent) => event.stopPropagation();

  return (
    <div
      className={`automx-item sched-${schedule.state}${open ? ' open' : ''}${
        enabled ? '' : ' off'
      }${isSystem ? ' system' : ''}${running ? ' running' : ''}${errored ? ' errored' : ''}${
        isDragging ? ' dragging' : ''
      }`}
      {...dragProps}
      data-id={a.id}
      data-trigger-type={a.trigger_type ?? ''}
      data-action-type={a.action_type ?? ''}
    >
      {/* the row's top edge is the run's progress bar */}
      <span
        className={`automx-edge${running ? ' running' : ''}`}
        style={running ? { width: `${percent}%` } : undefined}
        aria-hidden="true"
      />

      {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions --
          the chevron button below is the keyboard path; the row is a bigger
          target for the same thing */}
      <div className="automx-row" onClick={() => setOpen(!open)}>
        <span className="automx-tile">
          <AutomationArt automation={a} />
        </span>
        <div className="automx-row-text">
          <div className="automx-row-name" title={a.name}>
            {name}
          </div>
          <div className="automx-row-flow">
            {triggerLabel}
            {delay ? ` → wait ${delay}m` : ''} → {actionLabel}
            {thenItems.map((t) => ` → ${formatNotify(t.type)}`).join('')}
          </div>
        </div>
        <div className={`automx-row-last ${last?.tone ?? ''}`} title={last?.text}>
          {last?.text ?? ''}
        </div>
        <div className="automx-row-when">
          {schedule.ticking ? (
            <TickingScheduleLabel schedule={schedule} />
          ) : (
            <ScheduleLabel schedule={schedule} />
          )}
          {meta.lastRun ? <small>ran {meta.lastRun}</small> : null}
        </div>
        <div className="automx-row-acts">
          {/* eslint-disable-next-line jsx-a11y/label-has-associated-control, jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions --
              the input IS the control; the label is the styled switch */}
          <label className="automx-switch" onClick={stop}>
            <input
              type="checkbox"
              checked={enabled}
              aria-label={`${enabled ? 'Disable' : 'Enable'} ${a.name}`}
              onChange={() => on.onToggle?.(a)}
            />
            <span aria-hidden="true" />
          </label>
          <button
            type="button"
            className="automx-icon-btn"
            title="Run now"
            aria-label={`Run ${a.name} now`}
            onClick={(event) => {
              stop(event);
              on.onRun?.(a);
            }}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M4 2.5v11l9-5.5z" />
            </svg>
          </button>
          <button
            type="button"
            className="automx-icon-btn automx-chevron"
            aria-expanded={open}
            aria-label={`${open ? 'Hide' : 'Show'} details for ${a.name}`}
            onClick={(event) => {
              stop(event);
              setOpen(!open);
            }}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="m4 6 4 4 4-4" />
            </svg>
          </button>
        </div>
      </div>

      {open ? (
        <div className="automx-detail">
          <div className="automx-detail-main">
            <div className="automx-flow">
              {isEditableTrigger(a.trigger_type) ? (
                <TriggerCadence automation={a} label={triggerLabel} onSaved={on.onRefresh} />
              ) : (
                <span className="automx-block">
                  <small>When</small>
                  <b>{triggerLabel}</b>
                </span>
              )}
              {delay ? (
                <>
                  <span className="automx-arrow">→</span>
                  <span className="automx-block">
                    <small>Wait</small>
                    <b>{delay} minutes</b>
                  </span>
                </>
              ) : null}
              <span className="automx-arrow">→</span>
              <span className="automx-block">
                <small>Do</small>
                <b>{actionLabel}</b>
              </span>
              {thenItems.map((t, i) => (
                <span className="automx-then" key={`${t.type}-${i}`}>
                  <span className="automx-arrow">→</span>
                  <span className="automx-block">
                    <small>Then</small>
                    <b>{formatNotify(t.type)}</b>
                  </span>
                </span>
              ))}
            </div>

            {meta.error ? <p className="automx-detail-error">Failed: {meta.error}</p> : null}

            <div className="automx-figures">
              {figures.map((f) => (
                <div key={f.label}>
                  <b>{f.value}</b>
                  <span>{f.label}</span>
                </div>
              ))}
              <div>
                <b>{(a.run_count ?? 0).toLocaleString()}</b>
                <span>{a.run_count === 1 ? 'run so far' : 'runs so far'}</span>
              </div>
            </div>

            <div className="automx-detail-acts">
              <button
                type="button"
                className="automx-cap automx-cap-primary"
                onClick={() => on.onRun?.(a)}
              >
                Run now
              </button>
              <button type="button" className="automx-cap" onClick={() => on.onEdit?.(a)}>
                Edit
              </button>
              <button type="button" className="automx-cap" onClick={() => on.onShowHistory?.(a)}>
                Run history
              </button>
              {/* System automations are seeded and undeletable, so they have
                  no duplicate, group or delete. */}
              {isSystem ? null : (
                <>
                  <button type="button" className="automx-cap" onClick={() => on.onDuplicate?.(a)}>
                    Duplicate
                  </button>
                  <button
                    type="button"
                    className="automx-cap"
                    title={a.group_name ? `Group: ${a.group_name}` : 'Assign group'}
                    onClick={(event) => on.onAssignGroup?.(a, event)}
                  >
                    Move to group
                  </button>
                  <button
                    type="button"
                    className="automx-cap automx-cap-danger"
                    onClick={() => on.onDelete?.(a)}
                  >
                    Delete
                  </button>
                </>
              )}
            </div>
          </div>
          <LastRuns automation={a} now={now} />
          {progress ? <ProgressPanel state={progress} /> : null}
        </div>
      ) : null}
    </div>
  );
}
