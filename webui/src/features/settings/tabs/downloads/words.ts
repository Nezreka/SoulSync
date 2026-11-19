/**
 * The download order, in words (settings-bic DESIGN.md 5.2, 5.3): the hero
 * sentence, what a row's place means, its status, and what a change did.
 * Pure functions over the sources model's state, so the copy is tested.
 */

import type { IconName, OrderChange, Tone } from '../../ui';
import type { ChainKind, SourceState, SourcesState } from '../sources-model';

export interface SourceLook {
  name: string;
  /** a logo under /static/img/brands */
  logo?: string;
  /** the logo is a glyph, drawn smaller in its square */
  pad?: boolean;
  /** a dark mark, drawn white */
  invert?: boolean;
  /** the line icon when there is no logo file */
  icon: IconName;
  /** its brand colour, "r, g, b" (style.css [data-src] --brand) */
  brand: string;
  /** what kind of source it is, for the "Not used" line */
  group: string;
}

const BRANDS = '/static/img/brands/';

export const SOURCE_LOOK: Record<string, SourceLook> = {
  soulseek: {
    name: 'Soulseek',
    logo: `${BRANDS}slskd.png`,
    icon: 'link',
    brand: '74, 158, 255',
    group: 'Peer-to-peer',
  },
  youtube: {
    name: 'YouTube',
    logo: `${BRANDS}youtube.svg`,
    pad: true,
    icon: 'link',
    brand: '255, 60, 50',
    group: 'Public video',
  },
  tidal: {
    name: 'Tidal',
    logo: `${BRANDS}tidal.svg`,
    pad: true,
    invert: true,
    icon: 'wave',
    brand: '0, 208, 255',
    group: 'Streaming service',
  },
  qobuz: {
    name: 'Qobuz',
    logo: `${BRANDS}qobuz.svg`,
    pad: true,
    invert: true,
    icon: 'wave',
    brand: '0, 122, 239',
    group: 'Streaming service',
  },
  hifi: { name: 'HiFi', icon: 'wave', brand: '168, 120, 255', group: 'Streaming service' },
  deezer_dl: {
    name: 'Deezer',
    logo: `${BRANDS}deezer.png`,
    icon: 'wave',
    brand: '162, 56, 255',
    group: 'Streaming service',
  },
  lidarr: { name: 'Lidarr', icon: 'box', brand: '0, 200, 100', group: 'Library manager' },
  soundcloud: {
    name: 'SoundCloud',
    logo: `${BRANDS}soundcloud.png`,
    pad: true,
    invert: true,
    icon: 'wave',
    brand: '255, 85, 0',
    group: 'Streaming service',
  },
  torrent: {
    name: 'Torrent',
    icon: 'magnet',
    brand: '255, 59, 107',
    group: 'Through your indexers',
  },
  usenet: { name: 'Usenet', icon: 'news', brand: '108, 140, 255', group: 'Through your indexers' },
  extto: { name: 'External', icon: 'link', brand: '155, 123, 255', group: 'External site' },
  prowlarr: { name: 'Prowlarr', icon: 'search', brand: '255, 176, 32', group: 'Indexers' },
};

export const lookOf = (id: string): SourceLook =>
  SOURCE_LOOK[id] ?? { name: id, icon: 'link', brand: '', group: 'Source' };
export const nameOf = (id: string) => lookOf(id).name;

/** what each kind is called in a sentence */
export const KIND_WORD: Record<ChainKind, string> = {
  music: 'music',
  video: 'video',
  audiobooks: 'audiobooks',
};

const ORDINALS = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth'];
export const ordinal = (index: number) => ORDINALS[index] ?? `number ${index + 1}`;

/** "A", "A and B", "A, B and C" */
function list(names: string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** a piece of the hero sentence: plain, a source's name, or the warning */
export type HeroPart = { text: string; kind?: 'name' | 'warn' };

/**
 * The order read aloud. Music and video are tried in turn; audiobooks are
 * fetched as a whole folder, so their order is who is asked first.
 */
export function heroSentence(
  kind: ChainKind,
  order: string[],
  unready: Record<string, boolean> = {},
): HeroPart[] {
  const subject =
    kind === 'music'
      ? ['Music tries', 'Music downloads from']
      : kind === 'video'
        ? ['Movies and TV try', 'Movies and TV download from']
        : ['Books ask', 'Books download from'];
  const name = (id: string): HeroPart => ({ text: nameOf(id), kind: 'name' });
  if (order.length === 0) return [{ text: 'No source is picked yet. Add one below.' }];
  const parts: HeroPart[] = [];
  if (order.length === 1) {
    parts.push({ text: `${subject[1]} ` }, name(order[0]), { text: ' only.' });
  } else {
    parts.push({ text: `${subject[0]} ` }, name(order[0]), { text: ' first' });
    for (const id of order.slice(1)) parts.push({ text: ', then ' }, name(id));
    parts.push({ text: '.' });
  }
  const skipped = order.filter((id) => unready[id]).map(nameOf);
  if (skipped.length) {
    parts.push(
      { text: ' ' },
      {
        text: `${list(skipped)} ${skipped.length > 1 ? "aren't" : "isn't"} set up yet`,
        kind: 'warn',
      },
      { text: `, so ${skipped.length > 1 ? 'they are' : 'it is'} skipped for now.` },
    );
  }
  return parts;
}

/** the same sentence as plain text (the live region, the tests) */
export const heroText = (kind: ChainKind, order: string[], unready: Record<string, boolean> = {}) =>
  heroSentence(kind, order, unready)
    .map((part) => part.text)
    .join('');

/** what a row's place means */
export function roleText(
  kind: ChainKind,
  id: string,
  index: number,
  order: string[],
  unready: Record<string, boolean> = {},
): string {
  if (unready[id]) return 'Skipped until it is set up';
  if (order.length === 1) return `The only source for ${KIND_WORD[kind]}`;
  if (kind === 'audiobooks') {
    return index === 0 ? 'Asked first' : `Asked after ${nameOf(order[index - 1])}`;
  }
  return index === 0 ? 'Tried first' : `Tried when ${nameOf(order[index - 1])} finds nothing`;
}

/** what the slot says while a row is held over it */
export function dropText(kind: ChainKind, id: string, index: number): string {
  const verb = kind === 'audiobooks' ? 'asked' : 'tried';
  return `Drop here: ${nameOf(id)} is ${verb} ${ordinal(index)}`;
}

const STATE_WORDS: Record<SourceState, string> = {
  unknown: 'Not tested yet',
  testing: 'Testing…',
  ok: 'Connected',
  na: 'No test for this source',
  warn: 'Working, but needs a look',
  fail: 'Connection failed',
};
const STATE_TONE: Record<SourceState, Tone> = {
  unknown: 'idle',
  testing: 'busy',
  ok: 'ok',
  na: 'idle',
  warn: 'warn',
  fail: 'bad',
};

/** the server's words without the sign it leads a warning with */
const clean = (said: string) => said.replace(/^(?:\s|\u26A0|\uFE0F)+/u, '').trim();

/** a source's health in words: what the last test said, or that it needs setup */
export function statusOf(
  id: string,
  state: Pick<SourcesState, 'status' | 'said' | 'unready'>,
  { inOrder = false }: { inOrder?: boolean } = {},
): { tone: Tone; text: string } {
  const dot = state.status[id] || 'unknown';
  if (dot === 'testing') return { tone: 'busy', text: STATE_WORDS.testing };
  // the server skips it at download time until it is set up
  if (inOrder && state.unready[id]) return { tone: 'warn', text: 'Needs setup' };
  const said = clean(state.said[id] || '');
  if ((dot === 'warn' || dot === 'fail') && said) return { tone: STATE_TONE[dot], text: said };
  return { tone: STATE_TONE[dot], text: STATE_WORDS[dot] };
}

/** where else a source is used: "Video #1, Audiobooks #2" */
export function usedIn(uses: Array<{ label: string; pos: number }>): string {
  return uses.map((use) => `${use.label} #${use.pos}`).join(', ');
}

/** how each kind saves: music and audiobooks with the page, video at once */
export const SAVES: Record<ChainKind, string> = {
  music: 'It saves with your other changes.',
  video: 'The video order saves at once.',
  audiobooks: 'It saves with your other changes.',
};

/** what a change to the order did, for the toast */
export function changeText(kind: ChainKind, change: OrderChange, next: string[]): string {
  const name = nameOf(change.id);
  const verb = kind === 'audiobooks' ? 'asked' : 'tried';
  const what = KIND_WORD[kind];
  let said: string;
  if (change.type === 'remove') said = `${name} is no longer used for ${what}.`;
  else if (next.length === 1) said = `${name} is now the only source for ${what}.`;
  else if (change.type === 'add')
    said = `${name} is now used for ${what}, ${verb} ${ordinal(change.to)}.`;
  else said = `${name} is now ${verb} ${ordinal(change.to)} for ${what}.`;
  return `${said} ${SAVES[kind]}`;
}
