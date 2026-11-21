/**
 * a playlist's cover: its own picture, else four of its album covers in a
 * square, else a tint with its first letter. liked songs is always the heart.
 */

import { useState } from 'react';

import type { PlaylistSummary } from '../../-player.api';

import { playlistCover } from '../../-lib/playlists';
import { HeartIcon } from '../icons';
import s from './music.module.css';

function Img({ src, onFail }: { src: string; onFail: () => void }) {
  return <img src={src} alt="" loading="lazy" decoding="async" onError={onFail} />;
}

export function PlaylistCover({
  playlist,
  size = 'card',
}: {
  playlist: Pick<PlaylistSummary, 'name' | 'image_url' | 'cover_tiles' | 'liked'>;
  size?: 'card' | 'hero' | 'thumb';
}) {
  const cover = playlistCover(playlist);
  // a cover that won't load falls back: its own picture, then the album covers, then the letter
  const [failed, setFailed] = useState<Set<string>>(() => new Set());
  const fail = (src: string) => setFailed((f) => new Set(f).add(src));
  const image = cover.image && !failed.has(cover.image) ? cover.image : null;
  const good = (
    cover.image && !image ? playlistCover({ ...playlist, image_url: null }).tiles : cover.tiles
  ).filter((t) => !failed.has(t));
  const tiles = good.length >= 4 ? good.slice(0, 4) : good.slice(0, 1);
  return (
    <span className={s.cover} data-size={size} data-liked={playlist.liked ? 'true' : undefined}>
      {playlist.liked ? (
        <span className={s.coverLiked}>
          <HeartIcon filled />
        </span>
      ) : image ? (
        <Img src={image} onFail={() => fail(image)} />
      ) : tiles.length === 4 ? (
        <span className={s.coverTiles}>
          {tiles.map((t) => (
            <Img key={t} src={t} onFail={() => fail(t)} />
          ))}
        </span>
      ) : tiles.length ? (
        <Img src={tiles[0]} onFail={() => fail(tiles[0])} />
      ) : (
        <span className={s.coverLetter} aria-hidden="true">
          {playlist.name.trim().charAt(0).toUpperCase() || '♪'}
        </span>
      )}
    </span>
  );
}
