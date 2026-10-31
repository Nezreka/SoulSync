/**
 * The library's two list icons: the Wishlist (a heart with a sparkle, for an
 * album or a track you want) and the Watchlist (an eye with a play button,
 * for an artist you follow). Library v2 drew both as one bookmark and called
 * both "monitoring"; dev's words were the two lists, and these say which.
 *
 * Line icons in currentColor at any size. `on` fills them: the heart, and the
 * eye's iris with the play button cut out of it.
 */

export function WishlistIcon({ on = false, size }: { on?: boolean; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      data-list-icon="wishlist"
    >
      {on ? (
        <path
          d="M10.5 19.5S3 15 3 9.8A4.3 4.3 0 0 1 10.5 7a4.3 4.3 0 0 1 7.5 2.8c0 5.2-7.5 9.7-7.5 9.7z"
          fill="currentColor"
        />
      ) : (
        <>
          {/* open on the right, where the sparkle sits */}
          <path d="M17.6 8.3A4.3 4.3 0 0 0 10.5 7 4.3 4.3 0 0 0 3 9.8c0 5.2 7.5 9.7 7.5 9.7s3.9-2.3 6.1-5.7" />
          <path d="M5.9 9.6a2 2 0 0 1 1.5-1.6" strokeWidth={1.4} />
        </>
      )}
      <path
        d="M20.2 3.4l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"
        fill="currentColor"
        strokeWidth={1.1}
      />
    </svg>
  );
}

export function WatchlistIcon({ on = false, size }: { on?: boolean; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      data-list-icon="watchlist"
    >
      <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z" />
      {on ? (
        <>
          <mask id="watchlist-icon-play">
            <rect width="24" height="24" fill="white" />
            <path d="M10.9 10v4l3.2-2z" fill="black" stroke="black" strokeWidth={0.8} />
          </mask>
          <circle cx="12" cy="12" r="3.9" fill="currentColor" mask="url(#watchlist-icon-play)" />
        </>
      ) : (
        <>
          <circle cx="12" cy="12" r="3.9" />
          <path d="M10.9 10v4l3.2-2z" fill="currentColor" strokeWidth={1} />
        </>
      )}
    </svg>
  );
}
