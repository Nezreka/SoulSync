import { Fragment, useState } from 'react';

import { Button } from '@/components/form/form';
import { HelpHint } from '@/components/release-metadata/release-metadata';

import styles from './review-page.module.css';

export type FileDecision = { title: string; keep: string[]; quarantine: string[]; request: string };
const format = (path: string) => path.split('.').pop()?.toUpperCase() || 'File';

export function FileDecisions({
  items,
  onReview,
}: {
  items: Array<{ id: number; file_decision?: FileDecision | null }>;
  onReview?: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const rows = items.filter((item) => item.file_decision);
  const folders = [
    ...new Set(
      rows.flatMap((row) =>
        [...row.file_decision!.keep, ...row.file_decision!.quarantine].map((path) =>
          path.slice(0, path.lastIndexOf('/')),
        ),
      ),
    ),
  ];
  if (!rows.length) return null;
  return (
    <section className={styles.fileDecisionList} aria-label="File decisions included">
      <h4>
        File decisions included
        <span className={styles.fileDecisionCount}>{rows.length}</span>
        <HelpHint label="recoverable quarantine">
          Applying a quarantine decision moves the chosen file out of the active library into
          recoverable storage. Review the kept copy and any playlist references before applying. No
          file has moved yet.
        </HelpHint>
      </h4>
      {folders.length === 1 && (
        <p className={styles.commonFolder}>
          <span>Folder</span>
          <code>{folders[0]}</code>
        </p>
      )}
      <div className={styles.fileDecisionScroll}>
        <table>
          <thead>
            <tr>
              <th>Track</th>
              <th>Keep</th>
              <th>Quarantine</th>
              {onReview && <th>Review</th>}
            </tr>
          </thead>
          <tbody>
            {(expanded ? rows : rows.slice(0, 5)).map(({ id, file_decision: decision }) => (
              <Fragment key={id}>
                <tr>
                  <td>
                    <strong>{decision!.title}</strong>
                    {decision!.request && <small>Request: {decision!.request}</small>}
                  </td>
                  <td>
                    {decision!.keep.map((path) => (
                      <span className={styles.keepFormat} key={path}>
                        {format(path)}
                      </span>
                    ))}
                  </td>
                  <td>
                    {decision!.quarantine.map((path) => (
                      <span className={styles.quarantineFormat} key={path}>
                        {format(path)}
                      </span>
                    ))}
                  </td>
                  {onReview && (
                    <td>
                      <Button
                        type="button"
                        aria-label={`Review file decision for ${decision!.title}`}
                        onClick={() => onReview(id)}
                      >
                        Review
                      </Button>
                    </td>
                  )}
                </tr>
                <tr>
                  <td colSpan={onReview ? 4 : 3}>
                    <details className={styles.filePaths}>
                      <summary>File paths</summary>
                      <ul>
                        {(['keep', 'quarantine'] as const).flatMap((kind) =>
                          decision![kind].map((path) => (
                            <li key={path}>
                              <span>{kind === 'keep' ? 'Keep' : 'Quarantine'}</span>
                              <code>{path}</code>
                            </li>
                          )),
                        )}
                      </ul>
                    </details>
                  </td>
                </tr>
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 5 && (
        <Button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Show fewer file decisions' : `Show ${rows.length - 5} more file decisions`}
        </Button>
      )}
    </section>
  );
}
