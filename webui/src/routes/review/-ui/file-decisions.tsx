import { useState } from 'react';

import { Button } from '@/components/form/form';
import { HelpHint } from '@/components/release-metadata/release-metadata';

import { DisclosureFooter } from './disclosure-footer';
import styles from './review-page.module.css';

export type FileDecision = { title: string; keep: string[]; quarantine: string[]; request: string };
export type TrackRequest = { title: string; release?: string; state?: string };

export function TrackRequests({
  items,
}: {
  items: Array<{ requests?: TrackRequest[]; file_decision?: FileDecision | null }>;
}) {
  const requests = items.flatMap((item) =>
    item.requests?.length
      ? item.requests
      : item.file_decision?.request
        ? [{ title: item.file_decision.request }]
        : [],
  );
  if (!requests.length) return null;
  return (
    <section className={styles.requestList} aria-label="Request tracks">
      <h4>
        Request tracks <span>{requests.length}</span>
      </h4>
      <ul>
        {requests.map((request, index) => (
          <li key={`${request.title}-${index}`}>
            <div>
              <strong>{request.title}</strong>
              {request.release && <small>{request.release}</small>}
            </div>
            <span>
              {request.state && request.state !== 'planned'
                ? 'Already requested'
                : 'Add to wishlist'}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

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
              <tr key={id}>
                <td>
                  <strong>{decision!.title}</strong>
                </td>
                <td>
                  {decision!.keep.map((path) => (
                    <span className={styles.keepFormat} key={path}>
                      {format(path)}
                      {decision!.keep
                        .concat(decision!.quarantine)
                        .filter((other) => format(other) === format(path)).length > 1 && (
                        <small>{path.split('/').at(-1)}</small>
                      )}
                    </span>
                  ))}
                </td>
                <td>
                  {decision!.quarantine.map((path) => (
                    <span className={styles.quarantineFormat} key={path}>
                      {format(path)}
                      {decision!.keep
                        .concat(decision!.quarantine)
                        .filter((other) => format(other) === format(path)).length > 1 && (
                        <small>{path.split('/').at(-1)}</small>
                      )}
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
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 5 && (
        <DisclosureFooter
          expanded={expanded}
          onToggle={() => setExpanded(!expanded)}
          more={`Show ${rows.length - 5} more file decisions`}
          fewer="Show fewer file decisions"
        />
      )}
    </section>
  );
}
