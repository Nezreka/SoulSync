import { PageHeader } from '@/components/page-header';
import { Spinner } from '@/components/primitives';

import styles from './podcasts-page.module.css';

interface PodcastSearchBarProps {
  value: string;
  onChange: (val: string) => void;
  onClear: () => void;
  isSearching?: boolean;
  downloadsCount?: number;
  onOpenDownloads?: () => void;
  onOpenRssModal?: () => void;
  onOpenOpmlModal?: () => void;
  onSelectFeedUrl?: (url: string) => void;
}

export function PodcastSearchBar({
  value,
  onChange,
  onClear,
  isSearching,
  downloadsCount = 0,
  onOpenDownloads,
  onOpenRssModal,
  onOpenOpmlModal,
  onSelectFeedUrl,
}: PodcastSearchBarProps) {
  return (
    <>
      <PageHeader
        title="Podcasts"
        subtitle="Discover, search, and download podcast episodes"
        actions={
          <>
            {onOpenRssModal && (
              <button
                type="button"
                className="pgf-btn"
                onClick={onOpenRssModal}
                title="Add custom or private podcast RSS feed URL"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                  <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                </svg>
                <span>Add RSS</span>
              </button>
            )}

            {onOpenOpmlModal && (
              <button
                type="button"
                className="pgf-btn"
                onClick={onOpenOpmlModal}
                title="Import or export subscriptions via OPML"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                  <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                </svg>
                <span>OPML</span>
              </button>
            )}

            {downloadsCount > 0 && onOpenDownloads && (
              <button
                type="button"
                className="pgf-btn"
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
          </>
        }
      />

      <div className={styles.searchBarWrapper}>
        <span className={styles.searchIcon}>
          {isSearching ? (
            <Spinner size={16} tone="tinted" />
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
          placeholder="Search podcasts or paste RSS URL (e.g. Huberman, NPR, https://...)"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const trimmed = value.trim();
              if (
                (trimmed.startsWith('http://') || trimmed.startsWith('https://')) &&
                onSelectFeedUrl
              ) {
                e.preventDefault();
                onSelectFeedUrl(trimmed);
              }
            }
          }}
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
    </>
  );
}
