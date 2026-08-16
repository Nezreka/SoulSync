import { useState } from 'react';

import type { EnhancedAlbum } from '../-artist-detail.enhanced';

import { albumMetaFields, albumMetaUpdates } from '../-artist-detail.enhanced-album';

interface Props {
  album: EnhancedAlbum;
  /** Non-admins get a read-only row; the inputs and Save button are admin-only. */
  isAdmin: boolean;
  /** Applied to the album after a successful PUT, so the row above updates. */
  onSaved: (updates: Record<string, unknown>) => void;
}

/**
 * The editable album metadata row (renderAlbumMetaRow, library.js:4012).
 *
 * Every input stops its own click: the whole album row is a toggle, and typing
 * in a field must not collapse the panel you are typing into.
 */
export function AlbumMetaRow({ album, isAdmin, onSaved }: Props) {
  const fields = albumMetaFields(album);

  // Seeded from the album and reset when the album object itself changes —
  // a refetch replaces the record, and stale edits must not survive it.
  const [seenAlbum, setSeenAlbum] = useState(album);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((field) => [field.key, field.value])),
  );
  if (album !== seenAlbum) {
    setSeenAlbum(album);
    setValues(Object.fromEntries(albumMetaFields(album).map((f) => [f.key, f.value])));
  }

  const save = async () => {
    const { updates, invalidDate } = albumMetaUpdates(album, values);
    if (invalidDate) {
      window.showToast?.('Release Date must be YYYY-MM-DD (or just YYYY)', 'error');
      return;
    }
    if (Object.keys(updates).length === 0) {
      // Reported as an error, not a no-op: the vanilla did, and silence would
      // read as a save that quietly failed.
      window.showToast?.('No album changes to save', 'error');
      return;
    }

    try {
      const response = await fetch(`/api/library/album/${album.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error);

      onSaved(updates);
      window.showToast?.(
        `Album metadata saved (${(result.updated_fields || []).join(', ')})`,
        'success',
      );
    } catch (error) {
      window.showToast?.(`Failed to save: ${(error as Error).message}`, 'error');
    }
  };

  return (
    <div className="enhanced-album-meta-row" id={`enhanced-album-meta-${album.id}`}>
      {fields.map((field) => (
        <div className="enhanced-album-meta-field" key={field.key}>
          <label className="enhanced-album-meta-label">{field.label}</label>
          {isAdmin ? (
            <input
              className="enhanced-album-meta-input"
              type={field.type || 'text'}
              placeholder={field.placeholder}
              data-album-id={String(album.id)}
              data-field={field.key}
              value={values[field.key] ?? ''}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
            />
          ) : (
            <span className="enhanced-album-meta-value">{field.value || '—'}</span>
          )}
        </div>
      ))}

      {isAdmin ? (
        <div className="enhanced-album-meta-field">
          {/* An empty label, so the button lines up with the inputs. */}
          <label className="enhanced-album-meta-label">&nbsp;</label>
          <button
            type="button"
            className="enhanced-album-save-btn"
            onClick={(e) => {
              e.stopPropagation();
              void save();
            }}
          >
            Save Album
          </button>
        </div>
      ) : null}


    </div>
  );
}
