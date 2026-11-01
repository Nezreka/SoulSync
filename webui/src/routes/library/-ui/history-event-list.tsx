import {
  groupHistoryEvents,
  type HistoryGroup,
  type HistoryNode,
  type HistoryStep,
  historyOutcomeSummary,
  historySteps,
  historySubject,
  historyTone,
} from './history-groups';
import styles from './library-v2-page.module.css';

function eventDate(value: string | null, timeOnly = false): string {
  if (!value) return 'Unknown date';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    ...(timeOnly ? {} : { month: 'short' as const, day: 'numeric' as const }),
    hour: '2-digit',
    minute: '2-digit',
    ...(timeOnly ? { second: '2-digit' as const } : {}),
  }).format(date);
}

/** The pipeline steps of a track, in order, as status pills. */
export function HistorySteps({ steps }: { steps: HistoryStep[] }) {
  if (!steps.length) return null;
  return (
    <span className={styles.historySteps}>
      {steps.map((step, i) => (
        <span key={step.label} className={styles.historyStep}>
          {i > 0 ? <span aria-hidden="true">→</span> : null}
          <span className={styles.historyOutcome} data-tone={step.tone}>
            {step.label}
          </span>
        </span>
      ))}
    </span>
  );
}

/** One row per track: its pipeline on top, every event of it folded by kind
 *  underneath. Tracks without a pipeline step name what happened instead. */
export function HistoryTree({ nodes }: { nodes: HistoryNode[] }) {
  return (
    <>
      <div className={styles.historyColumns} aria-hidden="true">
        <span>Last</span>
        <span>Track / release</span>
        <span>What happened</span>
        <span>Events</span>
      </div>
      {nodes.map((node) => {
        const groups = groupHistoryEvents(node.entries, { byKind: true });
        const steps = historySteps(node.entries);
        const newest = node.entries[0]!;
        return (
          <details key={node.key} className={styles.historyGroup}>
            <summary className={styles.historyRow}>
              <time
                className={styles.historyDate}
                dateTime={newest.date ?? undefined}
                title={newest.date ?? undefined}
              >
                {eventDate(newest.date)}
              </time>
              <span className={styles.historyEvent}>
                <strong>{node.title}</strong>
                {node.subtitle ? (
                  <span className={styles.historyDetail}>{node.subtitle}</span>
                ) : null}
              </span>
              <span className={styles.historyEvent}>
                {steps.length ? (
                  <HistorySteps steps={steps} />
                ) : (
                  <span className={styles.historyDetail}>
                    {groups
                      .slice(0, 3)
                      .map(({ entries }) => {
                        const first = entries[0]!;
                        const label = first.title ?? first.event_type;
                        return entries.length > 1 ? `${label} ×${entries.length}` : label;
                      })
                      .join(' · ')}
                  </span>
                )}
              </span>
              <span className={styles.historyCount}>
                {node.entries.length}
                <span aria-hidden="true">⌄</span>
              </span>
            </summary>
            <div className={styles.historyExpanded}>
              <HistoryEventList groups={groups} nested />
            </div>
          </details>
        );
      })}
    </>
  );
}

export function HistoryEventList({
  groups,
  nested = false,
}: {
  groups: HistoryGroup[];
  /** inside a track of the tree: no header, no track name per row */
  nested?: boolean;
}) {
  return (
    <>
      {nested ? null : (
        <div className={styles.historyColumns} aria-hidden="true">
          <span>When</span>
          <span>Activity / source</span>
          <span>Result / affected files</span>
          <span>Events</span>
        </div>
      )}
      {groups.map((group, i) => {
        const first = group.entries[0]!;
        const multiple = group.entries.length > 1;
        return (
          <details key={`${group.key}-${i}`} className={styles.historyGroup}>
            <summary className={styles.historyRow}>
              <time
                className={styles.historyDate}
                dateTime={first.date ?? undefined}
                title={first.date ?? undefined}
              >
                {eventDate(first.date)}
              </time>
              <span className={styles.historyEvent}>
                <strong>{first.title ?? first.event_type}</strong>
                <span className={styles.historyDetail}>
                  {first.job_id?.replaceAll('_', ' ') || first.source}
                </span>
              </span>
              <span className={styles.historyEvent}>
                <span
                  className={styles.historyOutcome}
                  data-tone={multiple ? undefined : historyTone(first.status)}
                >
                  {multiple
                    ? historyOutcomeSummary(group.entries)
                    : first.status?.replaceAll('_', ' ') || first.detail || 'Result not recorded'}
                </span>
                <span className={styles.historyDetail}>
                  {first.status_basis === 'current_file' ? 'Current file status' : ''}
                  {!multiple && !nested
                    ? `${first.status_basis === 'current_file' ? ' · ' : ''}${historySubject(first)}`
                    : ''}
                </span>
              </span>
              <span className={styles.historyCount}>
                {group.entries.length}
                <span aria-hidden="true">⌄</span>
              </span>
            </summary>
            <div className={styles.historyExpanded}>
              <table className={styles.historyResults}>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Track / release</th>
                    <th>
                      {first.status_basis === 'current_file'
                        ? 'Current status / reason'
                        : 'Recorded result / details'}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {group.entries.map((entry, index) => (
                    <tr key={index}>
                      <td>
                        <time dateTime={entry.date ?? undefined}>
                          {eventDate(entry.date, true)}
                        </time>
                      </td>
                      <td>
                        <strong>{historySubject(entry)}</strong>
                        {entry.track_title && entry.album_title ? (
                          <small>{entry.album_title}</small>
                        ) : null}
                      </td>
                      <td>
                        {entry.status ? (
                          <strong data-status={entry.status.toLowerCase()}>
                            {entry.status.replaceAll('_', ' ')}
                          </strong>
                        ) : null}
                        {entry.detail ? (
                          <span>{entry.detail}</span>
                        ) : (
                          <span>No further detail recorded.</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        );
      })}
    </>
  );
}
