import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';
import { DialogFrame, DialogHeader } from '@/components/dialog';

import { invalidateLibraryV2 } from '../-library-v2.api';
import page from './library-v2-page.module.css';
import styles from './use-file-tags-modal.module.css';

/**
 * Use file tags (#2070): the Library takes what the files themselves say.
 *
 * Everything else writes the Library's metadata into the files. This is the
 * other way round, for someone who fixed their tags by hand and wants the
 * Library to match. It always shows what would change first; nothing is saved
 * until the button is pressed.
 */

type Entity = 'artists' | 'albums' | 'tracks';

interface Change {
  field: string;
  label: string;
  library: string | number | string[] | null;
  file: string | number | string[] | null;
}

interface PlanTrack {
  track_id: number;
  title: string | null;
  album_title: string | null;
  changes: Change[];
  move: { album_title: string; album_changes?: Change[] } | null;
  notes: string[];
  unreadable?: boolean;
}

interface PlanAlbum {
  album_id: number;
  title: string;
  changes: Change[];
  rename: string | null;
}

export interface UseFileTagsPlan {
  success?: boolean;
  tracks: PlanTrack[];
  albums: PlanAlbum[];
  counts: {
    tracks: number;
    changed_tracks: number;
    moves: number;
    albums: number;
    unreadable: number;
  };
  truncated?: boolean;
}

interface Applied {
  tracks_changed: number;
  albums_changed: number;
  albums_renamed: number;
  moved: number;
  albums_created: number;
  failed: { track_id: number; title: string | null; error: string }[];
}

const show = (value: Change['library']): string =>
  value === null || value === undefined || value === ''
    ? 'nothing'
    : Array.isArray(value)
      ? value.join(', ') || 'nothing'
      : String(value);

function ChangeLine({ change }: { change: Change }) {
  return (
    <li>
      {change.label}: <s>{show(change.library)}</s> → <b>{show(change.file)}</b>
    </li>
  );
}

const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** What applying did, as one sentence for the toast. */
export function describeApplied(result: Applied): string {
  const parts: string[] = [];
  if (result.albums_changed + result.albums_renamed)
    parts.push(
      plural(Math.max(result.albums_changed, result.albums_renamed), 'album', 'albums') +
        ' updated',
    );
  if (result.tracks_changed)
    parts.push(plural(result.tracks_changed, 'track', 'tracks') + ' updated');
  if (result.moved) parts.push(plural(result.moved, 'track', 'tracks') + ' moved to another album');
  if (!parts.length) return 'Nothing needed changing';
  const failed = result.failed.length
    ? `. ${plural(result.failed.length, 'move', 'moves')} failed`
    : '';
  return parts.join(', ') + failed;
}

export function UseFileTagsModal({
  entity,
  id,
  title,
  onClose,
}: {
  entity: Entity;
  id: number;
  title: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const path = `library/v2/${entity}/${id}/use-file-tags`;
  const preview = useQuery({
    queryKey: ['library-v2', 'use-file-tags', entity, id] as const,
    queryFn: () => readJson<UseFileTagsPlan>(apiClient.get(path, { timeout: 120_000 })),
    // the files are read fresh each time the window opens
    gcTime: 0,
    staleTime: 0,
  });
  const apply = useMutation({
    mutationFn: () => readJson<Applied>(apiClient.post(path, { timeout: 300_000 })),
    onSuccess: async (result) => {
      window.showToast?.(describeApplied(result), result.failed.length ? 'warning' : 'success');
      await invalidateLibraryV2(queryClient);
      onClose();
    },
    onError: (error: Error) =>
      window.showToast?.(`Could not use the file tags: ${error.message}`, 'error'),
  });

  const plan = preview.data;
  const changedTracks = (plan?.tracks ?? []).filter((t) => t.changes.length && !t.move);
  const moves = (plan?.tracks ?? []).filter((t) => t.move);
  const notes = (plan?.tracks ?? []).filter((t) => t.notes.length);
  const albums = plan?.albums ?? [];
  const nothing = Boolean(plan) && !changedTracks.length && !moves.length && !albums.length;

  return (
    <DialogFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      className={`${page.modal} ${page.modalFramed} ${page.modalWorkspace}`}
    >
      <DialogHeader
        title={`Use file tags: ${title}`}
        closeLabel="Close"
        compact
        className={page.dialogHead}
      >
        <span className={page.toolDescription}>
          The Library takes what the files themselves say. Changes are saved as your own edits, so a
          metadata refresh does not put the old values back.
        </span>
      </DialogHeader>
      <div className={page.modalContent}>
        <div className={styles.body}>
          {preview.isPending ? <p className={styles.summary}>Reading the files…</p> : null}
          {preview.isError ? (
            <p className={styles.summary}>Could not read the files: {preview.error.message}</p>
          ) : null}
          {nothing ? (
            <p className={styles.summary}>
              The Library already says what these files say
              {plan?.counts.unreadable
                ? `. ${plural(plan.counts.unreadable, 'file', 'files')} could not be read.`
                : '.'}
            </p>
          ) : null}

          {albums.length ? (
            <section className={styles.group}>
              <h3 className={styles.groupTitle}>{plural(albums.length, 'album', 'albums')}</h3>
              <div className={styles.rows}>
                {albums.map((album) => (
                  <div className={styles.row} key={album.album_id}>
                    <span className={styles.name}>{album.title}</span>
                    <ul className={styles.changes}>
                      {album.rename ? (
                        <li>
                          Title: <s>{album.title}</s> → <b>{album.rename}</b>
                        </li>
                      ) : null}
                      {album.changes.map((change) => (
                        <ChangeLine key={change.field} change={change} />
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {moves.length ? (
            <section className={styles.group}>
              <h3 className={styles.groupTitle}>
                {plural(moves.length, 'track moves', 'tracks move')} to the album its tag names
              </h3>
              <div className={styles.rows}>
                {moves.map((track) => (
                  <div className={styles.row} key={track.track_id}>
                    <span className={styles.name}>{track.title}</span>
                    <ul className={styles.changes}>
                      <li className={styles.move}>
                        <s>{track.album_title}</s> → <b>{track.move?.album_title}</b>
                      </li>
                      {track.changes.map((change) => (
                        <ChangeLine key={change.field} change={change} />
                      ))}
                      {(track.move?.album_changes ?? []).map((change) => (
                        <li key={`album-${change.field}`}>
                          {track.move?.album_title} {change.label.toLowerCase()}:{' '}
                          <b>{show(change.file)}</b>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {changedTracks.length ? (
            <section className={styles.group}>
              <h3 className={styles.groupTitle}>
                {plural(changedTracks.length, 'track', 'tracks')}
              </h3>
              <div className={styles.rows}>
                {changedTracks.map((track) => (
                  <div className={styles.row} key={track.track_id}>
                    <span className={styles.name}>{track.title}</span>
                    <ul className={styles.changes}>
                      {track.changes.map((change) => (
                        <ChangeLine key={change.field} change={change} />
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {notes.length ? (
            <section className={styles.group}>
              <h3 className={styles.groupTitle}>Left as they are</h3>
              <div className={styles.rows}>
                {notes.slice(0, 50).map((track) => (
                  <div className={styles.row} key={track.track_id}>
                    <span className={styles.name}>{track.title}</span>
                    <ul className={styles.changes}>
                      {track.notes.map((note) => (
                        <li className={styles.note} key={note}>
                          {note}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          <div className={styles.foot}>
            <span className={styles.footNote}>
              {plan?.truncated ? 'Only the first 1,000 tracks are shown and changed. ' : ''}
              {plan && !nothing && plan.counts.unreadable
                ? `${plural(plan.counts.unreadable, 'file', 'files')} could not be read. `
                : ''}
              {moves.length
                ? 'Files stay where they are on disk; Rename / Organize refiles them.'
                : ''}
            </span>
            <button type="button" className={page.btnGhost} onClick={onClose}>
              {nothing ? 'Close' : 'Cancel'}
            </button>
            {!nothing ? (
              <button
                type="button"
                className={page.btnPrimary}
                data-requires-write=""
                disabled={!plan || apply.isPending}
                onClick={() => apply.mutate()}
              >
                {apply.isPending ? 'Saving…' : 'Use these tags'}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </DialogFrame>
  );
}
