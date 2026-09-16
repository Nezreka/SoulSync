import { useEffect, useMemo, useState } from 'react';

import { ALL_PODCAST_CATEGORIES } from './podcast-categories';
import styles from './podcasts-page.module.css';

interface PodcastCategoryModalProps {
  selectedCategory: string;
  onSelectCategory: (categoryId: string) => void;
  onClose: () => void;
}

export function PodcastCategoryModal({
  selectedCategory,
  onSelectCategory,
  onClose,
}: PodcastCategoryModalProps) {
  const [filterQuery, setFilterQuery] = useState('');

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Filter categories by query
  const filteredCategories = useMemo(() => {
    const q = filterQuery.trim().toLowerCase();
    if (!q) return ALL_PODCAST_CATEGORIES;
    return ALL_PODCAST_CATEGORIES.filter(
      (c) =>
        c.label.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q) ||
        c.id.toLowerCase().includes(q),
    );
  }, [filterQuery]);

  return (
    <div
      className={styles.categoryModalOverlay}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Explore All Categories"
    >
      <div className={styles.categoryModalDialog} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className={styles.categoryModalHeader}>
          <div className={styles.categoryModalTitleRow}>
            <div>
              <h2 className={styles.categoryModalTitle}>Explore All Categories</h2>
              <p className={styles.categoryModalSubtitle}>
                Discover curated podcasts, top charts, and featured shows by genre
              </p>
            </div>
            <button
              type="button"
              className={styles.showNotesCloseBtn}
              onClick={onClose}
              aria-label="Close categories explorer"
            >
              ✕
            </button>
          </div>

          {/* Search input for categories */}
          <div className={styles.categorySearchWrapper}>
            <svg
              className={styles.categorySearchIcon}
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              className={styles.categorySearchInput}
              placeholder="Search 20 categories (e.g. sports, health, gaming, film)..."
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              autoFocus
            />
            {filterQuery && (
              <button
                type="button"
                className={styles.categorySearchClear}
                onClick={() => setFilterQuery('')}
                aria-label="Clear filter"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* Categories Grid */}
        <div className={styles.categoryGridContainer}>
          <div className={styles.categoryGrid}>
            {filteredCategories.map((cat) => {
              const isSelected = selectedCategory.toLowerCase() === cat.id.toLowerCase();
              return (
                <div
                  key={cat.id}
                  className={`${styles.categoryCard} ${isSelected ? styles.categoryCardSelected : ''}`}
                  onClick={() => {
                    onSelectCategory(cat.id);
                    onClose();
                  }}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onSelectCategory(cat.id);
                      onClose();
                    }
                  }}
                >
                  <div
                    className={styles.categoryCardGlow}
                    style={{ background: cat.gradient }}
                    aria-hidden="true"
                  />
                  <div className={styles.categoryCardTop}>
                    <span className={styles.categoryCardIcon}>{cat.icon}</span>
                    {isSelected && <span className={styles.categorySelectedTag}>Active</span>}
                  </div>
                  <h3 className={styles.categoryCardName}>{cat.label}</h3>
                  <p className={styles.categoryCardDesc}>{cat.description}</p>
                </div>
              );
            })}
          </div>

          {filteredCategories.length === 0 && (
            <div className={styles.emptyContainer} style={{ padding: '40px 0' }}>
              <p>No categories match "{filterQuery}".</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
