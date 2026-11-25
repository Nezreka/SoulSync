/**
 * what this profile may do, in plain data (#2166, piece F).
 *
 * the server's answer is GET /api/profiles/me/permissions: the member's
 * effective map and a plain-words summary. this file holds only pure
 * functions over that answer, so the shell bundle (which keeps the one live
 * copy, src/shell/my-permissions.ts) and the react bundle (which reads it
 * through window, ./my-permissions.ts) agree without sharing state.
 *
 * before the server answers, or when it can't, the answer is built from the
 * profile's old columns (canDownload in init.js). that's what the server
 * itself falls back to, so nothing flips when the real map lands.
 */

export type PermMode = 'none' | 'ask' | 'download';
export type PermMedia = 'music' | 'movies' | 'tv' | 'books' | 'podcasts';

export interface PermSummaryLine {
  category: string;
  text: string;
}

export interface PermSummary {
  sentence: string;
  lines: PermSummaryLine[];
  never: string[];
}

export interface RequestLimit {
  /** asks per window; 0 = no limit */
  n: number;
  days: number;
}

/** music, movies and TV each have their own request limit */
export type LimitKind = 'music' | 'movies' | 'tv';
export const LIMIT_KINDS: readonly LimitKind[] = ['music', 'movies', 'tv'];

/** "music asks", "movie asks", "TV asks" */
const LIMIT_NOUN: Record<LimitKind, string> = { music: 'music', movies: 'movie', tv: 'TV' };

export function askNoun(n: number, kind?: LimitKind | null): string {
  const base = n === 1 ? 'ask' : 'asks';
  return kind ? `${LIMIT_NOUN[kind]} ${base}` : base;
}

/** "5 asks a week", "1 ask a day", "3 asks every 10 days" */
export function limitRate(limit: RequestLimit, kind?: LimitKind | null): string {
  const span =
    limit.days === 1
      ? 'a day'
      : limit.days === 7
        ? 'a week'
        : limit.days === 30
          ? 'a month'
          : `every ${limit.days} days`;
  return `${limit.n} ${askNoun(limit.n, kind)} ${span}`;
}

export interface MyPermissions {
  profileId: number | null;
  isAdmin: boolean;
  /** admin | template | legacy, or "columns" while built from the old columns */
  source: string;
  effective: Record<string, unknown>;
  summary: PermSummary | null;
  /** true once the server's map is in. false = read from the profile columns */
  live: boolean;
}

export interface MePermissionsResponse {
  success?: boolean;
  profile_id?: number;
  is_admin?: boolean;
  source?: string;
  effective?: Record<string, unknown>;
  summary?: Partial<PermSummary> | null;
  error?: string;
}

const ACCESS_ROW: Record<PermMedia, string> = {
  music: 'music.access',
  movies: 'movies.access',
  tv: 'tv.access',
  books: 'books.access',
  podcasts: 'podcasts.access',
};

const MODES: PermMode[] = ['none', 'ask', 'download'];

function asMode(value: unknown): PermMode | null {
  return typeof value === 'string' && (MODES as string[]).includes(value)
    ? (value as PermMode)
    : null;
}

/** what the old columns say, for the moments before the server answers. books
 *  and podcasts follow the one can_download column today, and they have no
 *  Ask first, so a profile without downloads can't get them at all. */
export function fallbackPermissions(opts: {
  profileId: number | null;
  isAdmin: boolean;
  canDownload: boolean;
}): MyPermissions {
  const get: PermMode = opts.isAdmin || opts.canDownload ? 'download' : 'ask';
  const gets: PermMode = opts.isAdmin || opts.canDownload ? 'download' : 'none';
  return {
    profileId: opts.profileId,
    isAdmin: opts.isAdmin,
    source: opts.isAdmin ? 'admin' : 'columns',
    effective: {
      'music.access': true,
      'movies.access': true,
      'tv.access': true,
      'books.access': true,
      'podcasts.access': true,
      'music.get': get,
      'movies.get': get,
      'tv.get': get,
      'books.get': gets,
      'podcasts.get': gets,
      'account.connect': true,
      'requests.see_all': opts.isAdmin,
      'requests.moderate': opts.isAdmin,
    },
    summary: null,
    live: false,
  };
}

/** the server's answer, or null when it isn't one we can trust */
export function fromResponse(body: MePermissionsResponse | null): MyPermissions | null {
  if (!body || body.success !== true || !body.effective || typeof body.effective !== 'object') {
    return null;
  }
  const summary = body.summary
    ? {
        sentence: String(body.summary.sentence || ''),
        lines: Array.isArray(body.summary.lines) ? body.summary.lines : [],
        never: Array.isArray(body.summary.never) ? body.summary.never.map(String) : [],
      }
    : null;
  return {
    profileId: typeof body.profile_id === 'number' ? body.profile_id : null,
    isAdmin: body.is_admin === true,
    source: String(body.source || ''),
    effective: body.effective,
    summary,
    live: true,
  };
}

/** download, ask or none for one kind of media. a side that's off is none,
 *  whatever its mode says. admins always download. */
export function modeFor(perms: MyPermissions | null | undefined, media: PermMedia): PermMode {
  if (!perms) return 'download';
  if (perms.isAdmin) return 'download';
  const eff = perms.effective;
  if (eff[ACCESS_ROW[media]] === false) return 'none';
  // books and podcasts ask first too now (#2188): an ask there files a
  // request on the Requests page, like music
  return asMode(eff[`${media}.get`]) ?? 'none';
}

/** one yes/no row. admins have everything; an unknown row is a no. */
export function isOn(perms: MyPermissions | null | undefined, id: string): boolean {
  if (!perms) return false;
  if (perms.isAdmin) return true;
  return perms.effective[id] === true;
}

/** a row the server hasn't turned off: true until the answer says false.
 *  for hiding a control a member can't use without hiding it for everyone
 *  in the moment before the map arrives (the fallback has no page rows). */
export function notOff(perms: MyPermissions | null | undefined, id: string): boolean {
  if (!perms || perms.isAdmin) return true;
  return perms.effective[id] !== false;
}

/** music videos for this profile: the Videos part on, and explicit music not
 *  hidden (videos carry no explicit flag, so the server refuses them to a
 *  hide-explicit profile). admins and the moment before the map: yes. */
export function musicVideosAllowed(perms: MyPermissions | null | undefined): boolean {
  if (!perms || perms.isAdmin) return true;
  return (
    perms.effective['music.videos'] !== false && perms.effective['music.hide_explicit'] !== true
  );
}

export function hasAccess(perms: MyPermissions | null | undefined, media: PermMedia): boolean {
  if (!perms || perms.isAdmin) return true;
  return perms.effective[ACCESS_ROW[media]] !== false;
}

/** one kind's ask limit, or null when there is none (or it's an admin).
 * an older map with the one shared requests.limit row reads that */
export function requestLimit(
  perms: MyPermissions | null | undefined,
  kind: LimitKind = 'music',
): RequestLimit | null {
  if (!perms || perms.isAdmin) return null;
  const eff = perms.effective;
  const raw = (eff[`${kind}.request_limit`] ?? eff['requests.limit']) as
    | Partial<RequestLimit>
    | undefined;
  const n = Number(raw?.n) || 0;
  if (n <= 0) return null;
  return { n, days: Number(raw?.days) || 7 };
}

/** the kind an ask counts toward: a youtube video rides with TV, or with
 * movies for a profile that has no TV (the server's ask_limit_kind) */
export function limitKindFor(
  perms: MyPermissions | null | undefined,
  kind: string | null | undefined,
): LimitKind {
  const k = String(kind || '').toLowerCase();
  if (k === 'movie' || k === 'movies') return 'movies';
  if (k === 'youtube')
    return hasAccess(perms, 'tv') || !hasAccess(perms, 'movies') ? 'tv' : 'movies';
  if (['show', 'tv', 'season', 'episode', 'series'].includes(k)) return 'tv';
  return 'music';
}

/** a moderator answers other people's requests (never their own) */
export function isModerator(perms: MyPermissions | null | undefined): boolean {
  return isOn(perms, 'requests.moderate');
}

export function seesAllRequests(perms: MyPermissions | null | undefined): boolean {
  return isOn(perms, 'requests.see_all') || isModerator(perms);
}

/** "this week", "today", "in 30 days" */
export function limitSpan(days: number): string {
  if (days === 1) return 'today';
  if (days === 7) return 'this week';
  if (days === 30) return 'this month';
  return `in the last ${days} days`;
}

/** "3 of 5 movie asks left this week", "No music asks left this week", or ''.
 * the kind comes from the argument or the server's quota.kind */
export function asksLeftLine(
  quota:
    | { limit: number; remaining: number; days: number; kind?: string | null }
    | null
    | undefined,
  kind?: LimitKind | null,
): string {
  if (!quota || !(quota.limit > 0)) return '';
  const k =
    kind ?? (LIMIT_KINDS.includes(quota.kind as LimitKind) ? (quota.kind as LimitKind) : null);
  const span = limitSpan(quota.days);
  if (quota.remaining <= 0) return `No ${askNoun(2, k)} left ${span}`;
  return `${quota.remaining} of ${quota.limit} ${askNoun(quota.limit, k)} left ${span}`;
}

/** the server said no anyway (a race with an admin's change): its own words,
 *  or null when the body isn't a permission refusal */
export function refusalMessage(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const b = body as { error?: unknown; message?: unknown };
  if (b.error !== 'not_allowed') return null;
  return typeof b.message === 'string' && b.message
    ? b.message
    : "That isn't part of your profile. Ask an admin if you'd like it.";
}

/** the calm line when a whole kind of thing is off: "Audiobooks aren't part of
 *  your profile" */
export const OFF_TITLES: Record<PermMedia, string> = {
  music: 'Getting music isn’t part of your profile',
  movies: 'Movies aren’t part of your profile',
  tv: 'TV shows aren’t part of your profile',
  books: 'Audiobooks aren’t part of your profile',
  podcasts: 'Podcasts aren’t part of your profile',
};

/** the same when the side is there but getting things from it isn't */
export const GET_OFF_TITLES: Record<PermMedia, string> = {
  music: 'Getting music isn’t part of your profile',
  movies: 'Getting movies isn’t part of your profile',
  tv: 'Getting shows isn’t part of your profile',
  books: 'Getting books isn’t part of your profile',
  podcasts: 'Downloading episodes isn’t part of your profile',
};

/** the right calm title for what's off: the whole side, or only getting */
export function offTitle(perms: MyPermissions | null | undefined, media: PermMedia): string {
  return hasAccess(perms, media) ? GET_OFF_TITLES[media] : OFF_TITLES[media];
}

/** a stable fingerprint, so a poll that brings the same map changes nothing */
export function fingerprint(perms: MyPermissions | null): string {
  if (!perms) return '';
  return JSON.stringify([perms.profileId, perms.isAdmin, perms.live, perms.effective]);
}

const SIDE_NAMES: [PermMedia, string][] = [
  ['music', 'Music'],
  ['movies', 'movies'],
  ['tv', 'TV shows'],
  ['books', 'audiobooks'],
  ['podcasts', 'podcasts'],
];

/** "Music and movies are all still yours." for the sides still on, leaving
 *  out the one that's off. '' when nothing else is on (or for an admin). */
export function stillYours(perms: MyPermissions | null | undefined, except: PermMedia): string {
  if (!perms || perms.isAdmin) return '';
  const on = SIDE_NAMES.filter(([m]) => m !== except && hasAccess(perms, m)).map(([, n]) => n);
  if (!on.length) return '';
  const first = on[0].charAt(0).toUpperCase() + on[0].slice(1);
  const names = [first, ...on.slice(1)];
  if (names.length === 1) return `${names[0]} is still yours.`;
  const list = `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
  return `${list} are all still yours.`;
}

// ── "What you can do" (Profile & account) ─────────────────────────────────

export type CanDoId = 'music' | 'movies' | 'tv' | 'books' | 'requests';

export interface CanDoRow {
  id: CanDoId;
  label: string;
  /** the headline: "Downloads right away", "Asks first", "Not part of your profile" */
  lead: string;
  /** the small print after it: "explicit shown", "up to PG-13" */
  detail: string[];
  /** the lead is the good news (green), not the limit */
  strong: boolean;
  off: boolean;
}

export interface CanDo {
  sentence: string;
  rows: CanDoRow[];
  never: string[];
  /** something is switched off for them: the card says who to ask */
  anyOff: boolean;
}

const TV_LABELS: Record<string, string> = {
  G: 'TV-Y7',
  PG: 'TV-PG',
  'PG-13': 'TV-14',
  R: 'TV-MA',
};

const PART_LABELS: [string, string][] = [
  ['music.discover', 'Discover'],
  ['music.playlist_sync', 'Playlist sync'],
  ['music.explorer', 'Playlist Explorer'],
  ['music.import', 'Import'],
  ['music.stats', 'Listening stats'],
  ['music.watchlist', 'the music watchlist'],
  ['music.videos', 'Music videos'],
  ['music.sample_studio', 'Sample Studio'],
  ['music.apps', 'music apps'],
];

const VIDEO_PART_LABELS: [string, string][] = [
  ['video.discover', 'movie and TV discover'],
  ['video.calendar', 'the calendar'],
  ['video.youtube', 'YouTube channels'],
];

const OTHER_LABELS: [string, string][] = [
  ['chat.use', 'Chat & Arcade'],
  ['account.connect', 'connecting your own accounts'],
  ['automations.personal', 'your own automations'],
];

function joinWords(words: string[]): string {
  if (words.length <= 1) return words.join('');
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

function ratingWords(perms: MyPermissions, media: 'movies' | 'tv'): string {
  const eff = perms.effective;
  const movies = typeof eff['movies.max_rating'] === 'string' ? eff['movies.max_rating'] : 'any';
  if (media === 'movies') return movies && movies !== 'any' ? `up to ${movies}` : '';
  let tv = typeof eff['tv.max_rating'] === 'string' ? eff['tv.max_rating'] : 'follow';
  if (tv === 'follow') tv = movies;
  return tv && tv !== 'any' ? `up to ${TV_LABELS[tv as string] ?? tv}` : '';
}

/**
 * the member's own "what can I do", in their words, from the map itself so
 * it is the truth. read-only: Profile & account shows it, nothing edits it.
 */
export function describePermissions(
  perms: MyPermissions,
  opts: {
    used?: Partial<Record<LimitKind, number | null>> | null;
    /**
     * written for the member reading their own account (#2303): what they
     * can do, in everyday words, without the things they can't (a list of
     * switched-off features and "settings and people" read like a policy
     * dump). the admin's editor keeps the full account, limits and all.
     */
    forMember?: boolean;
  } = {},
): CanDo {
  const me = opts.forMember === true;
  if (perms.isAdmin) {
    return {
      sentence: 'You’re an admin: everything here is yours, including settings and people.',
      rows: [],
      never: [],
      anyOff: false,
    };
  }
  const eff = perms.effective;
  const rows: CanDoRow[] = [];
  const gets: string[] = [];
  const asks: string[] = [];
  const offRow = (id: CanDoId, label: string): CanDoRow => ({
    id,
    label,
    lead: 'Not part of your profile',
    detail: [],
    strong: false,
    off: true,
  });
  const modeLead = (mode: PermMode, none: string) =>
    me
      ? mode === 'download'
        ? 'Add anything you like'
        : mode === 'ask'
          ? 'Ask for it and an admin approves'
          : none
      : mode === 'download'
        ? 'Downloads right away'
        : mode === 'ask'
          ? 'Asks first'
          : none;
  // each kind's own limit, on its own row: "5 asks a week, 2 used"
  const limits: { kind: LimitKind; limit: RequestLimit }[] = [];
  const limitDetail = (kind: LimitKind, mode: PermMode): string[] => {
    const limit = mode === 'ask' ? requestLimit(perms, kind) : null;
    if (!limit) return [];
    limits.push({ kind, limit });
    const used = opts.used?.[kind];
    return [limitRate(limit) + (used != null ? `, ${Math.max(0, used)} used` : '')];
  };

  // music
  if (hasAccess(perms, 'music')) {
    const mode = modeFor(perms, 'music');
    const clean = eff['music.hide_explicit'] === true;
    const detail = [
      // a member is told a limit, not the absence of one
      ...(clean ? ['clean versions only'] : me ? [] : ['explicit shown']),
      ...limitDetail('music', mode),
    ];
    rows.push({
      id: 'music',
      label: 'Music',
      lead: modeLead(mode, 'Listen only'),
      detail,
      strong: mode === 'download',
      off: false,
    });
    if (mode === 'download') gets.push('music');
    else if (mode === 'ask') asks.push('music');
  } else if (!me) rows.push(offRow('music', 'Music'));

  // movies, tv
  for (const [media, label, noun] of [
    ['movies', 'Movies', 'movies'],
    ['tv', 'TV shows', 'shows'],
  ] as const) {
    if (!hasAccess(perms, media)) {
      if (!me) rows.push(offRow(media, label));
      continue;
    }
    const mode = modeFor(perms, media);
    const rating = ratingWords(perms, media);
    rows.push({
      id: media,
      label,
      lead: modeLead(mode, 'Watch only'),
      detail: [...(rating ? [rating] : []), ...limitDetail(media, mode)],
      strong: mode === 'download',
      off: false,
    });
    if (mode === 'download') gets.push(noun);
    else if (mode === 'ask') asks.push(noun);
  }

  // books and podcasts share a row, like the editor's rail
  const books = hasAccess(perms, 'books');
  const pods = hasAccess(perms, 'podcasts');
  if (!books && !pods) {
    if (!me) rows.push(offRow('books', 'Books & podcasts'));
  } else {
    const bits: string[] = [];
    if (books) {
      const m = modeFor(perms, 'books');
      bits.push(
        me
          ? m === 'download'
            ? 'add any audiobook'
            : m === 'ask'
              ? 'ask for audiobooks'
              : 'browse audiobooks'
          : m === 'download'
            ? 'audiobooks right away'
            : m === 'ask'
              ? 'audiobooks on request'
              : 'audiobooks to browse',
      );
      if (m === 'download') gets.push('audiobooks');
      else if (m === 'ask') asks.push('audiobooks');
    }
    if (pods) {
      const m = modeFor(perms, 'podcasts');
      bits.push(
        me
          ? m === 'download'
            ? 'follow podcasts and keep episodes'
            : m === 'ask'
              ? 'ask for podcast episodes'
              : 'follow and play podcasts'
          : m === 'download'
            ? 'podcasts download'
            : m === 'ask'
              ? 'podcast episodes on request'
              : 'podcasts to follow and play',
      );
      if (m === 'download') gets.push('podcast episodes');
      else if (m === 'ask') asks.push('podcast episodes');
    }
    const detail: string[] = [];
    if (!me) {
      if (!books) detail.push('no audiobooks');
      if (!pods) detail.push('no podcasts');
    }
    const lead = bits.join(', ');
    rows.push({
      id: 'books',
      label: me
        ? books && pods
          ? 'Books & podcasts'
          : books
            ? 'Audiobooks'
            : 'Podcasts'
        : 'Books & podcasts',
      lead: lead.charAt(0).toUpperCase() + lead.slice(1),
      detail,
      strong: bits.some(
        (b) =>
          b.endsWith('right away') ||
          b.endsWith('download') ||
          b.startsWith('add any') ||
          b.endsWith('keep episodes'),
      ),
      off: false,
    });
  }

  // requests: each kind's limit is on its own row above; this one names them
  // one limit for every kind that asks reads once: "5 asks a week each"
  const same =
    limits.length > 1 &&
    limits.length === asks.length &&
    limits.every((l) => l.limit.n === limits[0].limit.n && l.limit.days === limits[0].limit.days);
  const limitWords = same
    ? `${limitRate(limits[0].limit)} each`
    : joinWords(limits.map((l) => limitRate(l.limit, l.kind)));
  const moderator = isModerator(perms);
  const seeAll = seesAllRequests(perms);
  const reqDetail: string[] = [];
  if (moderator) reqDetail.push('you answer everyone’s asks but your own');
  else if (seeAll) reqDetail.push('you see everyone’s');
  else if (asks.length && !me) reqDetail.push('you see only your own');
  rows.push({
    id: 'requests',
    label: moderator ? 'Requests · moderator' : 'Requests',
    lead: asks.length
      ? limitWords || (me ? 'Ask for as much as you like' : 'No limit on asks')
      : gets.length
        ? me
          ? 'Nothing to wait for: what you add comes right away'
          : 'Nothing to ask: it comes right away'
        : 'Nothing to ask for',
    detail: reqDetail,
    strong: false,
    off: false,
  });

  // the sentence
  const parts: string[] = [];
  if (gets.length) {
    parts.push(
      me ? `Add ${joinWords(gets)} whenever you like.` : `You get ${joinWords(gets)} right away.`,
    );
  }
  if (asks.length) {
    parts.push(
      me
        ? `You can ask for ${joinWords(asks)}${limitWords ? `, ${limitWords}` : ''}, and an admin approves it.`
        : `You ask first for ${joinWords(asks)}${limitWords ? `, ${limitWords}` : ''}.`,
    );
  }
  if (!parts.length) parts.push('You can browse and play what’s here.');

  // never: what's off that they might look for
  const never: string[] = [];
  if (hasAccess(perms, 'music')) {
    for (const [id, label] of PART_LABELS) if (eff[id] === false) never.push(label);
  }
  if (hasAccess(perms, 'movies') || hasAccess(perms, 'tv')) {
    for (const [id, label] of VIDEO_PART_LABELS) if (eff[id] === false) never.push(label);
  }
  for (const [id, label] of OTHER_LABELS) if (eff[id] === false) never.push(label);
  const anyOff = never.length > 0 || rows.some((r) => r.off);
  // for the member, ``never`` is what they could ask for: the admin's own
  // places are no part of that
  if (!me) never.push('settings and people');

  return { sentence: parts.join(' '), rows, never, anyOff };
}

let adminNamePromise: Promise<string> | null = null;

/** the admin's name for "Ask Boulder", once per page. '' when we can't tell,
 *  and the copy says "your admin" */
export function loadAdminName(): Promise<string> {
  if (!adminNamePromise) {
    adminNamePromise = fetch('/api/profiles', { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { profiles?: { id?: number; name?: string; is_admin?: boolean }[] } | null) => {
        const profiles = d?.profiles ?? [];
        const admin = profiles.find((p) => p.id === 1) ?? profiles.find((p) => p.is_admin);
        return String(admin?.name ?? '');
      })
      .catch(() => '');
  }
  return adminNamePromise;
}

/** tests only */
export function _resetAdminName(): void {
  adminNamePromise = null;
}
