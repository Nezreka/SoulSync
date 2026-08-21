/**
 * Playlist cover art for a sync card.
 *
 * Every card on this page drew a glyph where the cover should be — an emoji, a
 * brand letter, CSS bars — while `image_url` was fetched, carried through the
 * mirror payloads and the account rows, and then dropped on the floor. So this
 * is less "add artwork" than "stop throwing it away": the data was already
 * here, the same way the Deezer progress frames were.
 *
 * Renders the CONTENTS of a tile, not the tile — the caller supplies the box.
 * That is what lets it drop into the existing `.playlist-card-icon` (already
 * 40px, already rounded, already brand-tinted) without moving a single pixel of
 * the current layout: with art the tile fills, without it the glyph shows
 * exactly as before. The brand tint becomes the empty state instead of the
 * normal one.
 *
 * The failed URL is remembered rather than a boolean flag, so a card whose art
 * changes gets a fresh attempt instead of being stuck on the glyph for the rest
 * of the session — playlist covers do change under us when a mirror re-syncs.
 */

import type { ReactNode } from 'react';

import { useState } from 'react';

import { thumb } from '@/platform/artwork-thumb';

export interface PlaylistArtProps {
  /** `image_url` off the row. Absent/empty simply yields the glyph. */
  url?: string | null;
  /** What the card drew before there was art — the last rung of the ladder. */
  glyph?: string;
  /**
   * A richer fallback than a glyph: the source's real brand mark. Takes
   * precedence over `glyph` when both are given.
   */
  fallback?: ReactNode;
}

/**
 * Read `image_url` off one of this page's loosely-typed playlist rows.
 *
 * The verticals keep the source payload as `Record<string, unknown>` and the
 * mirrored list comes back from a `SELECT *`, so every call site would
 * otherwise repeat the same cast. An empty string is treated as absent — the
 * column is nullable and several sources write `''` rather than NULL.
 */
export function playlistArtUrl(row: unknown): string | undefined {
  if (!row || typeof row !== 'object') return undefined;
  const url = (row as { image_url?: unknown }).image_url;
  return typeof url === 'string' && url ? url : undefined;
}

/**
 * The up-to-four distinct album covers a playlist borrowed from its discovered
 * tracks, off the row's `cover_tiles` JSON column.
 *
 * Fewer than four is deliberately NOT a collage: a 2x2 with two blanks reads as
 * a broken image, where one cover reads as a cover.
 */
export function playlistCoverTiles(row: unknown): string[] {
  if (!row || typeof row !== 'object') return [];
  const raw = (row as { cover_tiles?: unknown }).cover_tiles;
  if (Array.isArray(raw)) return raw.filter((t): t is string => typeof t === 'string' && !!t);
  if (typeof raw !== 'string' || !raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((t): t is string => typeof t === 'string' && !!t);
  } catch {
    return [];
  }
}

export interface PlaylistCollageProps {
  tiles: readonly string[];
  glyph?: string;
  fallback?: ReactNode;
}

/**
 * A playlist's artwork when the source gave us no poster of its own.
 *
 * Four covers make a 2x2; one to three make a single cover; none falls through
 * to the brand mark.
 */
export function PlaylistCollage({ tiles, glyph, fallback }: PlaylistCollageProps) {
  if (tiles.length >= 4) {
    return (
      <span className="playlist-card-collage">
        {tiles.slice(0, 4).map((tile) => (
          <img key={tile} src={thumb(tile, 'card')} alt="" loading="lazy" />
        ))}
      </span>
    );
  }
  return <PlaylistArt url={tiles[0]} glyph={glyph} fallback={fallback} />;
}

export function PlaylistArt({ url, glyph, fallback }: PlaylistArtProps) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const usable = url && url !== failedUrl ? url : null;

  if (!usable) return <span className="playlist-card-art-glyph">{fallback ?? glyph}</span>;

  return (
    <img
      className="playlist-card-art-img"
      src={thumb(usable, 'card')}
      alt=""
      loading="lazy"
      onError={() => setFailedUrl(usable)}
    />
  );
}
