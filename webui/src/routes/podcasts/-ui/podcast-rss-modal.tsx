import { useState, useEffect } from 'react';
import styles from './podcasts-page.module.css';

interface PodcastRssModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmitUrl: (url: string) => Promise<void> | void;
  isLoading?: boolean;
}

export function PodcastRssModal({
  isOpen,
  onClose,
  onSubmitUrl,
  isLoading,
}: PodcastRssModalProps) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (isOpen) {
      setUrl('');
      setError('');
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) {
      setError('Please enter an RSS feed URL');
      return;
    }
    if (!trimmed.startsWith('http://') && !trimmed.startsWith('https://')) {
      setError('URL must begin with http:// or https://');
      return;
    }
    setError('');
    onSubmitUrl(trimmed);
  };

  return (
    <div className={styles.modalBackdrop} onClick={onClose}>
      <div
        className={styles.modalDialog}
        style={{ maxWidth: 520 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <div className={styles.modalTitleGroup}>
            <div className={styles.modalIcon}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
              </svg>
            </div>
            <div>
              <h2 className={styles.modalTitle}>Add Podcast by RSS Feed</h2>
              <p className={styles.modalSubtitle}>
                Add custom, Patreon, Substack, or private member podcast feeds.
              </p>
            </div>
          </div>
          <button
            type="button"
            className={styles.modalCloseButton}
            onClick={onClose}
            aria-label="Close"
          >
            &times;
          </button>
        </div>

        <form onSubmit={handleSubmit} className={styles.modalBody}>
          <div className={styles.inputGroup}>
            <label className={styles.inputLabel}>RSS Feed URL</label>
            <input
              type="url"
              className={styles.modalInput}
              placeholder="https://feeds.patreon.com/private/12345 or https://example.com/rss"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                if (error) setError('');
              }}
              autoFocus
              required
              disabled={isLoading}
            />
            {error && <span className={styles.inputError}>{error}</span>}
            <span className={styles.inputHint}>
              Works with standard RSS 2.0 and iTunes-compliant podcast feeds.
            </span>
          </div>

          <div className={styles.modalActions}>
            <button
              type="button"
              className={styles.cancelButton}
              onClick={onClose}
              disabled={isLoading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className={styles.confirmButton}
              disabled={isLoading || !url.trim()}
            >
              {isLoading ? 'Loading Feed…' : 'Load Podcast'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
