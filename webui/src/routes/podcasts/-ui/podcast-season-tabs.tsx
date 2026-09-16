import styles from './podcasts-page.module.css';

interface PodcastSeasonTabsProps {
  seasons: number[];
  selectedSeason: number | null; // null = all
  onSelectSeason: (season: number | null) => void;
  sortOrder: 'newest' | 'oldest';
  onToggleSort: () => void;
  searchFilter: string;
  onSearchFilterChange: (text: string) => void;
  totalEpisodes: number;
  filteredCount: number;
}

export function PodcastSeasonTabs({
  seasons,
  selectedSeason,
  onSelectSeason,
  sortOrder,
  onToggleSort,
  searchFilter,
  onSearchFilterChange,
  totalEpisodes,
  filteredCount,
}: PodcastSeasonTabsProps) {
  return (
    <div className={styles.episodeFilterBar}>
      <div className={styles.seasonTabs}>
        <button
          type="button"
          className={`${styles.seasonTab} ${selectedSeason === null ? styles.seasonTabActive : ''}`}
          onClick={() => onSelectSeason(null)}
        >
          All Episodes ({totalEpisodes})
        </button>

        {seasons.map((s) => {
          const label = s >= 1900 ? String(s) : `Season ${s}`;
          return (
            <button
              key={s}
              type="button"
              className={`${styles.seasonTab} ${selectedSeason === s ? styles.seasonTabActive : ''}`}
              onClick={() => onSelectSeason(s)}
            >
              {label}
            </button>
          );
        })}
      </div>

      <div className={styles.episodeControlsRight}>
        <input
          type="text"
          className={styles.episodeSearchInput}
          placeholder="Filter episodes…"
          value={searchFilter}
          onChange={(e) => onSearchFilterChange(e.target.value)}
        />

        <button
          type="button"
          className={styles.sortBtn}
          onClick={onToggleSort}
          title={`Sorting ${sortOrder === 'newest' ? 'newest first' : 'oldest first'}. Click to toggle.`}
        >
          <span>{sortOrder === 'newest' ? '↓ Newest' : '↑ Oldest'}</span>
        </button>

        {searchFilter && (
          <span style={{ fontSize: 12, color: 'rgba(255, 255, 255, 0.5)' }}>
            ({filteredCount} matched)
          </span>
        )}
      </div>
    </div>
  );
}
