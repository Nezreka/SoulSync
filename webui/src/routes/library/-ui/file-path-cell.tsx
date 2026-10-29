import { copyRecordText } from '../../artist-detail/-artist-detail.db-record';
import styles from './library-v2-page.module.css';

/**
 * One stored file path, inside a table cell.
 *
 * Three things were wrong with printing the raw string. The column is 260px
 * wide and used `text-overflow: clip`, so a path was cut mid-word with nothing
 * to say it had been — and the part that identifies a file is at the END, so
 * what survived was the least useful half. It led with a library root the user
 * configured themselves, which is the same on every row. And there was no way
 * to get the whole value back out.
 *
 * `display` is the root-relative form the backend computes (`file.display_path`);
 * `path` stays the authority — it is what the tooltip shows and what the copy
 * button hands back, because it is the value that opens, moves and deletes.
 */
export function FilePathCellBody({
  path,
  display,
  empty = '—',
}: {
  path?: string | null;
  display?: string | null;
  empty?: string;
}) {
  const full = path ?? '';
  if (!full) return <span className={styles.muted}>{empty}</span>;
  return (
    <span className={styles.filePathInner}>
      <span className={styles.filePathText}>{display || full}</span>
      <button
        type="button"
        className={styles.filePathCopy}
        title="Copy full path"
        aria-label="Copy full path"
        onClick={(event) => {
          // Rows are clickable; copying a path is not "open this track".
          event.stopPropagation();
          void copyRecordText(full);
        }}
      >
        ⧉
      </button>
    </span>
  );
}
