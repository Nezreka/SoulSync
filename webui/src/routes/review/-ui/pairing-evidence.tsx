import styles from './review-page.module.css';
import { records, textValue } from './review-presentation';
const outcomes: Record<string, string> = {
  agrees: 'Agrees',
  conflicts: 'Needs review',
  unavailable: 'Unavailable',
  resolved: 'Resolved by album context',
  manual: 'Your choice',
};
export function PairingEvidence({ checks, reasons }: { checks: unknown; reasons?: string[] }) {
  const rows = records(checks);
  if (!rows.length && !reasons?.length) return null;
  return (
    <section className={styles.technicalCard} aria-label="Why this track was paired">
      <h5>Why this track was paired</h5>
      {rows.length ? (
        <div className={styles.evidenceTable}>
          <table>
            <thead>
              <tr>
                <th>Check</th>
                <th>Local evidence</th>
                <th>Release</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={textValue(row.check)} data-result={textValue(row.result)}>
                  <th scope="row">{textValue(row.check)}</th>
                  <td data-label="Local evidence">{textValue(row.local) || '—'}</td>
                  <td data-label="Release">{textValue(row.release) || '—'}</td>
                  <td>
                    <strong>{outcomes[textValue(row.result)] || 'Context'}</strong>
                    {Boolean(row.detail) && <small>{textValue(row.detail)}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          <ul>
            {reasons?.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          <p>Reanalyze this album for the full evidence table.</p>
        </>
      )}
    </section>
  );
}
