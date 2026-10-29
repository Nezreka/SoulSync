import { useState } from 'react';

import type { LibraryV2EnhanceTrack } from '../-library-v2.types';

import styles from './library-v2-page.module.css';
import { LibraryToolDialog } from './tool-dialog';

/** Enhance Quality (#1062 review, point 2): pick which files below their
 *  quality profile get upgraded, monitored or not. The picks are monitored and
 *  searched right away; the profile alone decides what counts as better, so a
 *  search can come back empty and the Wishlist keeps looking. Same workspace
 *  as Preview Retag and Rename / Organize. */
export function EnhanceQualityModal({
  title,
  tracks,
  onEnhance,
  onClose,
}: {
  title: string;
  tracks: LibraryV2EnhanceTrack[];
  onEnhance: (trackIds: number[]) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(() => new Set(tracks.map((t) => t.track_id)));
  const albums: { albumId: number; albumTitle: string; tracks: LibraryV2EnhanceTrack[] }[] = [];
  for (const track of tracks) {
    const group = albums.find((a) => a.albumId === track.album_id);
    if (group) group.tracks.push(track);
    else albums.push({ albumId: track.album_id, albumTitle: track.album_title, tracks: [track] });
  }

  function toggle(trackId: number) {
    const next = new Set(selected);
    if (next.has(trackId)) next.delete(trackId);
    else next.add(trackId);
    setSelected(next);
  }

  return (
    <LibraryToolDialog
      title={`Enhance Quality — ${title}`}
      onClose={onClose}
      description="Files below their quality profile. Picked tracks are monitored and searched now; when no better copy turns up, the Wishlist keeps looking."
      footer={
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={selected.size === 0}
            onClick={() => onEnhance([...selected])}
          >
            {`Enhance (${selected.size})`}
          </button>
        </div>
      }
    >
      <div className={styles.previewToolbar}>
        <button
          className={styles.btnGhost}
          type="button"
          onClick={() =>
            setSelected(
              selected.size === tracks.length ? new Set() : new Set(tracks.map((t) => t.track_id)),
            )
          }
        >
          {selected.size === tracks.length ? 'Deselect all' : 'Select all'}
        </button>
      </div>
      <div className={styles.resultsWrap}>
        <table className={styles.trackTable}>
          <thead>
            <tr>
              <th className={styles.colMonitor}></th>
              <th className={styles.colNum}>#</th>
              <th>Title</th>
              <th>Current file</th>
              <th>Upgrade to</th>
              <th>Monitoring</th>
            </tr>
          </thead>
          {albums.map((album) => (
            <tbody key={album.albumId} className={styles.retagAlbumGroup}>
              <tr className={styles.albumGroupHeaderRow}>
                <td colSpan={6} className={styles.albumGroupHeader}>
                  <span className={styles.retagAlbumHeading}>
                    <span>{album.albumTitle}</span>
                    <span className={styles.retagAlbumCount}>
                      {album.tracks.length} below profile
                    </span>
                  </span>
                </td>
              </tr>
              {album.tracks.map((t) => (
                <tr key={t.track_id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Enhance ${t.title}`}
                      checked={selected.has(t.track_id)}
                      onChange={() => toggle(t.track_id)}
                    />
                  </td>
                  <td className={styles.colNum}>{t.track_number ?? '—'}</td>
                  <td>{t.title}</td>
                  <td className={styles.qualityText}>{t.current ?? 'Unknown'}</td>
                  <td className={styles.qualityText} title={`Quality profile: ${t.profile}`}>
                    {t.target ?? t.profile}
                  </td>
                  <td className={styles.qualityText}>
                    <span className={t.monitored ? styles.statusOk : styles.statusNeutral}>
                      {t.monitored ? 'Monitored' : 'Not monitored'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </LibraryToolDialog>
  );
}
