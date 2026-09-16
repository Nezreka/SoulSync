import { useEffect, useState } from 'react';

import type { AudiobookBlockedRelease, AudiobookRecycledBook } from '../-audiobooks.types';

import {
  clearBlocklist,
  emptyRecycleBin,
  fetchBlocklist,
  fetchRecycleBin,
  purgeRecycledBook,
  restoreRecycledBook,
  unblockRelease,
} from '../-audiobooks.api';
import { AudiobookOverlay } from './audiobook-overlay';
import styles from './audiobooks-page.module.css';

type Pane = 'recycle' | 'blocklist';

function when(seconds: number): string {
  if (!seconds) return '';
  const days = Math.floor((Date.now() / 1000 - seconds) / 86400);
  if (days < 1) return 'today';
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

/**
 * The two things SoulSync is holding back: books you deleted, and releases it
 * will not fetch.
 *
 * One modal with two panes, the way the video side pairs them — they answer
 * the same question from opposite ends ("what did I remove, and can I undo
 * it") and splitting them across two entry points made neither discoverable.
 */
export function AudiobookReviewModal({
  onClose,
  initialPane = 'recycle',
}: {
  onClose: () => void;
  initialPane?: Pane;
}) {
  const [pane, setPane] = useState<Pane>(initialPane);
  const [bin, setBin] = useState<AudiobookRecycledBook[]>([]);
  const [keepDays, setKeepDays] = useState(7);
  const [blocked, setBlocked] = useState<AudiobookBlockedRelease[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');

  const load = async () => {
    const [recycle, blocklist] = await Promise.all([fetchRecycleBin(), fetchBlocklist()]);
    setBin(recycle.entries);
    setKeepDays(recycle.keepDays);
    setBlocked(blocklist);
    setLoading(false);
  };

  useEffect(() => {
    void load();
  }, []);

  const restore = async (entry: AudiobookRecycledBook) => {
    setBusy(entry.name);
    const result = await restoreRecycledBook(entry.name);
    setBusy('');
    if (result.ok) {
      setBin((prev) => prev.filter((e) => e.name !== entry.name));
      setMessage(`"${entry.original}" is back in your library.`);
    } else {
      setMessage(result.error || 'Could not put that book back.');
    }
  };

  const purge = async (entry: AudiobookRecycledBook) => {
    const ok = await window.showConfirmDialog?.({
      title: 'Delete permanently',
      message: `Erase "${entry.original}" for good? This cannot be undone.`,
      confirmText: 'Delete',
      destructive: true,
    });
    if (ok === false) return;
    setBusy(entry.name);
    await purgeRecycledBook(entry.name);
    setBusy('');
    setBin((prev) => prev.filter((e) => e.name !== entry.name));
  };

  const emptyAll = async () => {
    const ok = await window.showConfirmDialog?.({
      title: 'Empty the recycle bin',
      message: `Permanently erase all ${bin.length} books? This frees the space now and cannot be undone.`,
      confirmText: 'Empty bin',
      destructive: true,
    });
    if (ok === false) return;
    await emptyRecycleBin();
    setBin([]);
  };

  const unblock = async (key: string) => {
    await unblockRelease(key);
    setBlocked((prev) => prev.filter((r) => r.key !== key));
  };

  const clearAll = async () => {
    const ok = await window.showConfirmDialog?.({
      title: 'Clear the blocklist',
      message: `Unblock all ${blocked.length} releases? They can be found and grabbed again.`,
      confirmText: 'Clear',
      destructive: true,
    });
    if (ok === false) return;
    await clearBlocklist();
    setBlocked([]);
  };

  return (
    <AudiobookOverlay onClose={onClose} label="Recycle bin and blocklist">
      <div className={styles.modal}>
        <header className={styles.modalHeader}>
          <div>
            <span className={styles.modalEyebrow}>Review</span>
            <h2 className={styles.modalTitle}>Removed &amp; refused</h2>
          </div>
          <button type="button" className={styles.modalClose} onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </header>

        <div className={styles.reviewPills}>
          <button
            type="button"
            className={`${styles.reviewPill} ${pane === 'recycle' ? styles.reviewPillActive : ''}`}
            onClick={() => setPane('recycle')}
          >
            🗑 Recycle bin ({bin.length})
          </button>
          <button
            type="button"
            className={`${styles.reviewPill} ${pane === 'blocklist' ? styles.reviewPillActive : ''}`}
            onClick={() => setPane('blocklist')}
          >
            ⛔ Blocklist ({blocked.length})
          </button>
        </div>

        {message && <p className={styles.modalMessage}>{message}</p>}

        <div className={styles.modalBody}>
          {loading ? (
            <div className={styles.modalLoading}>
              <span className={styles.spinner} aria-hidden="true" />
              Loading…
            </div>
          ) : pane === 'recycle' ? (
            bin.length === 0 ? (
              <div className={styles.emptyState}>
                <h3>The bin is empty</h3>
                <p>
                  {keepDays > 0
                    ? `Deleted books wait here for ${keepDays} days before being erased, so a mis-click can be undone.`
                    : 'Recycling is turned off, so deleting a book erases it immediately.'}
                </p>
              </div>
            ) : (
              <>
                <p className={styles.blocklistNote}>
                  {keepDays > 0
                    ? `Erased automatically after ${keepDays} days. Until then the space is still used — empty the bin to free it now.`
                    : 'Recycling is turned off, so nothing new will arrive here.'}
                </p>
                <ul className={styles.releaseList}>
                  {bin.map((entry) => (
                    <li className={styles.releaseRow} key={entry.name}>
                      <div className={styles.releaseMain}>
                        <span className={styles.releaseTitle}>{entry.original}</span>
                        <div className={styles.releaseTags}>
                          <span className={styles.releaseTag}>
                            {entry.age_days < 1 ? 'today' : `${entry.age_days} days ago`}
                          </span>
                          {keepDays > 0 && (
                            <span className={styles.releaseTag}>
                              {Math.max(0, Math.ceil(keepDays - entry.age_days))} days left
                            </span>
                          )}
                        </div>
                        {entry.original_path && (
                          <span className={styles.releaseReasons} title={entry.original_path}>
                            {entry.original_path}
                          </span>
                        )}
                      </div>
                      <div className={styles.reviewRowActions}>
                        <button
                          type="button"
                          className={styles.releaseGrab}
                          onClick={() => void restore(entry)}
                          disabled={busy === entry.name}
                        >
                          {busy === entry.name ? '…' : 'Restore'}
                        </button>
                        <button
                          type="button"
                          className={styles.libraryDelete}
                          onClick={() => void purge(entry)}
                          disabled={busy === entry.name}
                        >
                          Delete
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className={styles.blocklistClear}
                  onClick={() => void emptyAll()}
                >
                  Empty the bin and free the space
                </button>
              </>
            )
          ) : blocked.length === 0 ? (
            <div className={styles.emptyState}>
              <h3>Nothing is blocked</h3>
              <p>
                A release lands here when its download fails, so the wishlist stops finding the same
                broken copy. You can also block one by hand from the releases list.
              </p>
            </div>
          ) : (
            <>
              <p className={styles.blocklistNote}>
                These are releases, not books. The books are still wanted — only these particular
                copies are refused.
              </p>
              <ul className={styles.releaseList}>
                {blocked.map((row) => (
                  <li className={styles.releaseRow} key={row.key}>
                    <div className={styles.releaseMain}>
                      <span className={styles.releaseTitle}>{row.release_title || row.key}</span>
                      <div className={styles.releaseTags}>
                        {row.book_title && (
                          <span className={styles.releaseTag}>{row.book_title}</span>
                        )}
                        {row.indexer && <span className={styles.releaseTag}>{row.indexer}</span>}
                        {row.protocol && <span className={styles.releaseTag}>{row.protocol}</span>}
                        {row.blocked_at > 0 && (
                          <span className={styles.releaseTag}>{when(row.blocked_at)}</span>
                        )}
                      </div>
                      {row.reason && <span className={styles.releaseReasons}>{row.reason}</span>}
                    </div>
                    <button
                      type="button"
                      className={styles.releaseGrab}
                      onClick={() => void unblock(row.key)}
                    >
                      Unblock
                    </button>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                className={styles.blocklistClear}
                onClick={() => void clearAll()}
              >
                Clear the whole blocklist
              </button>
            </>
          )}
        </div>
      </div>
    </AudiobookOverlay>
  );
}
