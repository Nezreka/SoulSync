import { useEffect, useState } from 'react';

import type { AudiobookFollowedAuthor } from '@/routes/audiobooks/-audiobooks.types';

import { unfollowAuthor, updateFollowedAuthor } from '@/routes/audiobooks/-audiobooks.api';

import styles from './watchlist-page.module.css';

interface AudiobookAuthorSettingsModalProps {
  author: AudiobookFollowedAuthor;
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
}

/**
 * Per-author settings, the same shape as the podcast show's.
 *
 * A podcast asks two questions — download automatically, and how long to keep
 * it. An author's are different because a book is not episodic: nothing needs
 * pruning, but the narrator DOES need answering, and it has to be answered
 * here because an auto-wishlisted book is queued without anyone seeing it.
 * There is no release modal to ask in.
 */
export function AudiobookAuthorSettingsModal({
  author,
  isOpen,
  onClose,
  onSaved,
}: AudiobookAuthorSettingsModalProps) {
  const [autoWishlist, setAutoWishlist] = useState(author.auto_wishlist !== 0);
  const [narratorMode, setNarratorMode] = useState(author.narrator_mode || 'exact');
  const [sinceDate, setSinceDate] = useState(author.since_date || '');
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);

  useEffect(() => {
    setAutoWishlist(author.auto_wishlist !== 0);
    setNarratorMode(author.narrator_mode || 'exact');
    setSinceDate(author.since_date || '');
  }, [author]);

  const save = async () => {
    setSaving(true);
    await updateFollowedAuthor(author.name, {
      auto_wishlist: autoWishlist ? 1 : 0,
      narrator_mode: narratorMode,
      since_date: sinceDate,
    });
    setSaving(false);
    onSaved();
    onClose();
  };

  const remove = async () => {
    // The app's own confirm, never window.confirm.
    const confirmed = await window.showConfirmDialog?.({
      title: 'Remove from Watchlist',
      message: `Stop watching ${author.name} for new releases?`,
      confirmText: 'Remove',
      destructive: true,
    });
    if (confirmed === false) return;
    setRemoving(true);
    await unfollowAuthor(author.name);
    setRemoving(false);
    onSaved();
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div
      className="modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Author Watchlist Settings"
    >
      <div
        className={`watchlist-artist-config-modal ${styles.podcastModal}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="wl-global-modal-head">
          <div className={styles.modalShowHeader}>
            {author.cover_url ? (
              <img src={author.cover_url} alt={author.name} className={styles.modalShowArt} />
            ) : (
              <div className={styles.modalShowArtPlaceholder}>📚</div>
            )}
            <div>
              <h2 className="wl-global-modal-title">{author.name}</h2>
              <p className="wl-global-modal-sub">New Release Preferences</p>
            </div>
          </div>
          <button className="wl-global-modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="watchlist-artist-config-content">
          <div className="watchlist-artist-config-body">
            <div className="config-section">
              <h3 className="config-section-title">New Releases</h3>
              <p className="config-section-subtitle">
                What to do when this author publishes something you do not already have.
              </p>
              <label className="config-option">
                <input
                  type="checkbox"
                  checked={autoWishlist}
                  onChange={(e) => setAutoWishlist(e.target.checked)}
                />
                <div className="config-option-content">
                  <div className="config-option-icon">⚡</div>
                  <div className="config-option-text">
                    <span className="config-option-title">Add new releases to the wishlist</span>
                    <span className="config-option-description">
                      Off, their new books are still found and counted, just not queued for
                      download.
                    </span>
                  </div>
                </div>
              </label>
            </div>

            <div className="config-section">
              <h3 className="config-section-title">Narrator</h3>
              <p className="config-section-subtitle">
                Answered here because nobody sees an auto-wishlisted book before it is queued. A
                book is always one narrator, never a mixture.
              </p>
              <div className={styles.retentionPresetList}>
                <button
                  type="button"
                  className={`${styles.retentionPresetBtn} ${narratorMode === 'exact' ? styles.retentionPresetBtnActive : ''}`}
                  onClick={() => setNarratorMode('exact')}
                >
                  Only the credited narrator
                </button>
                <button
                  type="button"
                  className={`${styles.retentionPresetBtn} ${narratorMode === 'any' ? styles.retentionPresetBtnActive : ''}`}
                  onClick={() => setNarratorMode('any')}
                >
                  Any narrator
                </button>
              </div>
              <span className={styles.retentionHint}>
                {narratorMode === 'any'
                  ? 'A release naming a different narrator is still accepted, just ranked lower.'
                  : 'A release naming a different narrator is rejected. Releases that name nobody are always allowed, because most do not say.'}
              </span>
            </div>

            <div className="config-section">
              <h3 className="config-section-title">Watching Since</h3>
              <p className="config-section-subtitle">
                Only books published after this date are picked up. Move it back to pull in titles
                you missed; following an author deliberately does not queue their whole back
                catalogue.
              </p>
              <div className={styles.retentionInputRow}>
                <label htmlFor="author-since-date" className={styles.retentionInputLabel}>
                  Published after:
                </label>
                <input
                  id="author-since-date"
                  type="date"
                  value={sinceDate}
                  onChange={(e) => setSinceDate(e.target.value)}
                  className={styles.retentionInput}
                />
              </div>
            </div>

            <div className="config-section">
              <h3 className="config-section-title">Danger Zone</h3>
              <div className={styles.dangerZoneRow}>
                <div>
                  <div className={styles.dangerTitle}>Remove from Watchlist</div>
                  <div className={styles.dangerSub}>
                    Stop watching this author for new releases. Anything already downloaded or
                    wishlisted is untouched.
                  </div>
                </div>
                <button
                  type="button"
                  className="btn btn--danger"
                  onClick={() => void remove()}
                  disabled={removing}
                >
                  {removing ? 'Removing...' : 'Remove'}
                </button>
              </div>
            </div>
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="btn btn--secondary"
              onClick={onClose}
              disabled={saving}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => void save()}
              disabled={saving}
            >
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
