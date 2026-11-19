/**
 * The Quality tab read back in words (settings-bic DESIGN.md 1, mockup 04):
 * one sentence per pill, from the settings on screen. Pure, so each sentence
 * is tested on its own.
 */

import {
  type Ladder,
  RT_LOSSLESS_FORMATS,
  type RankedTarget,
  clampCutoff,
  rtLabel,
} from '../quality';

/** "A", "A and B", "A, then B, then C", "A, then B, then C and 2 more" */
function inTurn(labels: string[]): string {
  if (labels.length <= 3) {
    if (labels.length < 2) return labels[0] ?? '';
    return `${labels.slice(0, -1).join(', then ')}, then ${labels[labels.length - 1]}`;
  }
  const rest = labels.length - 3;
  return `${labels.slice(0, 3).join(', then ')} and ${rest} more`;
}

/** a target as the sentence says it: "MP3 at 320 or better", "FLAC 24-bit" */
export function targetWords(t: RankedTarget): string {
  if (!RT_LOSSLESS_FORMATS.includes(t.format) && t.min_bitrate)
    return `${(t.format || 'any').toUpperCase()} at ${t.min_bitrate} or better`;
  return rtLabel(t);
}

/** the music pill: what the profile on screen takes, and when it stops upgrading */
export function musicSentence(name: string, ladder: Ladder): string {
  const who = name || 'This profile';
  const targets = ladder.targets;
  const takes = targets.length
    ? `${who} takes ${inTurn(targets.map(targetWords))}${targets.length > 1 ? '' : ' only'}.`
    : `${who} has no targets yet, so it ${ladder.fallback ? 'takes whatever turns up' : 'takes nothing'}.`;
  const fallback =
    targets.length && ladder.fallback ? ' When none of them turns up it takes what there is.' : '';
  let upgrades = '';
  if (ladder.upgradePolicy === 'none') upgrades = ' It never replaces a file you have.';
  else if (ladder.upgradePolicy === 'acceptable')
    upgrades = ' It stops upgrading once a file matches any of them.';
  else if (targets.length)
    upgrades = ` It keeps upgrading until it has ${targetWords(targets[clampCutoff(ladder.cutoff, targets)])}.`;
  return `${takes}${fallback}${upgrades}`;
}

export interface VideoWords {
  name: string;
  /** the ladder's switched-on tiers, best first, as their labels */
  tiers: string[];
  cutoff: string | null | undefined;
}

const CUTOFF_WORDS: Record<string, string> = {
  '2160p': '4K',
  '1080p': '1080p',
  '720p': '720p',
  '480p': 'SD',
};

/** the Movies and TV pill */
export function videoSentence({ name, tiers, cutoff }: VideoWords): string {
  const who = name || 'Default';
  const takes = tiers.length
    ? `${who} takes ${inTurn(tiers)}.`
    : `${who} has every quality switched off, so nothing is grabbed.`;
  const stop =
    cutoff === ''
      ? ' It never stops upgrading.'
      : ` It stops upgrading at ${CUTOFF_WORDS[cutoff ?? '1080p'] ?? cutoff}.`;
  return `${takes}${tiers.length ? stop : ''}`;
}

const RES_WORDS: Record<string, string> = {
  best: 'the best there is',
  '4320p': '8K',
  '2160p': '4K',
};
const CODEC_WORDS: Record<string, string> = { av1: 'AV1', vp9: 'VP9', h264: 'H.264' };

/** the YouTube pill */
export function youtubeSentence(p: {
  max_resolution?: string;
  video_codec?: string;
  container?: string;
}): string {
  const res = p.max_resolution || '1080p';
  const upTo = res === 'best' ? RES_WORDS.best : `up to ${RES_WORDS[res] ?? res}`;
  const codec = CODEC_WORDS[p.video_codec || ''];
  const box = (p.container || 'mp4').toUpperCase();
  return `Followed channels download ${upTo}, as ${box}${codec ? `, preferring ${codec}` : ''}.`;
}

/** the Audiobooks pill */
export function audiobookSentence(p: {
  first: string;
  allowed: string[];
  of: number;
  minKbps: number;
  maxKbps: number;
}): string {
  const first = p.first.toUpperCase();
  const allowed =
    p.allowed.length === 0 || p.allowed.length === p.of
      ? 'every format is allowed'
      : `only ${p.allowed.map((f) => f.toUpperCase()).join(', ')} ${p.allowed.length === 1 ? 'is' : 'are'} allowed`;
  const rates =
    p.minKbps && p.maxKbps
      ? ` Releases between ${p.minKbps} and ${p.maxKbps} kbps.`
      : p.minKbps
        ? ` Nothing below ${p.minKbps} kbps.`
        : p.maxKbps
          ? ` Nothing above ${p.maxKbps} kbps.`
          : '';
  return `Audiobooks prefer ${first}, and ${allowed}.${rates}`;
}
