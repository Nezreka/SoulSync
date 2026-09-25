import { useEffect, useState } from 'react';

import { fetchFindingAlbums, type FindingAlbumGroup } from '../-tools.api';

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
  }, [groupBy, jobId, status, findingType, q]);

  if (groups === null) {
    return <div className="repair-album-grid-empty">Grouping findings…</div>;
  }

  if (!groups.length) {
    return (
      <div className="repair-album-grid-empty">
        Nothing to group here. This view needs findings that recorded an album and
        artist, which today means the quality jobs.
      </div>
    );
  }

  return (
    <div className="repair-album-grid" role="list">
      {groups.map((group) => {
        const art = artFor(group);
        const name = group.group_by === 'artist' ? group.artist : group.album;
        return (
          <button
            type="button"
            role="listitem"
            className="repair-album-card"
            key={group.key}
            onClick={() => onOpen(group)}
            title={`Show the ${trackLabel(group.count)} flagged in ${name || 'this group'}`}
          >
            <div className="repair-album-art">
              {art ? (
                <img src={art} alt="" loading="lazy" />
              ) : (
                <span className="repair-album-art-fallback">{initial(group)}</span>
              )}
              <span className="repair-album-count">{group.count}</span>
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
              <div className="repair-album-quality">{qualityRange(group)}</div>
              <div className="repair-album-tracks">{trackLabel(group.count)}</div>
            </div>
          </button>
        );
      })}
    </div>
  );
}
