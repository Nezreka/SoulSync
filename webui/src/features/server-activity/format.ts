/**
 * Plain-words formatting for the server activity drawer. The backend sends
 * numbers (unix seconds, ms, kbps) and the strings the mockup shows verbatim;
 * everything relative ("Paused twice, 6 min", "7h 42m left") is made here.
 */

import type {
  GeoPlace,
  HistoryGroup,
  MediaKind,
  PlayMethod,
  Player,
  Pulse,
  ServerKind,
  Session,
  StatsPayload,
  Transcoder,
} from '@/shell/server-activity-types';

const DAYS_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const DAYS_LONG = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
/** the stats heatmap counts Monday as 0 */
export const WEEKDAYS_MON = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const WEEKDAYS_MON_LONG = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const pad = (n: number) => (n < 10 ? '0' : '') + n;

/** 11800 -> "11.8"; whole tens drop the decimal (40000 -> "40") */
export function mbpsNumber(kbps: number | null | undefined): string {
  const m = (kbps || 0) / 1000;
  if (m >= 100) return String(Math.round(m));
  const s = m.toFixed(1);
  return s.endsWith('.0') ? s.slice(0, -2) : s;
}

export function mbps(kbps: number | null | undefined): string {
  return mbpsNumber(kbps) + ' Mbps';
}

/** a player clock: 1:08:12, 54:31, 1:35 */
export function clock(ms: number | null | undefined): string {
  const t = Math.max(0, Math.floor((ms || 0) / 1000));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** a length of time: 2h 31m, 47m, 39s */
export function duration(ms: number | null | undefined): string {
  const t = Math.max(0, Math.round((ms || 0) / 1000));
  if (t < 60) return `${t}s`;
  const totalMin = Math.round(t / 60);
  if (totalMin < 60) return `${totalMin}m`;
  const h = Math.floor(totalMin / 60);
  return `${h}h ${pad(totalMin % 60)}m`;
}

/** hours as people say them: "286 hours", "1 hour", "40 minutes" */
export function hoursPhrase(hours: number): string {
  if (hours < 1) {
    const min = Math.round(hours * 60);
    return min === 1 ? '1 minute' : `${min} minutes`;
  }
  const h = hours < 10 ? Math.round(hours * 10) / 10 : Math.round(hours);
  return h === 1 ? '1 hour' : `${h.toLocaleString('en-US')} hours`;
}

/** a tile number for hours: 286, 9.4, 0.1 */
export function hoursNumber(hours: number): string {
  if (hours >= 100) return Math.round(hours).toLocaleString('en-US');
  if (hours >= 10) return String(Math.round(hours));
  return String(Math.round(hours * 10) / 10);
}

export function count(n: number, one: string, many = one + 's'): string {
  return `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
}

function asDate(epochSeconds: number): Date {
  return new Date(epochSeconds * 1000);
}

/** 9:14 pm */
export function timeOfDay(epochSeconds: number): string {
  const d = asDate(epochSeconds);
  const h = d.getHours();
  return `${h % 12 || 12}:${pad(d.getMinutes())} ${h < 12 ? 'am' : 'pm'}`;
}

/** an hour on the heatmap axis: 12a, 6a, 12p, 6p */
export function hourTick(h: number): string {
  if (h === 0) return '12a';
  if (h === 12) return '12p';
  return (h % 12) + (h < 12 ? 'a' : 'p');
}

/** "9 pm", "noon", "midnight" */
export function hourWords(h: number): string {
  const x = ((h % 24) + 24) % 24;
  if (x === 0) return 'midnight';
  if (x === 12) return 'noon';
  return `${x % 12} ${x < 12 ? 'am' : 'pm'}`;
}

/** Sat 2 Nov */
export function shortDate(epochSeconds: number): string {
  const d = asDate(epochSeconds);
  return `${DAYS_SHORT[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** Sat 2 Nov, 9:40 pm */
export function dateTime(epochSeconds: number): string {
  return `${shortDate(epochSeconds)}, ${timeOfDay(epochSeconds)}`;
}

/** a YYYY-MM-DD from the server, read as a calendar day (no zone shift) */
function parseDay(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

/** 2 Nov from 2026-11-02 */
export function dayLabel(iso: string): string {
  const d = parseDay(iso);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** Sat 2 Nov from 2026-11-02 */
export function dayLabelLong(iso: string): string {
  const d = parseDay(iso);
  return `${DAYS_SHORT[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** the YYYY-MM-DD a moment falls on in a zone (history day headings) */
export function isoDay(epochSeconds: number, tz?: string): string {
  try {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: tz,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(asDate(epochSeconds));
  } catch {
    const d = asDate(epochSeconds);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
}

/** Today, Yesterday, Saturday, or Sat 2 Nov once it is more than a week back */
export function dayHeading(iso: string, now: Date = new Date()): string {
  const day = parseDay(iso);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((today.getTime() - day.getTime()) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  if (diff > 1 && diff < 7) return DAYS_LONG[day.getDay()];
  const year = day.getFullYear() !== today.getFullYear() ? ` ${day.getFullYear()}` : '';
  return `${DAYS_SHORT[day.getDay()]} ${day.getDate()} ${MONTHS[day.getMonth()]}${year}`;
}

/** just now, 5 min ago, 3h ago, yesterday, 6 days ago, 2 Nov */
export function ago(epochSeconds: number | null | undefined, nowMs: number = Date.now()): string {
  if (!epochSeconds) return '';
  const s = Math.max(0, Math.floor(nowMs / 1000 - epochSeconds));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  const days = Math.floor(s / 86400);
  if (days === 1) return 'yesterday';
  if (days < 14) return `${days} days ago`;
  const d = asDate(epochSeconds);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

const TIMES = ['', 'once', 'twice'];

/** "Paused twice, 6 min" (null when it never paused) */
export function pausedPhrase(pauseCount: number, pausedMs: number): string | null {
  if (!pauseCount && !pausedMs) return null;
  const n = pauseCount || 1;
  const times = TIMES[n] ?? `${n} times`;
  const min = pausedMs / 60000;
  const long =
    min < 1 ? 'under a minute' : min < 60 ? `${Math.round(min)} min` : duration(pausedMs);
  return `Paused ${times}, ${long}`;
}

/** 3m 40s buffered, 45s buffered */
export function bufferedPhrase(seconds: number): string {
  const t = Math.round(seconds);
  if (t < 60) return `${t}s buffered`;
  return `${Math.floor(t / 60)}m ${pad(t % 60)}s buffered`;
}

export function initials(name: string | null | undefined): string {
  const parts = String(name || '?')
    .trim()
    .split(/[\s._-]+/)
    .filter(Boolean);
  const first = (parts[0] || '?')[0];
  const last = parts.length > 1 ? parts[parts.length - 1][0] : (parts[0] || '')[1] || '';
  return (first + last).toUpperCase();
}

/** a stable colour pair per name, from tokens only */
const AVATAR_TONES = [
  ['var(--wanted)', 'var(--info)'],
  ['var(--warning-strong)', 'var(--danger)'],
  ['var(--success-strong)', 'var(--info)'],
  ['var(--danger-light)', 'var(--wanted)'],
  ['var(--gold)', 'var(--warning-strong)'],
  ['var(--info)', 'var(--success)'],
];
export function avatarTone(name: string | null | undefined): string {
  let h = 0;
  for (const ch of String(name || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const [a, b] = AVATAR_TONES[h % AVATAR_TONES.length];
  return `linear-gradient(135deg, ${a}, ${b})`;
}

const SERVER_WORDS: Record<ServerKind, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  soulsync: 'SoulSync',
};

/** Plex, Jellyfin or SoulSync (its own player) */
export function serverWord(server: ServerKind | string | null | undefined): string {
  return SERVER_WORDS[server as ServerKind] ?? 'Server';
}

export const METHOD_LABEL: Record<PlayMethod, string> = {
  direct_play: 'Direct Play',
  direct_stream: 'Direct Stream',
  transcode: 'Transcode',
};

export const KIND_LABEL: Record<MediaKind, string> = {
  movie: 'Movie',
  episode: 'Episode',
  track: 'Track',
  audiobook: 'Audiobook',
  podcast: 'Podcast',
  clip: 'Clip',
  live: 'Live TV',
  other: 'Item',
};

export function isAudioKind(kind: MediaKind): boolean {
  return kind === 'track' || kind === 'audiobook' || kind === 'podcast';
}

export function isBookKind(kind: MediaKind): boolean {
  return kind === 'audiobook' || kind === 'podcast';
}

const GENERIC_DEVICES = new Set(['tv', 'unknown', 'other', 'web', 'browser']);
const BROWSERS = /^(firefox|chrome|chromium|safari|edge|opera|brave|vivaldi)\b/i;

/**
 * The device the way a person would name it: "Apple TV", "Plexamp on iPhone",
 * "Plex on Pixel 8", "Shield TV", "Jellyfin Web".
 */
export function deviceLabel(
  player: { [K in keyof Player]?: Player[K] | null } | null | undefined,
): string {
  if (!player) return '';
  const product = (player.product || '').trim();
  const candidates = [player.device, player.name]
    .map((x) => (x || '').trim())
    .filter((x) => x && !GENERIC_DEVICES.has(x.toLowerCase()) && !BROWSERS.test(x));
  const where = candidates[0];
  if (!where) return product;
  if (!product) return where;
  if (product.toLowerCase().includes(where.toLowerCase())) return where;
  if (/\btv\b/i.test(where) || /\btv\b/i.test(product)) return where;
  const app = /^plex for /i.test(product)
    ? 'Plex'
    : /^jellyfin\b/i.test(product)
      ? 'Jellyfin'
      : product;
  return `${app} on ${where}`;
}

/** Home, Remote, or the city when the place is known */
export function whereLabel(remote: boolean, place: GeoPlace | string | null | undefined): string {
  if (!remote) return 'Home';
  if (!place) return 'Remote';
  return typeof place === 'string' ? place : place.short || place.label || 'Remote';
}

/** Intel Quick Sync, decode + encode */
export function transcoderEngine(t: Transcoder): string {
  if (t.hw_decode && t.hw_encode && t.hw_decode === t.hw_encode) {
    return `${t.hw_decode}, decode + encode`;
  }
  if (t.hw_decode && t.hw_encode) return `${t.hw_decode} decode, ${t.hw_encode} encode`;
  if (t.hw_encode) return `${t.hw_encode} encode, software decode`;
  if (t.hw_decode) return `${t.hw_decode} decode, software encode`;
  return t.hw ? 'Hardware' : 'Software';
}

/** Running at 2.4× · 3m 40s buffered · resting */
export function transcoderLine(t: Transcoder): string {
  const bits: string[] = [];
  // a paused transcode reports 0×; that is not a speed worth showing
  if (t.speed) bits.push(`Running at ${Math.round(t.speed * 10) / 10}×`);
  if (t.buffered_s != null) bits.push(bufferedPhrase(t.buffered_s));
  if (t.throttled) bits.push('resting');
  return bits.join(' · ') || (t.hw ? 'Hardware' : 'Software');
}

/** the Transcoder cell of the pulse strip: "Hardware", "2 hardware, 1 software" */
export function transcoderMix(p: Pulse): string {
  const { running, hw, sw } = p.transcoders;
  if (!running) return 'None running';
  if (hw === running) return 'Hardware';
  if (sw === running) return 'Software';
  return `${hw} hardware, ${sw} software`;
}

/** 2 watching · 2 listening */
export function streamsMix(p: Pulse): string {
  const bits: string[] = [];
  if (p.watching) bits.push(`${p.watching} watching`);
  if (p.listening) bits.push(`${p.listening} listening`);
  if (!bits.length) return 'Nothing playing';
  return bits.join(' · ');
}

/** "secure, not relayed", "relayed through plex.tv" (plex only) */
export function connectionNote(secure: boolean | null, relayed: boolean | null): string {
  const bits: string[] = [];
  if (secure === true) bits.push('secure');
  if (secure === false) bits.push('not encrypted');
  if (relayed === true) bits.push('relayed through plex.tv');
  if (relayed === false) bits.push('not relayed');
  return bits.join(', ');
}

/** the first sentence of the reason is the headline, the rest the detail */
export function splitReason(text: string): [string, string] {
  const at = text.indexOf('. ');
  if (at < 0) return [text, ''];
  return [text.slice(0, at + 1), text.slice(at + 2)];
}

/** what the person shows on the card's who line */
export function whoLine(s: Session, multiServer = false): string[] {
  const bits = [deviceLabel(s.player), whereLabel(s.connection.remote, s.connection.location)];
  // with both servers set, which one this stream is on
  if (multiServer) bits.push(serverWord(s.server));
  return bits.filter(Boolean);
}

/** "S2 E10" style subtitle is the server's; the kind word fills a blank one */
export function sessionSubtitle(s: Session): string {
  if (s.kind === 'movie') return [s.year, 'Movie'].filter(Boolean).join(' · ');
  return s.subtitle || KIND_LABEL[s.kind];
}

export interface QualityBadge {
  key: string;
  text: string;
  /** split on the arrow so it can sit quieter */
  from?: string;
  to?: string;
  hires?: boolean;
}

function arrow(key: string, text: string): QualityBadge {
  const at = text.indexOf('→');
  if (at < 0) return { key, text };
  return { key, text, from: text.slice(0, at).trim(), to: text.slice(at + 1).trim() };
}

/** the quiet badges after the method badge, in the mockup's order */
export function qualityBadges(s: Session): QualityBadge[] {
  const out: QualityBadge[] = [];
  const pb = s.playback;
  const audioKind = isAudioKind(s.kind);
  const { quality, change } = pb.summary;
  if (audioKind) {
    const src = pb.audio?.source;
    const q = quality || src?.quality;
    if (q) out.push({ key: 'q', text: q, hires: !!src?.hires });
    if (s.kind === 'track' && src?.channel_layout) {
      out.push({ key: 'ch', text: src.channel_layout });
    }
    if (change) out.push(arrow('chg', change));
  } else {
    if (change && change.includes('→')) out.push(arrow('chg', change));
    else {
      if (quality) out.push({ key: 'q', text: quality });
      if (change) out.push({ key: 'chg', text: change });
    }
    const v = pb.video;
    if (v?.source?.hdr && v.stream && !v.stream.hdr && v.decision === 'transcode') {
      out.push(arrow('hdr', `${v.source.hdr} → SDR`));
    }
    const a = pb.audio;
    if (a?.source && !a.changed) {
      const label = [a.source.codec, a.source.atmos ? 'Atmos' : '', a.source.channel_layout]
        .filter(Boolean)
        .join(' ');
      if (label) out.push({ key: 'a', text: label });
    }
  }
  if (s.transcoder && pb.method === 'transcode') {
    out.push({ key: 'hw', text: s.transcoder.hw ? 'Hardware' : 'Software' });
  }
  if (s.bandwidth_kbps) out.push({ key: 'bw', text: mbps(s.bandwidth_kbps) });
  // the who line already says Remote when the place isn't known
  if (s.connection.remote && s.connection.location) out.push({ key: 'rem', text: 'Remote' });
  return out;
}

/** 2h 31m · 100%, 1h 12m · book 41%, from Plex history */
export function playedLine(
  g: Pick<HistoryGroup, 'source' | 'played_ms' | 'percent' | 'book_percent' | 'live'>,
): string {
  if (g.source === 'plex_history') return 'from Plex history';
  const dur = duration(g.played_ms);
  if (g.book_percent != null) return `${dur} · book ${g.book_percent}%`;
  if (g.percent != null) return `${dur} · ${g.percent}%`;
  return dur;
}

export const RANGE_WORDS: Record<number, string> = {
  7: '7 days',
  30: '30 days',
  90: '90 days',
  365: 'year',
};

const KIND_WORDS: Record<string, string> = {
  tv: 'TV',
  films: 'films',
  music: 'music',
  books: 'books and podcasts',
  other: 'other things',
};

export interface Segment {
  text: string;
  strong?: boolean;
}

/**
 * The stats hero: "In the last 30 days 6 people played 286 hours across both
 * servers. Most of it was TV, evenings are busiest, and 1 in 6 plays had to
 * be converted, mostly for maya away from home."
 */
export function heroSentence(stats: StatsPayload): Segment[] {
  const h = stats.headline;
  const range = RANGE_WORDS[stats.days] ?? `${stats.days} days`;
  const out: Segment[] = [];
  const push = (text: string, strong = false) => out.push({ text, strong });
  const servers =
    h.servers.length > 2
      ? ' across every server'
      : h.servers.length > 1
        ? ' across both servers'
        : h.servers.length === 1
          ? ` on ${serverWord(h.servers[0])}`
          : '';
  push(`In the last ${range} `);
  push(count(h.people, 'person', 'people'), true);
  push(' played ');
  push(hoursPhrase(h.hours), true);
  push(`${servers}.`);
  const tail: Segment[][] = [];
  if (h.top_kind && h.top_kind !== 'other') {
    tail.push([{ text: `Most of it was ${KIND_WORDS[h.top_kind]}`, strong: true }]);
  }
  if (h.busiest_daypart) {
    const word = h.busiest_daypart === 'night' ? 'late nights' : `${h.busiest_daypart}s`;
    tail.push([{ text: `${word} are busiest` }]);
  }
  if (h.converted_one_in && h.converted_pct) {
    const conv: Segment[] = [
      { text: `1 in ${h.converted_one_in} plays had to be converted`, strong: true },
    ];
    if (h.top_converted_user) {
      const away =
        h.top_converted_user_away_pct != null && h.top_converted_user_away_pct >= 50
          ? ' away from home'
          : '';
      conv.push({ text: `, mostly for ${h.top_converted_user}${away}` });
    }
    tail.push(conv);
  } else if (h.converted_pct === 0) {
    tail.push([{ text: 'every play went out as is', strong: true }]);
  }
  if (tail.length) {
    push(' ');
    tail.forEach((part, i) => {
      if (i > 0) push(i === tail.length - 1 ? ', and ' : ', ');
      part.forEach((seg, j) => {
        // the sentence starts with a capital, the joined parts don't
        const text = i === 0 && j === 0 ? seg.text[0].toUpperCase() + seg.text.slice(1) : seg.text;
        push(text, seg.strong);
      });
    });
    push('.');
  }
  return out;
}

/** "Busiest: Saturday, 9 to 11 pm · quietest: Monday, 4 am" */
export function heatmapNote(stats: StatsPayload): string {
  const hm = stats.heatmap;
  const bits: string[] = [];
  if (hm.busiest) {
    const { weekday, hour_start, hour_end } = hm.busiest;
    const from = hourWords(hour_start);
    const to = hourWords(hour_end);
    // "9 to 11 pm" when both ends share am or pm, "11 am to 1 pm" when not
    const sameHalf =
      hour_start % 12 !== 0 && hour_end % 12 !== 0 && hour_start < 12 === hour_end % 24 < 12;
    const span =
      hour_end - hour_start > 1
        ? `${sameHalf ? from.replace(/ (am|pm)$/, '') : from} to ${to}`
        : from;
    bits.push(`Busiest: ${WEEKDAYS_MON_LONG[weekday]}, ${span}`);
  }
  if (hm.quietest && hm.max > 0) {
    bits.push(
      `quietest: ${WEEKDAYS_MON_LONG[hm.quietest.weekday]}, ${hourWords(hm.quietest.hour)}`,
    );
  }
  return bits.join(' · ');
}

/** "Away from home 80%", "All at home", "Always away" */
export function awayPhrase(pct: number | null): string | null {
  if (pct == null) return null;
  if (pct === 0) return 'All at home';
  if (pct === 100) return 'Always away';
  return `Away from home ${pct}%`;
}

export function browserZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}
