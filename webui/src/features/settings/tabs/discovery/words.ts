/**
 * The Discovery tab's hero sentences (settings-bic DESIGN.md 3.4): each pill
 * read back in words from the settings on screen. Pure, so each sentence is
 * tested from sample settings.
 */

import type { HeroPart } from '../downloads/words';

export type { HeroPart };

const name = (text: string): HeroPart => ({ text, kind: 'name' });
const warn = (text: string): HeroPart => ({ text, kind: 'warn' });
const say = (text: string): HeroPart => ({ text });

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// ── Recommendations ──

/** the dial runs 0 to 1. The old section summary compared it with 25 and 60,
 *  so it always read "plays it safe" (AUDIT 32): these are the same steps,
 *  on the dial's own scale */
export function adventureWord(n: number): 'safe' | 'balanced' | 'adventurous' {
  return n <= 0.25 ? 'safe' : n <= 0.6 ? 'balanced' : 'adventurous';
}

export const LOOKBACK_WORDS: Record<string, string> = {
  '7': 'the last 7 days',
  '30': 'the last 30 days',
  '90': 'the last 90 days',
  '180': 'the last 6 months',
  all: 'their whole discography',
};

export function recommendationsSentence(o: {
  adventurousness: number;
  statsEnabled: boolean;
  statsInterval: number;
  /** music side only, once it loaded */
  lookback?: string | null;
  hemisphere?: string | null;
}): HeroPart[] {
  const word = adventureWord(o.adventurousness);
  const parts: HeroPart[] =
    word === 'safe'
      ? [say('Recommendations '), name('play it safe'), say(', close to what you already know.')]
      : word === 'balanced'
        ? [
            say('Recommendations are '),
            name('balanced'),
            say(' between what you know and new finds.'),
          ]
        : [say('Recommendations are '), name('adventurous'), say(', with more obscure picks.')];
  if (o.statsEnabled)
    parts.push(
      say(' Play history is read from your media server every '),
      name(plural(o.statsInterval, 'minute')),
      say('.'),
    );
  else parts.push(say(' Play history is not collected.'));
  if (o.lookback)
    parts.push(
      say(' New artists are scanned back over '),
      name(LOOKBACK_WORDS[o.lookback] ?? `${o.lookback} days`),
    );
  if (o.lookback && o.hemisphere)
    parts.push(
      say(', and seasonal playlists follow the '),
      name(`${o.hemisphere === 'southern' ? 'southern' : 'northern'} hemisphere`),
      say('.'),
    );
  else if (o.lookback) parts.push(say('.'));
  return parts;
}

// ── Playlists ──

export const SYNC_MODE_NAMES: Record<string, string> = {
  replace: 'Replace',
  reconcile: 'Reconcile',
  append: 'Append',
};

export function playlistsSentence(o: {
  syncMode: string;
  wishlistMissing: boolean;
  createBackup: boolean;
  m3uExport: boolean;
  libraryM3u: boolean;
}): HeroPart[] {
  const parts: HeroPart[] =
    o.syncMode === 'replace'
      ? [say('A playlist sync '), name('replaces'), say(' the playlist on your server')]
      : o.syncMode === 'append'
        ? [
            say('A playlist sync '),
            name('only adds'),
            say(' new tracks to the playlist on your server'),
          ]
        : [say('A playlist sync '), name('edits'), say(' the playlist on your server in place')];
  parts.push(
    o.wishlistMissing
      ? say(' and adds missing tracks to the wishlist.')
      : say(' and leaves missing tracks off the wishlist.'),
  );
  if (o.syncMode === 'replace' && o.createBackup)
    parts.push(say(' A backup copy is kept on the server first.'));
  if (o.m3uExport && o.libraryM3u)
    parts.push(
      say(' An '),
      name('M3U file'),
      say(' is saved with each playlist download, and a '),
      name('whole-library M3U'),
      say(' is kept up to date.'),
    );
  else if (o.m3uExport)
    parts.push(say(' An '), name('M3U file'), say(' is saved with each playlist download.'));
  else if (o.libraryM3u)
    parts.push(say(' A '), name('whole-library M3U'), say(' is kept up to date.'));
  else parts.push(say(' No M3U files are written.'));
  return parts;
}

// ── Import Lists ──

export interface ImportListLine {
  name: string;
  enabled: boolean;
}

export function importListsSentence(lists: ImportListLine[] | null): HeroPart[] {
  if (lists === null) return [say('Loading your import lists.')];
  if (!lists.length)
    return [say('No import lists yet. Add one and anything new on it is added for you.')];
  const on = lists.filter((l) => l.enabled).map((l) => l.name.trim() || 'an unnamed list');
  const off = lists.length - on.length;
  if (!on.length)
    return [
      warn(`Every list is switched off`),
      say(`, so nothing is added from ${off === 1 ? 'it' : `the ${off}`}.`),
    ];
  const parts: HeroPart[] = [say('SoulSync watches ')];
  on.forEach((n, i) => {
    if (i) parts.push(say(i === on.length - 1 ? ' and ' : ', '));
    parts.push(name(n));
  });
  parts.push(
    say(
      `. Anything new on ${on.length === 1 ? 'it' : 'them'} is wishlisted if it is a movie and followed if it is a show.`,
    ),
  );
  if (off)
    parts.push(say(` ${plural(off, 'more list')} ${off === 1 ? 'is' : 'are'} switched off.`));
  return parts;
}
