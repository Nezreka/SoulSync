import { useState } from 'react';

/** Bits the songs list and the copies dialog share. */

export function duration(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return '';
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/** The album's cover, or a note when there is none or it won't load. */
export function SongCover({ url }: { url?: string | null }) {
  const [failed, setFailed] = useState(false);
  if (!url || failed) return <span>♪</span>;
  return <img src={url} alt="" loading="lazy" onError={() => setFailed(true)} />;
}
