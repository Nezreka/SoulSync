import type {
  MusicRequestList,
  MusicRequestRow,
  MusicRequestStatus,
  MusicVideoRequest,
  RequestItem,
  RequestQuota,
  RequestTab,
} from './-requests.types';

export const REQUEST_TABS: ReadonlyArray<{ id: RequestTab; label: string }> = [
  { id: 'waiting', label: 'Waiting' },
  { id: 'on-the-way', label: 'On the way' },
  { id: 'available', label: 'Available' },
  { id: 'declined', label: 'Declined' },
  { id: 'all', label: 'All' },
];

function isVideoRow(row: MusicRequestRow | MusicVideoRequest): row is MusicVideoRequest {
  return 'video_id' in row;
}

// waiting asks first, then the history newest first. videos merge into the
// same two blocks: track groups keep their server order, video pendings sort
// newest-first after them, and both histories interleave by resolved_at.
export function buildRequestItems(list: MusicRequestList): RequestItem[] {
  const pending: RequestItem[] = list.pending.map((group) => ({
    source: 'pending',
    status: 'pending',
    group,
  }));
  // videos have no wishlist to derive a pending queue from, so pending video
  // asks arrive as stored rows
  const pendingVideos: RequestItem[] = [...list.pendingVideos]
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
    .map(
      (video): RequestItem =>
        video.status === 'pending'
          ? { source: 'video-pending', status: 'pending', video }
          : { source: 'video-history', status: video.status, video },
    );
  const history: RequestItem[] = [...list.history, ...list.videoHistory]
    .sort((a, b) => String(b.resolved_at || '').localeCompare(String(a.resolved_at || '')))
    .map((row): RequestItem => {
      if (isVideoRow(row)) {
        return row.status === 'pending'
          ? { source: 'video-pending', status: 'pending', video: row }
          : { source: 'video-history', status: row.status, video: row };
      }
      return { source: 'history', status: row.status, row };
    });
  return [...pending, ...pendingVideos, ...history];
}

const TAB_STATUSES: Record<Exclude<RequestTab, 'all'>, MusicRequestStatus> = {
  waiting: 'pending',
  'on-the-way': 'approved',
  available: 'available',
  declined: 'declined',
};

// videos share the same status strings, so they land in the right tabs as-is
export function filterRequestItems(items: RequestItem[], tab: RequestTab): RequestItem[] {
  if (tab === 'all') return items;
  const status = TAB_STATUSES[tab];
  return items.filter((item) => item.status === status);
}

export function countForTab(items: RequestItem[], tab: RequestTab): number {
  return filterRequestItems(items, tab).length;
}

/** "Waiting · 3" for the tabs that are worth a number, plain label otherwise. */
export function tabLabel(tab: { id: RequestTab; label: string }, count: number): string {
  if ((tab.id === 'waiting' || tab.id === 'on-the-way') && count > 0) {
    return `${tab.label} · ${count}`;
  }
  return tab.label;
}

export function statusText(item: RequestItem): string {
  switch (item.status) {
    case 'pending':
      return 'Waiting';
    case 'approved':
      return 'On the way';
    case 'available':
      return 'In your library';
    case 'declined':
      return 'Declined';
    case 'removed':
      return 'Removed before it arrived';
  }
}

export function itemKey(item: RequestItem): string {
  if (item.source === 'video-pending' || item.source === 'video-history') {
    return `v:${item.video.id}`;
  }
  return item.source === 'pending'
    ? `p:${item.group.profile_id}:${item.group.key}`
    : `h:${item.row.id}`;
}

export function itemTitle(item: RequestItem): string {
  if (item.source === 'video-pending' || item.source === 'video-history') {
    return item.video.title || 'Untitled';
  }
  return (item.source === 'pending' ? item.group.title : item.row.title) || 'Untitled';
}

export function itemKind(item: RequestItem): 'album' | 'track' | 'video' {
  if (item.source === 'video-pending' || item.source === 'video-history') return 'video';
  return item.source === 'pending' ? item.group.kind : item.row.kind;
}

export function itemImage(item: RequestItem): string {
  if (item.source === 'video-pending' || item.source === 'video-history') {
    return item.video.thumbnail_url || '';
  }
  return (item.source === 'pending' ? item.group.image_url : item.row.image_url) || '';
}

export function itemTracks(item: RequestItem) {
  if (item.source === 'video-pending' || item.source === 'video-history') return [];
  return item.source === 'pending' ? item.group.tracks : item.row.tracks;
}

/**
 * artist · 12 tracks for albums, artist · album for a lone track,
 * channel · Music video for videos.
 */
export function subLine(item: RequestItem): string {
  if (item.source === 'video-pending' || item.source === 'video-history') {
    const channel = item.video.channel;
    return channel ? `${channel} · Music video` : 'Music video';
  }
  const artist = item.source === 'pending' ? item.group.artist : item.row.artist;
  const count =
    item.source === 'pending'
      ? item.group.track_count || item.group.tracks.length
      : item.row.track_count || item.row.tracks.length;
  const bits: string[] = [];
  if (artist) bits.push(artist);
  if (itemKind(item) === 'album') {
    if (count) bits.push(`${count} track${count === 1 ? '' : 's'}`);
  } else if (
    item.source === 'pending' &&
    item.group.album &&
    item.group.album !== item.group.title
  ) {
    bits.push(item.group.album);
  }
  return bits.join(' · ');
}

/** "Kim asked · 2 days ago" (admins), "You asked · 2 days ago" (members). */
export function whoAsked(item: RequestItem, isAdmin: boolean, now = Date.now()): string {
  const name =
    item.source === 'pending'
      ? item.group.requester_name
      : item.source === 'history'
        ? item.row.requester_name
        : item.video.requester_name;
  const who = isAdmin ? name || 'Someone' : 'You';
  const when =
    item.source === 'pending'
      ? item.group.created_at
      : item.source === 'history'
        ? item.row.resolved_at
        : item.source === 'video-pending'
          ? item.video.created_at
          : item.video.resolved_at;
  const ago = relativeTime(when, now);
  if (item.source === 'pending' || item.source === 'video-pending') {
    return ago ? `${who} asked · ${ago}` : `${who} asked`;
  }
  return ago ? `${who} asked · decided ${ago}` : `${who} asked`;
}

// sqlite hands back "YYYY-MM-DD HH:MM:SS" in utc with no zone
export function parseServerTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const text = String(value).trim();
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(text) ? text : `${text.replace(' ', 'T')}Z`;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

export function relativeTime(value: string | null | undefined, now = Date.now()): string {
  const ms = parseServerTime(value);
  if (ms == null) return '';
  const secs = Math.max(0, Math.round((now - ms) / 1000));
  if (secs < 60) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? '' : 's'} ago`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months} month${months === 1 ? '' : 's'} ago`;
  const years = Math.round(months / 12);
  return `${years} year${years === 1 ? '' : 's'} ago`;
}

export function emptyText(tab: RequestTab, isAdmin: boolean, asksFirst: boolean): string {
  if (tab === 'waiting') {
    if (isAdmin) return 'Nothing waiting. When someone asks for music, it lands here.';
    return asksFirst
      ? 'Nothing waiting. Add an album or track to your wishlist and it comes here as a request.'
      : 'Nothing waiting.';
  }
  if (tab === 'on-the-way') return 'Nothing on the way right now.';
  if (tab === 'available') return 'Nothing has arrived yet.';
  if (tab === 'declined') return 'Nothing declined.';
  return isAdmin ? 'No requests yet.' : 'You haven’t asked for anything yet.';
}

function quotaSpan(days: number): string {
  if (days === 1) return 'today';
  if (days === 7) return 'this week';
  if (days === 30) return 'this month';
  return `in the last ${days} days`;
}

/** "2 of 3 requests left this week", or that they're used up. '' with no limit. */
export function quotaLine(quota: RequestQuota | null | undefined): string {
  if (!quota || quota.limit <= 0) return '';
  const noun = quota.limit === 1 ? 'request' : 'requests';
  const span = quotaSpan(quota.days);
  if (quota.remaining <= 0) {
    return quota.limit === 1
      ? `You’ve used your request ${span}`
      : `You’ve used all ${quota.limit} requests ${span}`;
  }
  return `${quota.remaining} of ${quota.limit} ${noun} left ${span}`;
}
