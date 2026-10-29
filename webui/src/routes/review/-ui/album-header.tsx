import { ActionMenu } from '@/components/action-menu/action-menu';
import { Button } from '@/components/form/form';

import type { ReviewController } from './use-review-controller';

import { ReviewArtwork } from './review-artwork';
import styles from './review-page.module.css';
import { textValue } from './review-presentation';

type Props = Pick<
  ReviewController,
  | 'albumNumber'
  | 'albumTitleChange'
  | 'leaveAlbum'
  | 'mutationBusy'
  | 'nextCaseId'
  | 'previousCaseId'
  | 'queue'
  | 'reanalyzeAlbum'
  | 'reviewCase'
  | 'selectCase'
  | 'wishlistedCount'
>;

export function AlbumHeader({
  albumNumber,
  albumTitleChange,
  leaveAlbum,
  mutationBusy,
  nextCaseId,
  previousCaseId,
  queue,
  reanalyzeAlbum,
  reviewCase,
  selectCase,
  wishlistedCount,
}: Props) {
  if (!reviewCase) return null;
  return (
    <div className={styles.albumHeader}>
      <div className={styles.albumIdentity}>
        <ReviewArtwork
          title={reviewCase.album || 'Unassigned files'}
          url={reviewCase.album_thumb_url}
          size="header"
        />
        <div>
          <div className={styles.eyebrow}>
            {albumNumber === null
              ? 'Album review'
              : `Album ${albumNumber} of ${queue?.total ?? '—'}`}
            {' · '}
            {Math.max(0, reviewCase.count - wishlistedCount)} pending change
            {reviewCase.count - wishlistedCount === 1 ? '' : 's'}
            {wishlistedCount > 0 &&
              ` · ${wishlistedCount} requested track${wishlistedCount === 1 ? '' : 's'}`}
          </div>
          <h2>{reviewCase.current_album_title || reviewCase.album || 'Unassigned files'}</h2>
          <p>{reviewCase.artist || 'Artist not established'}</p>
          {albumTitleChange && (
            <p className={styles.plannedAlbumTitle}>
              Library album title after apply: <strong>{textValue(albumTitleChange.new)}</strong>
            </p>
          )}
        </div>
      </div>
      <nav className={styles.nav} aria-label="Album navigation">
        <Button
          type="button"
          disabled={!previousCaseId}
          onClick={() => previousCaseId && selectCase(previousCaseId)}
        >
          ← Previous album
        </Button>
        <Button
          type="button"
          disabled={!nextCaseId}
          onClick={() => nextCaseId && selectCase(nextCaseId)}
        >
          Next album →
        </Button>
      </nav>
      <div className={styles.albumMaintenance}>
        <Button
          type="button"
          disabled={mutationBusy}
          title="Recheck this album and rebuild its plan using valid cached audio and catalog evidence"
          onClick={() => void reanalyzeAlbum()}
        >
          Reanalyze album
        </Button>
        <ActionMenu
          trigger={(props) => (
            <Button {...props} variant="ghost" aria-label="Album maintenance options">
              More ▾
            </Button>
          )}
          items={[
            {
              key: 'leave',
              label: 'Leave album entirely as-is…',
              disabled: mutationBusy || !reviewCase.items.length,
              onSelect: () => void leaveAlbum(),
            },
            {
              key: 'catalogue',
              label: 'Refresh catalog evidence',
              title:
                'Fetch fresh catalog records and reanalyze this album. Cached audio fingerprints are reused.',
              disabled: mutationBusy,
              onSelect: () => void reanalyzeAlbum(true),
            },
          ]}
        />
      </div>
    </div>
  );
}
