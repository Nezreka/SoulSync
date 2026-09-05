import { useCallback, useEffect, useRef, useState } from 'react';

import type { AutomationSchedule } from '../-automations.format';
import type { Automation } from '../-automations.types';

import { updateAutomationTrigger } from '../-automations.api';
import {
  automationMeta,
  automationSchedule,
  formatAction,
  formatTrigger,
} from '../-automations.format';
import { automationIcon, formatNotify } from '../-automations.icons';
import {
  type AutomationRunState,
  isFinished,
  isRunning,
  PROGRESS_HIDE_MS,
  useSecondTick,
} from '../-automations.progress';

export interface AutomationCardHandlers {
  onRun?: (a: Automation) => void;
  onToggle?: (a: Automation) => void;
  onEdit?: (a: Automation) => void;
  onDuplicate?: (a: Automation) => void;
  onAssignGroup?: (a: Automation, event: React.MouseEvent) => void;
  onDelete?: (a: Automation) => void;
  onShowHistory?: (a: Automation) => void;
  /** Refetch after a card edited something the server owns (the schedule). */
  onRefresh?: () => void;
  /** Label for a trigger/action type not in the static maps, from /blocks. */
  blockLabel?: (type: string) => string | undefined;
  /**
   * The side's master switch is off. Not a handler, but it rides this same
   * bag because the section spreads it straight through to every card.
   */
  paused?: boolean;
}

interface Props extends AutomationCardHandlers {
  automation: Automation;
  /** Live run state for this automation, if one is in flight or just ended. */
  progress?: AutomationRunState;
  /** draggable + drag handlers, empty for system automations. */
  dragProps?: Record<string, unknown>;
  /** This card is the one being dragged. */
  isDragging?: boolean;
}

/**
 * The run panel: bar, phase, and the streaming log.
 *
 * A finished panel lingers for 30s then collapses, matching the vanilla hide
 * timer — long enough to read the result, short enough not to accumulate.
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
    hidden ? '' : 'visible',
    done ? 'finished' : '',
    state.status === 'error' ? 'error' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes}>
      {/* No bar here any more: the card's top edge IS the progress bar, the
          same device the maintenance job tiles use. A second bar inside the
          panel was the same number twice. */}
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
 * The schedule state, as one coloured phrase.
 *
 * The vanilla page ticks `.auto-next-run[data-next]` from ONE module-level
 * setInterval that rewrites every match in the document each second. That
 * script is still loaded while this page runs, so a React node carrying both
 * the class AND the attribute would have two owners for one text node.
 *
 * The tile's label carries neither, so React owns it alone — and it only
 * mounts the shared tick while the label is actually a countdown.
 */
function ScheduleLabel({ schedule }: { schedule: AutomationSchedule }) {
  return <span className={`auto-tile-next ${schedule.state}`}>{schedule.label}</span>;
}

function TickingScheduleLabel({ schedule }: { schedule: AutomationSchedule }) {
  // One shared interval for every countdown on the page, mirroring the single
  // module-level timer the vanilla page used — not one timer per card.
  useSecondTick();
  return <ScheduleLabel schedule={schedule} />;
}

// ── The cadence editor ───────────────────────────────────────────────────────

const UNITS = ['minutes', 'hours', 'days'] as const;
type IntervalUnit = (typeof UNITS)[number];

/**
 * Editable trigger types, and why the list stops where it does.
 *
 * `schedule` is an interval and `daily_time` is a wall-clock time — both are
 * one control each, so they belong on the card face. `weekly_time` needs a day
 * multi-select and `monthly_time` a day-of-month picker; putting either here
 * would be a second builder growing inside a card. Those keep leading to the
 * real builder, which already does them properly.
 */
function isEditableTrigger(type: string | null | undefined): boolean {
  return type === 'schedule' || type === 'daily_time';
}

/**
 * The trigger chip, made editable in place.
 *
 * The chip already said "Every 6 hours" — it just could not be changed without
 * opening the builder, choosing the WHEN block and finding the field. This is
 * the maintenance tiles' cadence control in the automations' own vocabulary,
 * and it sits where the fact was already being read rather than adding a
 * second copy of it somewhere else on the card.
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
          : `${a.name} now runs every ${interval} ${unit}`,
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
        className="flow-trigger auto-flow-editable"
        title="Change when this runs"
        onClick={(event) => {
          event.stopPropagation();
          setEditing(true);
        }}
      >
        {label}
      </button>
    );
  }

  return (
    <span
      className="auto-cadence-edit"
      onClick={(event) => event.stopPropagation()}
      // User cards are draggable for reordering, and a drag begun inside a
      // field would reorder the list instead of selecting the text. dragstart
      // bubbles from the field, so cancelling it here keeps the inputs usable
      // without making the card itself undraggable.
      onDragStart={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }}
    >
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
        className="auto-cadence-save"
        disabled={saving}
        onClick={() => void save()}
      >
        {saving ? '…' : 'Save'}
      </button>
      <button type="button" className="auto-cadence-cancel" onClick={() => setEditing(false)}>
        Cancel
      </button>
    </span>
  );
}

/**
 * One automation, as a tile.
 *
 * What it replaced: a flat row — dot, name, flow line, then up to six facts
 * joined by "·" and five equal-weight emoji buttons that only appeared on
 * hover. Everything the card knew was in that one grey line, so the question it
 * gets asked most (when does this next happen) read the same as its least
 * (how many times has it run), and the only way to change the answer was to
 * open the builder.
 *
 * It is now the same chassis as the maintenance job tiles on the tools page:
 * a glow edge that doubles as the run's progress bar, a head that carries
 * identity and a badge that leads to history, the WHEN → DO → THEN flow with
 * an editable cadence, a schedule line stating one state in one colour, and a
 * foot pairing the leftover facts with the controls.
 *
 * Seams that survive the reshape, because the vanilla progress renderer and
 * the drag/drop code reach through them:
 *   `.automation-card[data-id]`, `data-trigger-type`, `data-action-type`,
 *   `.automation-status` (whose class is swapped wholesale by that renderer,
 *   and is NOT the card's class), `.automation-output` + `.auto-progress-*`,
 *   and a checkbox input the renderer reads to restore the dot after a run.
 */
export function AutomationCard({
  automation: a,
  blockLabel,
  progress,
  dragProps,
  isDragging,
  paused = false,
  ...on
}: Props) {
  const enabled = a.enabled === true || a.enabled === 1;
  const isSystem = a.is_system === true || a.is_system === 1;
  const running = isRunning(progress);
  const now = Date.now();
  const meta = automationMeta(a, now, paused);
  const schedule = automationSchedule(a, now, paused, running);
  /**
   * A run that ended badly is the one thing on this page worth crossing the
   * room for, and it used to be a grey text fragment at the END of the meta
   * line. It gets a state on the card and leads the facts.
   *
   * A run in flight outranks it: the error describes the LAST run, and while
   * a new one is going the card should read as busy, not broken.
   */
  const errored = Boolean(meta.error) && !running;
  const thenItems = a.then_actions ?? [];
  const delay = (a.action_config as { delay?: number } | null)?.delay ?? 0;

  const triggerLabel = `${automationIcon(a.trigger_type)} ${formatTrigger(a.trigger_type, a.trigger_config, blockLabel)}`;
  const actionLabel = `${automationIcon(a.action_type)} ${formatAction(a.action_type, blockLabel)}`;
  // 2% so a run that has just started still shows an edge rather than nothing.
  const rawPercent = Number(progress?.progress);
  const percent = running
    ? Math.max(2, Math.min(100, Number.isFinite(rawPercent) ? rawPercent : 0))
    : 0;

  // The facts left over once the schedule line took the countdown and the head
  // took the run count: what happened LAST time. Assembled as nodes rather
  // than a joined string so the separator lands between exactly the parts that
  // are present, and worst news first.
  const metaParts: React.ReactNode[] = [];
  if (meta.error)
    metaParts.push(
      <span className="auto-meta-fail" title={meta.error}>
        ⚠ Failed: {meta.error}
      </span>,
    );
  if (meta.lastRun) metaParts.push(<>Last: {meta.lastRun}</>);
  if (!meta.error && meta.result)
    metaParts.push(
      <span
        className={`auto-last-result${meta.result.kind === 'skipped' ? ' skipped' : ''}`}
        title={`Last run: ${meta.result.text}`}
      >
        {meta.result.text}
      </span>,
    );

  return (
    <div
      className={`automation-card automation-tile sched-${schedule.state}${
        enabled ? '' : ' disabled'
      }${isSystem ? ' system' : ''}${running ? ' running' : ''}${errored ? ' errored' : ''}${
        meta.paused ? ' paused' : ''
      }${isDragging ? ' dragging' : ''}`}
      {...dragProps}
      data-id={a.id}
      data-trigger-type={a.trigger_type ?? ''}
      data-action-type={a.action_type ?? ''}
    >
      {/* The glow edge IS the progress bar — the same device the maintenance
          tiles use, so a running automation reads as one object filling up
          rather than a panel appearing and shoving the layout around. */}
      <span
        className={`auto-tile-edge${running ? ' running' : ''}`}
        style={running ? { width: `${percent}%` } : undefined}
        aria-hidden="true"
      />

      <div className="auto-tile-head">
        {/* While running the dot shows the run, not the enabled state. */}
        <span
          className={`automation-status ${running ? 'running' : enabled ? 'enabled' : 'disabled'}`}
        />
        <span className="automation-name" title={a.name}>
          {a.name}
        </span>
        {meta.runs ? (
          <button
            type="button"
            className="auto-tile-badge auto-runs-link"
            title="View run history"
            onClick={(event) => {
              event.stopPropagation();
              on.onShowHistory?.(a);
            }}
          >
            {meta.runs.toLocaleString()} runs
          </button>
        ) : null}
      </div>

      <div className="automation-flow">
        {isEditableTrigger(a.trigger_type) ? (
          <TriggerCadence automation={a} label={triggerLabel} onSaved={on.onRefresh} />
        ) : (
          <span className="flow-trigger">{triggerLabel}</span>
        )}
        <span className="flow-arrow">→</span>
        {delay ? (
          <>
            <span className="flow-delay">⏳ {delay}m</span>
            <span className="flow-arrow">→</span>
          </>
        ) : null}
        <span className="flow-action">{actionLabel}</span>
        {thenItems.map((t, i) => (
          <span key={`${t.type}-${i}`}>
            <span className="flow-arrow">→</span>
            <span className="flow-notify">{formatNotify(t.type)}</span>
          </span>
        ))}
      </div>

      <div className="auto-tile-schedule">
        {schedule.ticking ? (
          <TickingScheduleLabel schedule={schedule} />
        ) : (
          <ScheduleLabel schedule={schedule} />
        )}
      </div>

      <div className="auto-tile-foot">
        <span className="automation-meta">
          {metaParts.map((part, i) => (
            <span key={i}>
              {i > 0 ? ' · ' : null}
              {part}
            </span>
          ))}
        </span>

        <div className="automation-actions">
          {/* eslint-disable-next-line jsx-a11y/label-has-associated-control -- the
              input IS the control; the label is the styled switch, as in vanilla. */}
          <label className="automation-toggle" onClick={(e) => e.stopPropagation()}>
            <input
              type="checkbox"
              checked={enabled}
              aria-label={`${enabled ? 'Disable' : 'Enable'} ${a.name}`}
              onChange={() => on.onToggle?.(a)}
            />
            <span className="toggle-slider" />
          </label>
          <button
            type="button"
            className="automation-run-btn"
            title="Run now"
            onClick={(e) => {
              e.stopPropagation();
              on.onRun?.(a);
            }}
          >
            ▶
          </button>
          <button
            type="button"
            className="automation-edit-btn"
            title="Edit"
            onClick={(e) => {
              e.stopPropagation();
              on.onEdit?.(a);
            }}
          >
            ⚙
          </button>
          {/* System automations are seeded and undeletable, so they expose no
              duplicate / group / delete affordances at all. */}
          {isSystem ? null : (
            <>
              <button
                type="button"
                className="automation-dupe-btn"
                title="Duplicate"
                onClick={(e) => {
                  e.stopPropagation();
                  on.onDuplicate?.(a);
                }}
              >
                📋
              </button>
              <button
                type="button"
                className={`automation-group-btn${a.group_name ? ' grouped' : ''}`}
                data-group={a.group_name ?? ''}
                title={a.group_name ? `Group: ${a.group_name}` : 'Assign group'}
                onClick={(e) => {
                  e.stopPropagation();
                  on.onAssignGroup?.(a, e);
                }}
              >
                📁
              </button>
              {/* The one irreversible control, kept off the end of a row of
                  five look-alikes it used to sit inside. */}
              <span className="auto-tile-sep" aria-hidden="true" />
              <button
                type="button"
                className="automation-delete-btn"
                title="Delete"
                onClick={(e) => {
                  e.stopPropagation();
                  on.onDelete?.(a);
                }}
              >
                🗑
              </button>
            </>
          )}
        </div>
      </div>

      {progress ? <ProgressPanel state={progress} /> : null}
    </div>
  );
}
