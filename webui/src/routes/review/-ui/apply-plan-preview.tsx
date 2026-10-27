import { useState, type ReactNode } from 'react';

import { Button, TextInput } from '@/components/form/form';
import { MetadataComparison } from '@/components/metadata-comparison/metadata-comparison';

import { fetchReviewCase } from '../-review.api';
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
    artist?: string;
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
  onSelectionChange,
}: {
  preview: ReadyPreview;
  onSelectionChange?: (selection: { case_ids: string[]; organize_case_ids: string[] }) => void;
  expanded: boolean;
  destinations: Array<string | { current: string; planned: string }>;
}) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(
    () => new Set(preview.albums.map((album) => album.case_id)),
  );
  const [organized, setOrganized] = useState<Set<string>>(
    () =>
      new Set(
        destinations.length && preview.albums.length === 1 ? [preview.albums[0].case_id] : [],
      ),
  );
  const [folderPreviews, setFolderPreviews] = useState<
    Record<
      string,
      { loading?: boolean; error?: string; moves?: Array<{ current: string; planned: string }> }
    >
  >({});
  const visible = preview.albums.filter((album) =>
    `${album.album} ${album.artist || ''}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  const update = (ids: Set<string>, folders: Set<string>) => {
    setSelected(ids);
    setOrganized(folders);
    onSelectionChange?.({
      case_ids: [...ids],
      organize_case_ids: [...folders].filter((id) => ids.has(id)),
    });
  };
  const inspectFolders = async (key: string) => {
    setFolderPreviews((current) => ({ ...current, [key]: { loading: true } }));
    try {
      const reviewCase = await fetchReviewCase(key);
      if (!reviewCase.plan?.can_organize || !reviewCase.album_id)
        throw new Error(
          'Folder organization becomes available after the file decisions have been applied.',
        );
      const response = await fetch(
        `/api/library/album/${encodeURIComponent(String(reviewCase.album_id))}/reorganize/preview`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode: 'tags',
            review_case_id: key,
            plan_signature: reviewCase.plan_signature,
          }),
        },
      );
      const result = await response.json();
      if (!response.ok || !result.success)
        throw new Error(result.error || 'Could not verify folder changes.');
      const changes = (result.tracks || [])
        .filter(
          (track: {
            file_exists?: boolean;
            collision?: boolean;
            matched?: boolean;
            current_path?: string;
            new_path?: string;
          }) =>
            track.file_exists &&
            !track.collision &&
            track.matched !== false &&
            track.current_path &&
            track.new_path,
        )
        .map((track: { current_path: string; new_path: string }) => ({
          current: track.current_path.split('/').slice(0, -1).join('/'),
          planned: track.new_path.split('/').slice(0, -1).join('/'),
        }))
        .filter((move: { current: string; planned: string }) => move.current !== move.planned);
      setFolderPreviews((current) => ({
        ...current,
        [key]: {
          moves: [
            ...new Map(
              changes.map(
                (move: { current: string; planned: string }) =>
                  [`${move.current}→${move.planned}`, move] as const,
              ),
            ).values(),
          ] as Array<{ current: string; planned: string }>,
        },
      }));
    } catch (error) {
      setFolderPreviews((current) => ({ ...current, [key]: { error: (error as Error).message } }));
    }
  };
  const selectedCount = preview.albums
    .filter((album) => selected.has(album.case_id))
    .reduce((total, album) => total + album.actions.length, 0);
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
          {selectedCount} selected action{selectedCount === 1 ? '' : 's'}
        </strong>
        <span>
          {selected.size} of {preview.albums.length} albums selected
        </span>
      </div>
      <p>
        Changes are grouped below. A track can appear in several categories; it is counted once in
        the action total.
      </p>
      {preview.albums.length > 1 && (
        <div className={styles.batchSearch}>
          <TextInput
            aria-label="Search ready albums"
            placeholder="Search albums or artists…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <span>
            {visible.length} shown · {selected.size} selected. Searching does not change the
            selection.
          </span>
          {onSelectionChange && (
            <div>
              <Button
                onClick={() => update(new Set(visible.map((album) => album.case_id)), organized)}
              >
                Select only shown albums
              </Button>
              <Button
                onClick={() =>
                  update(new Set(preview.albums.map((album) => album.case_id)), organized)
                }
              >
                Select all albums
              </Button>
            </div>
          )}
        </div>
      )}
      {visible.length === 0 && <p>No matching albums.</p>}
      {visible.map((album) => (
        <div key={album.case_id || album.album} className={styles.batchAlbum}>
          {onSelectionChange && preview.albums.length > 1 && (
            <label className={styles.batchSelection}>
              <input
                type="checkbox"
                checked={selected.has(album.case_id)}
                onChange={(event) => {
                  const next = new Set(selected);
                  if (event.target.checked) next.add(album.case_id);
                  else next.delete(album.case_id);
                  update(next, organized);
                }}
              />{' '}
              Apply {album.album}
              {album.artist ? ` · ${album.artist}` : ''}
            </label>
          )}
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
            {onSelectionChange && !destinations.length && (
              <section
                className={styles.batchFolders}
                aria-label={`Folder changes for ${album.album}`}
              >
                <Button
                  disabled={folderPreviews[album.case_id]?.loading}
                  onClick={() => void inspectFolders(album.case_id)}
                >
                  {folderPreviews[album.case_id]?.loading
                    ? 'Checking folder changes…'
                    : 'Review folder changes'}
                </Button>
                {folderPreviews[album.case_id]?.error && (
                  <p>{folderPreviews[album.case_id].error}</p>
                )}
                {folderPreviews[album.case_id]?.moves &&
                  (folderPreviews[album.case_id].moves!.length ? (
                    <>
                      <table>
                        <thead>
                          <tr>
                            <th>Current folder</th>
                            <th>Planned folder</th>
                          </tr>
                        </thead>
                        <tbody>
                          {folderPreviews[album.case_id].moves!.map((move) => (
                            <tr key={`${move.current}→${move.planned}`}>
                              <td>
                                <code>{move.current}</code>
                              </td>
                              <td>
                                <code>{move.planned}</code>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <label>
                        <input
                          type="checkbox"
                          checked={organized.has(album.case_id)}
                          disabled={!selected.has(album.case_id)}
                          onChange={(event) => {
                            const next = new Set(organized);
                            if (event.target.checked) next.add(album.case_id);
                            else next.delete(album.case_id);
                            update(selected, next);
                          }}
                        />{' '}
                        Organize this album after applying
                      </label>
                    </>
                  ) : (
                    <p>No folder moves are needed.</p>
                  ))}
              </section>
            )}
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
        </div>
      ))}
      <p className={styles.muted}>
        Albums with open decisions are excluded. Each plan is checked again before execution.
      </p>
    </div>
  );
}
