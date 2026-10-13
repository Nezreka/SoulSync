import { useEffect, useState } from 'react';

import { thumb } from '@/platform/artwork-thumb';

import { getAlbumVinylTheme } from '../../tools/-ui/album-cover-fallback';
import styles from './review-page.module.css';

export function ReviewArtwork({
  title,
  url,
  fallbackUrl,
  size,
}: {
  title: string;
  url?: string | null;
  fallbackUrl?: string | null;
  size: 'list' | 'header' | 'release';
}) {
  const [attempt, setAttempt] = useState(0);
  useEffect(() => setAttempt(0), [url, fallbackUrl]);
  const sources = [url, fallbackUrl].filter(
    (source, index, all): source is string => Boolean(source) && all.indexOf(source) === index,
  );
  const imageUrl = sources[attempt]
    ? thumb(sources[attempt], size === 'header' ? 'card' : 'rail')
    : null;
  const theme = getAlbumVinylTheme(title);
  return (
    <span className={styles.reviewArtwork} data-size={size} aria-hidden="true">
      {imageUrl ? (
        <img
          src={imageUrl}
          alt=""
          loading="lazy"
          onError={() => setAttempt((value) => value + 1)}
        />
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
