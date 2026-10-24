import { FileDecisions, type FileDecision } from './file-decisions';
import styles from './review-page.module.css';
import { changeFieldLabel, changeValue } from './review-presentation';

export type ReadyPreview = {
  signature: string;
  action_count: number;
  file_actions: number;
  albums: Array<{
    album: string;
    case_id: string;
    actions: Array<{
      id: number;
      description: string;
      file_path?: string;
      title?: string;
      changes?: Array<{ field: string; old: unknown; new: unknown; kind: string }>;
      file_decision?: FileDecision | null;
    }>;
  }>;
};
const categories = [
  ['numbering', 'Disc and track numbering'],
  ['fills', 'Metadata fills'],
  ['corrections', 'Metadata corrections'],
  ['filenames', 'Filename changes'],
  ['album', 'Album metadata'],
] as const;

export function ApplyPlanPreview({
  preview,
  expanded,
  destinations,
}: {
  preview: ReadyPreview;
  expanded: boolean;
  destinations: string[];
}) {
  const folders = [...new Set(destinations)];
  const common = folders.length ? folders[0].split('/').slice(0, -1) : [];
  while (common.length && !folders.every((folder) => folder.startsWith(`${common.join('/')}/`)))
    common.pop();
  const parent = common.join('/');
  return (
    <div className={styles.applyPreview}>
      <div className={styles.applyTotals}>
        <strong>
          {preview.action_count} planned action{preview.action_count === 1 ? '' : 's'}
        </strong>
        <span>
          {preview.albums.length} fully decided album{preview.albums.length === 1 ? '' : 's'}
        </span>
      </div>
      <p>
        Changes are grouped below. A track can appear in several categories; it is counted once in
        the action total.
      </p>
      {preview.albums.map((album) => (
        <details className={styles.applyAlbum} key={album.case_id || album.album} open={expanded}>
          <summary>
            <strong>{album.album}</strong>
            <span>{album.actions.length} actions</span>
          </summary>
          {categories.map(([key, title]) => {
            const actions = album.actions.filter((action) =>
              action.changes?.some((change) => change.kind === key),
            );
            if (!actions.length) return null;
            return (
              <details className={styles.applyCategory} key={key} data-kind={key}>
                <summary>
                  <strong>{title}</strong>
                  <span>
                    {key === 'album'
                      ? 'Shared album change'
                      : `${actions.length} track${actions.length === 1 ? '' : 's'}`}
                  </span>
                </summary>
                <ul>
                  {actions.map((action) => (
                    <li key={action.id}>
                      {key !== 'album' && (
                        <strong>
                          {action.title || action.file_path?.split('/').pop() || 'Track'}
                        </strong>
                      )}
                      <table>
                        <thead>
                          <tr>
                            <th>Field</th>
                            <th>Current</th>
                            <th>Planned</th>
                          </tr>
                        </thead>
                        <tbody>
                          {action
                            .changes!.filter((change) => change.kind === key)
                            .map((change, index) => (
                              <tr key={index}>
                                <td>
                                  {change.field === 'library_album_title'
                                    ? 'Library album title'
                                    : changeFieldLabel(change.field)}
                                </td>
                                <td>{changeValue(change.old) || '—'}</td>
                                <td>{changeValue(change.new) || '—'}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </li>
                  ))}
                </ul>
              </details>
            );
          })}
          <FileDecisions items={album.actions} />
          {album.actions
            .filter((action) => !action.changes?.length && !action.file_decision)
            .map((action) => (
              <p key={action.id}>{action.description}</p>
            ))}
        </details>
      ))}
      {folders.length > 0 && (
        <section className={styles.organizationPreview} aria-label="Destination folders">
          <h4>Organize after applying</h4>
          {parent && <code>{parent}</code>}
          <ul>
            {folders.map((folder) => (
              <li key={folder}>
                <code>{parent ? folder.slice(parent.length + 1) : folder}</code>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className={styles.muted}>
        Albums with open decisions are excluded. Each plan is checked again before execution.
      </p>
    </div>
  );
}
