/**
 * Downloads (video): the rows the video view polls
 * (/api/video/downloads/active, -vdl.api.ts fetchVideoDownloads; read from
 * the view's feed while it is mounted, polled here only when it isn't), read the
 * way the page's own journey reads them (-dl.format.ts videoJourney): a
 * free-slot wait with the peer's slots and queue (search_ctx.peer), a
 * transfer with no progress for 10 minutes, a re-search, a failure with its
 * backups, an import that didn't land, a copy still under the cutoff.
 * Admins also get the video Health checks for sources, indexers and the
 * download monitor (core/help/health.py check_video).
 *
 * Like music, rows carry no owner: a member gets the why, no titles, no
 * buttons.
 */

import type { VdlDownload } from '@/routes/active-downloads/-vdl.types';

import { vdlBackups } from '@/routes/active-downloads/-dl.format';
import {
  blockVideoRelease,
  cancelVideoDownload,
  fetchVideoDownloads,
  retryVideoDownload,
} from '@/routes/active-downloads/-vdl.api';
import { videoDownloadsFeed } from '@/routes/active-downloads/-dl.feed';
import {
  vdlCanBlock,
  vdlCardTitle,
  vdlIsRetryable,
  vdlParseCtx,
} from '@/routes/active-downloads/-vdl.helpers';

import type { HealthFinding } from '../api';

// the alias below copies the base entry, so it has to be registered first
import './base';
import { pageHelpFor, registerPageHelp, type AnswerContext, type PageAnswer } from './index';
import {
  Act,
  Acts,
  Note,
  Why,
  WhyRow,
  ago,
  findingAnswers,
  names,
  plural,
  revealPage,
  sentence,
  stampMs,
  toast,
  useFeedOrPoll,
  useHealthFindings,
} from './kit';

/** the page's own stall rule (videoJourney): no progress for 10 minutes */
export const VIDEO_STALL_MS = 10 * 60_000;

export interface VideoDownloadsData {
  downloads: VdlDownload[];
  findings?: HealthFinding[];
  now?: number;
}

/** the video checks that stop a download: sources, indexers, the monitor */
export function stopsVideoDownloads(f: HealthFinding): boolean {
  return /^video\.(source_|indexers|monitor)/.test(f.id);
}

type Ok = { ok?: boolean; success?: boolean; error?: string; wishlist_search?: boolean } | null;
const okOf = (r: Ok) => !!r && (r.ok === true || r.success === true);

/** the video row menu's Manual import (vdl-list.tsx vdlGotoVideoImport) */
function gotoVideoImport(): void {
  revealPage();
  if (document.body.getAttribute('data-side') !== 'video') window._switchAppSide?.('video');
  document.dispatchEvent(new CustomEvent('soulsync:video-navigate', { detail: 'video-import' }));
}

function one(rows: VdlDownload[], isAdmin: boolean, what: string): string {
  return isAdmin ? vdlCardTitle(rows[0]).trim() : what;
}

export function videoDownloadAnswers(
  data: VideoDownloadsData | undefined,
  ctx: AnswerContext,
  refresh: () => void,
  recheck?: () => void,
): PageAnswer[] {
  if (!data) return [];
  const { isAdmin } = ctx;
  const now = data.now ?? Date.now();
  const rows = data.downloads;
  const out: PageAnswer[] = [];

  if (isAdmin) out.push(...findingAnswers(data.findings, stopsVideoDownloads, { onDone: recheck }));

  // ── waiting for a free slot ──
  const queued = rows.filter((d) => d.status === 'queued');
  if (queued.length) {
    const peer = vdlParseCtx(queued[0]).peer;
    const who = isAdmin && queued[0].username ? queued[0].username : 'the uploader';
    let why = `${queued.length === 1 ? "It's" : "They're"} waiting for a free download slot. ${
      queued.length === 1 ? 'It starts' : 'They start'
    } on ${queued.length === 1 ? 'its' : 'their'} own.`;
    if (peer && (peer.slots != null || peer.queue != null)) {
      const bits: string[] = [];
      if (peer.slots === 0) bits.push('has no free upload slots');
      else if (peer.slots != null) bits.push(`has ${plural(peer.slots, 'free upload slot')}`);
      if (peer.queue) bits.push(`${peer.queue.toLocaleString()} people ahead of you`);
      why = `${queued.length === 1 ? "It's" : "They're"} queued on the uploader's side, not in SoulSync. ${who === 'the uploader' ? 'The uploader' : who} ${bits.join(' and ')}.`;
    }
    out.push({
      id: 'queued',
      title:
        queued.length === 1
          ? `Why is ${one(queued, isAdmin, 'a download')} waiting?`
          : `Why are ${queued.length} downloads waiting?`,
      live: true,
      body: (
        <>
          <p>{why}</p>
          {isAdmin && queued.length > 1 ? (
            <Why>
              {queued.slice(0, 3).map((d) => (
                <WhyRow
                  key={d.id}
                  art={d.poster_url}
                  fallback={d.title ?? ''}
                  title={vdlCardTitle(d)}
                  sub={d.release_title}
                />
              ))}
            </Why>
          ) : null}
        </>
      ),
    });
  }

  // ── no progress for a while ──
  const stuck = rows.filter((d) => {
    if (d.status !== 'downloading') return false;
    const moved = stampMs(d.progress_at);
    return moved !== null && now - moved > VIDEO_STALL_MS;
  });
  if (stuck.length) {
    const first = stuck[0];
    const backups = vdlBackups(first);
    out.push({
      id: 'stuck',
      title:
        stuck.length === 1
          ? `Why is ${one(stuck, isAdmin, 'a download')} stuck?`
          : `Why are ${stuck.length} downloads stuck?`,
      live: true,
      body: (
        <>
          <p>
            {stuck.length === 1 ? "It hasn't" : "They haven't"} moved in a while
            {stuck.length === 1 ? ` (last progress ${ago(first.progress_at, now)})` : ''}. The
            release may have few or no seeders or peers right now.
          </p>
          {isAdmin && stuck.length > 1 ? (
            <Why>
              {stuck.slice(0, 3).map((d) => (
                <WhyRow
                  key={d.id}
                  art={d.poster_url}
                  fallback={d.title ?? ''}
                  title={vdlCardTitle(d)}
                  sub={`Last progress ${ago(d.progress_at, now)}`}
                />
              ))}
            </Why>
          ) : null}
          {isAdmin && stuck.length === 1 && backups ? (
            <Note>
              {plural(backups, 'backup release')} {backups === 1 ? 'is' : 'are'} lined up if you
              cancel and retry.
            </Note>
          ) : null}
          {isAdmin && stuck.length === 1 ? (
            <Acts>
              <Act
                kind="secondary"
                onClick={async () => {
                  await cancelVideoDownload(first.id);
                  refresh();
                }}
              >
                Cancel
              </Act>
            </Acts>
          ) : null}
        </>
      ),
    });
  }

  // ── trying another release ──
  const searching = rows.filter((d) => d.status === 'searching');
  if (searching.length) {
    out.push({
      id: 'searching',
      title:
        searching.length === 1
          ? `Why is ${one(searching, isAdmin, 'a download')} searching again?`
          : `Why are ${searching.length} downloads searching again?`,
      live: true,
      body: (
        <p>
          The release {searching.length === 1 ? 'it' : 'each'} grabbed didn't work out, so SoulSync
          is looking for another. Nothing to do.
        </p>
      ),
    });
  }

  // ── failed ──
  const failed = rows.filter((d) => d.status === 'failed');
  if (failed.length) {
    const first = failed[0];
    const backups = vdlBackups(first);
    out.push({
      id: 'failed',
      title:
        failed.length === 1
          ? `Why did ${one(failed, isAdmin, 'a download')} fail?`
          : `Why did ${failed.length} downloads fail?`,
      live: true,
      body: isAdmin ? (
        <>
          {failed.length === 1 ? (
            <p>
              {sentence(first.error || 'No reason was recorded')}
              {Number(first.attempts) > 1 ? ` It was tried ${first.attempts} times.` : ''}
            </p>
          ) : (
            <Why>
              {failed.slice(0, 3).map((d) => (
                <WhyRow
                  key={d.id}
                  art={d.poster_url}
                  fallback={d.title ?? ''}
                  title={vdlCardTitle(d)}
                  sub={d.error || 'No reason was recorded'}
                  wrap
                />
              ))}
            </Why>
          )}
          {failed.length === 1 && backups ? (
            <Note>
              {plural(backups, 'backup release')} {backups === 1 ? 'is' : 'are'} still lined up.
            </Note>
          ) : null}
          {failed.length === 1 && (vdlIsRetryable(first) || vdlCanBlock(first)) ? (
            <Acts>
              {vdlCanBlock(first) ? (
                <Act
                  kind="primary"
                  onClick={async () => {
                    const res = (await blockVideoRelease(first.id)) as Ok;
                    if (!okOf(res)) toast(res?.error || 'Could not block that release', 'error');
                    else {
                      const r2 = (await retryVideoDownload(first.id, true)) as Ok;
                      toast(
                        !okOf(r2)
                          ? 'Release blocked'
                          : r2?.wishlist_search
                            ? 'Release blocked, searching for another release'
                            : 'Release blocked, retrying with another',
                        'success',
                      );
                    }
                    refresh();
                  }}
                >
                  Block and retry
                </Act>
              ) : null}
              {vdlIsRetryable(first) ? (
                <Act
                  kind="secondary"
                  onClick={async () => {
                    const res = (await retryVideoDownload(first.id)) as Ok;
                    if (okOf(res)) toast('Retrying', 'info');
                    else toast(res?.error || 'Retry failed', 'error');
                    refresh();
                  }}
                >
                  Retry
                </Act>
              ) : null}
            </Acts>
          ) : null}
        </>
      ) : (
        <p>{sentence(first.error || 'No reason was recorded')}</p>
      ),
    });
  }

  // ── downloaded, didn't import ──
  const unimported = rows.filter((d) => d.status === 'import_failed');
  if (unimported.length) {
    const first = unimported[0];
    out.push({
      id: 'import-failed',
      title:
        unimported.length === 1
          ? `Why didn't ${one(unimported, isAdmin, 'a download')} import?`
          : `Why didn't ${unimported.length} downloads import?`,
      live: true,
      body: (
        <>
          {unimported.length === 1 || !isAdmin ? (
            <p>
              {sentence(
                first.error || 'The file downloaded but could not be moved into your library',
              )}
            </p>
          ) : (
            <Why>
              {unimported.slice(0, 3).map((d) => (
                <WhyRow
                  key={d.id}
                  art={d.poster_url}
                  fallback={d.title ?? ''}
                  title={vdlCardTitle(d)}
                  sub={d.error || 'No reason was recorded'}
                  wrap
                />
              ))}
            </Why>
          )}
          {isAdmin ? (
            <>
              <Note>
                The file is still in the download folder, so Manual import can file{' '}
                {unimported.length === 1 ? 'it' : 'them'} by hand.
              </Note>
              <Acts>
                <Act kind="primary" icon="inbox" onClick={gotoVideoImport}>
                  Manual import
                </Act>
              </Acts>
            </>
          ) : null}
        </>
      ),
    });
  }

  // ── done, still hunting a better copy ──
  const watching = rows.filter((d) => d.status === 'completed' && d.upgrade_watch);
  if (watching.length) {
    out.push({
      id: 'upgrade',
      title:
        watching.length === 1
          ? `Why is ${one(watching, isAdmin, 'a finished download')} still being watched?`
          : `Why are ${watching.length} finished downloads still being watched?`,
      live: true,
      body: (
        <p>
          {watching.length === 1 ? "It's" : "They're"} in your library, but below your quality
          cutoff, so SoulSync keeps looking for a better copy and swaps it in when one turns up.
          {isAdmin && watching.length > 1
            ? ` ${names(watching.map((d) => vdlCardTitle(d).trim()))}.`
            : ''}
        </p>
      ),
    });
  }

  if (out.length) out[0] = { ...out[0], open: true };
  return out;
}

export function useVideoDownloadAnswers(ctx: AnswerContext): PageAnswer[] {
  // the video view's own poll when it is mounted, else ours
  const rows = useFeedOrPoll(videoDownloadsFeed, fetchVideoDownloads, 3000);
  const health = useHealthFindings(ctx.isAdmin);
  if (!rows.loaded) return [];
  return videoDownloadAnswers(
    { downloads: rows.data?.downloads ?? [], findings: health.data },
    ctx,
    rows.reload,
    health.reload,
  );
}

// the video side's own id, and the unified page on ?media=video (page.ts)
registerPageHelp({ id: 'video-downloads', useAnswers: useVideoDownloadAnswers });
registerPageHelp({
  ...pageHelpFor('video-downloads'),
  id: 'video-active-downloads',
  useAnswers: useVideoDownloadAnswers,
});
