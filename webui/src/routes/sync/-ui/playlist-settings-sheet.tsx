/**
 * A playlist's settings, in one place.
 *
 * These used to sit in the card's ⋯ menu beside the actions: organize, the
 * quality profile, the sync mode, auto-remove, the source link, cover art and
 * rename. A menu is for doing things; these are how a playlist BEHAVES, and
 * mixing the two made a thirteen-item menu nobody could scan. Every control
 * here calls the same handler the menu did, so nothing about what they do
 * changed, only where they live.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import {
  fetchRotationPreview,
  rotationStatus,
  rotationSummary,
  type RotationPreview,
} from '../-sync.rotation';
import { isUserPlaylist, LIKED_SOURCE_ID } from '../../../features/playlists/user-playlists';
import styles from './playlists.module.css';

export interface PlaylistSettingsSheetProps {
  row: MirroredPlaylistRow;
  /** the shown name, which may be a rename */
  name: string;
  /** the source, as people say it */
  sourceLabel: string;
  /** the current cadence, as the card shows it */
  scheduleLabel: string;
  /** opens the card's schedule picker at this button; absent when it can't be scheduled */
  onSchedule?: (anchor: HTMLElement) => void;
  /** '' clears the rename */
  onRename: (customName: string) => void;
  onToggleOrganize: (enabled: boolean) => void;
  /** rotating (#2039); absent hides the row */
  onToggleRotating?: (enabled: boolean) => void;
  /** '' = back to the global mode */
  onSetSyncMode: (mode: string) => void;
  onToggleAutoRemove?: () => void;
  onEditSource?: () => void;
  onGenerateCover?: () => void;
  onClear?: () => void;
  onDelete: () => void;
  onClose: () => void;
}

export function PlaylistSettingsSheet({
  row,
  name,
  sourceLabel,
  scheduleLabel,
  onSchedule,
  onRename,
  onToggleOrganize,
  onToggleRotating,
  onSetSyncMode,
  onToggleAutoRemove,
  onEditSource,
  onGenerateCover,
  onClear,
  onDelete,
  onClose,
}: PlaylistSettingsSheetProps) {
  const own = isUserPlaylist(row);
  const [draft, setDraft] = useState(row.custom_name ?? '');
  // what was last saved from here: a rename then a clear in one sitting must
  // both save, and the row only catches up after its reload
  const committed = useRef((row.custom_name ?? '').trim());

  // The quality profile select is markup from a legacy global, hydrated after
  // mount, because its options come from a store React does not own. Same
  // contract the card menu used. Absent global, absent control.
  const profileHtml =
    typeof window.playlistQualityProfileSelectHtml === 'function'
      ? window.playlistQualityProfileSelectHtml(row.source_playlist_id, row.source, true)
      : '';
  // one object per html string: react rewrites dangerouslySetInnerHTML whenever
  // the prop OBJECT changes, which would wipe the hydrated options
  const profileMarkup = useMemo(() => ({ __html: profileHtml }), [profileHtml]);
  const { source_playlist_id: sourcePlaylistId, source, quality_profile_id: profileId } = row;
  useEffect(() => {
    if (!profileHtml) return;
    if (typeof window.hydratePlaylistQualityProfileSelects !== 'function') return;
    void window.hydratePlaylistQualityProfileSelects(sourcePlaylistId, source, profileId);
  }, [profileHtml, sourcePlaylistId, source, profileId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const commitName = () => {
    const next = draft.trim();
    if (next === committed.current) return;
    committed.current = next;
    onRename(next);
  };

  const spotifyRequest =
    Boolean(row.is_request_list) && (row.source === 'spotify' || row.source === 'spotify_public');

  // a request list drops a track once it downloads, and Liked Songs is not a
  // rotation. the server refuses both, so the row is not offered
  const canRotate =
    Boolean(onToggleRotating) &&
    !row.is_request_list &&
    !(own && row.source_playlist_id === LIKED_SOURCE_ID);
  // what rotating does for THIS playlist right now, read again whenever the
  // toggle lands so the words follow it
  const [rotation, setRotation] = useState<RotationPreview | null>(null);
  const { id: rowId, rotating: rowRotating } = row;
  useEffect(() => {
    if (!canRotate) return;
    let live = true;
    void fetchRotationPreview(rowId).then((preview) => {
      if (live) setRotation(preview);
    });
    return () => {
      live = false;
    };
  }, [canRotate, rowId, rowRotating]);
  const rotationLine = rotationSummary(rotation);
  const rotationState = rotationStatus(rotation);

  return (
    <>
      <button
        type="button"
        className={styles.scrim}
        aria-label="Close settings"
        tabIndex={-1}
        onClick={onClose}
      />
      <div
        className={`playlist-settings-sheet ${styles.sheet}`}
        role="dialog"
        aria-modal="true"
        aria-label={`Settings for ${name}`}
      >
        <div className={styles.sheetHead}>
          <div>
            <h2 className={styles.sheetTitle}>Settings</h2>
            <p className={styles.sheetSub}>{name}</p>
          </div>
          <button
            type="button"
            className={styles.done}
            onClick={() => {
              commitName();
              onClose();
            }}
          >
            Done
          </button>
        </div>

        <div className={styles.group}>
          <div className={styles.row}>
            <div className={styles.rowText}>
              <div className={styles.rowTitle}>Name</div>
              <div className={styles.rowDesc}>
                {own ? 'Shown here and on your server' : `Leave blank to use “${row.name ?? ''}”`}
              </div>
            </div>
            <input
              className={styles.field}
              aria-label="Name"
              value={draft}
              placeholder={row.name ?? ''}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitName}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitName();
              }}
            />
          </div>
          {onGenerateCover ? (
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowTitle}>Cover art</div>
                <div className={styles.rowDesc}>A new cover from its genres and mood</div>
              </div>
              <button type="button" className={styles.link} onClick={onGenerateCover}>
                Generate cover art
              </button>
            </div>
          ) : null}
        </div>

        <div className={styles.groupLabel}>Syncing</div>
        <div className={styles.group}>
          <div className={styles.row}>
            <div className={styles.rowText}>
              <div className={styles.rowTitle}>Schedule</div>
              <div className={styles.rowDesc}>
                {onSchedule ? scheduleLabel : 'Auto-Sync cannot refresh this source'}
              </div>
            </div>
            {onSchedule ? (
              <button
                type="button"
                className={styles.link}
                onClick={(e) => onSchedule(e.currentTarget)}
              >
                Change
              </button>
            ) : (
              <span className={styles.rowDesc}>Can’t be scheduled</span>
            )}
          </div>
          <div className={styles.row}>
            <div className={styles.rowText}>
              <div className={styles.rowTitle}>Sync mode</div>
              <div className={styles.rowDesc}>
                What happens on your server when the source changes
              </div>
            </div>
            <select
              className={styles.field}
              aria-label="Sync mode"
              value={row.sync_mode ?? ''}
              onChange={(e) => onSetSyncMode(e.target.value)}
            >
              <option value="">Use global setting</option>
              <option value="reconcile">Reconcile (match the source)</option>
              <option value="append">Append only (never remove tracks)</option>
              <option value="replace">Replace (delete and recreate)</option>
            </select>
          </div>
        </div>

        <div className={styles.groupLabel}>Downloads</div>
        <div className={styles.group}>
          {profileHtml ? (
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowTitle}>Quality profile</div>
              </div>
              <span className={styles.profileSlot} dangerouslySetInnerHTML={profileMarkup} />
            </div>
          ) : null}
          <div className={styles.row}>
            <div className={styles.rowText}>
              <div className={styles.rowTitle}>Organize by playlist</div>
              <div className={styles.rowDesc}>
                Downloads go into a folder named after this playlist. Missed tracks download
                directly instead of going to the wishlist
              </div>
            </div>
            <label className={styles.switch}>
              <input
                type="checkbox"
                aria-label="Organize by playlist"
                checked={Boolean(row.organize_by_playlist)}
                onChange={(e) => onToggleOrganize(e.target.checked)}
              />
              <span />
            </label>
          </div>
          {canRotate ? (
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowTitle}>Rotating</div>
                <div className={styles.rowDesc}>
                  Removes a song this playlist downloaded once it leaves the playlist. Songs you
                  played, favourited or still have in another playlist stay
                </div>
                {rotationLine ? (
                  <div className={styles.rowNote} data-testid="rotation-summary">
                    {rotationLine}
                  </div>
                ) : null}
                {rotationState ? (
                  <div className={styles.rowNote} data-testid="rotation-status">
                    {rotationState}{' '}
                    <a className={styles.rowNoteLink} href="/tools?job=expired_download_cleaner">
                      Cleaner settings
                    </a>
                  </div>
                ) : null}
              </div>
              <label className={styles.switch}>
                <input
                  type="checkbox"
                  aria-label="Rotating"
                  checked={Boolean(row.rotating)}
                  onChange={(e) => onToggleRotating?.(e.target.checked)}
                />
                <span />
              </label>
            </div>
          ) : null}
          {spotifyRequest && onToggleAutoRemove ? (
            <div className={styles.row}>
              <div className={styles.rowText}>
                <div className={styles.rowTitle}>Auto-remove fulfilled</div>
                <div className={styles.rowDesc}>
                  Removes a track from the Spotify playlist once it’s in your library
                </div>
              </div>
              <label className={styles.switch}>
                <input
                  type="checkbox"
                  aria-label="Auto-remove fulfilled"
                  checked={Boolean(row.auto_remove_fulfilled)}
                  onChange={onToggleAutoRemove}
                />
                <span />
              </label>
            </div>
          ) : null}
        </div>

        {!own && onEditSource ? (
          <>
            <div className={styles.groupLabel}>Source</div>
            <div className={styles.group}>
              <div className={styles.row}>
                <div className={styles.rowText}>
                  <div className={styles.rowTitle}>{sourceLabel} playlist</div>
                  <div className={styles.rowDesc}>The playlist this one follows</div>
                </div>
                <button type="button" className={styles.link} onClick={onEditSource}>
                  Edit source link
                </button>
              </div>
            </div>
          </>
        ) : null}

        <div className={styles.foot}>
          {onClear ? (
            <button
              type="button"
              className={`${styles.link} ${styles.linkQuiet}`}
              onClick={onClear}
            >
              Clear &amp; re-identify
            </button>
          ) : (
            <span />
          )}
          <button
            type="button"
            className={`${styles.link} ${styles.linkDanger}`}
            onClick={onDelete}
          >
            {own ? 'Delete playlist' : 'Delete mirror'}
          </button>
        </div>
      </div>
    </>
  );
}
