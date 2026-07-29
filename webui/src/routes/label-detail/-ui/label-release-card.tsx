import { useEffect, useRef } from 'react';

import type { LabelRelease } from '../-label-detail.types';

import { coverUrl, ownershipOverlay, releaseKey } from '../-label-detail.helpers';

/**
 * One release, as the SAME .release-card/.album-card the artist-detail
 * discography uses — that shared markup is why this page looks like the rest of
 * the app rather than a bespoke grid.
 *
 * The cover is not an <img>: it is a background image on .album-card-image,
 * painted only once the lazy loader has PROVED the url loads. A plain <img
 * src> would show a broken-image glyph for every release the external lookup
 * cannot resolve, which on a deep catalog is a lot of them.
 */
export function LabelReleaseCard({
  release,
  owned,
  checked,
  resolvedCover,
  onVisible,
  onOpen,
  onOpenArtist,
}: {
  release: LabelRelease;
  owned: ReadonlySet<string>;
  checked: ReadonlySet<string>;
  resolvedCover: string;
  onVisible: (key: string, url: string, element: Element) => () => void;
  onOpen: (release: LabelRelease) => void;
  onOpenArtist: (release: LabelRelease) => void;
}) {
  const key = releaseKey(release);
  const endpoint = coverUrl(release);
  const overlay = ownershipOverlay(key, owned, checked);
  const imageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Nothing to watch for once it is painted, and nothing to look up when the
    // release has neither an id nor a name.
    if (resolvedCover || !endpoint) return;
    const element = imageRef.current;
    if (!element) return;
    return onVisible(key, endpoint, element);
  }, [key, endpoint, resolvedCover, onVisible]);

  return (
    <div
      className="release-card album-card"
      data-key={key}
      data-album-type={release.primary_type || 'album'}
      onClick={() => onOpen(release)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onOpen(release);
        }
      }}
    >
      <div
        className="album-card-image"
        ref={imageRef}
        style={resolvedCover ? { backgroundImage: `url("${resolvedCover}")` } : undefined}
      >
        {overlay ? (
          <div className={`completion-overlay ${overlay === 'owned' ? 'completed' : 'missing'}`}>
            <span className="completion-status">{overlay === 'owned' ? '✓ Owned' : 'Missing'}</span>
          </div>
        ) : null}
        {release.artist_id ? (
          <button
            className="label-card-artist-btn"
            title="Go to artist"
            data-role="artist"
            type="button"
            onClick={(event) => {
              // Without this the card's own handler also fires and the
              // download modal opens behind the artist page.
              event.stopPropagation();
              onOpenArtist(release);
            }}
          >
            👤
          </button>
        ) : null}
      </div>
      <div className="album-card-content">
        <div className="album-card-name">{release.album}</div>
        <div className="album-card-year">
          <span className="lc-artist">{release.artist}</span>
          {release.year ? ` · ${release.year}` : ''}
        </div>
      </div>
    </div>
  );
}
