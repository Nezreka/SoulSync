import styles from './podcasts-page.module.css';

interface PodcastSearchBarProps {
  value: string;
  onChange: (val: string) => void;
  onClear: () => void;
  isSearching?: boolean;
  downloadsCount?: number;
  onOpenDownloads?: () => void;
}

export function PodcastSearchBar({
  value,
  onChange,
  onClear,
  isSearching,
  downloadsCount = 0,
  onOpenDownloads,
}: PodcastSearchBarProps) {
  return (
    <div className={styles.heroHeader}>
      <div className={styles.heroHeaderTitleRow}>
        <div className={styles.pageTitleGroup}>
          <h1 className={styles.pageTitle}>
            <span className={styles.pageTitleIcon}>
              <svg
                width="28"
                height="28"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="2" />
                <path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14" />
              </svg>
            </span>
            Podcasts
          </h1>
          <p className={styles.pageSubtitle}>Discover, search, and download podcast episodes</p>
        </div>

        {downloadsCount > 0 && onOpenDownloads && (
          <button
            type="button"
            className={styles.downloadsBadge}
            onClick={onOpenDownloads}
            title="View downloaded podcast episodes"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
            <span>{downloadsCount} Downloads</span>
          </button>
        )}
      </div>

      <div className={styles.searchBarWrapper}>
        <span className={styles.searchIcon}>
          {isSearching ? (
            <div className={styles.spinner} style={{ width: 16, height: 16, borderWidth: 2 }} />
          ) : (
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
          )}
        </span>

        <input
          type="text"
          className={styles.searchInput}
          placeholder="Search podcasts by title, topic, or host (e.g. Huberman, NPR, Tech)..."
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />

        {value && (
          <button
            type="button"
            className={styles.searchClearBtn}
            onClick={onClear}
            title="Clear search"
            aria-label="Clear search"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
