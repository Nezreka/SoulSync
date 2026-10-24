import type { ReactNode } from 'react';

import { MetadataComparison } from '@/components/metadata-comparison/metadata-comparison';

import {
  FileDecisions,
  TrackRequests,
  type TrackRequest,
  type FileDecision,
} from './file-decisions';
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
      requests?: TrackRequest[];
    }>;
  }>;
};
function AlbumSection({
  title,
  count,
  single,
  expanded,
  children,
}: {
  title: string;
  count: number;
  single: boolean;
  expanded: boolean;
  children: ReactNode;
}) {
  const heading = (
    <>
      <strong>{title}</strong>
      <span>{count} actions</span>
    </>
  );
  return single ? (
    <section className={styles.applyAlbum}>
      <h3>{heading}</h3>
      {children}
    </section>
  ) : (
    <details className={styles.applyAlbum} open={expanded}>
      <summary>{heading}</summary>
      {children}
    </details>
  );
}
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
  destinations: Array<string | { current: string; planned: string }>;
}) {
  const moves = [
    ...new Map(
      destinations.map((entry) => {
        const move = typeof entry === 'string' ? { current: '', planned: entry } : entry;
        return [`${move.current}→${move.planned}`, move] as const;
      }),
    ).values(),
  ];
  const folders = [...new Set(moves.map((move) => move.planned))];
  const changesFolders = moves.some((move) => move.current !== move.planned);
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
        <AlbumSection
          key={album.case_id || album.album}
          title={album.album}
          count={album.actions.length}
          single={preview.albums.length === 1}
          expanded={expanded}
        >
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
                <MetadataComparison
                  compact
                  rows={actions.flatMap((action) =>
                    action
                      .changes!.filter((change) => change.kind === key)
                      .map((change) => ({
                        group: String(action.id),
                        track:
                          key === 'album'
                            ? undefined
                            : action.title || action.file_path?.split('/').pop() || 'Track',
                        field:
                          change.field === 'library_album_title'
                            ? 'Library album title'
                            : changeFieldLabel(change.field),
                        current: changeValue(change.old),
                        planned: changeValue(change.new),
                      })),
                  )}
                />
              </details>
            );
          })}
          <TrackRequests items={album.actions} />
          <FileDecisions items={album.actions} />
          {album.actions
            .filter(
              (action) =>
                !action.changes?.length && !action.file_decision && !action.requests?.length,
            )
            .map((action) => (
              <p key={action.id}>{action.description}</p>
            ))}
          {preview.albums.length === 1 && folders.length > 0 && (
            <details
              className={styles.applyCategory}
              data-kind="organization"
              aria-label="Destination folders"
            >
              <summary>
                <strong>Organize after applying</strong>
                <span>
                  {changesFolders
                    ? `${folders.length} folder${folders.length === 1 ? '' : 's'}`
                    : 'No folder moves'}
                </span>
              </summary>
              <p>
                {changesFolders
                  ? 'Move files into these folders after applying the planned changes.'
                  : 'Folder locations stay the same. Planned filename changes are shown above.'}
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Current folder</th>
                    <th>Planned folder</th>
                  </tr>
                </thead>
                <tbody>
                  {moves.map((move) => (
                    <tr key={`${move.current}→${move.planned}`}>
                      <td>
                        <code>{move.current || 'Current album folder'}</code>
                      </td>
                      <td>
                        <code>{move.planned}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </AlbumSection>
      ))}
      <p className={styles.muted}>
        Albums with open decisions are excluded. Each plan is checked again before execution.
      </p>
    </div>
  );
}
