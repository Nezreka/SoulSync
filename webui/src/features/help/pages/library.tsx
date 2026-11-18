/**
 * Library, both sides.
 *
 * Music: a scan running or the last one failing (/api/database/update/status,
 * the dashboard's scan poll, api/database_admin.py), tracks filed under
 * Unknown Artist (/api/library/unmatched-summary, the page's own banner read),
 * and the media server not answering (/status for everyone; Health with its
 * fixes for the admin).
 *
 * Video: the library folders and drives, from Health's video checks
 * (core/help/health.py check_video), admin only.
 *
 * A member hears what the scan is doing and whether the server answers; the
 * Unknown Artist review is the admin's.
 */

import type { LibraryV2UnmatchedSummary } from '@/routes/library/-library-v2.api';

import type { HealthFinding } from '../api';

import { registerPageHelp, type AnswerContext, type PageAnswer } from './index';
import {
  Act,
  Acts,
  Bar,
  findingAnswers,
  getJson,
  plural,
  pressPage,
  readArticle,
  sentence,
  useHealthFindings,
  useLive,
} from './kit';
import { stopsVideoDownloads } from './video-downloads';

export interface ScanStatus {
  status?: string;
  phase?: string;
  progress?: number;
  processed?: number;
  total?: number;
  error_message?: string;
}

export interface ShellStatus {
  media_server?: { type?: string | null; connected?: boolean | null } | null;
  active_media_server?: string | null;
}

export interface LibraryData {
  scan?: ScanStatus;
  unmatched?: LibraryV2UnmatchedSummary;
  status?: ShellStatus;
  findings?: HealthFinding[];
  /** the page's Review button is on screen */
  canReview?: boolean;
}

const SERVER_NAMES: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  navidrome: 'Navidrome',
  emby: 'Emby',
  soulsync: 'SoulSync',
};

export function libraryAnswers(
  data: LibraryData | undefined,
  ctx: AnswerContext,
  recheck?: () => void,
): PageAnswer[] {
  if (!data) return [];
  const { isAdmin } = ctx;
  const out: PageAnswer[] = [];

  if (isAdmin) {
    out.push(
      ...findingAnswers(data.findings, (f) => f.id === 'services.media_server', {
        onDone: recheck,
      }),
    );
  } else if (data.status?.media_server && data.status.media_server.connected === false) {
    const type = data.status.media_server.type || data.status.active_media_server || '';
    const name = SERVER_NAMES[type] || 'Your media server';
    out.push({
      id: 'server-down',
      title: "Why isn't new music showing up?",
      live: true,
      body: (
        <p>
          {name} isn't answering right now, so the library can't pick up new music until it's back.
        </p>
      ),
    });
  }

  const scan = data.scan;
  if (scan?.status === 'running') {
    const pct = Number(scan.progress) || 0;
    const counted = scan.total
      ? `${(scan.processed ?? 0).toLocaleString()} of ${plural(scan.total, 'item')}`
      : '';
    out.push({
      id: 'scan',
      title: 'What is the library scan doing?',
      live: true,
      body: (
        <>
          <p>
            {sentence(scan.phase || 'Reading your media server')}
            {counted ? ` ${counted} so far.` : ''} New and changed music shows up as it goes.
          </p>
          {pct > 0 ? <Bar pct={pct} label="Library scan" /> : null}
        </>
      ),
    });
  } else if (scan?.status === 'error') {
    out.push({
      id: 'scan-failed',
      title: 'Why did the last library scan fail?',
      live: true,
      tone: isAdmin ? 'warn' : undefined,
      body: (
        <p>
          {sentence(scan.error_message || scan.phase || 'It stopped with an error and no message')}
        </p>
      ),
    });
  }

  const unmatched = data.unmatched;
  if (isAdmin && unmatched && unmatched.count > 0) {
    const { count } = unmatched;
    const ready = Number(unmatched.ready) || 0;
    out.push({
      id: 'unmatched',
      title:
        count === 1
          ? 'Why is a track under Unknown Artist?'
          : `Why are ${count} tracks under Unknown Artist?`,
      live: true,
      body: (
        <>
          <p>
            {count === 1 ? 'Its' : 'Their'} tags couldn't be read when{' '}
            {count === 1 ? 'it was' : 'they were'} imported, so SoulSync couldn't file{' '}
            {count === 1 ? 'it' : 'them'} under an artist.{' '}
            {ready > 0
              ? `${ready === count ? (count === 1 ? 'It has' : 'All have') : `${ready} ${ready === 1 ? 'has' : 'have'}`} a strong match ready, found by listening to the audio.`
              : "It's listening to the audio to find out what they are."}
          </p>
          <Acts>
            {data.canReview ? (
              <Act kind="primary" onClick={() => void pressPage('.library-unmatched-btn')}>
                Review {count === 1 ? 'it' : 'them'}
              </Act>
            ) : null}
            <Act kind="ghost" onClick={() => readArticle('lib-matching')}>
              How matching works
            </Act>
          </Acts>
        </>
      ),
    });
  }

  if (out.length) out[0] = { ...out[0], open: true };
  return out;
}

export function useLibraryAnswers(ctx: AnswerContext): PageAnswer[] {
  const scan = useLive(() => getJson<ScanStatus>('/api/database/update/status'), 3000);
  const unmatched = useLive(
    ctx.isAdmin ? () => getJson<LibraryV2UnmatchedSummary>('/api/library/unmatched-summary') : null,
    60_000,
  );
  const status = useLive(ctx.isAdmin ? null : () => getJson<ShellStatus>('/status'), 30_000);
  const health = useHealthFindings(ctx.isAdmin);
  if (!scan.loaded) return [];
  return libraryAnswers(
    {
      scan: scan.data,
      unmatched: unmatched.data,
      status: status.data,
      findings: health.data,
      canReview: !!document.querySelector('.library-unmatched-btn'),
    },
    ctx,
    health.reload,
  );
}

/** the video library's own folders and drives; sources belong to Downloads */
export function videoLibraryKeep(f: HealthFinding): boolean {
  return (
    f.id.startsWith('video.') &&
    !stopsVideoDownloads(f) &&
    !/^video\.(import_lists|repair_errors|youtube_health)/.test(f.id)
  );
}

export function useVideoLibraryAnswers(ctx: AnswerContext): PageAnswer[] {
  const health = useHealthFindings(ctx.isAdmin);
  if (!ctx.isAdmin || !health.loaded) return [];
  const out = findingAnswers(health.data, videoLibraryKeep, { onDone: health.reload });
  return out;
}

registerPageHelp({ id: 'library', useAnswers: useLibraryAnswers });
registerPageHelp({ id: 'video-library', useAnswers: useVideoLibraryAnswers });
