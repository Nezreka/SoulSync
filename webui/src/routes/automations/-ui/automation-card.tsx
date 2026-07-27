import { useEffect, useRef, useState } from 'react';

import type { Automation } from '../-automations.types';

import { automationMeta, formatAction, formatTrigger, timeUntil } from '../-automations.format';
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
  /** Label for a trigger/action type not in the static maps, from /blocks. */
  blockLabel?: (type: string) => string | undefined;
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
      <div className="auto-progress-bar-wrap">
        {/* A finished run reads 100% regardless of the last reported number. */}
        <div
          className="auto-progress-bar"
          style={{ width: `${done ? 100 : (state.progress ?? 0)}%` }}
        />
      </div>
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
 * Live "Next: in 4m" countdown.
 *
 * The vanilla page ticks these from ONE module-level setInterval that rewrites
 * `.auto-next-run[data-next]` across the whole document every second. That
 * script is still loaded while this page runs, so a React node carrying both
 * the class AND the attribute would be written to by vanilla between renders
 * and overwritten by React on the next one — two owners for one text node.
 *
 * Keeping the class (it is styled) but omitting data-next puts this node
 * outside that selector, so React owns it alone.
 */
function NextRun({ nextRun }: { nextRun: string }) {
  // One shared interval for every countdown on the page, mirroring the single
  // module-level timer the vanilla page used — not one timer per card.
  useSecondTick();
  return <span className="auto-next-run">Next: {timeUntil(nextRun)}</span>;
}

/** One automation row. Markup mirrors renderAutomationCard so the CSS applies unchanged. */
export function AutomationCard({
  automation: a,
  blockLabel,
  progress,
  dragProps,
  isDragging,
  ...on
}: Props) {
  const enabled = a.enabled === true || a.enabled === 1;
  const isSystem = a.is_system === true || a.is_system === 1;
  const running = isRunning(progress);
  const meta = automationMeta(a);
  const thenItems = a.then_actions ?? [];
  const delay = (a.action_config as { delay?: number } | null)?.delay ?? 0;

  const triggerLabel = `${automationIcon(a.trigger_type)} ${formatTrigger(a.trigger_type, a.trigger_config, blockLabel)}`;
  const actionLabel = `${automationIcon(a.action_type)} ${formatAction(a.action_type, blockLabel)}`;

  // Assembled as nodes rather than a joined string so the separator lands
  // between exactly the parts that are present, as the vanilla join did.
  const metaParts: React.ReactNode[] = [];
  if (meta.lastRun) metaParts.push(<>Last: {meta.lastRun}</>);
  if (meta.nextRun && a.next_run) metaParts.push(<NextRun nextRun={a.next_run} />);
  if (meta.listening) metaParts.push(<>Listening</>);
  if (meta.runs)
    metaParts.push(
      <span
        className="auto-runs-link"
        title="View run history"
        onClick={(e) => {
          e.stopPropagation();
          on.onShowHistory?.(a);
        }}
      >
        Runs: {meta.runs}
      </span>,
    );
  if (meta.error) metaParts.push(<>Error: {meta.error}</>);
  else if (meta.result)
    metaParts.push(
      <span className="auto-last-result" title={`Last run: ${meta.result.full}`}>
        {meta.result.shown}
      </span>,
    );

  return (
    <div
      className={`automation-card${enabled ? '' : ' disabled'}${isSystem ? ' system' : ''}${
        running ? ' running' : ''
      }${isDragging ? ' dragging' : ''}`}
      {...dragProps}
      data-id={a.id}
      data-trigger-type={a.trigger_type ?? ''}
      data-action-type={a.action_type ?? ''}
    >
      {/* While running the dot shows the run, not the enabled state. */}
      <div
        className={`automation-status ${running ? 'running' : enabled ? 'enabled' : 'disabled'}`}
      />
      <div className="automation-info">
        <div className="automation-name" title={a.name}>
          {a.name}
        </div>
        <div className="automation-flow">
          <span className="flow-trigger">{triggerLabel}</span>
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
        <div className="automation-meta">
          {metaParts.map((part, i) => (
            <span key={i}>
              {i > 0 ? ' · ' : null}
              {part}
            </span>
          ))}
        </div>
      </div>
      <div className="automation-actions">
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

      {progress ? <ProgressPanel state={progress} /> : null}
    </div>
  );
}
