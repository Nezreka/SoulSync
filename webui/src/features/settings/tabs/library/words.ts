/**
 * The Library tab's hero sentences (settings-bic DESIGN.md 3.4): each pill
 * and kind read back in words from the settings on screen. Pure, so each
 * sentence is tested from sample settings.
 */

import type { HeroPart } from '../downloads/words';

export type { HeroPart };

const name = (text: string): HeroPart => ({ text, kind: 'name' });
const warn = (text: string): HeroPart => ({ text, kind: 'warn' });
const say = (text: string): HeroPart => ({ text });

/** "a, b and c" */
export function listWords(items: string[]): string {
  if (items.length <= 1) return items[0] ?? '';
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/** names read as names: "a, b and c", or with `each` between every two ("a, then b, then c") */
function names(items: string[], each?: string): HeroPart[] {
  const parts: HeroPart[] = [];
  items.forEach((item, index) => {
    if (index) parts.push(say(each ?? (index === items.length - 1 ? ' and ' : ', ')));
    parts.push(name(item));
  });
  return parts;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// ── Folders ──

export function musicFoldersSentence(o: {
  download: string;
  transfer: string;
  /** the library folder is still the bare-metal install default */
  installDefault: boolean;
}): HeroPart[] {
  const parts = [
    say('Downloads land in '),
    name(o.download || './downloads'),
    say(' and finished music moves to '),
    name(o.transfer || './Transfer'),
    say('.'),
  ];
  if (o.installDefault)
    parts.push(say(' '), warn('Your music library is still the install folder default.'));
  return parts;
}

export function audiobookFoldersSentence(o: {
  path: string;
  extra: number;
  /** null until the list loaded */
  routed: number | null;
}): HeroPart[] {
  const parts = [say('Finished books go to '), name(o.path || './audiobooks'), say('.')];
  if (o.extra) parts.push(say(` SoulSync also reads ${plural(o.extra, 'more folder')}.`));
  if (o.routed)
    parts.push(
      say(` Downloads by ${plural(o.routed, 'spoken-word artist')} are filed with the books.`),
    );
  return parts;
}

export function podcastFoldersSentence(path: string): HeroPart[] {
  return [say('Episodes are saved in '), name(path || './podcasts'), say('.')];
}

export function videoFoldersSentence(
  c: Record<string, unknown> | null,
  status: string,
): HeroPart[] {
  if (status === 'failed') return [warn('The video folders did not load.')];
  if (!c) return [say('Loading the video folders.')];
  const str = (key: string) => (typeof c[key] === 'string' ? (c[key] as string).trim() : '');
  const parts: HeroPart[] = [
    say('Downloads land in '),
    name(str('download_path') || './downloads'),
    say('.'),
  ];
  const movies = str('movies_path');
  const tv = str('tv_path');
  if (movies && tv)
    parts.push(say(' Movies go to '), name(movies), say(' and TV shows to '), name(tv), say('.'));
  else if (movies)
    parts.push(say(' Movies go to '), name(movies), say('. '), warn('No TV folder is set yet.'));
  else if (tv)
    parts.push(say(' TV shows go to '), name(tv), say('. '), warn('No movies folder is set yet.'));
  else parts.push(say(' '), warn('No movies or TV folder is set yet.'));
  return parts;
}

// ── Naming ──

/** a template in plain words: "$author/$series" reads "Author / Series" */
const TOKEN_WORDS: Record<string, string> = {
  author: 'Author',
  authorletter: 'Author letter',
  narrator: 'Narrator',
  title: 'Title',
  series: 'Series',
  seriespos: 'Series position',
  year: 'Year',
  asin: 'ASIN',
  show: 'Show',
  podcast: 'Show',
  artist: 'Artist',
  season: 'Season number',
  seasonnum: 'Season number',
  episode: 'Episode number',
  episodenum: 'Episode number',
  date: 'Date',
  type: 'Type',
};

export function templateWords(template: string): string {
  return template
    .replace(/\$\{([a-z]+)\}|\$([a-z]+)/gi, (_, a: string, b: string) => {
      const key = (a || b).toLowerCase();
      return TOKEN_WORDS[key] ?? key;
    })
    .split('/')
    .map((part) => part.trim())
    .filter(Boolean)
    .join(' / ');
}

/** the server's preview of the album template, as one line of folders */
export function pathWords(path: string): string {
  return path
    .split('/')
    .map((part) => part.trim())
    .filter(Boolean)
    .join(' / ');
}

export function musicNamingSentence(o: {
  on: boolean;
  /** the server's answer for the album template; null until it came */
  album: string | null;
  template: string;
}): HeroPart[] {
  if (!o.on)
    return [
      say('Custom templates are off, so files keep '),
      name("SoulSync's built-in layout"),
      say('.'),
    ];
  return [
    say('An album track is saved as '),
    name(o.album ? pathWords(o.album) : templateWords(o.template)),
  ];
}

export function audiobookNamingSentence(template: string): HeroPart[] {
  return [say('A book is saved as '), name(templateWords(template)), say('.')];
}

export function podcastNamingSentence(template: string): HeroPart[] {
  return [say('An episode is saved as '), name(templateWords(template)), say('.')];
}

export function videoNamingSentence(movie: string | null, episode: string | null): HeroPart[] {
  if (!movie) return [say('Loading the video naming.')];
  const parts = [say('A movie is saved as '), name(pathWords(movie))];
  if (episode) parts.push(say(' and an episode as '), name(pathWords(episode)));
  parts.push(say('.'));
  return parts;
}

// ── Tags and artwork ──

export function musicTagsSentence(o: {
  enabled: boolean;
  /** the services whose IDs are written */
  services: string[];
  /** where cover art comes from, first to last; empty: the metadata source */
  art: string[];
  artwork: boolean;
}): HeroPart[] {
  if (!o.enabled)
    return [
      warn('Post-processing is off,'),
      say(' so downloads are kept as they arrive: no tags, artwork or lyrics are added.'),
    ];
  const parts: HeroPart[] = o.services.length
    ? [say('Downloads are tagged with IDs from '), ...names(o.services), say('.')]
    : [say('Downloads are tagged, with no service IDs written.')];
  if (o.artwork) {
    if (o.art.length) parts.push(say(' Cover art comes from '), name(o.art[0]), say(' first.'));
    else parts.push(say(' Cover art comes from the metadata source.'));
  }
  return parts;
}

export function audiobookTagsSentence(o: {
  tags: boolean;
  embed: boolean;
  cover: boolean;
  sidecars: boolean;
}): HeroPart[] {
  const bits = [
    o.tags && 'tags',
    o.embed && 'cover art inside the file',
    o.cover && 'a cover.jpg',
    o.sidecars && 'metadata sidecars',
  ].filter((b): b is string => !!b);
  if (!bits.length) return [say('Books are kept as they arrive: nothing is written into them.')];
  return [say('Books get '), name(listWords(bits)), say('.')];
}

export function videoTagsSentence(o: {
  subs: boolean;
  artwork: boolean;
  nfo: boolean;
}): HeroPart[] {
  const yes = [o.subs && 'matching subtitles', o.artwork && 'artwork', o.nfo && 'NFO files'].filter(
    (b): b is string => !!b,
  );
  const no = [!o.subs && 'subtitles', !o.artwork && 'artwork', !o.nfo && 'NFO files'].filter(
    (b): b is string => !!b,
  );
  if (!yes.length)
    return [
      say('Finished files are filed alone: no subtitles, artwork or NFO files go with them.'),
    ];
  const parts = [say('Finished files are filed with '), name(listWords(yes)), say('.')];
  if (no.length) {
    const words = listWords(no);
    const are = no.length > 1 || words.endsWith('s') ? 'are' : 'is';
    parts.push(say(` ${words[0].toUpperCase()}${words.slice(1)} ${are} left out.`));
  }
  return parts;
}

// ── Import and files ──

export function musicImportSentence(o: { source: string; permanent: boolean }): HeroPart[] {
  return [
    say('The Import page searches '),
    name(o.source || 'any source'),
    say('. '),
    o.permanent
      ? say('Your music library folder is permanent, so a deep scan never moves files out.')
      : say(
          'Your music library folder is a drop area: a deep scan may move files it does not know to the import folder.',
        ),
  ];
}

export function audiobookImportSentence(o: {
  percent: number;
  days: number;
  merge: boolean;
}): HeroPart[] {
  const parts = [
    say('A book is imported once '),
    name(`${o.percent}%`),
    say(' of its runtime is in, and given up on after '),
    name(plural(o.days, 'day')),
    say('.'),
  ];
  if (o.merge) parts.push(say(' Books in many files are merged into one M4B.'));
  return parts;
}

const FORMAT_WORDS: Record<string, string> = {
  audio: 'audio only',
  video: 'video only',
  both: 'audio and video',
};

export function podcastImportSentence(format: string): HeroPart[] {
  return [say('Podcasts are kept as '), name(FORMAT_WORDS[format] ?? FORMAT_WORDS.audio), say('.')];
}

export function videoImportSentence(
  o: {
    move: boolean;
    verify: boolean;
    replace: boolean;
    recycle: boolean;
    days: number;
  } | null,
): HeroPart[] {
  if (!o) return [say('Loading what happens to video files.')];
  const parts = [
    say('A finished file is '),
    name(o.move ? 'moved in' : 'copied in'),
    say(o.verify ? ' and checked with ffprobe.' : '.'),
    say(
      o.replace
        ? ' A better copy replaces the one you have.'
        : ' A better copy never replaces the one you have.',
    ),
  ];
  parts.push(
    o.recycle
      ? say(` Deleted files go to the recycle bin for ${plural(o.days, 'day')}.`)
      : say(' Deleted files are erased.'),
  );
  return parts;
}

// ── Subtitles ──

export function subtitlesSentence(
  o: {
    on: boolean;
    langs: string;
    providers: string[];
    whisper: boolean;
  } | null,
): HeroPart[] {
  if (!o) return [say('Loading the subtitle settings.')];
  if (!o.on) return [say('Subtitles are '), warn('not downloaded'), say('.')];
  const langs = o.langs
    .split(/[\s,;]+/)
    .filter(Boolean)
    .join(', ');
  const parts: HeroPart[] = [say('Subtitles in '), name(langs || 'en'), say(' come from ')];
  parts.push(...names(o.providers, ', then '), say('.'));
  if (o.whisper) parts.push(say(' When none has them, Whisper transcribes the audio.'));
  return parts;
}
