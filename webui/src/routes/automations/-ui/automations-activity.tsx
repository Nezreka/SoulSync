import type {
  Automation,
  AutomationActivity,
  AutomationActivityDay,
  AutomationActivityRow,
} from '../-automations.types';

import { displayName, timeAgo } from '../-automations.format';

interface Props {
  activity: AutomationActivity | undefined;
  loading: boolean;
  automations: Automation[];
  /** Open one automation's full run history. */
  onShowHistory: (a: Automation) => void;
}

const BAR_PX = 116;

function took(value: number): string {
  if (!Number.isFinite(value) || value < 1) return '<1s';
  if (value < 90) return `${Math.round(value)}s`;
  return `${Math.round(value / 60)}m`;
}

function dayLabel(day: string): string {
  // a local calendar day, so it is built from its parts, never parsed as utc
  const [y, m, d] = day.split('-').map(Number);
  if (!y || !m || !d) return day;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
}

function what(row: AutomationActivityRow): string {
  if (row.failed && row.last_error) return row.last_error;
  const done = row.runs - row.failed - row.skipped;
  // a clean row has nothing to add to its chip
  if (!row.failed && !row.skipped) return '';
  if (done > 0) return `${done} completed`;
  return row.skipped ? 'Nothing to do each time' : '';
}

function DayBars({ days }: { days: AutomationActivityDay[] }) {
  const peak = Math.max(1, ...days.map((d) => d.completed + d.failed + d.skipped));
  const px = (n: number) => `${Math.round((n / peak) * BAR_PX)}px`;
  return (
    <>
      <div className="automx-bars">
        {days.map((d, i) => (
          <div
            className="automx-bar-day"
            key={d.day}
            style={{ '--automx-i': i } as React.CSSProperties}
            title={`${dayLabel(d.day)}: ${d.completed} completed, ${d.failed} failed, ${d.skipped} skipped`}
          >
            <i className="automx-bar-ok" style={{ height: px(d.completed) }} />
            {d.failed ? <i className="automx-bar-bad" style={{ height: px(d.failed) }} /> : null}
            {d.skipped ? <i className="automx-bar-skip" style={{ height: px(d.skipped) }} /> : null}
          </div>
        ))}
      </div>
      <div className="automx-bar-labels" aria-hidden="true">
        {days.map((d) => (
          <span key={d.day}>{dayLabel(d.day)}</span>
        ))}
      </div>
      <div className="automx-legend">
        <span>
          <i className="automx-bar-ok" />
          Completed
        </span>
        <span>
          <i className="automx-bar-bad" />
          Failed
        </span>
        <span>
          <i className="automx-bar-skip" />
          Skipped
        </span>
      </div>
    </>
  );
}

/**
 * What the automations did: the last 24 hours by automation, trouble first,
 * over a week of runs by day.
 *
 * Runs are grouped by automation on purpose. A flat list of runs is four
 * hundred lines a day, and three hundred of them are "Clean Completed
 * Downloads, completed". One line per automation says the same and leaves
 * room for the one that failed twenty three times.
 */
export function AutomationsActivity({ activity, loading, automations, onShowHistory }: Props) {
  const rows = activity?.recent ?? [];
  const days = activity?.days ?? [];
  const totals = activity?.totals ?? { runs: 0, failed: 0, skipped: 0 };
  const byId = new Map(automations.map((a) => [a.id, a]));
  const now = Date.now();

  if (loading && !activity) return <div className="automx-none">Loading activity…</div>;

  return (
    <div className="automx-activity">
      <section className="automx-card" aria-label="Totals">
        <div className="automx-totals">
          <div>
            <b>{totals.runs.toLocaleString()}</b>
            <span>runs in the last 24 hours</span>
          </div>
          <div>
            <b className={totals.failed ? 'bad' : ''}>{totals.failed.toLocaleString()}</b>
            <span>failed</span>
          </div>
          <div>
            <b>{totals.skipped.toLocaleString()}</b>
            <span>skipped, nothing to do</span>
          </div>
          <div>
            <b>{rows.length.toLocaleString()}</b>
            <span>automations ran</span>
          </div>
        </div>
        {days.length > 1 ? <DayBars days={days} /> : null}
      </section>

      <section className="automx-card" aria-label="The last 24 hours, by automation">
        <div className="automx-card-head">
          <h2>The last 24 hours, by automation</h2>
          <span>Trouble first. A row opens that automation’s run history.</span>
        </div>
        {rows.length === 0 ? (
          <div className="automx-none">Nothing ran in the last 24 hours.</div>
        ) : (
          <div className="automx-table-scroll">
            <div className="automx-table" role="table">
              <div className="automx-tr automx-th" role="row">
                <span role="columnheader">Automation</span>
                <span role="columnheader">Runs</span>
                <span role="columnheader">Result</span>
                <span role="columnheader">What happened</span>
                <span role="columnheader">Avg</span>
                <span role="columnheader">Last</span>
              </div>
              {rows.map((row) => {
                const automation = byId.get(row.automation_id);
                return (
                  <button
                    key={row.automation_id}
                    type="button"
                    role="row"
                    className="automx-tr"
                    disabled={!automation}
                    onClick={() => automation && onShowHistory(automation)}
                  >
                    <span role="cell" className="automx-td-name">
                      {automation ? displayName(automation) : row.name}
                    </span>
                    <span role="cell" className="automx-td-num">
                      {row.runs}
                    </span>
                    <span role="cell" className="automx-td-chips">
                      {row.failed ? (
                        <span className="automx-chip bad">{row.failed} failed</span>
                      ) : null}
                      {row.skipped ? (
                        <span className="automx-chip">{row.skipped} skipped</span>
                      ) : null}
                      {!row.failed && !row.skipped ? (
                        <span className="automx-chip ok">All completed</span>
                      ) : null}
                    </span>
                    <span role="cell" className="automx-td-what" title={what(row)}>
                      {what(row)}
                    </span>
                    <span role="cell" className="automx-td-num">
                      {took(row.avg_seconds)}
                    </span>
                    <span role="cell" className="automx-td-num">
                      {row.last_started ? timeAgo(row.last_started, now) : ''}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
