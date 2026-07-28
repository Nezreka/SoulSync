import type { DiscographyBucket, DiscographyRelease } from '../-artist-detail.types';

import {
  type DiscographyFilterState,
  isReleaseHidden,
  isSectionHidden,
  releaseFlags,
  sectionCounts,
  sectionStatsLabels,
} from '../-artist-detail.filters';
import { ReleaseCard } from './release-card';

const HEADINGS: Record<DiscographyBucket, string> = {
  albums: 'Albums',
  eps: 'EPs',
  singles: 'Singles',
};

interface Props {
  bucket: DiscographyBucket;
  releases: DiscographyRelease[];
  filters: DiscographyFilterState;
  isMusicBrainz: boolean;
  isSourceArtist: boolean;
  onOpen: (release: DiscographyRelease) => void;
}

/**
 * One discography bucket. Mirrors the #<bucket>-section markup in index.html
 * and the behaviour applyDiscographyFilters gave it.
 *
 * Hidden cards are NOT rendered here, where the vanilla rendered them and set
 * `display: none`. The observable result is the same and the DOM is smaller;
 * the one thing it changes is that a hidden card's image never gets requested,
 * which is a strict improvement for a 200-release MusicBrainz discography.
 */
export function DiscographySection({
  bucket,
  releases,
  filters,
  isMusicBrainz,
  isSourceArtist,
  onOpen,
}: Props) {
  const counts = sectionCounts(releases, isMusicBrainz, filters);
  if (isSectionHidden(bucket, counts, filters)) return null;

  const labels = sectionStatsLabels(counts);
  const visible = releases.filter(
    (release) => !isReleaseHidden(release, releaseFlags(release, isMusicBrainz), filters),
  );

  return (
    <div className="discography-section" id={`${bucket}-section`}>
      <div className="section-header">
        <h3>{HEADINGS[bucket]}</h3>
        <div className="section-stats">
          <span id={`${bucket}-owned-count`}>{labels.owned}</span>
          <span id={`${bucket}-missing-count`}>{labels.missing}</span>
        </div>
      </div>
      <div className="releases-grid" id={`${bucket}-grid`}>
        {visible.map((release, index) => (
          <ReleaseCard
            // The index is ALWAYS part of the key, not merely a fallback for a
            // missing id: MusicBrainz can list the same release-group id twice
            // inside ONE bucket, and `id ?? index` still collides there —
            // React then warns and may reuse the wrong DOM node. Cards are
            // stateless, so an index-bearing key costs nothing.
            key={`${bucket}-${index}-${release.id ?? ''}`}
            release={release}
            isMusicBrainz={isMusicBrainz}
            isSourceArtist={isSourceArtist}
            onOpen={onOpen}
          />
        ))}
      </div>
    </div>
  );
}
