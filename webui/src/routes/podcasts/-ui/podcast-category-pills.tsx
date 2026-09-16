import { ALL_PODCAST_CATEGORIES, POPULAR_PILL_CATEGORIES } from './podcast-categories';
import styles from './podcasts-page.module.css';

interface PodcastCategoryPillsProps {
  selectedCategory: string;
  onSelectCategory: (category: string) => void;
  onOpenCategoryModal: () => void;
}

export function PodcastCategoryPills({
  selectedCategory,
  onSelectCategory,
  onOpenCategoryModal,
}: PodcastCategoryPillsProps) {
  // Check if active category is outside the standard popular pills
  const isInPopular = POPULAR_PILL_CATEGORIES.some(
    (c) => c.id.toLowerCase() === selectedCategory.toLowerCase(),
  );
  const activeCustomDef = !isInPopular
    ? ALL_PODCAST_CATEGORIES.find((c) => c.id.toLowerCase() === selectedCategory.toLowerCase())
    : null;

  return (
    <div className={styles.categoryPills} role="tablist" aria-label="Podcast categories">
      {POPULAR_PILL_CATEGORIES.map((cat) => {
        const isActive = selectedCategory.toLowerCase() === cat.id.toLowerCase();
        return (
          <button
            key={cat.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            className={`${styles.categoryPill} ${isActive ? styles.categoryPillActive : ''}`}
            onClick={() => onSelectCategory(cat.id)}
          >
            <span style={{ marginRight: 4 }}>{cat.icon}</span>
            <span>{cat.label}</span>
          </button>
        );
      })}

      {/* If an extended category is active, show it as a highlighted pill */}
      {activeCustomDef && (
        <button
          type="button"
          role="tab"
          aria-selected={true}
          className={`${styles.categoryPill} ${styles.categoryPillActive}`}
          onClick={() => onSelectCategory(activeCustomDef.id)}
        >
          <span style={{ marginRight: 4 }}>{activeCustomDef.icon}</span>
          <span>{activeCustomDef.label}</span>
        </button>
      )}

      {/* Button to open Category Explorer Modal */}
      <button
        type="button"
        className={`${styles.categoryPill} ${styles.allCategoriesBtn}`}
        onClick={onOpenCategoryModal}
        title="Explore all 20 podcast categories"
        aria-label="All Categories (20)"
      >
        <span style={{ marginRight: 4 }}>🗂️</span>
        <span>All Categories (20)</span>
      </button>
    </div>
  );
}
