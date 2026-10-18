import { useState } from 'react';

import { thumb, type ThumbVariant } from '@/platform/artwork-thumb';

import styles from './artwork.module.css';
/** The image is keyed by URL so an error never leaks to a different album. */
export function Artwork({
  url,
  title,
  variant = 'rail',
  className = '',
  decorative = false,
}: {
  url?: string | null;
  title: string;
  variant?: ThumbVariant;
  className?: string;
  decorative?: boolean;
}) {
  return (
    <ArtworkImage
      key={url || ''}
      url={url}
      title={title}
      variant={variant}
      className={className}
      decorative={decorative}
    />
  );
}
function ArtworkImage({
  url,
  title,
  variant,
  className,
  decorative,
}: {
  url?: string | null;
  title: string;
  variant: ThumbVariant;
  className: string;
  decorative: boolean;
}) {
  const [broken, setBroken] = useState(false);
  return url && !broken ? (
    <img
      src={thumb(url, variant)}
      alt={decorative ? '' : title}
      loading="lazy"
      className={`${styles.artwork} ${className}`}
      onError={() => setBroken(true)}
    />
  ) : (
    <span
      className={`${styles.fallback} ${className}`}
      role={decorative ? undefined : 'img'}
      aria-label={decorative ? undefined : `No artwork for ${title}`}
      aria-hidden={decorative || undefined}
    >
      ♫
    </span>
  );
}
