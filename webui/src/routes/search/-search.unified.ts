import type {
  SearchAlbum,
  SearchArtist,
  SearchPlaylist,
  SearchTrack,
  SourceResults,
} from './-search.types';

import { SOURCE_ORDER } from './-search.types';

/**
 * "All sources" (#2051): one search, asked of every metadata source at once.
 *
 * Not a source. It has no client and no cache slot of its own: it is a way of
 * LOOKING at the per-source caches the page already keeps. Picking it fills
 * every member's slot (each with the same single-source request a click on
 * that source would have made) and this module folds what has arrived so far
 * into one list. Switching to a single source afterwards is instant, because
 * its answer is already there.
 *
 * The same release from three sources is three results with nothing that says
 * so: a search result carries no shared id (a UPC or an ISRC only comes with
 * the detail lookup). So results are GROUPED FOR DISPLAY by what a person
 * would call the same thing, and nothing is ever stored as "these are one":
 *
 *   artist   same name
 *   album    same first artist, same title, both albums or both singles/EPs,
 *            years within one of each other (or unknown), and not a three
 *            track release against a six track one
 *   track    same first artist, same title, lengths within five seconds (or
 *            unknown)
 *
 * and a group holds at most ONE result per source: two artists called Nirvana
 * on the same source are two artists, whatever they are called.
 *
 * Titles keep their edition words. "OK Computer" and "OK Computer OKNOTOK
 * 1997 2017" stay apart, and so do a song and its remix. What is dropped is
 * only what sources spell differently for the same thing: case, accents,
 * punctuation, and a "(feat. X)" one source writes and another leaves off.
 *
 * A wrong guess here shares a card between two things until the user looks
 * closer. That is why the card says which sources it stands for.
 */

export const ALL_SOURCES = 'all';

export const ALL_SOURCES_LABEL = 'All sources';

/** Not metadata providers: each is its own tab on the page. */
const NOT_CATALOG = new Set(['youtube_videos', 'soulseek']);

/**
 * The sources "All sources" asks: every catalog source that is on the page
 * and set up. One with no credentials is left out rather than asked, since it
 * could only answer with an empty list.
 */
export function unifiedMembers(
  visible: readonly string[],
  configured: Readonly<Record<string, boolean>>,
): string[] {
  return visible.filter((source) => !NOT_CATALOG.has(source) && configured[source] !== false);
}

/**
 * Which source's copy stands for a group, and the order the marks read in:
 * the user's own primary source first, then the picker's order.
 */
export function sourcePriority(primary: string | undefined | null): string[] {
  const first = primary === 'spotify_free' ? 'spotify' : (primary ?? '');
  const rest = (SOURCE_ORDER as readonly string[]).filter((source) => source !== first);
  return first && (SOURCE_ORDER as readonly string[]).includes(first) ? [first, ...rest] : rest;
}

const FEAT_PAREN = /[([]\s*(?:feat\.?|ft\.?|featuring|with)\s[^)\]]*[)\]]/gi;
const FEAT_TAIL = /\s(?:feat\.?|ft\.?|featuring)\s.*$/i;

/** Case, accents and punctuation folded away. Parentheses' words are kept. */
export function foldText(value: unknown): string {
  return String(value ?? '')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** A title as sources agree on it: without the credit some of them append. */
export function foldTitle(value: unknown): string {
  return foldText(
    String(value ?? '')
      .replace(FEAT_PAREN, ' ')
      .replace(FEAT_TAIL, ' '),
  );
}

/** The first credited artist, folded. "A, B" and "A & B" and "A feat. B" are A. */
export function foldFirstArtist(value: unknown): string {
  const text = String(value ?? '');
  const first = text.split(/,|;|\s&\s|\sx\s|\s(?:feat\.?|ft\.?|featuring|with)\s/i)[0] ?? text;
  return foldText(first) || foldText(text);
}

function albumArtist(album: SearchAlbum): string {
  return foldFirstArtist(album.artists?.[0]?.name ?? album.artist ?? '');
}

function trackArtist(track: SearchTrack): string {
  return foldFirstArtist(track.artists?.[0] ?? track.artist ?? '');
}

function yearOf(value: unknown): number | null {
  const year = Number.parseInt(String(value ?? '').slice(0, 4), 10);
  return year >= 1000 && year <= 3000 ? year : null;
}

/** Albums and compilations against singles and EPs: sources disagree on EP or
 *  single, never on whether a twelve track record is one. */
function albumClass(album: SearchAlbum): 'long' | 'short' {
  const kind = String(album.album_type ?? '').toLowerCase();
  return kind === 'single' || kind === 'ep' ? 'short' : 'long';
}

function sizesClash(a: SearchAlbum, b: SearchAlbum): boolean {
  const ca = Number(a.total_tracks) || 0;
  const cb = Number(b.total_tracks) || 0;
  if (!ca || !cb) return false;
  return Math.min(ca, cb) <= 3 && Math.max(ca, cb) >= 6;
}

export function sameArtist(a: SearchArtist, b: SearchArtist): boolean {
  const name = foldText(a.name);
  return Boolean(name) && name === foldText(b.name);
}

export function sameAlbum(a: SearchAlbum, b: SearchAlbum): boolean {
  const title = foldTitle(a.name);
  if (!title || title !== foldTitle(b.name)) return false;
  const artistA = albumArtist(a);
  const artistB = albumArtist(b);
  // an album with no artist on one side cannot be told from a namesake
  if (!artistA || artistA !== artistB) return false;
  if (albumClass(a) !== albumClass(b)) return false;
  const yearA = yearOf(a.release_date);
  const yearB = yearOf(b.release_date);
  if (yearA !== null && yearB !== null && Math.abs(yearA - yearB) > 1) return false;
  return !sizesClash(a, b);
}

const TRACK_LENGTH_SLACK_MS = 5000;

export function sameTrack(a: SearchTrack, b: SearchTrack): boolean {
  const title = foldTitle(a.name);
  if (!title || title !== foldTitle(b.name)) return false;
  const artistA = trackArtist(a);
  const artistB = trackArtist(b);
  if (!artistA || artistA !== artistB) return false;
  const lengthA = Number(a.duration_ms) || 0;
  const lengthB = Number(b.duration_ms) || 0;
  if (lengthA && lengthB && Math.abs(lengthA - lengthB) > TRACK_LENGTH_SLACK_MS) return false;
  return true;
}

interface Group<T> {
  /** source -> that source's copy */
  copies: Map<string, T>;
  /** how strongly the sources vouch for it, see `vouch` */
  score: number;
  /** where the group first appeared, to keep ties in arrival order */
  seen: number;
}

/**
 * What one source's ranking is worth to a group. Each source that has the
 * thing adds a share, bigger the higher it ranked it, so three sources that
 * all put an album near the top outweigh one source's first hit. That matters:
 * a source searched for "radiohead" will happily lead with a stranger's song
 * called Radiohead, and agreement is the only thing here that says the band's
 * own records are what was meant.
 */
const RANK_SOFTENER = 10;

function vouch(rank: number): number {
  return 1 / (RANK_SOFTENER + rank);
}

/**
 * Fold each source's ranked list into groups, then order the groups by how
 * strongly the sources vouch for them, ties in the order they were first
 * seen (the user's primary source is read first).
 */
function fold<T extends { source?: string; sources?: string[] }>(
  lists: Array<{ source: string; items: readonly T[] }>,
  same: (a: T, b: T) => boolean,
  priority: readonly string[],
): T[] {
  const groups: Group<T>[] = [];
  let seen = 0;
  for (const { source, items } of lists) {
    items.forEach((item, rank) => {
      const home = groups.find(
        (group) =>
          !group.copies.has(source) && [...group.copies.values()].some((copy) => same(copy, item)),
      );
      if (home) {
        home.copies.set(source, item);
        home.score += vouch(rank);
        return;
      }
      groups.push({ copies: new Map([[source, item]]), score: vouch(rank), seen: seen++ });
    });
  }
  const order = (source: string) => {
    const at = priority.indexOf(source);
    return at === -1 ? priority.length : at;
  };
  return groups
    .sort((a, b) => b.score - a.score || a.seen - b.seen)
    .map((group) => {
      const sources = [...group.copies.keys()].sort((a, b) => order(a) - order(b));
      const lead = sources[0];
      // the copy is stamped with the source it came from: every click on the
      // card resolves THERE, and an id only means anything on its own source
      return { ...(group.copies.get(lead) as T), source: lead, sources };
    });
}

function playlistKey(playlist: SearchPlaylist, source: string): string {
  return `${playlist.source ?? source}:${String(playlist.id ?? playlist.name ?? '')}`;
}

/**
 * Everything the member sources have answered so far, as one result set.
 *
 * `answered` maps a source to its results and is read in `priority` order. A
 * source the server answered from somewhere else (`served` names where) is
 * left out: its results belong to the source that really produced them, which
 * is asked on its own, so counting them would be counting that source twice.
 */
export function mergeUnified(
  members: readonly string[],
  answered: Readonly<Partial<Record<string, SourceResults>>>,
  served: Readonly<Record<string, string>>,
  primary?: string | null,
): SourceResults {
  const priority = sourcePriority(primary);
  const ordered = [...members].sort((a, b) => {
    const ia = priority.indexOf(a);
    const ib = priority.indexOf(b);
    return (ia === -1 ? priority.length : ia) - (ib === -1 ? priority.length : ib);
  });
  const real = ordered.filter((source) => {
    if (!answered[source]) return false;
    const from = served[source];
    // spotify answered by its no-login client is still spotify
    return !from || (from === 'spotify_free' && source === 'spotify');
  });
  const lists = <K extends 'artists' | 'albums' | 'tracks'>(key: K) =>
    real.map((source) => ({ source, items: (answered[source] as SourceResults)[key] }));

  const playlists: SearchPlaylist[] = [];
  const havePlaylist = new Set<string>();
  for (const source of real) {
    for (const playlist of (answered[source] as SourceResults).playlists) {
      const key = playlistKey(playlist, source);
      if (havePlaylist.has(key)) continue;
      havePlaylist.add(key);
      playlists.push({ ...playlist, source: playlist.source ?? source });
    }
  }

  // "In Your Library" is the catalogue's answer, the same whoever was asked
  let dbArtists: SearchArtist[] = [];
  for (const source of ordered) {
    const found = answered[source]?.db_artists;
    if (found?.length) {
      dbArtists = found;
      break;
    }
  }

  return {
    db_artists: dbArtists,
    artists: fold<SearchArtist>(lists('artists'), sameArtist, priority),
    albums: fold<SearchAlbum>(lists('albums'), sameAlbum, priority),
    tracks: fold<SearchTrack>(lists('tracks'), sameTrack, priority),
    playlists,
    videos: [],
  };
}

export interface UnifiedProgress {
  /** how many member sources there are */
  total: number;
  /** the ones that have answered */
  answered: string[];
  /** still searching */
  pending: string[];
  /** asked, and no answer came back */
  failed: string[];
}

/** Where a search across all sources stands, for the line above the results. */
export function unifiedProgress(
  members: readonly string[],
  answered: Readonly<Partial<Record<string, SourceResults>>>,
  loading: ReadonlySet<string>,
  failed: ReadonlySet<string>,
): UnifiedProgress {
  return {
    total: members.length,
    answered: members.filter((source) => Boolean(answered[source])),
    pending: members.filter((source) => !answered[source] && loading.has(source)),
    failed: members.filter((source) => !answered[source] && failed.has(source)),
  };
}
