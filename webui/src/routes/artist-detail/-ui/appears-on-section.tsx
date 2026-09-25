import { useEffect, useState } from 'react';

import {
  type AppearsOnTrack,
  formatDuration,
  loadAppearsOn,
  queueRow,
  subLine,
} from '../-artist-detail.appears-on';

/**
 * features and collabs you own that are filed under another artist. "We Found
 * Love" lives under calvin harris, but it's a rihanna song too.
 *
 * renders nothing until there's something to show, so a library that hasn't
 * been through the spotify or deezer worker yet doesn't get an empty heading.
 */

const COLLAPSED = 8;

export interface AppearsOnSectionProps {
  artistId: string | number | undefined;
  artistName: string;
}

export function AppearsOnSection({ artistId, artistName }: AppearsOnSectionProps) {
  const [tracks, setTracks] = useState<AppearsOnTrack[]>([]);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setTracks([]);
    setExpanded(false);
    if (artistId === undefined || artistId === null || artistId === '') return;
    let live = true;
    void loadAppearsOn(artistId).then((rows) => {
      if (live) setTracks(rows);
    });
    return () => {
      live = false;
    };
  }, [artistId]);

  const playable = tracks.filter((t) => t.file_path);
  if (!playable.length) return null;

  const shown = expanded ? playable : playable.slice(0, COLLAPSED);

  const playFrom = (index: number) => {
    // the whole list as the queue, starting at the one clicked
    const queue = [...playable.slice(index), ...playable.slice(0, index)].map((t) =>
      queueRow(t, artistName),
    );
    void window.playTrackList?.(queue, `${artistName}, appears on`);
  };

  return (
    <section className="artist-appears-on-section" aria-label="Appears on">
      <div className="artist-appears-on-head">
        <h3 className="artist-appears-on-title">Appears on</h3>
        <span className="artist-appears-on-count">
          {playable.length} {playable.length === 1 ? 'track' : 'tracks'}
        </span>
        <button
          type="button"
          className="artist-appears-on-play-all"
          title="Play all"
          aria-label="Play all"
          onClick={() => playFrom(0)}
        >
          ▶
        </button>
      </div>

      <ul className="artist-appears-on-list">
        {shown.map((t, i) => (
          <li key={String(t.id)}>
            <button
              type="button"
              className="artist-appears-on-row"
              title={`Play ${t.title ?? ''}`}
              onClick={() => playFrom(i)}
            >
              {t.album_thumb_url ? (
                <img
                  className="artist-appears-on-art"
                  src={t.album_thumb_url}
                  alt=""
                  loading="lazy"
                />
              ) : (
                <span
                  className="artist-appears-on-art artist-appears-on-art-empty"
                  aria-hidden="true"
                />
              )}
              <span className="artist-appears-on-text">
                <span className="artist-appears-on-name">{t.title}</span>
                <span className="artist-appears-on-sub">{subLine(t, artistName)}</span>
              </span>
              <span className="artist-appears-on-time">{formatDuration(t.duration)}</span>
            </button>
          </li>
        ))}
      </ul>

      {playable.length > COLLAPSED ? (
        <button
          type="button"
          className="artist-appears-on-more"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'Show less' : `Show all ${playable.length}`}
        </button>
      ) : null}
    </section>
  );
}
