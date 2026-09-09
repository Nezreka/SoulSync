import { Link } from '@tanstack/react-router';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { AudiobookFollowedAuthor } from '@/routes/audiobooks/-audiobooks.types';

import {
  fetchFollowedAuthors,
  runAuthorScan,
  unfollowAuthor,
} from '@/routes/audiobooks/-audiobooks.api';

import { AudiobookAuthorSettingsModal } from './audiobook-author-settings-modal';
// Deliberately the podcast tab's stylesheet, not a private one. A followed
// author and a followed show are the same idea and were drawn two different
// ways; reusing the classes means one card design to maintain and any future
// change to it lands on both.
import styles from './watchlist-page.module.css';

function relativeTime(seconds: number): string {
  if (!seconds) return 'never';
  const delta = Date.now() / 1000 - seconds;
  if (delta < 90) return 'just now';
  const minutes = Math.round(delta / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

interface WatchlistAudiobooksTabProps {
  searchFilter?: string;
}

/**
 * Authors you follow, and what following them has turned up.
 *
 * Following an author does not queue their back catalogue — it records the day
 * you followed and picks up only what they publish after it. That is why each
 * card shows the watching-since date: without it "0 releases" reads as a
 * broken scan rather than an author who has not published since Tuesday.
 */
export function WatchlistAudiobooksTab({ searchFilter = '' }: WatchlistAudiobooksTabProps) {
  const [authors, setAuthors] = useState<AudiobookFollowedAuthor[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [notice, setNotice] = useState('');
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [settingsFor, setSettingsFor] = useState<AudiobookFollowedAuthor | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    const rows = await fetchFollowedAuthors();
    setAuthors(rows);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Close the dropdown on an outside click, the same way the podcast cards do.
  useEffect(() => {
    if (!openMenu) return;
    const onDocClick = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpenMenu(null);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [openMenu]);

  const remove = async (name: string) => {
    setOpenMenu(null);
    await unfollowAuthor(name);
    setAuthors((prev) => prev.filter((a) => a.name !== name));
  };

  const scanNow = async () => {
    setScanning(true);
    setNotice('');
    const summary = await runAuthorScan();
    setScanning(false);
    if (summary) {
      const found = summary.wishlisted ?? 0;
      setNotice(
        found > 0
          ? `Wishlisted ${found} new ${found === 1 ? 'release' : 'releases'}.`
          : 'No new releases since the last look.',
      );
    }
    void load();
  };

  const filtered = searchFilter
    ? authors.filter((a) => a.name.toLowerCase().includes(searchFilter.toLowerCase()))
    : authors;

  if (loading) {
    return (
      <div className={styles.podcastsTabContainer}>
        <p style={{ color: 'var(--text-secondary, #9aa0aa)' }}>Loading followed authors…</p>
      </div>
    );
  }

  if (authors.length === 0) {
    return (
      <div className="watchlist-page-empty" style={{ padding: '40px 0', textAlign: 'center' }}>
        <p style={{ color: 'var(--text-secondary, #9aa0aa)' }}>
          You are not following any authors yet. Open an author from the audiobooks page and use the
          watchlist button to have their next release picked up automatically.
        </p>
        <Link to="/audiobooks" className="wl-primary-btn">
          Browse Audiobooks
        </Link>
      </div>
    );
  }

  return (
    <div className={styles.podcastsTabContainer}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 16,
          marginBottom: 14,
          flexWrap: 'wrap',
        }}
      >
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-secondary, #9aa0aa)' }}>
          Checked once a day. Only books published after you followed an author are picked up.
        </p>
        <button
          type="button"
          className="wl-primary-btn"
          onClick={() => void scanNow()}
          disabled={scanning}
        >
          {scanning ? 'Checking…' : 'Check now'}
        </button>
      </div>

      {notice && (
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: 'var(--text-secondary, #9aa0aa)' }}>
          {notice}
        </p>
      )}

      {filtered.length === 0 ? (
        <div className="watchlist-page-empty" style={{ padding: '40px 0' }}>
          <p style={{ color: 'var(--text-secondary, #9aa0aa)' }}>
            No authors match &quot;{searchFilter}&quot;
          </p>
        </div>
      ) : (
        <div className={styles.podcastsGrid}>
          {filtered.map((author) => {
            const isMenuOpen = openMenu === author.name;
            const autoOn = author.auto_wishlist !== 0;

            return (
              <Link
                key={author.name}
                to="/audiobooks/author/$name"
                params={{ name: author.name }}
                className={styles.podcastCard}
                aria-label={author.name}
              >
                <div className={styles.podcastArtWrapper}>
                  {/* Their own cover art, the same source the author pages use. */}
                  {author.cover_url ? (
                    <img
                      src={author.cover_url}
                      alt={author.name}
                      className={styles.podcastArt}
                      loading="lazy"
                    />
                  ) : (
                    <div className={styles.podcastArtPlaceholder}>📚</div>
                  )}

                  <div
                    className={styles.podcastActions}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                    }}
                    ref={isMenuOpen ? menuRef : undefined}
                  >
                    <button
                      type="button"
                      className={styles.podcastMenuBtn}
                      title="Author options"
                      aria-label="Author options"
                      onClick={() => setOpenMenu(isMenuOpen ? null : author.name)}
                    >
                      •••
                    </button>

                    {isMenuOpen && (
                      <div className={styles.podcastDropdownMenu}>
                        <button
                          type="button"
                          className={styles.podcastDropdownItem}
                          onClick={() => {
                            setOpenMenu(null);
                            setSettingsFor(author);
                          }}
                        >
                          <span>⚙️</span>
                          <span>Watchlist Settings</span>
                        </button>
                        <button
                          type="button"
                          className={`${styles.podcastDropdownItem} ${styles.podcastDropdownItemDanger}`}
                          onClick={() => void remove(author.name)}
                        >
                          <span>🗑️</span>
                          <span>Remove from Watchlist</span>
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                <div className={styles.podcastContent}>
                  <div className={styles.podcastTitle} title={author.name}>
                    {author.name}
                  </div>
                  <div className={styles.podcastAuthor}>
                    {author.since_date ? `Watching since ${author.since_date}` : 'Author'}
                  </div>

                  <div className={styles.podcastBadgesRow}>
                    {autoOn ? (
                      <span
                        className={styles.podcastBadgeSuccess}
                        title="New releases are added to your wishlist automatically"
                      >
                        ⚡ Auto-wishlist
                      </span>
                    ) : (
                      <span
                        className={styles.podcastBadgeMuted}
                        title="New releases are recorded but not downloaded"
                      >
                        👁️ Monitored
                      </span>
                    )}

                    <span
                      className={styles.podcastBadgeRetention}
                      title={
                        author.narrator_mode === 'any'
                          ? 'Any narrator is accepted for their new releases'
                          : 'Only the narrator credited on the book itself'
                      }
                    >
                      {author.narrator_mode === 'any' ? '🎙️ Any narrator' : '🎙️ Exact narrator'}
                    </span>

                    {author.found_total > 0 && (
                      <span className={styles.podcastBadgeCount}>{author.found_total} found</span>
                    )}
                  </div>

                  <div className={styles.podcastFooter}>
                    <span className={styles.podcastScanText}>
                      {author.last_error
                        ? `Last check failed: ${author.last_error}`
                        : `Checked ${relativeTime(author.last_scanned_at)}`}
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}

      {settingsFor && (
        <AudiobookAuthorSettingsModal
          author={settingsFor}
          isOpen={Boolean(settingsFor)}
          onClose={() => setSettingsFor(null)}
          onSaved={() => void load()}
        />
      )}
    </div>
  );
}
