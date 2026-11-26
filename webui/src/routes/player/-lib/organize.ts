/**
 * a device's downloads in sections by kind, the way the downloads page
 * shows them: tv shows (each show a card, its seasons inside), movies,
 * youtube, music (albums and playlists as cards, loose songs as one
 * "Songs" card), audiobooks, podcasts, home videos. empty sections are
 * left out. cards sort newest download first, or by name.
 */

import type { GroupType, ItemGroup } from './offline';

export type SectionId = 'tv' | 'movies' | 'youtube' | 'music' | 'books' | 'podcasts' | 'home';
export type SortBy = 'recent' | 'az';

export const SECTIONS: { id: SectionId; label: string }[] = [
  { id: 'tv', label: 'TV shows' },
  { id: 'movies', label: 'Movies' },
  { id: 'youtube', label: 'YouTube' },
  { id: 'music', label: 'Music' },
  { id: 'books', label: 'Audiobooks' },
  { id: 'podcasts', label: 'Podcasts' },
  { id: 'home', label: 'Home videos' },
];

/** what organizing needs of a download, on a phone or saved by this browser */
export interface Entry {
  item_type: string;
  item_id: string;
  title?: string | null;
  artwork?: string | null;
  size_bytes?: number | null;
  group?: ItemGroup | null;
}

export interface Season<E> {
  id: string;
  label: string;
  items: E[];
}

export interface Card<E> {
  key: string;
  /** what the sheet and a remove act on; songs is the loose tracks together */
  type: GroupType | 'songs';
  id: string;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  items: E[];
  /** a show's seasons, in order, each with its episodes */
  seasons: Season<E>[];
  bytes: number;
  /** the newest download in it (ms) */
  at: number;
}

export interface Section<E> {
  id: SectionId;
  label: string;
  cards: Card<E>[];
  count: number;
  bytes: number;
}

export function sectionOf(itemType: string): SectionId {
  switch (itemType) {
    case 'episode':
    case 'season':
    case 'show':
      return 'tv';
    case 'movie':
      return 'movies';
    case 'youtube':
      return 'youtube';
    case 'audiobook':
      return 'books';
    case 'podcast_episode':
    case 'podcast':
      return 'podcasts';
    case 'home':
    case 'home_album':
      return 'home';
    default:
      return 'music';
  }
}

const MUSIC_GROUPS = new Set(['album', 'playlist', 'artist']);

/** the card an entry sits on: [type, id] */
function cardOf(e: Entry): [GroupType | 'songs', string] {
  const g = e.group;
  switch (e.item_type) {
    case 'episode':
      if (g?.type === 'season') return ['show', g.id.split(':')[0] || g.id];
      if (g?.type === 'show') return ['show', g.id];
      return ['episode', e.item_id];
    case 'track':
      if (g && MUSIC_GROUPS.has(g.type)) return [g.type, g.id];
      return ['songs', 'all'];
    case 'podcast_episode':
      return ['podcast', g?.type === 'podcast' ? g.id : e.item_id.split(':')[0]];
    case 'home':
      return g?.type === 'home_album' ? ['home_album', g.id] : ['home', e.item_id];
    default:
      return [e.item_type as GroupType, g?.type === e.item_type ? g.id : e.item_id];
  }
}

/** "Season 2" from a season group, "Specials" for season 0 */
export function seasonLabel(id: string, subtitle?: string | null): string {
  const n = Number(id.split(':')[1]);
  if (n === 0) return 'Specials';
  if (subtitle) return subtitle;
  return Number.isFinite(n) ? `Season ${n}` : 'Episodes';
}

/** S2 E5 out of an episode's title, for order */
function episodeOrder(title?: string | null): number {
  const m = /S(\d+)\s*E(\d+)/i.exec(title ?? '');
  return m ? Number(m[1]) * 10_000 + Number(m[2]) : Number.MAX_SAFE_INTEGER;
}

function seasonsOf<E extends Entry>(items: E[]): Season<E>[] {
  const by = new Map<string, Season<E>>();
  for (const it of items) {
    const g = it.group;
    const id = g?.type === 'season' ? g.id : '';
    let s = by.get(id);
    if (!s) {
      s = { id, label: id ? seasonLabel(id, g?.subtitle) : 'Episodes', items: [] };
      by.set(id, s);
    }
    s.items.push(it);
  }
  const num = (id: string) => {
    const n = Number(id.split(':')[1]);
    // specials after the numbered seasons, loose episodes last
    return !id ? 1e9 : n === 0 ? 1e8 : n;
  };
  return [...by.values()].sort((a, b) => num(a.id) - num(b.id));
}

/** a list of downloads as the page's sections, in the page's order */
export function organize<E extends Entry>(
  entries: E[],
  at: (e: E) => number,
  sort: SortBy = 'recent',
): Section<E>[] {
  const cards = new Map<string, Card<E> & { section: SectionId }>();
  for (const e of entries) {
    const [type, id] = cardOf(e);
    const key = `${type}:${id}`;
    let card = cards.get(key);
    if (!card) {
      const g = e.group;
      const grouped = type !== 'songs' && g && g.type !== e.item_type;
      card = {
        key,
        type,
        id,
        section: sectionOf(e.item_type),
        title:
          type === 'songs'
            ? 'Songs'
            : (grouped || type === e.item_type ? g?.title : null) || e.title || 'Untitled',
        subtitle: type === 'songs' ? null : grouped && type !== 'show' ? g?.subtitle : null,
        artwork: type === 'songs' ? null : (g?.artwork ?? e.artwork),
        items: [],
        seasons: [],
        bytes: 0,
        at: 0,
      };
      cards.set(key, card);
    }
    card.items.push(e);
    card.bytes += e.size_bytes || 0;
    card.at = Math.max(card.at, at(e) || 0);
    if (!card.artwork && e.artwork && type !== 'songs') card.artwork = e.artwork;
  }
  for (const card of cards.values()) {
    if (card.type === 'show') {
      card.items.sort((a, b) => episodeOrder(a.title) - episodeOrder(b.title));
      card.seasons = seasonsOf(card.items);
    } else if (card.type === 'songs') {
      card.items.sort((a, b) => (at(b) || 0) - (at(a) || 0));
    }
  }
  const byName = (a: Card<E>, b: Card<E>) =>
    a.title.localeCompare(b.title, undefined, { sensitivity: 'base', numeric: true });
  const order = sort === 'az' ? byName : (a: Card<E>, b: Card<E>) => b.at - a.at || byName(a, b);
  return SECTIONS.map(({ id, label }) => {
    const mine = [...cards.values()].filter((c) => c.section === id).sort(order);
    return {
      id,
      label,
      cards: mine,
      count: mine.reduce((n, c) => n + c.items.length, 0),
      bytes: mine.reduce((n, c) => n + c.bytes, 0),
    };
  }).filter((s) => s.cards.length > 0);
}

/** a time an item carries (iso or epoch ms) as ms, 0 when it has none */
export function stamp(value?: string | number | null): number {
  if (value == null || value === '') return 0;
  if (typeof value === 'number') return value;
  const t = Date.parse(value);
  return Number.isFinite(t) ? t : 0;
}
