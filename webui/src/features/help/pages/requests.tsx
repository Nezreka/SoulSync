/**
 * Requests, both sides. The server already scopes the list (admins see
 * everyone's, a member only their own):
 *   music  GET /api/requests/music (api/music_requests.py list_music_requests,
 *          -requests.api.ts fetchMusicRequests): waiting groups, decided
 *          history, music video asks, asks_first, the quota
 *   video  GET /api/video/requests (api/video/requests.py video_request_list):
 *          rows with state + progress (annotate_request_progress), the quota
 *
 * A member hears where their own asks are and why they wait; an admin hears
 * who is waiting on them and what approving does. No answer approves or
 * declines anything: those sheets have choices the page asks for.
 */

import type {
  MusicRequestGroup,
  MusicRequestList,
  MusicRequestRow,
  MusicVideoRequest,
  RequestQuota,
} from '@/routes/requests/-requests.types';

import { fetchMusicRequests } from '@/routes/requests/-requests.api';

import { registerPageHelp, type AnswerContext, type PageAnswer } from './index';
import {
  Act,
  Acts,
  Bar,
  Note,
  Why,
  WhyRow,
  ago,
  currentProfileId,
  getJson,
  goHref,
  names,
  plural,
  pressPage,
  readArticle,
  stampMs,
  useLive,
} from './kit';

const RECENT_DECLINE_MS = 14 * 86_400_000;

function quotaNote(quota: RequestQuota | null) {
  if (!quota) return null;
  return (
    <Note>
      You've used {quota.used} of your {plural(quota.limit, 'request')} in the last{' '}
      {plural(quota.days, 'day')}.
    </Note>
  );
}

function quotaAnswers(quota: RequestQuota | null): PageAnswer[] {
  if (!quota || quota.remaining > 0) return [];
  return [
    {
      id: 'quota',
      title: "Why can't I ask for more?",
      live: true,
      body: (
        <p>
          You've used all {plural(quota.limit, 'request')} for the last {plural(quota.days, 'day')}.
          Each one stops counting {quota.days} days after you made it, so you can ask again as they
          drop off.
        </p>
      ),
    },
  ];
}

const APPROVAL: PageAnswer = {
  id: 'why-approval',
  title: 'Why do my requests need approval?',
  body: <p>Your profile asks first before anything downloads. Only your admin can change that.</p>,
};

// ── music ───────────────────────────────────────────────────────────────

type Waiting =
  | {
      kind: 'group';
      title: string;
      at?: string | null;
      image?: string | null;
      group: MusicRequestGroup;
    }
  | {
      kind: 'video';
      title: string;
      at?: string | null;
      image?: string | null;
      video: MusicVideoRequest;
    };

function waitingOf(list: MusicRequestList): Waiting[] {
  const groups: Waiting[] = list.pending.map((group) => ({
    kind: 'group',
    title: group.title,
    at: group.created_at,
    image: group.image_url,
    group,
  }));
  const videos: Waiting[] = list.pendingVideos
    .filter((v) => v.status === 'pending')
    .map((video) => ({
      kind: 'video',
      title: video.title,
      at: video.created_at,
      image: video.thumbnail_url,
      video,
    }));
  return [...groups, ...videos].sort((a, b) => (stampMs(b.at) ?? 0) - (stampMs(a.at) ?? 0));
}

type Decided = { title: string; status: string; at?: string | null; reply?: string | null };

function decidedOf(list: MusicRequestList): Decided[] {
  const rows: Array<MusicRequestRow | MusicVideoRequest> = [...list.history, ...list.videoHistory];
  return rows
    .map((r) => ({ title: r.title, status: r.status, at: r.resolved_at, reply: r.admin_response }))
    .sort((a, b) => (stampMs(b.at) ?? 0) - (stampMs(a.at) ?? 0));
}

export function musicRequestAnswers(
  list: MusicRequestList | undefined,
  ctx: AnswerContext,
  now = Date.now(),
): PageAnswer[] {
  if (!list) return [];
  const waiting = waitingOf(list);
  const decided = decidedOf(list);
  const out: PageAnswer[] = [];

  if (ctx.isAdmin) {
    if (waiting.length) {
      const askers = [
        ...new Set(
          waiting.map(
            (w) =>
              (w.kind === 'group' ? w.group.requester_name : w.video.requester_name) || 'Someone',
          ),
        ),
      ];
      out.push({
        id: 'admin-waiting',
        title:
          waiting.length === 1
            ? `${askers[0]} is waiting on you`
            : `${plural(waiting.length, 'request')} ${waiting.length === 1 ? 'is' : 'are'} waiting on you`,
        live: true,
        body: (
          <>
            <p>{names(askers)} asked, and nothing downloads until you say yes or no.</p>
            <Why>
              {waiting.slice(0, 3).map((w) => {
                const who =
                  (w.kind === 'group' ? w.group.requester_name : w.video.requester_name) ||
                  'Someone';
                const what =
                  w.kind === 'video'
                    ? 'music video'
                    : w.group.kind === 'album'
                      ? plural(w.group.track_count || w.group.tracks.length, 'track')
                      : 'track';
                const when = ago(w.at, now);
                return (
                  <WhyRow
                    key={
                      w.kind === 'group'
                        ? `g:${w.group.profile_id}:${w.group.key}`
                        : `v:${w.video.id}`
                    }
                    art={w.image}
                    fallback={w.title}
                    title={w.title || 'Untitled'}
                    sub={[who, what, when ? `asked ${when}` : ''].filter(Boolean).join(' · ')}
                  />
                );
              })}
            </Why>
            {waiting.length > 3 ? <Note>And {plural(waiting.length - 3, 'more')}.</Note> : null}
            <Acts>
              <Act kind="primary" onClick={() => goHref('/requests?tab=waiting')}>
                Show waiting
              </Act>
            </Acts>
          </>
        ),
      });
    }
    out.push({
      id: 'admin-approve',
      title: 'What happens when I approve?',
      body: (
        <>
          <p>
            The tracks go on the asker's wishlist and download from there. When they land in the
            library, the request moves to Available and the asker is told.
          </p>
          <Acts>
            <Act kind="ghost" onClick={() => readArticle('prof-requests')}>
              Request, approve, wishlist
            </Act>
          </Acts>
        </>
      ),
    });
    if (out.length) out[0] = { ...out[0], open: true };
    return out;
  }

  // ── member: their own asks ──
  const onTheWay = decided.filter((d) => d.status === 'approved');
  for (const w of waiting.slice(0, 2)) {
    const example = onTheWay[0]?.title;
    out.push({
      id: `wait:${w.kind === 'group' ? w.group.key : w.video.id}`,
      title: `When will ${w.title || 'it'} arrive?`,
      live: true,
      body: (
        <>
          <p>
            It's waiting for your admin to say yes. Once they do, it goes on your wishlist and
            downloads from there{example ? `, like ${example}, which is on its way now` : ''}.
          </p>
          {quotaNote(list.quota)}
        </>
      ),
    });
  }
  for (const d of onTheWay.slice(0, 2)) {
    const when = ago(d.at, now);
    out.push({
      id: `onway:${d.title}`,
      title: `When will ${d.title || 'it'} arrive?`,
      live: true,
      body: (
        <p>
          Your admin said yes{when ? ` ${when}` : ''}. It's on your wishlist now, and SoulSync
          downloads wishlist tracks on its regular runs. You'll get a note when it's in your
          library.
        </p>
      ),
    });
  }
  const declined = decided.find(
    (d) => d.status === 'declined' && now - (stampMs(d.at) ?? 0) < RECENT_DECLINE_MS,
  );
  if (declined) {
    out.push({
      id: 'declined',
      title: `Why was ${declined.title || 'my request'} declined?`,
      live: true,
      body: declined.reply ? (
        <p>
          Your admin said: <q>{declined.reply}</q>
        </p>
      ) : (
        <p>Your admin didn't leave a reason.</p>
      ),
    });
  }
  out.push(...quotaAnswers(list.quota));
  if (list.asksFirst) out.push(APPROVAL);
  if (waiting.length || onTheWay.length) {
    out.push({
      id: 'show-mine',
      title: 'Where do I see all of mine?',
      body: (
        <>
          <p>
            Waiting is what your admin hasn't answered yet; On the way is what they said yes to.
          </p>
          <Acts>
            <Act kind="secondary" onClick={() => goHref('/requests?tab=on-the-way')}>
              Show On the way
            </Act>
          </Acts>
        </>
      ),
    });
  }
  if (out.length) out[0] = { ...out[0], open: true };
  return out;
}

export function useMusicRequestAnswers(ctx: AnswerContext): PageAnswer[] {
  const pid = currentProfileId();
  const list = useLive(() => fetchMusicRequests(pid), 20_000);
  if (!list.loaded) return [];
  return musicRequestAnswers(list.data, ctx);
}

// ── video ───────────────────────────────────────────────────────────────

export interface VideoRequestRow {
  id: number;
  profile_id?: number;
  requester_name?: string | null;
  kind?: string | null;
  title?: string | null;
  year?: number | string | null;
  poster_url?: string | null;
  status?: string | null;
  admin_response?: string | null;
  created_at?: string | null;
  resolved_at?: string | null;
  in_library?: boolean | null;
  available_at?: string | null;
  state?: 'available' | 'partial' | 'failed' | 'on_the_way' | null;
  progress?: { owned?: number; wanted?: number; failed?: number; total?: number } | null;
}

export interface VideoRequestList {
  success?: boolean;
  requests?: VideoRequestRow[];
  quota?: Partial<RequestQuota> | null;
}

/** where an approved row stands, the page's own rule (video-requests.js bucketOf) */
function videoBucket(
  r: VideoRequestRow,
): 'pending' | 'onway' | 'partial' | 'failed' | 'available' | 'denied' {
  if (r.status === 'pending') return 'pending';
  if (r.status === 'denied') return 'denied';
  if (r.state === 'available' || (!r.state && (r.in_library || r.available_at))) return 'available';
  if (r.state === 'partial') return 'partial';
  if (r.state === 'failed') return 'failed';
  return 'onway';
}

function titleOf(r: VideoRequestRow): string {
  return r.title || 'it';
}

function normQuota(raw: Partial<RequestQuota> | null | undefined): RequestQuota | null {
  const limit = Number(raw?.limit) || 0;
  if (limit <= 0) return null;
  const used = Math.max(0, Number(raw?.used) || 0);
  return {
    limit,
    days: Number(raw?.days) || 7,
    used,
    remaining:
      raw?.remaining == null ? Math.max(0, limit - used) : Math.max(0, Number(raw.remaining) || 0),
  };
}

export function videoRequestAnswers(
  data: VideoRequestList | undefined,
  ctx: AnswerContext,
  now = Date.now(),
): PageAnswer[] {
  if (!data) return [];
  const rows = data.requests ?? [];
  const out: PageAnswer[] = [];

  if (ctx.isAdmin) {
    const pending = rows.filter((r) => r.status === 'pending');
    if (pending.length) {
      // one card per title, the way the page groups them
      const byTitle = new Map<string, VideoRequestRow[]>();
      for (const r of pending) {
        const key = `${r.kind}:${r.title}`;
        byTitle.set(key, [...(byTitle.get(key) ?? []), r]);
      }
      const groups = [...byTitle.values()];
      out.push({
        id: 'admin-waiting',
        title:
          groups.length === 1
            ? `${titleOf(groups[0][0])} is waiting on you`
            : `${plural(groups.length, 'request')} are waiting on you`,
        live: true,
        body: (
          <>
            <p>Nothing is searched for until you approve it.</p>
            <Why>
              {groups.slice(0, 3).map((g) => (
                <WhyRow
                  key={g[0].id}
                  art={g[0].poster_url}
                  fallback={g[0].title ?? ''}
                  title={`${g[0].title || 'Untitled'}${g[0].year ? ` (${g[0].year})` : ''}`}
                  sub={[
                    names(g.map((r) => r.requester_name || 'Someone')),
                    ago(g[0].created_at, now) ? `asked ${ago(g[0].created_at, now)}` : '',
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                />
              ))}
            </Why>
            {groups.length > 3 ? <Note>And {plural(groups.length - 3, 'more')}.</Note> : null}
            <Acts>
              <Act kind="primary" onClick={() => void pressPage('[data-vreq-tab="pending"]')}>
                Show waiting
              </Act>
            </Acts>
          </>
        ),
      });
    }
    const stuck = rows.filter((r) => r.status === 'approved' && videoBucket(r) === 'failed');
    if (stuck.length) {
      out.push({
        id: 'admin-failed',
        title:
          stuck.length === 1
            ? `Why couldn't SoulSync find ${titleOf(stuck[0])}?`
            : `Why couldn't SoulSync find ${stuck.length} approved requests?`,
        live: true,
        body: (
          <p>
            {stuck.length === 1 ? 'It was' : 'They were'} approved, but the search came back with
            nothing SoulSync could use. Search again from {stuck.length === 1 ? 'its' : 'each'} card
            here, for example once a better release is out.
          </p>
        ),
      });
    }
    out.push({
      id: 'admin-approve',
      title: 'What happens when I approve?',
      body: (
        <>
          <p>
            A movie goes on the wishlist and a show on the watchlist with the episodes asked for.
            SoulSync searches from there, and the request moves to Available once it's in your
            library.
          </p>
          <Acts>
            <Act kind="ghost" onClick={() => readArticle('vreq-flow')}>
              How requests work
            </Act>
          </Acts>
        </>
      ),
    });
    if (out.length) out[0] = { ...out[0], open: true };
    return out;
  }

  // ── member ──
  const quota = normQuota(data.quota);
  const onway = rows.filter((r) => r.status === 'approved' && videoBucket(r) === 'onway');
  const pending = rows.filter((r) => r.status === 'pending');
  for (const r of pending.slice(0, 2)) {
    const example = onway.find((o) => o.id !== r.id);
    out.push({
      id: `wait:${r.id}`,
      title: `When will ${titleOf(r)} arrive?`,
      live: true,
      body: (
        <>
          <p>
            It's waiting for your admin to approve it. Once they do, SoulSync starts looking for it
            {example ? `, like ${titleOf(example)}, which is on its way now` : ''}.
          </p>
          {quotaNote(quota)}
        </>
      ),
    });
  }
  for (const r of rows.filter((x) => x.status === 'approved').slice(0, 3)) {
    const bucket = videoBucket(r);
    const p = r.progress ?? {};
    if (bucket === 'onway') {
      out.push({
        id: `onway:${r.id}`,
        title: `When will ${titleOf(r)} arrive?`,
        live: true,
        body: (
          <p>
            Your admin said yes{ago(r.resolved_at, now) ? ` ${ago(r.resolved_at, now)}` : ''}, and
            SoulSync is looking for it. It shows as In your library here once it lands.
          </p>
        ),
      });
    } else if (bucket === 'partial') {
      const owned = Number(p.owned) || 0;
      const total = Number(p.total) || 0;
      const wanted = Number(p.wanted) || 0;
      const failed = Number(p.failed) || 0;
      out.push({
        id: `partial:${r.id}`,
        title: `Why is only part of ${titleOf(r)} here?`,
        live: true,
        body: (
          <>
            <p>
              {total
                ? `${owned} of ${plural(total, 'episode')} ${owned === 1 ? 'is' : 'are'} in your library. `
                : ''}
              {wanted
                ? `${plural(wanted, 'episode')} ${wanted === 1 ? 'is' : 'are'} still being looked for. `
                : ''}
              {failed ? `${plural(failed, 'episode')} couldn't be found yet.` : ''}
            </p>
            {total ? (
              <Bar pct={(owned / total) * 100} label={`${owned} of ${total} episodes`} />
            ) : null}
          </>
        ),
      });
    } else if (bucket === 'failed') {
      out.push({
        id: `failed:${r.id}`,
        title: `Why couldn't SoulSync find ${titleOf(r)}?`,
        live: true,
        body: (
          <p>
            SoulSync looked, but nothing it found could be used. Your admin can search again, for
            example once a better release is out.
          </p>
        ),
      });
    }
  }
  const declined = rows
    .filter((r) => r.status === 'denied' && now - (stampMs(r.resolved_at) ?? 0) < RECENT_DECLINE_MS)
    .sort((a, b) => (stampMs(b.resolved_at) ?? 0) - (stampMs(a.resolved_at) ?? 0))[0];
  if (declined) {
    out.push({
      id: 'declined',
      title: `Why was ${titleOf(declined)} declined?`,
      live: true,
      body: declined.admin_response ? (
        <p>
          Your admin said: <q>{declined.admin_response}</q>
        </p>
      ) : (
        <p>Your admin didn't leave a reason.</p>
      ),
    });
  }
  out.push(...quotaAnswers(quota));
  // the video Requests page only shows for a profile that asks first (profile-nav.js)
  out.push(APPROVAL);
  if (out.length) out[0] = { ...out[0], open: true };
  return out;
}

export function useVideoRequestAnswers(ctx: AnswerContext): PageAnswer[] {
  const data = useLive(() => getJson<VideoRequestList>('/api/video/requests'), 20_000);
  if (!data.loaded) return [];
  return videoRequestAnswers(data.data, ctx);
}

registerPageHelp({ id: 'requests', useAnswers: useMusicRequestAnswers });
registerPageHelp({ id: 'video-requests', useAnswers: useVideoRequestAnswers });
