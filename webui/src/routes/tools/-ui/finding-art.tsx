/**
 * The picture beside a finding: its album cover, or the artist when there is
 * no cover. The server looks it up (core.repair_worker.attach_finding_art),
 * so a row only ever says where the picture is.
 *
 * A cover that fails to load, or a finding about nothing in the library (an
 * orphan file, an empty folder), gets a quiet glyph in the same box, so the
 * rows keep one left edge.
 */

import { useEffect, useState } from 'react';

function NoteGlyph() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 18V5l11-2v13" />
      <circle cx="6.5" cy="18" r="2.5" />
      <circle cx="17.5" cy="16" r="2.5" />
    </svg>
  );
}

function FileGlyph() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

export interface FindingArtProps {
  album?: string | null;
  artist?: string | null;
  /** a finding about a bare file, not a track: the glyph says so */
  file?: boolean;
}

export function FindingArt({ album, artist, file }: FindingArtProps) {
  const sources = [album, artist].filter((url): url is string => Boolean(url));
  const [failed, setFailed] = useState(0);
  // a new row in the same slot: start again from its cover
  useEffect(() => setFailed(0), [album, artist]);
  const src = sources[failed];
  const round = Boolean(src) && src === artist && src !== album;

  return (
    <span className={`repair-art${round ? ' round' : ''}${src ? '' : ' empty'}`} aria-hidden="true">
      {src ? (
        <img src={src} alt="" loading="lazy" onError={() => setFailed((count) => count + 1)} />
      ) : file ? (
        <FileGlyph />
      ) : (
        <NoteGlyph />
      )}
    </span>
  );
}

/**
 * Up to four covers as one tile, the way a playlist shows what is in it: what
 * an issue type is about, before you read a word. A type with no pictures (an
 * orphan file belongs to no album) gets the glyph in the same tile, so every
 * row starts on one edge.
 */
export function FindingArtMosaic({
  urls,
  file,
}: {
  urls?: readonly string[] | null;
  file?: boolean;
}) {
  const [broken, setBroken] = useState<ReadonlySet<string>>(() => new Set());
  const shown = (urls || []).filter((url) => url && !broken.has(url)).slice(0, 4);
  if (!shown.length) {
    return (
      <span className="repair-art-mosaic empty" aria-hidden="true">
        {file ? <FileGlyph /> : <NoteGlyph />}
      </span>
    );
  }
  return (
    <span className={`repair-art-mosaic n${shown.length}`} aria-hidden="true">
      {shown.map((url) => (
        <img
          src={url}
          alt=""
          loading="lazy"
          key={url}
          onError={() => setBroken((current) => new Set(current).add(url))}
        />
      ))}
    </span>
  );
}
