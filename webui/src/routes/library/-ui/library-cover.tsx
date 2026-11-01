import { useEffect, useState, type ReactNode } from 'react';

import { parseArtworkTarget, watchPendingArtwork } from './artwork-pending';

/**
 * a cover from soulsync's artwork endpoint. a cold one 404s while the server
 * builds it, so this paints the provider's cover meanwhile and waits on the
 * build (one batched poll for the page) instead of guessing with retries.
 */
export function LibraryCover({
  src,
  remote,
  alt = '',
  className,
  fallback,
}: {
  src: string;
  remote?: string | null;
  alt?: string;
  className?: string;
  fallback: ReactNode;
}) {
  const [state, setState] = useState({ src, failed: false, remoteFailed: false, version: 0 });
  if (state.src !== src) setState({ src, failed: false, remoteFailed: false, version: 0 });
  const { failed, remoteFailed, version } = state;
  const standIn = Boolean(remote) && !remoteFailed;

  // a painted stand-in stays for this view; the built cover is the next load's
  useEffect(() => {
    if (!failed || standIn) return;
    const target = parseArtworkTarget(src);
    if (!target) return;
    return watchPendingArtwork(target.kind, target.id, (ready) => {
      if (ready == null) return;
      setState((current) =>
        current.src === src ? { ...current, failed: false, version: ready } : current,
      );
    });
  }, [failed, standIn, src]);

  const local = version ? `${src}${src.includes('?') ? '&' : '?'}v=${version}` : src;
  const usingRemote = failed && standIn;
  const shown = usingRemote ? (remote as string) : failed ? '' : local;
  if (!shown) return <>{fallback}</>;
  return (
    <img
      className={className}
      src={shown}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() =>
        setState((current) =>
          current.src !== src
            ? current
            : usingRemote
              ? { ...current, remoteFailed: true }
              : { ...current, failed: true },
        )
      }
    />
  );
}
