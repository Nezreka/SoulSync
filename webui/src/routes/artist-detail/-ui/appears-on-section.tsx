import { useEffect, useState } from 'react';

import { LibraryCover } from '@/routes/library/-ui/library-cover';

import {
  type AppearsOnAlbum,
  type AppearsOnData,
  type AppearsOnTrack,
  EMPTY_APPEARS_ON,
  albumSubLine,
  albumTracks,
  formatDuration,
  loadAppearsOn,
  queueRow,
  subLine,
} from '../-artist-detail.appears-on';

/**
 * collab albums and features you own that are filed under another artist.
 * watch the throne lives under jay-z, "we found love" under calvin harris, but
 * they're kanye's and rihanna's too.
 *
 * renders nothing until there's something to show, so a library that hasn't
 * been through the spotify or deezer worker yet doesn't get an empty heading.
 */

const COLLAPSED = 8;

function PendingCover({
  src,
  remote,
  className,
}: {
  src: string;
  remote?: string | null;
  className: string;
}) {
  return (
    <LibraryCover
      src={src}
      remote={remote}
      className={className}
      fallback={<span className={`${className} artist-appears-on-art-empty`} aria-hidden="true" />}
    />
  );
}

export interface AppearsOnSectionProps {
  artistId: string | number | undefined;
  artistName: string;
}

function countLabel(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function AppearsOnSection({ artistId, artistName }: AppearsOnSectionProps) {
  const [data, setData] = useState<AppearsOnData>(EMPTY_APPEARS_ON);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setData(EMPTY_APPEARS_ON);
    setExpanded(false);
    if (artistId === undefined || artistId === null || artistId === '') return;
    let live = true;
    void loadAppearsOn(artistId).then((next) => {
      if (live) setData(next);
    });
    return () => {
      live = false;
    };
  }, [artistId]);

  const albums = data.albums.filter((a) => albumTracks(a).length);
  const playable = data.tracks.filter((t) => t.file_path);
  if (!albums.length && !playable.length) return null;

  const play = (queue: AppearsOnTrack[]) => {
    void window.playTrackList?.(
      queue.map((t) => queueRow(t, artistName)),
      `${artistName}, appears on`,
    );
  };
  const playAlbum = (album: AppearsOnAlbum) => play(albumTracks(album));
  // the whole song list as the queue, starting at the one clicked
  const playFrom = (index: number) => play([...playable.slice(index), ...playable.slice(0, index)]);
  const playAll = () => play([...albums.flatMap(albumTracks), ...playable]);

  const shown = expanded ? playable : playable.slice(0, COLLAPSED);
  const counts = [
    albums.length ? countLabel(albums.length, 'album', 'albums') : '',
    playable.length ? countLabel(playable.length, 'track', 'tracks') : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <section className="artist-appears-on-section" aria-label="Appears on">
      <div className="artist-appears-on-head">
        <h3 className="artist-appears-on-title">Appears on</h3>
        <span className="artist-appears-on-count">{counts}</span>
        <button
          type="button"
          className="artist-appears-on-play-all"
          title="Play all"
          aria-label="Play all"
          onClick={playAll}
        >
          ▶
        </button>
      </div>

      {albums.length ? (
        <ul className="artist-appears-on-albums">
          {albums.map((a) => (
            // the cover plays the album, its name opens it on the artist it
            // is filed under
            <li key={String(a.id)} className="artist-appears-on-album">
              <button
                type="button"
                className="artist-appears-on-album-play"
                title={`Play ${a.title ?? ''}`}
                onClick={() => playAlbum(a)}
              >
                {a.thumb_url ? (
                  <PendingCover
                    className="artist-appears-on-album-art"
                    src={a.thumb_url}
                    remote={a.thumb_remote_url}
                  />
                ) : (
                  <span
                    className="artist-appears-on-album-art artist-appears-on-art-empty"
                    aria-hidden="true"
                  />
                )}
              </button>
              <a
                className="artist-appears-on-album-open"
                href={`/library?artist=${a.artist_id}&album=${a.id}`}
                title={`Open ${a.title ?? ''}`}
              >
                <span className="artist-appears-on-name">{a.title}</span>
                <span className="artist-appears-on-sub">{albumSubLine(a, artistName)}</span>
              </a>
            </li>
          ))}
        </ul>
      ) : null}

      {playable.length ? (
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
                  <PendingCover
                    className="artist-appears-on-art"
                    src={t.album_thumb_url}
                    remote={t.album_thumb_remote_url}
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
      ) : null}

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
