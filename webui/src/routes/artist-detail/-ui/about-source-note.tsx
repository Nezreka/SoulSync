import type { ArtistAboutSource } from '../-artist-detail.types';

import { ABOUT_SOURCE_NAMES } from '../-artist-detail.hero-stats';

/**
 * the quiet line under the bio that says where it came from when it is not
 * last.fm's: "Bio from Wikipedia · Tags from MusicBrainz". wikipedia text is
 * cc by-sa, so its name links back to the article.
 */
export function AboutSourceNote({
  bioSource,
  bioUrl,
  tagsSource,
}: {
  bioSource?: ArtistAboutSource | null;
  bioUrl?: string | null;
  tagsSource?: ArtistAboutSource | null;
}) {
  const showBio = Boolean(bioSource && bioSource !== 'lastfm');
  const showTags = Boolean(tagsSource && tagsSource !== 'lastfm');
  if (!showBio && !showTags) return null;
  return (
    <p className="artist-about-source">
      {showBio && bioSource ? (
        <span>
          Bio from{' '}
          {bioUrl ? (
            <a href={bioUrl} target="_blank" rel="noopener noreferrer">
              {ABOUT_SOURCE_NAMES[bioSource]}
            </a>
          ) : (
            ABOUT_SOURCE_NAMES[bioSource]
          )}
        </span>
      ) : null}
      {showBio && showTags ? <span aria-hidden="true"> · </span> : null}
      {showTags && tagsSource ? <span>Tags from {ABOUT_SOURCE_NAMES[tagsSource]}</span> : null}
    </p>
  );
}
