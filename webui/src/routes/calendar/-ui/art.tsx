import { useState } from 'react';

/** Artwork that steps aside when it can't load, so a missing still shows the
 * card's tinted backdrop rather than the browser's broken-image icon. */
export function Art({ src, lazy = true }: { src: string; lazy?: boolean }) {
  const [failed, setFailed] = useState<string | null>(null);
  if (failed === src) return null;
  return (
    <img
      src={src}
      alt=""
      loading={lazy ? 'lazy' : undefined}
      decoding="async"
      onError={() => setFailed(src)}
    />
  );
}
