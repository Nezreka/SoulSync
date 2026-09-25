import type { LibraryV2HistoryEntry } from '../-library-v2.api';

import { historyOutcomeSummary, historySubject } from './history-groups';
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

export function HistoryEventList({
  groups,
}: {
  groups: Array<{ key: string; entries: LibraryV2HistoryEntry[] }>;
}) {
  return (
    <>
      <div className={styles.historyColumns} aria-hidden="true">
        <span>When</span>
        <span>Activity / source</span>
        <span>Result / affected files</span>
        <span>Events</span>
      </div>
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
                <span>
                  {multiple
                    ? historyOutcomeSummary(group.entries)
                    : first.status?.replaceAll('_', ' ') || first.detail || 'Result not recorded'}
                </span>
                <span className={styles.historyDetail}>
                  {first.status_basis === 'current_file' ? 'Current file status' : ''}
                  {!multiple
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
