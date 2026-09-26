/**
 * The discovery inbox: one place for things worth coming back to.
 *
 * The server keeps it (core/discovery/inbox.py): watchlist releases, what's
 * coming out, recommendations saved from their ⋯ menu, and concerts when
 * Ticketmaster is set up. It refreshes in the background, and when a source
 * doesn't answer it says so and shows the rest, so this never renders blank.
 */

import type { Explanation } from './-discover.explanation';
import type { RecentAlbum } from './-discover.recent-releases';

import { explanationLine } from './-discover.explanation';

export type InboxKind = 'new_release' | 'upcoming' | 'saved_rec' | 'concert';
export type InboxView = 'new' | 'saved';

export interface InboxItem {
  id: number;
  /** An InboxKind; typed wide because it arrives as JSON. */
  kind: string;
  title: string;
  artist_name?: string;
  image_url?: string;
  item_date?: string;
  state?: string;
  payload?: {
    ids?: Record<string, string>;
    source?: string;
    url?: string;
    entity_type?: string;
    explanation?: Explanation | null;
  };
}

export interface InboxPayload {
  success?: boolean;
  view?: InboxView;
  items?: InboxItem[];
  counts?: { unread?: number };
  unanswered?: string[];
  refreshing?: boolean;
}

/** How many rows show before "Show all". */
export const INBOX_PREVIEW = 6;

export const INBOX_EMPTY: Record<InboxView, string> = {
  new: 'Nothing new. Releases from artists you watch land here, and so does anything you save.',
  saved: 'Nothing saved. Use ⋯ → Save for later on any recommendation.',
};

const KIND_LABEL: Record<InboxKind, string> = {
  new_release: 'New release',
  upcoming: 'Coming soon',
  saved_rec: 'Saved',
  concert: 'Live',
};

export function inboxKindLabel(kind: string): string {
  return KIND_LABEL[kind as InboxKind] ?? '';
}

const SOURCE_NAME: Record<string, string> = {
  releases: 'Your release scan',
  concerts: 'Ticketmaster',
};

/** "Ticketmaster didn't answer, showing the rest" - never a blank inbox. */
export function unansweredLine(sources: string[] | undefined): string {
  const names = (sources ?? []).map((s) => SOURCE_NAME[s] ?? s);
  if (!names.length) return '';
  const who =
    names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
  return `${who} didn't answer, showing the rest`;
}

function parseDay(value: string | undefined): Date | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}/.test(value)) return null;
  const d = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function dayDiff(from: Date, to: Date): number {
  return Math.round((to.getTime() - from.getTime()) / 86_400_000);
}

function shortDate(d: Date): string {
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/** The line under the title: when, and for a saved rec, why. */
export function inboxWhen(item: InboxItem, today: Date = new Date()): string {
  const day = parseDay(item.item_date);
  const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  switch (item.kind) {
    case 'new_release': {
      if (!day) return 'New release';
      const ago = dayDiff(day, midnight);
      if (ago <= 0) return 'Out today';
      return ago === 1 ? 'Out yesterday' : `Out ${ago} days ago`;
    }
    case 'upcoming': {
      if (!day) return 'Coming soon';
      const until = dayDiff(midnight, day);
      if (until <= 0) return 'Out today';
      if (until === 1) return 'Out tomorrow';
      return until < 14 ? `Out in ${until} days` : `Out ${shortDate(day)}`;
    }
    case 'concert':
      return day ? `Live ${shortDate(day)}` : 'Live';
    case 'saved_rec':
      return explanationLine(item.payload?.explanation ?? undefined) || 'Saved';
    default:
      return '';
  }
}

/** A release item in the shape the album opener already takes. */
export function inboxReleaseAlbum(item: InboxItem): RecentAlbum {
  const ids = item.payload?.ids ?? {};
  return {
    album_name: item.title,
    artist_name: item.artist_name ?? '',
    album_cover_url: item.image_url ?? '',
    source: item.payload?.source ?? '',
    album_spotify_id: ids.spotify,
    album_deezer_id: ids.deezer,
    album_itunes_id: ids.itunes,
  };
}

export function isRelease(item: InboxItem): boolean {
  return item.kind === 'new_release' || item.kind === 'upcoming';
}

/** A saved artist's first provider id, for its artist page. */
export function inboxArtistRef(item: InboxItem): { id: string; source: string } | null {
  if (item.kind !== 'saved_rec' || item.payload?.entity_type !== 'artist') return null;
  const [source, id] = Object.entries(item.payload?.ids ?? {}).find(([, v]) => Boolean(v)) ?? [];
  return source && id ? { id, source } : null;
}
