/**
 * Downloads (music): why things are stuck, from the rows the page already
 * polls (/api/downloads/all, -adl.api.ts fetchDownloads; read from the page's
 * feed while it is mounted, polled here only when it isn't) and their
 * live_detail (core/downloads/live_detail.py): the raw slskd queue state,
 * how long it has waited, the uploader's slots and queue, held imports. A
 * not-found row reads its search breakdown (task_worker.py error_message)
 * and, for the first one, the recorded decision (fetchTaskDetail). Admins
 * also get the review counts and the Health findings that stop downloads.
 *
 * Rows carry no owner, so a member gets the plain why with no names and no
 * actions (plan 4.10: their own items only).
 */

import { useEffect, useState } from 'react';

import type {
  AdlBatch,
  AdlDownload,
  AdlReviewSummary,
  AdlTaskDetail,
} from '@/routes/active-downloads/-adl.types';

import { rejectionSummary } from '@/features/downloads/decisions';
import {
  cancelTask,
  clearCompleted,
  downloadTaskNext,
  fetchDownloads,
  fetchReviewQueueSummary,
  fetchTaskDetail,
} from '@/routes/active-downloads/-adl.api';
import { musicDownloadsFeed } from '@/routes/active-downloads/-dl.feed';
import { downloadFailureSummary } from '@/routes/active-downloads/-adl.helpers';
import { openDownloadTaskSearch } from '@/routes/library/-ui/interactive-search-host';

import type { HealthFinding } from '../api';

import { registerPageHelp, type AnswerContext, type PageAnswer } from './index';
import {
  Act,
  Acts,
  Note,
  Why,
  WhyRow,
  findingAnswers,
  names,
  openSettings,
  plural,
  pressPage,
  readArticle,
  revealPage,
  sentence,
  spoken,
  toast,
  useFeedOrPoll,
  useHealthFindings,
  useLive,
} from './kit';

export interface MusicDownloadsData {
  downloads: AdlDownload[];
  batches: AdlBatch[];
  /** admin only; null when unknown */
  review: AdlReviewSummary | null;
  /** the recorded decision for the first not-found row */
  missDetail: AdlTaskDetail | null;
  findings?: HealthFinding[];
}

export interface MusicDownloadActions {
  refresh: () => void;
  recheck?: () => void;
}

const isRemoteQueue = (d: AdlDownload) =>
  /queued/i.test(d.live_detail?.slskd_state ?? '') &&
  /remote/i.test(d.live_detail?.slskd_state ?? '');
const isLocalQueue = (d: AdlDownload) =>
  /queued/i.test(d.live_detail?.slskd_state ?? '') &&
  /local/i.test(d.live_detail?.slskd_state ?? '');
const isLive = (d: AdlDownload) => !d.is_persistent_history;

/** the health findings that stop a music download (core/help/health.py ids) */
export function stopsDownloads(f: HealthFinding): boolean {
  return /^services\.download|^paths\.(download|transfer|staging|mapping)/.test(f.id);
}

/** cancel coordinates are (playlist_id, track_index), as the row's own button */
function cancellable(d: AdlDownload): boolean {
  return Boolean(d.playlist_id) && d.track_index !== undefined && d.track_index !== null;
}

async function cancelRows(rows: AdlDownload[], refresh: () => void): Promise<void> {
  let done = 0;
  for (const row of rows.filter(cancellable)) {
    try {
      const res = await cancelTask(row.playlist_id, row.track_index);
      if (res.success) done += 1;
    } catch {
      /* counted below */
    }
  }
  if (done)
    toast(done === 1 ? `Cancelled "${rows[0].title}"` : `Cancelled ${done} downloads`, 'info');
  else toast('Cancel failed', 'error');
  refresh();
}

/** what the page's row offers a failed music track: the inspector (adl-row canSearch) */
function canInspect(d: AdlDownload, batches: AdlBatch[]): boolean {
  if (!isLive(d) || !['failed', 'not_found'].includes(d.status)) return false;
  const batch = batches.find((b) => b.batch_id === d.batch_id);
  return !batch?.batch_type;
}

function inspect(d: AdlDownload): void {
  revealPage();
  void openDownloadTaskSearch(d.task_id, { name: d.title, artist: d.artist, album: d.album });
}

/** "Queued 18 min · a queue of 41 · 0 free slots" */
function queueLine(d: AdlDownload): string {
  const live = d.live_detail;
  const bits: string[] = [];
  if (live?.queued_seconds != null) bits.push(`Queued ${spoken(live.queued_seconds)}`);
  const q = live?.picked?.queue_length;
  if (q != null) bits.push(`${q.toLocaleString()} ahead`);
  const slots = live?.picked?.free_upload_slots;
  if (slots != null) bits.push(plural(slots, 'free slot'));
  return bits.join(' · ');
}

/** what the uploader's slots and queue mean, after their name */
function uploaderSays(row: AdlDownload, count: number): string {
  const slots = row.live_detail?.picked?.free_upload_slots;
  const queue = row.live_detail?.picked?.queue_length;
  const these = count === 1 ? 'it' : 'these';
  if (slots === 0) {
    const ahead = queue ? ` and ${queue.toLocaleString()} people ahead of you` : '';
    return `has no free upload slots${ahead}, so ${these} can sit for hours.`;
  }
  if (slots != null && slots > 0) {
    return `has ${plural(slots, 'free upload slot')}, so ${these} should start soon.`;
  }
  if (queue) return `has ${queue.toLocaleString()} people ahead of you.`;
  return `sends ${count === 1 ? 'it' : 'them'} when it's your turn.`;
}

function byUploader(rows: AdlDownload[]): Array<[string, AdlDownload[]]> {
  const groups = new Map<string, AdlDownload[]>();
  for (const row of rows) {
    const user = row.live_detail?.username || '';
    groups.set(user, [...(groups.get(user) ?? []), row]);
  }
  return [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
}

/** "after 3 queries", and every "22 results but none passed" summed */
export function readSearchBreakdown(error: string | null | undefined): {
  queries: number | null;
  results: number;
} {
  const text = error || '';
  const q = /after (\d+) quer/.exec(text);
  let results = 0;
  for (const m of text.matchAll(/(\d+) results? but none passed/g)) results += Number(m[1]);
  return { queries: q ? Number(q[1]) : null, results };
}

function missWhy(d: AdlDownload, detail: AdlTaskDetail | null): string[] {
  const out: string[] = [];
  const { queries, results } = readSearchBreakdown(d.error);
  const searched = queries ? `SoulSync searched ${plural(queries, 'way')}` : 'SoulSync searched';
  if (results > 0) {
    out.push(
      `${searched} and ${plural(results, 'result')} came back, but none matched closely enough to trust.`,
    );
  } else if (queries) {
    out.push(`${searched} and none of your sources had it.`);
  } else {
    const summary = downloadFailureSummary(d)?.message;
    out.push(summary || 'None of your sources had a file that fit.');
  }
  const counts = detail?.task_id === d.task_id ? detail.decision?.rejected_counts : undefined;
  if (counts && Object.keys(counts).length) out.push(`Passed over: ${rejectionSummary(counts)}.`);
  return out;
}

/** "3 searches · 22 results, none close enough" */
function missLine(d: AdlDownload): string {
  const { queries, results } = readSearchBreakdown(d.error);
  const bits = [d.artist];
  if (queries) bits.push(plural(queries, 'search', 'searches'));
  bits.push(
    results ? `${plural(results, 'result')}, none close enough` : 'nothing on your sources',
  );
  return bits.filter(Boolean).join(' · ');
}

function reasonOf(d: AdlDownload): string {
  return downloadFailureSummary(d)?.message || d.error?.trim() || 'No reason was recorded.';
}

export function musicDownloadAnswers(
  data: MusicDownloadsData | undefined,
  ctx: AnswerContext,
  act: MusicDownloadActions,
): PageAnswer[] {
  if (!data) return [];
  const { isAdmin } = ctx;
  const live = data.downloads.filter(isLive);
  const out: PageAnswer[] = [];

  if (isAdmin) out.push(...findingAnswers(data.findings, stopsDownloads, { onDone: act.recheck }));

  // ── waiting in an uploader's queue ──
  const remote = live.filter(isRemoteQueue);
  if (remote.length) {
    const groups = byUploader(remote);
    const title =
      remote.length === 1
        ? `Why is ${remote[0].title || 'this download'} stuck in Queued?`
        : `Why are ${remote.length} downloads stuck in Queued?`;
    if (!isAdmin) {
      out.push({
        id: 'queued-remote',
        title: remote.length === 1 ? 'Why is a download stuck in Queued?' : title,
        live: true,
        body: (
          <p>
            {remote.length === 1 ? "It's" : "They're"} waiting in an uploader's queue on Soulseek,
            not in SoulSync. Each starts when its uploader gets to it.
          </p>
        ),
      });
    } else {
      const [user, top] = groups[0];
      const lead =
        groups.length === 1 && user ? (
          <p>
            {remote.length === 1 ? "It's" : "They're"} queued on the uploader's side, not in
            SoulSync. <b>{user}</b> {uploaderSays(top[0], top.length)}
          </p>
        ) : (
          <p>
            They're queued on the uploaders' side, not in SoulSync. Each uploader sends files in
            their own order, so these start when their turn comes.
          </p>
        );
      out.push({
        id: 'queued-remote',
        title,
        live: true,
        body: (
          <>
            {lead}
            <Why>
              {groups.slice(0, 3).flatMap(([who, rows]) => {
                const first = rows[0];
                const rest = rows.slice(1);
                const lines = [
                  <WhyRow
                    key={`${who}:${first.task_id}`}
                    art={first.artwork}
                    fallback={first.title}
                    title={first.title || 'Untitled'}
                    sub={[queueLine(first), groups.length > 1 && who ? who : '']
                      .filter(Boolean)
                      .join(' · ')}
                  />,
                ];
                if (rest.length) {
                  lines.push(
                    <WhyRow
                      key={`${who}:rest`}
                      art={rest[0].artwork}
                      fallback={rest[0].title}
                      title={names(rest.map((r) => r.title || 'Untitled'))}
                      sub="Same uploader, same queue"
                    />,
                  );
                }
                return lines;
              })}
            </Why>
            {groups.length > 3 ? (
              <Note>And {plural(groups.length - 3, 'more uploader')}.</Note>
            ) : null}
            {remote.some(cancellable) ? (
              <Acts>
                <Act kind="secondary" onClick={() => cancelRows(remote, act.refresh)}>
                  {remote.length === 1 ? 'Cancel' : `Cancel these ${remote.length}`}
                </Act>
              </Acts>
            ) : null}
          </>
        ),
      });
    }
  }

  // ── waiting for one of slskd's own slots ──
  const local = live.filter(isLocalQueue);
  if (local.length) {
    out.push({
      id: 'queued-local',
      title:
        local.length === 1
          ? `Why is ${isAdmin ? local[0].title || 'a download' : 'a download'} queued in slskd?`
          : `Why are ${local.length} downloads queued in slskd?`,
      live: true,
      body: (
        <p>
          Your Soulseek client is holding {local.length === 1 ? 'it' : 'them'} until one of its own
          download slots frees up. {local.length === 1 ? 'It starts' : 'They start'} on{' '}
          {local.length === 1 ? 'its' : 'their'} own.
        </p>
      ),
    });
  }

  // ── waiting their turn in SoulSync ──
  const turn = live.filter(
    (d) => ['queued', 'pending'].includes(d.status) && !d.live_detail?.slskd_state,
  );
  if (turn.length) {
    const first = turn[0];
    out.push({
      id: 'queued-turn',
      title:
        turn.length === 1
          ? `Why hasn't ${isAdmin ? first.title || 'a download' : 'a download'} started?`
          : `Why haven't ${turn.length} downloads started?`,
      live: true,
      body: (
        <>
          <p>
            SoulSync runs a few downloads at a time and the rest wait their turn in order.{' '}
            {turn.length === 1 ? 'It starts' : 'They start'} as the ones ahead finish.
          </p>
          {isAdmin ? (
            <Acts>
              <Act
                kind="secondary"
                onClick={async () => {
                  try {
                    const res = await downloadTaskNext(first.task_id);
                    if (res.success)
                      toast(`"${first.title || 'Track'}" will download next`, 'success');
                    else toast(res.error || 'Could not move track', 'error');
                  } catch {
                    toast('Could not move track', 'error');
                  }
                  act.refresh();
                }}
              >
                Download {turn.length === 1 ? 'it' : `"${first.title || 'the first'}"`} next
              </Act>
            </Acts>
          ) : null}
        </>
      ),
    });
  }

  // ── failed ──
  const failed = live.filter((d) => d.status === 'failed');
  if (failed.length) {
    const reasons = new Map<string, AdlDownload[]>();
    for (const d of failed) reasons.set(reasonOf(d), [...(reasons.get(reasonOf(d)) ?? []), d]);
    const grouped = [...reasons.entries()].sort((a, b) => b[1].length - a[1].length);
    const searchable = failed.find((d) => canInspect(d, data.batches));
    out.push({
      id: 'failed',
      title:
        failed.length === 1
          ? `Why did ${isAdmin ? failed[0].title || 'a download' : 'a download'} fail?`
          : `Why did ${failed.length} downloads fail?`,
      live: true,
      body: isAdmin ? (
        <>
          <Why>
            {grouped.slice(0, 3).map(([reason, rows]) => (
              <WhyRow
                key={reason}
                art={rows[0].artwork}
                fallback={rows[0].title}
                title={names(rows.map((r) => r.title || 'Untitled'))}
                sub={reason}
                wrap
              />
            ))}
          </Why>
          {grouped.length > 3 ? (
            <Note>And {plural(grouped.length - 3, 'other reason')}.</Note>
          ) : null}
          <Acts>
            {searchable ? (
              <Act kind="primary" icon="search" onClick={() => inspect(searchable)}>
                See what every source has
              </Act>
            ) : null}
            <Act
              kind={searchable ? 'ghost' : 'secondary'}
              onClick={async () => {
                try {
                  const res = await clearCompleted('failed');
                  if (res.success) toast('Cleared the failed downloads', 'success');
                  else toast(res.error || 'Failed to clear downloads', 'error');
                } catch {
                  toast('Failed to clear downloads', 'error');
                }
                act.refresh();
              }}
            >
              Clear failed
            </Act>
          </Acts>
        </>
      ) : (
        <p>{sentence(grouped[0][0]) || 'No reason was recorded.'}</p>
      ),
    });
  }

  // ── not found ──
  const missed = live.filter((d) => d.status === 'not_found');
  if (missed.length) {
    const first = missed[0];
    const why = missWhy(first, data.missDetail);
    out.push({
      id: 'not-found',
      title:
        missed.length === 1
          ? `Why wasn't ${isAdmin ? first.title || 'a track' : 'a track'} found?`
          : `Why weren't ${missed.length} tracks found?`,
      live: true,
      body: (
        <>
          {missed.length === 1 || !isAdmin ? (
            <>
              <p>{why[0]}</p>
              {why[1] ? <Note>{why[1]}</Note> : null}
            </>
          ) : (
            <>
              <p>Each was searched on your sources and nothing came back close enough to trust.</p>
              <Why>
                {missed.slice(0, 3).map((d) => (
                  <WhyRow
                    key={d.task_id}
                    art={d.artwork}
                    fallback={d.title}
                    title={d.title || 'Untitled'}
                    sub={missLine(d)}
                  />
                ))}
              </Why>
              {missed.length > 3 ? <Note>And {plural(missed.length - 3, 'more')}.</Note> : null}
              {why[1] ? (
                <Note>
                  {first.title ? `${first.title}: ` : ''}
                  {why[1]}
                </Note>
              ) : null}
            </>
          )}
          {isAdmin ? (
            <Acts>
              {canInspect(first, data.batches) ? (
                <Act
                  kind={missed.length === 1 ? 'primary' : 'secondary'}
                  icon="search"
                  onClick={() => inspect(first)}
                >
                  See what every source has
                </Act>
              ) : null}
              <Act kind="ghost" onClick={() => readArticle('search-quality')}>
                What counts as a match
              </Act>
            </Acts>
          ) : null}
        </>
      ),
    });
  }

  // ── held before import ──
  const held = live.filter((d) => d.live_detail?.held_reason);
  if (held.length) {
    out.push({
      id: 'held',
      title:
        held.length === 1
          ? `Why is ${isAdmin ? held[0].title || 'a download' : 'a download'} held?`
          : `Why are ${held.length} downloads held?`,
      live: true,
      body: isAdmin ? (
        <>
          <p>
            SoulSync downloaded {held.length === 1 ? 'it' : 'them'} but is holding off on the
            import.
          </p>
          <Why>
            {held.slice(0, 3).map((d) => (
              <WhyRow
                key={d.task_id}
                art={d.artwork}
                fallback={d.title}
                title={d.title || 'Untitled'}
                sub={d.live_detail?.held_reason}
                wrap
              />
            ))}
          </Why>
        </>
      ) : (
        <p>
          {sentence(held[0].live_detail?.held_reason ?? '') ||
            'It downloaded, and SoulSync is holding off on the import.'}
        </p>
      ),
    });
  }

  // ── review (admin) ──
  if (isAdmin && data.review && data.review.total > 0) {
    const { quarantine, unverified, total } = data.review;
    out.push({
      id: 'review',
      title:
        total === 1
          ? 'Why is a download waiting in Review?'
          : `Why are ${total} downloads waiting in Review?`,
      live: true,
      body: (
        <>
          {quarantine > 0 ? (
            <p>
              {plural(quarantine, 'file')} failed a check after downloading, so SoulSync set{' '}
              {quarantine === 1 ? 'it' : 'them'} aside instead of filing{' '}
              {quarantine === 1 ? 'it' : 'them'}.
            </p>
          ) : null}
          {unverified > 0 ? (
            <p>
              {plural(unverified, 'track')} {unverified === 1 ? 'is' : 'are'} in your library, but
              AcoustID couldn't confirm {unverified === 1 ? "it's" : "they're"} the right song.
            </p>
          ) : null}
          <Note>They wait there until you approve or delete them.</Note>
          <Acts>
            <Act
              kind="primary"
              onClick={() => void pressPage('#adl-view-tabs [data-filter="unverified"]')}
            >
              Open Review
            </Act>
            <Act kind="ghost" onClick={() => readArticle('search-postprocess')}>
              How the checks work
            </Act>
          </Acts>
        </>
      ),
    });
  }

  // ── where files go (admin, always true) ──
  if (isAdmin) {
    out.push({
      id: 'where',
      title: 'Where do finished downloads go?',
      body: (
        <>
          <p>
            Into your transfer folder, tagged and renamed with your naming template. Anything that
            fails a check waits in Review instead.
          </p>
          <Acts>
            <Act kind="secondary" icon="folder" onClick={() => openSettings('downloads')}>
              Open Folder Setup
            </Act>
          </Acts>
        </>
      ),
    });
  }

  if (out.length) out[0] = { ...out[0], open: true };
  return out;
}

export function useMusicDownloadAnswers(ctx: AnswerContext): PageAnswer[] {
  // the page's own 2s poll when it is mounted, else ours
  const rows = useFeedOrPoll(musicDownloadsFeed, fetchDownloads, 3000);
  const review = useLive(ctx.isAdmin ? fetchReviewQueueSummary : null, 15_000);
  const health = useHealthFindings(ctx.isAdmin);

  const downloads = rows.data?.downloads ?? [];
  const firstMiss = downloads.find((d) => d.status === 'not_found' && isLive(d))?.task_id ?? null;
  const [missDetail, setMissDetail] = useState<AdlTaskDetail | null>(null);
  useEffect(() => {
    if (!firstMiss) return undefined;
    let alive = true;
    void fetchTaskDetail(firstMiss).then((d) => {
      if (alive) setMissDetail(d);
    });
    return () => {
      alive = false;
    };
  }, [firstMiss]);

  if (!rows.loaded) return [];
  return musicDownloadAnswers(
    {
      downloads,
      batches: rows.data?.batches ?? [],
      review: review.data ?? null,
      missDetail,
      findings: health.data,
    },
    ctx,
    { refresh: rows.reload, recheck: health.reload },
  );
}

registerPageHelp({ id: 'active-downloads', useAnswers: useMusicDownloadAnswers });
