import { useEffect, useRef, useState } from 'react';

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
  const [loaded, setLoaded] = useState(false);
  const imageRef = useRef<HTMLImageElement>(null);
  useEffect(() => {
    if (imageRef.current?.complete && imageRef.current.naturalWidth > 0) setLoaded(true);
  }, []);
  return url && !broken ? (
    <span className={styles.frame} data-loading={!loaded || undefined}>
      {!loaded && (
        <span className={styles.placeholder} aria-hidden="true">
          ♫
        </span>
      )}
      <img
        ref={imageRef}
        src={thumb(url, variant)}
        alt={decorative ? '' : title}
        loading="lazy"
        className={`${styles.artwork} ${loaded ? 'is-loaded' : 'is-loading'} ${className}`}
        data-loading={!loaded || undefined}
        onLoad={() => setLoaded(true)}
        onError={() => setBroken(true)}
      />
    </span>
  ) : (
    <span
      className={`${styles.fallback} ${className}`}
      title={broken ? 'Artwork could not be loaded' : 'No artwork available'}
      role={decorative ? undefined : 'img'}
      aria-label={decorative ? undefined : `No artwork for ${title}`}
      aria-hidden={decorative || undefined}
    >
      ♫
    </span>
  );
}
