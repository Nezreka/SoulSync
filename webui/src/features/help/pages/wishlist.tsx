/**
 * Wishlist (music): why something keeps retrying, and when the next try is.
 * The page's own reads, all scoped to the signed-in profile by the session
 * (-wishlist.api.ts):
 *   /api/wishlist/stats          is_auto_processing, next_run_in_seconds
 *   /api/wishlist/tracks         retry_count, last_attempted, failure_reason
 *   /api/wishlist/retry-profile  the cooldown ladder failing tracks wait on
 * The failing threshold and the reasons are the page's (-wishlist.types.ts
 * WL_FAILING_ATTEMPTS, -wishlist.helpers.ts troubleKind / describeTrouble).
 *
 * The wishlist is the viewer's own, so a member gets the same answers; the
 * retry is the page's own bulk retry, which the server gates.
 */

import type {
  ParsedWishlistTrack,
  WishlistRetryProfileResponse,
  WishlistStatsResponse,
  WishlistTracksResponse,
} from '@/routes/wishlist/-wishlist.types';

import { bulkWishlistActionChunked } from '@/routes/wishlist/-wishlist.api';
import {
  describeTrouble,
  parseWishlistTrack,
  shortStamp,
  troubleKind,
} from '@/routes/wishlist/-wishlist.helpers';

import { registerPageHelp, type AnswerContext, type PageAnswer } from './index';
import {
  Act,
  Acts,
  Note,
  Why,
  WhyRow,
  getJson,
  goHref,
  plural,
  spoken,
  toast,
  useLive,
} from './kit';

export interface WishlistData {
  stats?: WishlistStatsResponse;
  tracks: ParsedWishlistTrack[];
  retry?: WishlistRetryProfileResponse;
}

/** the wait before a track's next try: its rung on the ladder, else the cap */
export function cooldownFor(retry: number, profile?: WishlistRetryProfileResponse): number | null {
  const p = profile?.profile;
  if (!p) return null;
  const rung = p.ladder?.[String(retry)];
  const secs = typeof rung === 'number' ? rung : p.max_cooldown;
  return typeof secs === 'number' && secs > 0 ? secs : null;
}

const KIND_SAYS = {
  rejected: (n: number) =>
    `${plural(n, 'track')} downloaded, then got thrown out because the file came in bad.`,
  notfound: (n: number) => `${plural(n, 'track')} had no match on any of your sources yet.`,
  failed: (n: number) => `${plural(n, 'track')} found a file, but the download keeps failing.`,
} as const;

export function wishlistAnswers(
  data: WishlistData | undefined,
  _ctx: AnswerContext,
  refresh: () => void,
  now = Date.now(),
): PageAnswer[] {
  if (!data) return [];
  const out: PageAnswer[] = [];
  const { stats, tracks } = data;
  const total = stats?.total ?? tracks.length;

  if (stats?.is_auto_processing) {
    out.push({
      id: 'running',
      title: 'Is the wishlist downloading right now?',
      live: true,
      body: (
        <>
          <p>Yes, a wishlist run is going. Tracks leave the list as they land in your library.</p>
          <Acts>
            <Act kind="secondary" onClick={() => goHref('/active-downloads')}>
              Open Downloads
            </Act>
          </Acts>
        </>
      ),
    });
  } else if (total > 0 && stats?.next_run_in_seconds && stats.next_run_in_seconds > 0) {
    out.push({
      id: 'next',
      title: 'When does the wishlist try again?',
      live: true,
      body: (
        <p>
          In {spoken(stats.next_run_in_seconds)}. Each run goes through your wishlist; a track that
          failed recently waits out its cooldown first.
        </p>
      ),
    });
  }

  const failing = tracks.filter((t) => t.failing).sort((a, b) => b.retry - a.retry);
  if (failing.length) {
    const top = failing[0];
    const counts = { rejected: 0, notfound: 0, failed: 0 };
    for (const t of failing) {
      const kind = troubleKind(t);
      if (kind !== 'hunting') counts[kind] += 1;
    }
    const cooldown = cooldownFor(top.retry, data.retry);
    const label = data.retry?.profile?.label;
    const ids = failing.map((t) => t.id).filter(Boolean);
    out.push({
      id: 'failing',
      title:
        failing.length === 1
          ? `Why does ${top.track} keep failing?`
          : `Why do ${failing.length} tracks keep failing?`,
      live: true,
      body: (
        <>
          {failing.length === 1 ? (
            <p>{describeTrouble(top).detail}</p>
          ) : (
            (['rejected', 'notfound', 'failed'] as const)
              .filter((k) => counts[k])
              .map((k) => <p key={k}>{KIND_SAYS[k](counts[k])}</p>)
          )}
          <Why>
            {failing.slice(0, 3).map((t) => {
              const last = shortStamp(t.lastTried, now);
              return (
                <WhyRow
                  key={t.id || `${t.artist}:${t.track}`}
                  art={t.image}
                  fallback={t.track}
                  title={t.track}
                  sub={[
                    t.artist,
                    plural(t.retry, 'try', 'tries'),
                    last ? `last ${last}` : '',
                    failing.length > 1 ? describeTrouble(t).headline : '',
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                />
              );
            })}
          </Why>
          {cooldown ? (
            <Note>
              Each failure pushes the next try further out.{' '}
              {failing.length === 1 ? 'It' : top.track} waits {spoken(cooldown)} between tries now
              {label ? `, on your ${label} retry profile` : ''}.
            </Note>
          ) : null}
          <Acts>
            {ids.length ? (
              <Act
                kind="primary"
                icon="refresh"
                onClick={async () => {
                  try {
                    const res = await bulkWishlistActionChunked('retry', ids);
                    const results = res.results ?? [];
                    const ok = results.filter((r) => r.ok).length;
                    toast(
                      `Retry: ${ok}/${ids.length} succeeded`,
                      results.length > 0 && ok === results.length ? 'success' : 'info',
                    );
                  } catch (error) {
                    toast(`Error: ${(error as Error).message}`, 'error');
                  }
                  refresh();
                }}
              >
                {failing.length === 1 ? 'Try it now' : 'Try them all now'}
              </Act>
            ) : null}
            {failing.length > 1 ? (
              <Act kind="secondary" onClick={() => goHref('/wishlist?failing=true')}>
                Show only these
              </Act>
            ) : null}
          </Acts>
        </>
      ),
    });
  }

  const upgrades = tracks.filter((t) => t.upgrade);
  if (upgrades.length) {
    out.push({
      id: 'upgrades',
      title:
        upgrades.length === tracks.length
          ? 'Why is the wishlist full of tracks I already have?'
          : 'Why are tracks I already have on here?',
      live: true,
      body: (
        <p>
          {upgrades.length === tracks.length
            ? upgrades.length === 1
              ? "It's an upgrade"
              : "They're upgrades"
            : `${upgrades.length} of ${plural(tracks.length, 'track')} ${upgrades.length === 1 ? 'is an upgrade' : 'are upgrades'}`}
          : the file you have is below what your quality profile asks for, so SoulSync is looking
          for a better copy. The one you have stays until a better one lands.
        </p>
      ),
    });
  }

  if (out.length) out[0] = { ...out[0], open: true };
  return out;
}

function parseAll(res: WishlistTracksResponse | undefined, type: 'album' | 'single') {
  return (res?.tracks ?? [])
    .map((row) => parseWishlistTrack(row, type))
    .filter((t): t is ParsedWishlistTrack => t !== null);
}

async function loadTracks(): Promise<ParsedWishlistTrack[]> {
  const [albums, singles] = await Promise.all([
    getJson<WishlistTracksResponse>('/api/wishlist/tracks?category=albums'),
    getJson<WishlistTracksResponse>('/api/wishlist/tracks?category=singles'),
  ]);
  return [...parseAll(albums, 'album'), ...parseAll(singles, 'single')];
}

/** the page shows audiobooks and the video tabs under the same route */
function onMusic(): boolean {
  const media = new URLSearchParams(window.location.search).get('media');
  return !media || media === 'music';
}

export function useWishlistAnswers(ctx: AnswerContext): PageAnswer[] {
  const music = onMusic();
  const stats = useLive(
    music ? () => getJson<WishlistStatsResponse>('/api/wishlist/stats') : null,
    15_000,
  );
  const tracks = useLive(music ? loadTracks : null, 60_000);
  const retry = useLive(
    music ? () => getJson<WishlistRetryProfileResponse>('/api/wishlist/retry-profile') : null,
    0,
  );
  if (!music || !tracks.loaded) return [];
  return wishlistAnswers(
    { stats: stats.data, tracks: tracks.data ?? [], retry: retry.data },
    ctx,
    () => {
      stats.reload();
      tracks.reload();
    },
  );
}

registerPageHelp({ id: 'wishlist', useAnswers: useWishlistAnswers });
