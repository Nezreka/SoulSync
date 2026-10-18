import { useState } from 'react';

import { Button } from '@/components/form/form';
import { copyRecordText } from '@/components/record-inspector/record-helpers';

import styles from './metadata-comparison.module.css';
export interface MetadataChange {
  field: string;
  current?: unknown;
  planned?: unknown;
  changed?: boolean;
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
}: {
  rows: MetadataChange[];
  currentLabel?: string;
  plannedLabel?: string;
}) {
  const [copied, setCopied] = useState('');
  return (
    <div className={styles.comparison}>
      <table>
        <thead>
          <tr>
            <th>Field</th>
            <th>{currentLabel}</th>
            <th>{plannedLabel}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.field} data-changed={row.changed !== false}>
              <th scope="row">{row.field}</th>
              {[row.current, row.planned].map((value, index) => {
                const text = valueText(value);
                return (
                  <td key={index} data-label={index ? plannedLabel : currentLabel}>
                    <span>{text || <em>Not set</em>}</span>
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
