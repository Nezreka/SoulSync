import { Artwork } from '@/components/artwork/artwork';

import styles from './review-page.module.css';
export function ReviewArtwork({
  title,
  url,
  size,
}: {
  title: string;
  url?: string | null;
  fallbackUrl?: string | null;
  size: 'list' | 'header' | 'release';
}) {
  return (
    <span className={styles.reviewArtwork} data-size={size} aria-hidden="true">
      <Artwork title={title} url={url} variant={size === 'header' ? 'card' : 'rail'} decorative />
    </span>
  );
}
