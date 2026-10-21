import clsx from 'clsx';

import { DialogFrame } from '@/components/dialog';

import styles from './smart-delete-dialog.module.css';

/**
 * The two-option delete dialog (library.js _showSmartDeleteDialog 3139 /
 * _showAlbumDeleteDialog 4061). Deliberately NOT window.confirm and not the
 * shared confirm modal: the vanilla offered a real choice — remove the DB row
 * only, or take the files with it — and blacklisting is intentionally absent
 * here (it lives in Source Info, where real download-source data exists).
 */

export interface SmartDeleteCopy {
  title: string;
  keepDesc: string;
  deleteTitle: string;
  deleteDesc: string;
  deleteChoice: string;
}

export const TRACK_DELETE_COPY: SmartDeleteCopy = {
  title: 'Delete Track',
  keepDesc: 'Remove the database entry only. File stays on disk.',
  deleteTitle: 'Delete File Too',
  deleteDesc: 'Remove from library and delete the audio file from disk.',
  deleteChoice: 'delete_file',
};

export const ALBUM_DELETE_COPY: SmartDeleteCopy = {
  title: 'Delete Album',
  keepDesc: 'Remove the album and all tracks from the database. Files on disk are not affected.',
  deleteTitle: 'Delete Files Too',
  deleteDesc:
    'Remove from library and delete all audio files from disk. Empty album folder will be cleaned up.',
  deleteChoice: 'delete_files',
};

export function SmartDeleteDialog({
  copy,
  onChoose,
  onClose,
}: {
  copy: SmartDeleteCopy;
  /** 'db_only' or the copy's destructive choice ('delete_file'/'delete_files'). */
  onChoose: (choice: string) => void;
  onClose: () => void;
}) {
  return (
    <DialogFrame
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      viewportClassName={clsx('modal-overlay', styles.overlay)}
      className="smart-delete-modal"
      aria-label={copy.title}
    >
      <div className="smart-delete-header">
        <h3>{copy.title}</h3>
        <button className="smart-delete-close" type="button" onClick={onClose}>
          ×
        </button>
      </div>
      <p className="smart-delete-desc">
        How should this {copy.title === 'Delete Album' ? 'album' : 'track'} be deleted?
      </p>
      <div className="smart-delete-options">
        <button className="smart-delete-option" type="button" onClick={() => onChoose('db_only')}>
          <div className="smart-delete-option-icon">📋</div>
          <div className="smart-delete-option-info">
            <div className="smart-delete-option-title">Remove from Library</div>
            <div className="smart-delete-option-desc">{copy.keepDesc}</div>
          </div>
        </button>
        <button
          className="smart-delete-option destructive"
          type="button"
          onClick={() => onChoose(copy.deleteChoice)}
        >
          <div className="smart-delete-option-icon">🗑️</div>
          <div className="smart-delete-option-info">
            <div className="smart-delete-option-title">{copy.deleteTitle}</div>
            <div className="smart-delete-option-desc">{copy.deleteDesc}</div>
          </div>
        </button>
      </div>
    </DialogFrame>
  );
}
