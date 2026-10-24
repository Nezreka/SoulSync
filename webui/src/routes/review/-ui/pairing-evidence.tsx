import styles from './review-page.module.css';
import { records, textValue } from './review-presentation';

const outcomes: Record<string, string> = {
  agrees: 'Agrees',
  conflicts: 'Differs',
  unavailable: 'Not available',
  resolved: 'Resolved by album context',
  manual: 'Your choice',
};
export function PairingEvidence({ checks, reasons }: { checks: unknown; reasons?: string[] }) {
  const rows = records(checks);
  if (!rows.length && !reasons?.length) return null;
  return (
    <section className={styles.pairingChecks} aria-label="Why this track was paired">
      <h4>Why this track was paired</h4>
      {rows.length ? (
        rows.map((row) => (
          <details key={textValue(row.check)} data-result={textValue(row.result)}>
            <summary>
              <span>{textValue(row.check)}</span>
              <strong>{outcomes[textValue(row.result)] || 'Context'}</strong>
            </summary>
            <dl>
              <div>
                <dt>Local evidence</dt>
                <dd>{textValue(row.local) || 'Not available'}</dd>
              </div>
              <div>
                <dt>Release</dt>
                <dd>{textValue(row.release) || 'Not available'}</dd>
              </div>
            </dl>
            {Boolean(row.detail) && <p>{textValue(row.detail)}</p>}
          </details>
        ))
      ) : (
        <ul>
          {reasons?.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
