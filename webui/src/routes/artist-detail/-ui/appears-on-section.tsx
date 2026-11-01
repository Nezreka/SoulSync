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
  /** opens the release from this page, so back comes here again */
  onOpenAlbum?: (album: AppearsOnAlbum) => void;
  /** opens the artist the release is filed under */
  onOpenArtist?: (artistId: string | number) => void;
}

/** "with Post Malone · 2018", the artist it is filed under as a link */
function AlbumSub({
  album,
  artistName,
  onOpenArtist,
}: {
  album: AppearsOnAlbum;
  artistName: string;
  onOpenArtist?: (artistId: string | number) => void;
}) {
  const text = albumSubLine(album, artistName);
  const name = album.artist_name ?? '';
  const at = name && onOpenArtist && album.artist_id != null ? text.indexOf(name) : -1;
  if (at < 0) return <span className="artist-appears-on-sub">{text}</span>;
  return (
    <span className="artist-appears-on-sub">
      {text.slice(0, at)}
      <button
        type="button"
        className="artist-appears-on-link"
        title={`Open ${name}`}
        onClick={() => onOpenArtist?.(album.artist_id!)}
      >
        {name}
      </button>
      {text.slice(at + name.length)}
    </span>
  );
}

function countLabel(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function AppearsOnSection({
  artistId,
  artistName,
  onOpenAlbum,
  onOpenArtist,
}: AppearsOnSectionProps) {
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

  // a wanted track not here yet is what puts a guest in the library, so it
  // is listed too, with nothing to play
  const shows = (t: AppearsOnTrack) => Boolean(t.file_path || t.wanted);
  const albums = data.albums.filter((a) => (a.tracks ?? []).some(shows));
  const listed = data.tracks.filter(shows);
  const playable = listed.filter((t) => t.file_path);
  if (!albums.length && !listed.length) return null;

  const play = (queue: AppearsOnTrack[]) => {
    void window.playTrackList?.(
      queue.map((t) => queueRow(t, artistName)),
      `${artistName}, appears on`,
    );
  };
  const playAlbum = (album: AppearsOnAlbum) => play(albumTracks(album));
  // the whole song list as the queue, starting at the one clicked
  const playFrom = (track: AppearsOnTrack) => {
    const index = playable.indexOf(track);
    play([...playable.slice(index), ...playable.slice(0, index)]);
  };
  const playAll = () => play([...albums.flatMap(albumTracks), ...playable]);
  const canPlay = albums.some((a) => albumTracks(a).length) || playable.length > 0;

  const shown = expanded ? listed : listed.slice(0, COLLAPSED);
  const counts = [
    albums.length ? countLabel(albums.length, 'album', 'albums') : '',
    listed.length ? countLabel(listed.length, 'track', 'tracks') : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <section className="artist-appears-on-section" aria-label="Appears on">
      <div className="artist-appears-on-head">
        <h3 className="artist-appears-on-title">Appears on</h3>
        <span className="artist-appears-on-count">{counts}</span>
        {canPlay ? (
          <button
            type="button"
            className="artist-appears-on-play-all"
            title="Play all"
            aria-label="Play all"
            onClick={playAll}
          >
            ▶
          </button>
        ) : null}
      </div>

      {albums.length ? (
        <ul className="artist-appears-on-albums">
          {albums.map((a) => (
            // the cover plays the album, its name opens it from this page and
            // the artist it is filed under opens theirs
            <li key={String(a.id)} className="artist-appears-on-album">
              <button
                type="button"
                className="artist-appears-on-album-play"
                title={
                  albumTracks(a).length ? `Play ${a.title ?? ''}` : 'Wanted, not downloaded yet'
                }
                disabled={!albumTracks(a).length}
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
              {onOpenAlbum ? (
                <button
                  type="button"
                  className="artist-appears-on-name artist-appears-on-link"
                  title={`Open ${a.title ?? ''}`}
                  onClick={() => onOpenAlbum(a)}
                >
                  {a.title}
                </button>
              ) : (
                <span className="artist-appears-on-name">{a.title}</span>
              )}
              <AlbumSub album={a} artistName={artistName} onOpenArtist={onOpenArtist} />
            </li>
          ))}
        </ul>
      ) : null}

      {listed.length ? (
        <ul className="artist-appears-on-list">
          {shown.map((t) => (
            <li key={String(t.id)}>
              <button
                type="button"
                className="artist-appears-on-row"
                title={t.file_path ? `Play ${t.title ?? ''}` : 'Wanted, not downloaded yet'}
                disabled={!t.file_path}
                onClick={() => playFrom(t)}
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
                <span className="artist-appears-on-time">
                  {t.file_path ? formatDuration(t.duration) : 'Wanted'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {listed.length > COLLAPSED ? (
        <button
          type="button"
          className="artist-appears-on-more"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'Show less' : `Show all ${listed.length}`}
        </button>
      ) : null}
    </section>
  );
}
