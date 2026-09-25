import { useState } from 'react';

/**
 * A wishlist cover/photo that survives a cold Library-v2 artwork build.
 *
 * `src` is the local artwork endpoint (`/api/library/v2/artwork/...`), which is
 * the long-term truth — a manual cover pick, an embedded cover, a library with
 * no internet — and is served straight off disk once cached. While a cold entity
 * is still being built the endpoint answers 404, and an `<img>` cannot read the
 * `X-Artwork-Pending` header that explains why, so `fallback` (the provider CDN
 * url) is painted for that one render instead of an empty tile. The next visit
 * picks the local copy up.
 *
 * Deliberately NOT the Library v2 page's `Artwork` component: that one lives in
 * `library-v2-page.tsx`, and importing it here would pull the entire library
 * page into the wishlist bundle for a 20-line behaviour. It also subscribes to
 * the pending-artwork channel to re-render the moment a build finishes — worth
 * it on a page whose whole subject is artwork, not on a wishlist row.
 */
export function WishlistCover({
  src,
  fallback,
  className,
  placeholder,
  alt = '',
}: {
  src: string;
  fallback?: string;
  className?: string;
  /** Rendered when nothing loads. Omit to render nothing at all. */
  placeholder?: React.ReactNode;
  alt?: string;
}) {
  // Keyed to the src it belongs to and reset during render, not in an effect:
  // an effect fires after the render has already committed, so a list refetch
  // landing a new url would paint one frame with the previous url's failure
  // state still applied.
  const [state, setState] = useState({ src, failed: false, fallbackFailed: false });
  if (state.src !== src) setState({ src, failed: false, fallbackFailed: false });
  const failed = state.src === src && state.failed;
  const fallbackFailed = state.src === src && state.fallbackFailed;

  const usingFallback = failed && !fallbackFailed && Boolean(fallback) && fallback !== src;
  const shown = usingFallback ? (fallback as string) : src;

  if (!shown || (failed && !usingFallback)) {
    return placeholder ?? null;
  }
  return (
    <img
      className={className}
      src={shown}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() =>
        setState((current) =>
          current.src === src
            ? { ...current, [usingFallback ? 'fallbackFailed' : 'failed']: true }
            : current,
        )
      }
    />
  );
}
