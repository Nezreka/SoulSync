import { Link } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useState } from 'react';

import type {
  AudiobookNarratorMode,
  AudiobookWishlistCounts,
  AudiobookWishlistEntry,
  AudiobookWishlistStatus,
  AudiobookWishlistSummary,
} from '@/routes/audiobooks/-audiobooks.types';

import {
  fetchWishlist,
  removeFromWishlist,
  retryWishlistEntry,
  runWishlistPass,
  searchWishlistBook,
  setNarratorMode,
} from '@/routes/audiobooks/-audiobooks.api';
import { AudiobookReleasesModal } from '@/routes/audiobooks/-ui/audiobook-releases-modal';

import styles from './wishlist-audiobooks.module.css';

const FILTERS: { key: AudiobookWishlistStatus | 'all'; label: string; dotClass?: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'wanted', label: 'Looking', dotClass: styles.dotLooking },
  { key: 'grabbed', label: 'In progress', dotClass: styles.dotActive },
  { key: 'done', label: 'In library', dotClass: styles.dotDone },
  { key: 'failed', label: 'Not found yet', dotClass: styles.dotFailed },
  { key: 'cancelled', label: 'Cancelled', dotClass: styles.dotCancelled },
];

const STATUS_LABELS: Record<AudiobookWishlistStatus, string> = {
  wanted: 'Looking',
  searching: 'Searching now',
  grabbed: 'Queued',
  cancelled: 'Cancelled',
  done: 'In your library',
  failed: 'Not found yet',
};

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

function getStatusBadgeClass(status: AudiobookWishlistStatus): string {
  switch (status) {
    case 'done':
      return styles.badgeDone;
    case 'grabbed':
      return styles.badgeActive;
    case 'searching':
      return styles.badgeSearching;
    case 'wanted':
      return styles.badgeLooking;
    case 'failed':
      return styles.badgeFailed;
    default:
      return '';
  }
}

/**
 * The audiobooks half of the wishlist page.
 *
 * Designed with a premium aesthetic featuring:
 * - Glassmorphic command hero with live automation status
 * - Interactive status chips with glowing indicator dots
 * - Dual view modes: Gallery Grid & Bookshelf List
 * - 3D book cover depth with spine sheen and hover elevation
 * - Narrator lock toggles and series badges
 */
export function WishlistAudiobooks({
  onCountChange,
}: {
  onCountChange?: (count: number) => void;
} = {}) {
  const [items, setItems] = useState<AudiobookWishlistEntry[]>([]);
  const [counts, setCounts] = useState<AudiobookWishlistCounts | null>(null);
  const [worker, setWorker] = useState<AudiobookWishlistSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<AudiobookWishlistStatus | 'all'>('all');
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchingAsin, setSearchingAsin] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const [releasesFor, setReleasesFor] = useState<AudiobookWishlistEntry | null>(null);

  const [viewMode, setViewMode] = useState<'grid' | 'shelf'>(() => {
    try {
      const stored = window.localStorage.getItem('audiobookWishlistView');
      return stored === 'shelf' ? 'shelf' : 'grid';
    } catch {
      return 'grid';
    }
  });

  const handleSetViewMode = (mode: 'grid' | 'shelf') => {
    setViewMode(mode);
    try {
      window.localStorage.setItem('audiobookWishlistView', mode);
    } catch {
      // ignore local storage errors
    }
  };

  const load = useCallback(async () => {
    const view = await fetchWishlist();
    setItems(view.items);
    setCounts(view.counts);
    setWorker(view.worker);
    setLoading(false);
    onCountChange?.(view.counts?.total ?? view.items.length);
  }, [onCountChange]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 20000);
    return () => window.clearInterval(timer);
  }, [load]);

  const shown = useMemo(() => {
    let list = items;
    if (filter !== 'all') {
      list = list.filter((item) => item.status === filter);
    }
    const q = query.trim().toLowerCase();
    if (q) {
      list = list.filter((item) => {
        const title = (item.title || '').toLowerCase();
        const authors = (item.authors || []).join(' ').toLowerCase();
        const narrators = (item.narrators || []).join(' ').toLowerCase();
        const series = (item.series_title || '').toLowerCase();
        return (
          title.includes(q) ||
          authors.includes(q) ||
          narrators.includes(q) ||
          series.includes(q)
        );
      });
    }
    return list;
  }, [items, filter, query]);

  const searchNow = async () => {
    setSearching(true);
    setNotice('');
    const summary = await runWishlistPass();
    setSearching(false);
    if (!summary) {
      setNotice('Could not run a search pass.');
    } else {
      const checked = summary.checked ?? 0;
      setNotice(
        checked === 0
          ? 'Nothing was due for another look yet.'
          : `Checked ${checked}, sent ${summary.grabbed ?? 0} to downloads.`,
      );
    }
    await load();
  };

  const searchBook = async (asin: string) => {
    setSearchingAsin(asin);
    setNotice('');
    const res = await searchWishlistBook(asin);
    setSearchingAsin(null);
    if (!res.success) {
      setNotice(`Search failed for ${asin}: ${res.error || 'Unknown error'}`);
    } else if (res.outcome?.grabbed) {
      setNotice('Found and sent to downloads!');
    } else if (res.outcome?.found && res.outcome.found > 0) {
      setNotice(`Found ${res.outcome.found} releases (not grabbed).`);
    } else {
      setNotice('No releases found for this title yet.');
    }
    await load();
  };

  const changeNarratorMode = async (asin: string, mode: AudiobookNarratorMode) => {
    setItems((current) =>
      current.map((item) => (item.asin === asin ? { ...item, narrator_mode: mode } : item)),
    );
    const ok = await setNarratorMode(asin, mode);
    if (!ok) await load();
  };

  const remove = async (asin: string) => {
    setItems((current) => current.filter((item) => item.asin !== asin));
    await removeFromWishlist(asin);
    await load();
  };

  const lookAgain = async (asin: string) => {
    setItems((current) =>
      current.map((item) =>
        item.asin === asin
          ? { ...item, status: 'wanted', last_error: '', last_attempt_at: 0 }
          : item,
      ),
    );
    const ok = await retryWishlistEntry(asin);
    if (!ok) await load();
  };

  const backoffHours = Math.round((worker?.retry_after_seconds ?? 0) / 3600);

  return (
    <div className={styles.panel}>
      {/* ── Glassmorphic Command Hero ───────────────────────────────────── */}
      <section className={styles.heroBanner}>
        <div className={styles.heroContent}>
          <div className={styles.heroTagline}>
            <span className={styles.heroPill}>
              <span className={styles.heroPulseDot} />
              Automation Active
            </span>
            <span className={styles.heroTitle}>
              {worker?.automation_name ?? 'Auto-Process Audiobook Wishlist'}
            </span>
          </div>

          <p className={styles.heroDesc}>
            Searches indexers on a schedule
            {backoffHours > 0 && ` — each title waits ${backoffHours} hours between attempts to prevent rate-limiting`}
            . Customise frequency anytime on the Automations page.
          </p>

          {counts && (
            <div className={styles.heroStats}>
              <div className={styles.heroStatItem}>
                <span className={styles.heroStatVal}>{counts.total}</span>
                <span className={styles.heroStatLabel}>Total</span>
              </div>
              <div className={styles.heroStatItem}>
                <span className={styles.heroStatVal}>{counts.wanted ?? 0}</span>
                <span className={styles.heroStatLabel}>Looking</span>
              </div>
              <div className={styles.heroStatItem}>
                <span className={styles.heroStatVal}>{(counts.searching ?? 0) + (counts.grabbed ?? 0)}</span>
                <span className={styles.heroStatLabel}>In Flight</span>
              </div>
              <div className={styles.heroStatItem}>
                <span className={styles.heroStatVal}>{counts.done ?? 0}</span>
                <span className={styles.heroStatLabel}>In Library</span>
              </div>
            </div>
          )}
        </div>

        <div className={styles.heroActions}>
          <button
            type="button"
            className={styles.searchNow}
            onClick={() => void searchNow()}
            disabled={searching}
            aria-label="Search now"
          >
            {searching ? (
              <>
                <span className={styles.searchNowSpinner} />
                Searching…
              </>
            ) : (
              <>⚡ Search now</>
            )}
          </button>
        </div>
      </section>

      {notice && <p className={styles.notice}>{notice}</p>}

      {/* ── Interactive Toolbar ────────────────────────────────────────── */}
      <div className={styles.toolbar}>
        <div className={styles.searchBar}>
          <span className={styles.searchIcon}>🔍</span>
          <input
            type="text"
            className={styles.searchInput}
            placeholder="Filter by title, author, narrator, or series…"
            aria-label="Filter audiobook wishlist"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              type="button"
              className={styles.searchClear}
              onClick={() => setQuery('')}
              title="Clear filter"
              aria-label="Clear filter"
            >
              ✕
            </button>
          )}
        </div>

        <div className={styles.controlsRow}>
          {counts && counts.total > 0 && (
            <nav className={styles.filters} aria-label="Filter by status">
              {FILTERS.map((entry) => {
                const total = entry.key === 'all' ? counts.total : (counts[entry.key] ?? 0);
                if (entry.key !== 'all' && total === 0) return null;
                const isSelected = filter === entry.key;
                return (
                  <button
                    key={entry.key}
                    type="button"
                    className={`${styles.filter} ${isSelected ? styles.filterOn : ''}`}
                    onClick={() => setFilter(entry.key)}
                  >
                    <span className={`${styles.filterDot} ${entry.dotClass || ''}`} />
                    {entry.label}
                    <span className={styles.filterCount}>{total}</span>
                  </button>
                );
              })}
            </nav>
          )}

          {/* View Mode Switcher */}
          <div className={styles.viewSwitch} role="group" aria-label="View mode">
            <button
              type="button"
              className={`${styles.viewBtn} ${viewMode === 'grid' ? styles.viewBtnActive : ''}`}
              onClick={() => handleSetViewMode('grid')}
              title="Poster Gallery View"
            >
              ⊞ Grid
            </button>
            <button
              type="button"
              className={`${styles.viewBtn} ${viewMode === 'shelf' ? styles.viewBtnActive : ''}`}
              onClick={() => handleSetViewMode('shelf')}
              title="Detailed Shelf View"
            >
              ☰ Shelf
            </button>
          </div>
        </div>
      </div>

      {/* ── Content Presentation ───────────────────────────────────────── */}
      {loading ? (
        <div className={styles.skeleton} aria-hidden="true" />
      ) : shown.length === 0 ? (
        <div className={styles.empty}>
          <div className={styles.emptyIcon}>🎧</div>
          <h3>
            {items.length === 0
              ? 'No audiobooks wanted yet'
              : query
                ? `No audiobooks matching "${query}"`
                : 'Nothing in this state'}
          </h3>
          <p>
            {items.length === 0
              ? 'Add an audiobook from the catalogue and SoulSync will continually look across your indexers.'
              : query
                ? 'Try adjusting your search query or clear the filter.'
                : 'Try selecting a different status filter above.'}
          </p>
          {items.length === 0 ? (
            <Link to="/audiobooks" className={styles.browseLink}>
              Browse audiobooks
            </Link>
          ) : query ? (
            <button
              type="button"
              className={styles.browseLink}
              onClick={() => setQuery('')}
            >
              Clear filter
            </button>
          ) : null}
        </div>
      ) : viewMode === 'shelf' ? (
        /* ── Bookshelf Detailed List View ─────────────────────────────── */
        <ul className={styles.shelfList}>
          {shown.map((item) => (
            <li className={styles.shelfRow} key={item.asin}>
              <div className={styles.shelfThumb}>
                <Link to="/audiobooks/$asin" params={{ asin: item.asin }}>
                  {item.cover_url ? (
                    <img className={styles.shelfCover} src={item.cover_url} alt="" loading="lazy" />
                  ) : (
                    <span className={styles.coverBlank} aria-hidden="true">
                      &#9835;
                    </span>
                  )}
                </Link>
              </div>

              <div className={styles.shelfInfo}>
                <Link to="/audiobooks/$asin" params={{ asin: item.asin }} className={styles.shelfTitle}>
                  {item.title}
                </Link>
                <div className={styles.shelfMeta}>
                  <span>{item.authors.join(', ')}</span>
                  {item.narrators.length > 0 && (
                    <button
                      type="button"
                      className={`${styles.narratorPill} ${
                        item.narrator_mode === 'exact' ? styles.narratorLocked : styles.narratorAny
                      }`}
                      onClick={() =>
                        void changeNarratorMode(
                          item.asin,
                          item.narrator_mode === 'exact' ? 'any' : 'exact',
                        )
                      }
                      title={
                        item.narrator_mode === 'exact'
                          ? `Narrator locked to ${item.narrators[0]} — click to accept any`
                          : 'Any narrator accepted — click to lock'
                      }
                    >
                      🎙️ {item.narrators[0]} {item.narrator_mode === 'exact' ? '🔒' : ''}
                    </button>
                  )}
                  {item.series_title && (
                    <span className={styles.series}>
                      <span className={styles.seriesBadge}>
                        {item.series_sequence ? `Book ${item.series_sequence} of ` : ''}
                      </span>
                      {item.series_title}
                    </span>
                  )}
                  <span className={styles.trail} title={item.last_error || undefined}>
                    {item.attempt_count === 0
                      ? 'Not looked for yet'
                      : `Looked ${item.attempt_count}\u00d7 \u00b7 last ${relativeTime(item.last_attempt_at)}`}
                    {item.last_error && <span className={styles.trailError}> · {item.last_error}</span>}
                  </span>
                </div>
              </div>

              <div className={styles.shelfDetails}>
                <div className={styles.shelfBadgeCol}>
                  <span className={`${styles.badge} ${getStatusBadgeClass(item.status)}`}>
                    <span className={styles.badgePulse} />
                    {item.status === 'grabbed'
                      ? {
                          downloading: 'Downloading',
                          queued: 'Queued',
                          paused: 'Paused',
                          unavailable: 'Waiting for client',
                          staged: 'Checking files',
                          importing: 'Importing',
                          cancelled: 'Cancelled',
                        }[item.download_status || ''] || 'Waiting for client'
                      : (STATUS_LABELS[item.status] ?? item.status)}
                  </span>
                </div>

                <div className={styles.shelfActions}>
                  <button
                    type="button"
                    className={`${styles.action} ${styles.actionPrimary}`}
                    title="Search indexers and grab now"
                    aria-label={`Search for ${item.title} now`}
                    disabled={searchingAsin === item.asin}
                    onClick={() => void searchBook(item.asin)}
                  >
                    {searchingAsin === item.asin ? 'Searching…' : 'Search'}
                  </button>
                  {(item.status === 'failed' || item.status === 'cancelled') && (
                    <button
                      type="button"
                      className={styles.action}
                      title="Look again on next pass"
                      onClick={() => void lookAgain(item.asin)}
                    >
                      Look again
                    </button>
                  )}
                  <button
                    type="button"
                    className={styles.action}
                    onClick={() => setReleasesFor(item)}
                  >
                    Find releases
                  </button>
                  <button
                    type="button"
                    className={`${styles.action} ${styles.actionDanger}`}
                    onClick={() => void remove(item.asin)}
                  >
                    Remove
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        /* ── Gallery Poster Grid View ─────────────────────────────────── */
        <ul className={styles.grid}>
          {shown.map((item) => (
            <li className={styles.card} key={item.asin}>
              <div className={styles.coverContainer}>
                <Link
                  to="/audiobooks/$asin"
                  params={{ asin: item.asin }}
                  className={styles.coverLink}
                  aria-label={item.title}
                >
                  <div className={styles.art}>
                    {item.cover_url ? (
                      <img className={styles.cover} src={item.cover_url} alt="" loading="lazy" />
                    ) : (
                      <span className={styles.coverBlank} aria-hidden="true">
                        &#9835;
                      </span>
                    )}
                    {/* 3D book spine gradient sheen */}
                    <span className={styles.spineSheen} aria-hidden="true" />
                    <span className={styles.scrim} aria-hidden="true" />
                    <span className={`${styles.badge} ${getStatusBadgeClass(item.status)}`}>
                      <span className={styles.badgePulse} />
                      {item.status === 'grabbed'
                        ? {
                            downloading: 'Downloading',
                            queued: 'Queued',
                            paused: 'Paused',
                            unavailable: 'Waiting for client',
                            staged: 'Checking files',
                            importing: 'Importing',
                            cancelled: 'Cancelled',
                          }[item.download_status || ''] || 'Waiting for client'
                        : (STATUS_LABELS[item.status] ?? item.status)}
                    </span>
                  </div>
                </Link>
                <button
                  type="button"
                  className={styles.quickRemove}
                  title="Remove from wishlist"
                  aria-label={`Remove ${item.title} from wishlist`}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    void remove(item.asin);
                  }}
                >
                  ✕
                </button>
              </div>

              <div className={styles.info}>
                <Link to="/audiobooks/$asin" params={{ asin: item.asin }} className={styles.title}>
                  {item.title}
                </Link>
                <span className={styles.byline}>{item.authors.join(', ')}</span>

                {item.narrators.length > 0 && (
                  <button
                    type="button"
                    className={`${styles.narratorPill} ${
                      item.narrator_mode === 'exact' ? styles.narratorLocked : styles.narratorAny
                    }`}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      void changeNarratorMode(
                        item.asin,
                        item.narrator_mode === 'exact' ? 'any' : 'exact',
                      );
                    }}
                    title={
                      item.narrator_mode === 'exact'
                        ? `Narrator locked to ${item.narrators[0]} — click to accept any narrator`
                        : 'Any narrator accepted — click to lock'
                    }
                  >
                    🎙️ {item.narrators[0]} {item.narrator_mode === 'exact' ? '🔒' : ''}
                  </button>
                )}

                {item.series_title && (
                  <span className={styles.series}>
                    <span className={styles.seriesBadge}>
                      {item.series_sequence ? `Book ${item.series_sequence} of ` : ''}
                    </span>
                    {item.series_title}
                  </span>
                )}

                <span className={styles.trail} title={item.last_error || undefined}>
                  {item.attempt_count === 0
                    ? 'Not looked for yet'
                    : `Looked ${item.attempt_count}\u00d7 \u00b7 last ${relativeTime(item.last_attempt_at)}`}
                  {item.last_error && <span className={styles.trailError}> · {item.last_error}</span>}
                </span>
              </div>

              <div className={styles.actions} onClick={(e) => e.stopPropagation()}>
                <button
                  type="button"
                  className={`${styles.action} ${styles.actionPrimary}`}
                  title="Search indexers and grab now"
                  aria-label={`Search for ${item.title} now`}
                  disabled={searchingAsin === item.asin}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    void searchBook(item.asin);
                  }}
                >
                  {searchingAsin === item.asin ? 'Searching…' : 'Search'}
                </button>
                {item.narrators.length > 0 && (
                  <button
                    type="button"
                    className={styles.action}
                    title={
                      item.narrator_mode === 'exact'
                        ? `Only ${item.narrators[0]}'s reading will do \u2014 click to accept any narrator`
                        : 'Any narrator will do \u2014 click to hold out for the one you picked'
                    }
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      void changeNarratorMode(
                        item.asin,
                        item.narrator_mode === 'exact' ? 'any' : 'exact',
                      );
                    }}
                  >
                    {item.narrator_mode === 'exact' ? 'Narrator locked' : 'Any narrator'}
                  </button>
                )}
                {(item.status === 'failed' || item.status === 'cancelled') && (
                  <button
                    type="button"
                    className={styles.action}
                    title={
                      item.status === 'cancelled'
                        ? 'Cancelled downloads are never retried on their own \u2014 want it again'
                        : 'Skip the wait and look on the next pass'
                    }
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      void lookAgain(item.asin);
                    }}
                  >
                    Look again
                  </button>
                )}
                <button
                  type="button"
                  className={styles.action}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setReleasesFor(item);
                  }}
                >
                  Find releases
                </button>
                <button
                  type="button"
                  className={`${styles.action} ${styles.actionDanger}`}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    void remove(item.asin);
                  }}
                >
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {releasesFor && (
        <AudiobookReleasesModal
          asin={releasesFor.asin}
          title={releasesFor.title}
          onClose={() => setReleasesFor(null)}
        />
      )}
    </div>
  );
}
