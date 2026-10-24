import { Fragment, useState } from 'react';

import { Button } from '@/components/form/form';
import { copyRecordText } from '@/components/record-inspector/record-helpers';

import styles from './metadata-comparison.module.css';
export interface MetadataChange {
  field: string;
  current?: unknown;
  planned?: unknown;
  changed?: boolean;
  track?: string;
  group?: string;
}
function valueText(value: unknown): string {
  return value == null || value === ''
    ? ''
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value);
}
export function MetadataComparison({
  rows,
  currentLabel = 'Current',
  plannedLabel = 'Planned',
  compact = false,
}: {
  rows: MetadataChange[];
  compact?: boolean;
  currentLabel?: string;
  plannedLabel?: string;
}) {
  const [copied, setCopied] = useState('');
  return (
    <div className={styles.comparison} data-compact={compact || undefined}>
      <table>
        <thead>
          <tr>
            {rows.some((row) => row.track) && <th>Track</th>}
            <th>Field</th>
            <th>{currentLabel}</th>
            <th aria-label="Change" />
            <th>{plannedLabel}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={`${row.group || row.track || ''}:${row.field}:${index}`}
              data-changed={row.changed !== false}
            >
              {rows.some((item) => item.track) &&
                (index === 0 || rows[index - 1].group !== row.group) && (
                  <th
                    scope="rowgroup"
                    rowSpan={rows.filter((item) => item.group === row.group).length}
                  >
                    {row.track}
                  </th>
                )}
              <th scope="row">{row.field}</th>
              {[row.current, row.planned].map((value, index) => {
                const text = valueText(value);
                return (
                  <Fragment key={index}>
                    {index === 1 && (
                      <td
                        className={styles.direction}
                        aria-label={
                          row.changed === false
                            ? 'Unchanged'
                            : !valueText(row.current)
                              ? 'Added'
                              : !valueText(row.planned)
                                ? 'Removed'
                                : 'Changed'
                        }
                      >
                        {row.changed === false ? '✓' : '→'}
                      </td>
                    )}
                    <td data-label={index ? plannedLabel : currentLabel}>
                      <span className={index ? styles.planned : styles.current}>
                        {text ? (
                          index === 0 && row.changed !== false ? (
                            <del>{text}</del>
                          ) : (
                            text
                          )
                        ) : (
                          <em>Not set</em>
                        )}
                      </span>
                      {text.length > 60 && (
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Copy ${index ? plannedLabel : currentLabel} ${row.field}`}
                          onClick={() => {
                            void copyRecordText(text)
                              .then(() => setCopied('Value copied'))
                              .catch(() => setCopied('Could not copy value'));
                          }}
                        >
                          ⧉
                        </Button>
                      )}
                    </td>
                  </Fragment>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <span className={styles.feedback} role="status">
        {copied}
      </span>
    </div>
  );
}
