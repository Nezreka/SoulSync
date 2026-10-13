import { useEffect, useState } from 'react';

import { thumb } from '@/platform/artwork-thumb';

import { getAlbumVinylTheme } from '../../tools/-ui/album-cover-fallback';
import styles from './review-page.module.css';

export function ReviewArtwork({
  title,
  url,
  size,
}: {
  title: string;
  url?: string | null;
  size: 'list' | 'header' | 'release';
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  const imageUrl = url && !failed ? thumb(url, size === 'header' ? 'card' : 'rail') : null;
  const theme = getAlbumVinylTheme(title);
  return (
    <span className={styles.reviewArtwork} data-size={size} aria-hidden="true">
      {imageUrl ? (
        <img src={imageUrl} alt="" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <span
          className={styles.reviewArtworkFallback}
          style={{ background: theme.bg, color: theme.text }}
        >
          {title.trim().charAt(0).toUpperCase() || '♪'}
        </span>
      )}
    </span>
  );
}
