import { useState } from 'react';

import { thumb } from '@/platform/artwork-thumb';

import type { Automation, AutomationPlaylist } from '../-automations.types';

/**
 * What stands at the head of an automation's row: the cover of the playlist
 * it syncs when it syncs one, otherwise a line glyph for the kind of work.
 *
 * The page used emoji for this. They render differently on every platform,
 * and a column of them reads as decoration rather than as a column of kinds.
 */

export type AutomationGlyph =
  | 'clock'
  | 'bolt'
  | 'playlist'
  | 'wrench'
  | 'radar'
  | 'database'
  | 'broom'
  | 'headphones'
  | 'star'
  | 'download'
  | 'layers'
  | 'calendar'
  | 'feed';

const PATHS: Record<AutomationGlyph, React.ReactNode> = {
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  bolt: <path d="M13 3 5 13.5h6L10 21l8-10.5h-6z" />,
  playlist: (
    <>
      <path d="M4 7h11M4 12h11M4 17h7" />
      <circle cx="18.5" cy="16.5" r="2.5" />
      <path d="M21 16.5V7l-2.5.8" />
    </>
  ),
  wrench: <path d="M14.5 6.5a4 4 0 0 0 5 5L10 21l-3-3 9.5-9.5zM14.5 6.5 17 4" />,
  radar: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 12 18 6" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="6.5" rx="7" ry="3" />
      <path d="M5 6.5v11c0 1.7 3.1 3 7 3s7-1.3 7-3v-11M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" />
    </>
  ),
  broom: <path d="M14 4 9.5 13M6 13h9l1.5 7h-12z" />,
  headphones: (
    <>
      <path d="M5 14v-2a7 7 0 0 1 14 0v2" />
      <rect x="3.5" y="13.5" width="4" height="6" rx="1.5" />
      <rect x="16.5" y="13.5" width="4" height="6" rx="1.5" />
    </>
  ),
  star: <path d="m12 4 2.4 5 5.6.700-4.1 3.8 1.1 5.5-5-2.8-5 2.8 1.1-5.5L4 9.7 9.6 9z" />,
  download: <path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14" />,
  // artwork laid over artwork, and titles gathered into a set
  layers: <path d="m12 4 8.5 4.5L12 13 3.5 8.5zM3.5 12.5 12 17l8.5-4.5M3.5 16.5 12 21l8.5-4.5" />,
  calendar: (
    <>
      <rect x="4" y="5.5" width="16" height="14.5" rx="2.5" />
      <path d="M4 10h16M8.5 3.5v4M15.5 3.5v4" />
    </>
  ),
  feed: (
    <>
      <circle cx="6" cy="18" r="1.5" />
      <path d="M4.5 11.5a8 8 0 0 1 8 8M4.5 5a14.5 14.5 0 0 1 14.5 14.5" />
    </>
  ),
};

/** The glyph for an automation, by what it does and then by what starts it. */
export function automationGlyph(
  a: Pick<Automation, 'action_type' | 'trigger_type' | 'owned_by'>,
): AutomationGlyph {
  const action = a.action_type ?? '';
  if (a.owned_by === 'auto_sync' || /playlist|mirrored/.test(action)) return 'playlist';
  // the video side's own kinds of work, before the words they share with music
  if (/overlay|collection/.test(action)) return 'layers';
  if (/airing/.test(action)) return 'calendar';
  if (/rss_sync|fresh_refresh|import_lists/.test(action)) return 'feed';
  if (/wishlist/.test(action)) return 'star';
  if (/listening/.test(action)) return 'headphones';
  if (/database|backup/.test(action)) return 'database';
  if (/clean|purge|quarantine/.test(action)) return 'broom';
  if (/watchlist|scan|discover/.test(action)) return 'radar';
  if (/repair|quality|duplicate/.test(action)) return 'wrench';
  if (/download/.test(action)) return 'download';
  if (a.trigger_type && !/^(schedule|daily_time|weekly_time|monthly_time)$/.test(a.trigger_type))
    return 'bolt';
  return 'clock';
}

export function Glyph({ name }: { name: AutomationGlyph }) {
  return (
    <svg className="automx-glyph" viewBox="0 0 24 24" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

function tiles(playlist: AutomationPlaylist | null | undefined): string[] {
  const raw = playlist?.cover_tiles;
  if (Array.isArray(raw)) return raw.filter((t) => typeof t === 'string' && !!t);
  if (typeof raw !== 'string' || !raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((t): t is string => typeof t === 'string' && !!t)
      : [];
  } catch {
    return [];
  }
}

/** The playlist's own poster, else the first album cover it borrowed. */
export function playlistCover(playlist: AutomationPlaylist | null | undefined): string | null {
  return playlist?.image_url || tiles(playlist)[0] || null;
}

interface ArtProps {
  automation: Automation;
  /** the big tile on the overview shelf, where a missing cover shows the name */
  named?: string;
}

/** The contents of a tile. The caller supplies the box. */
export function AutomationArt({ automation, named }: ArtProps) {
  const [failed, setFailed] = useState<string | null>(null);
  const cover = playlistCover(automation.playlist);
  if (cover && cover !== failed) {
    return (
      <img
        className="automx-art-img"
        src={thumb(cover, 'card')}
        alt=""
        loading="lazy"
        onError={() => setFailed(cover)}
      />
    );
  }
  if (named) return <span className="automx-art-name">{named}</span>;
  return <Glyph name={automationGlyph(automation)} />;
}
