import type { BasicSearchWarning } from '../-basic.types';

import styles from './basic.module.css';

/** `BrokenTracker didn't answer` / `3 indexers didn't answer` */
export function warningHeadline(warnings: BasicSearchWarning[]): string {
  const who = warnings.length === 1 ? warnings[0].indexer : `${warnings.length} indexers`;
  return `${who} didn't answer, so these results might be missing some releases.`;
}

/**
 * prowlarr indexers that failed while the others answered (#1831).
 *
 * a note, not an error: the results are real and stay usable. it only says
 * the list is incomplete, with the per-indexer reasons one click away.
 */
export function BasicWarnings({ warnings }: { warnings: BasicSearchWarning[] }) {
  if (!warnings.length) return null;
  return (
    <div className={styles.warning} role="status">
      <span className={styles.warningHeadline}>{warningHeadline(warnings)}</span>
      <details className={styles.warningDetails}>
        <summary>Details</summary>
        <ul>
          {warnings.map((warning) => (
            <li key={`${warning.source}:${warning.indexer}`}>
              <b>{warning.indexer}</b>
              <span className={styles.warningSource}>{warning.source}</span>
              <span className={styles.warningError}>{warning.error}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
