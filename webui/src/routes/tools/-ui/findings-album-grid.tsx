import { useEffect, useState } from 'react';

import { fetchFindingAlbums, type FindingAlbumGroup } from '../-tools.api';
import { VinylCoverFallback } from './album-cover-fallback';

/**
 * The upgrade backlog as albums or artists, not as forty thousand rows.
 *
 * Nobody decides one track at a time whether to re-acquire it. The decision is
 * "re-rip this album properly" or "everything by them is a bad rip", so that is
 * the unit this view offers. Reported by Lil-Uzi-Chimp (Aug 26 2026): "An
 * 'album' or 'artist' view would also be nice if I would like to fix an album."
 *
 * Worst audio leads. The album carrying the lowest-quality file is the one most
 * worth fixing, and ties break on how many tracks are affected, so a twelve
 * track 128kbps album outranks one stray.
 *
 * The artwork rides on the finding itself (the scanner stores album_thumb_url
 * and artist_thumb_url at scan time), so a hundred cards cost one request, not
 * a hundred lookups.
 */

export interface FindingsAlbumGridProps {
  groupBy: 'album' | 'artist';
  jobId?: string;
  status?: string;
  findingType?: string;
  q?: string;
  /** Drill into one group — the surface switches back to the flat list, filtered. */
  onOpen: (group: FindingAlbumGroup) => void;
  selectedGroupKey?: string | null;
  /** bumps when a job finishes, so the grid picks up what it found (#1386) */
  refreshToken?: number;
}

/** Album art first, artist as the fallback, then a letter tile. Never a broken
 *  image: a grid of missing-image icons reads as a broken page. */
function artFor(group: FindingAlbumGroup): string | null {
  if (group.group_by === 'artist') return group.artist_thumb_url || null;
  return group.album_thumb_url || group.artist_thumb_url || null;
}

function initial(group: FindingAlbumGroup): string {
  const source = group.group_by === 'artist' ? group.artist : group.album;
  return (source || '?').trim().charAt(0).toUpperCase() || '?';
}

function findingTagLabel(type: string): string {
  switch (type) {
    case 'corrupt_audio':
      return 'Corrupt';
    case 'short_preview_track':
      return 'Preview';
    case 'quality_upgrade':
      return 'Low Quality';
    case 'fake_lossless':
      return 'Fake FLAC';
    case 'dead_file':
      return 'Dead File';
    case 'missing_discography_track':
      return 'Missing';
    case 'missing_lyrics':
      return 'Missing Lyrics';
    default:
      return type.replace(/_/g, ' ');
  }
}

/** "MP3 128kbps" alone when everything matches, "MP3 128kbps → MP3 320kbps"
 *  when the album is mixed. Saying "128 to 128" would be noise. */
export function qualityRange(group: FindingAlbumGroup): string {
  const worst = group.worst_quality || '';
  const best = group.best_quality || '';
  if (!worst && !best) return '';
  if (!best || worst === best) return worst || best;
  return `${worst} → ${best}`;
}

export function trackLabel(count: number): string {
  return count === 1 ? '1 track' : `${count} tracks`;
}

export function FindingsAlbumGrid({
  groupBy,
  jobId,
  status,
  findingType,
  q,
  onOpen,
  selectedGroupKey,
  refreshToken = 0,
}: FindingsAlbumGridProps) {
  const [groups, setGroups] = useState<FindingAlbumGroup[] | null>(null);

  useEffect(() => {
    let live = true;
    setGroups(null);
    void fetchFindingAlbums({ groupBy, jobId, status, findingType, q }).then((rows) => {
      if (live) setGroups(rows);
    });
    return () => {
      live = false;
    };
  }, [groupBy, jobId, status, findingType, q, refreshToken]);

  if (groups === null) {
    return <div className="repair-album-grid-empty">Grouping findings…</div>;
  }

  if (!groups.length) {
    return (
      <div className="repair-album-grid-empty">
        Nothing to group here. This view needs findings that recorded an album and artist, which
        today means the quality jobs.
      </div>
    );
  }

  return (
    <div className="repair-album-grid" role="list">
      {groups.map((group) => {
        const art = artFor(group);
        const name = group.group_by === 'artist' ? group.artist : group.album;
        const isSelected = selectedGroupKey === group.key;

        return (
          <button
            type="button"
            role="listitem"
            className={`repair-album-card ${isSelected ? 'active' : ''}`}
            key={group.key}
            onClick={() => onOpen(group)}
            title={`Show the ${trackLabel(group.count)} flagged in ${name || 'this group'}`}
          >
            <div className="repair-album-art">
              {art ? (
                <img src={art} alt="" loading="lazy" />
              ) : (
                <VinylCoverFallback name={name || 'Unknown'} initialChar={initial(group)} />
              )}
              <span className="repair-album-count">{group.count}</span>
              {group.error_count && group.error_count > 0 ? (
                <span
                  className="repair-album-error-badge"
                  title={`${group.error_count} fix attempts failed`}
                >
                  ⚠️ {group.error_count}
                </span>
              ) : null}
            </div>
            <div className="repair-album-meta">
              <div className="repair-album-name" title={name || ''}>
                {name || 'Unknown'}
              </div>
              {group.group_by === 'album' && group.artist ? (
                <div className="repair-album-artist" title={group.artist}>
                  {group.artist}
                </div>
              ) : null}
              {qualityRange(group) ? (
                <div className="repair-album-quality">{qualityRange(group)}</div>
              ) : null}
              <div className="repair-album-tracks">{trackLabel(group.count)}</div>

              {group.finding_types && group.finding_types.length > 0 ? (
                <div className="repair-album-tags">
                  {group.finding_types.slice(0, 2).map((ft) => (
                    <span key={ft} className={`repair-album-tag ${ft}`}>
                      {findingTagLabel(ft)}
                    </span>
                  ))}
                  {group.finding_types.length > 2 ? (
                    <span className="repair-album-tag more">+{group.finding_types.length - 2}</span>
                  ) : null}
                </div>
              ) : null}
            </div>
          </button>
        );
      })}
    </div>
  );
}
