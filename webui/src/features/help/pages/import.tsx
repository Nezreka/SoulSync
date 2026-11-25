/**
 * Import: why a file is held. The page's inbox read (/api/import/inbox,
 * -import.api.ts fetchImportInbox) already says what the worker made of each
 * item (status, confidence, error_message, identification_method), whether
 * auto-import is on, and which folders it couldn't list. The words for each
 * status are the page's (-import.inbox.ts INBOX_STATUS_META, isAttention).
 *
 * The import folder is the server's, not a profile's, so a member reads the
 * same why; the switch and the filter are left to the admin.
 */

import type { ImportInboxItem, ImportInboxPayload } from '@/routes/import/-import.types';

import { fetchImportInbox } from '@/routes/import/-import.api';
import { confidencePercent } from '@/routes/import/-import.inbox';

import { registerPageHelp, type AnswerContext, type PageAnswer } from './index';
import {
  Act,
  Acts,
  Bar,
  Note,
  Path,
  Why,
  WhyRow,
  goHref,
  plural,
  pressPage,
  readArticle,
  sentence,
  useLive,
} from './kit';

const inStaging = (i: ImportInboxItem) => i.in_staging;

function pct(item: ImportInboxItem): string {
  const value = confidencePercent(item);
  return value == null ? '' : `${value}% sure`;
}

function itemRows(items: ImportInboxItem[], sub: (i: ImportInboxItem) => string, wrap = false) {
  return (
    <Why>
      {items.slice(0, 3).map((i) => (
        <WhyRow
          key={i.key}
          art={i.image_url}
          fallback={i.name}
          title={i.name || i.folder_name}
          sub={sub(i)}
          wrap={wrap}
        />
      ))}
    </Why>
  );
}

function more(items: ImportInboxItem[]) {
  return items.length > 3 ? <Note>And {plural(items.length - 3, 'more')}.</Note> : null;
}

export function importAnswers(
  data: ImportInboxPayload | undefined,
  ctx: AnswerContext,
): PageAnswer[] {
  if (!data || data.success === false) return [];
  const { isAdmin } = ctx;
  const items = (data.items ?? []).filter(inStaging);
  const worker = data.worker;
  const running = Boolean(worker?.running);
  const out: PageAnswer[] = [];
  const showAttention = isAdmin ? (
    <Act kind="secondary" onClick={() => goHref('/import?filter=attention')}>
      Show what needs me
    </Act>
  ) : null;

  if (data.scanning && data.progress) {
    const { scanned, total } = data.progress;
    out.push({
      id: 'scanning',
      title: 'Why is the list still filling in?',
      live: true,
      body: (
        <>
          <p>
            SoulSync is still reading the import folder: {scanned.toLocaleString()} of{' '}
            {plural(total, 'file')} so far. Items show up as it goes.
          </p>
          {total > 0 ? (
            <Bar pct={(scanned / total) * 100} label="Reading the import folder" />
          ) : null}
        </>
      ),
    });
  }

  const review = items.filter((i) => i.status === 'needs_review');
  if (review.length) {
    const first = review[0];
    out.push({
      id: 'needs-review',
      title:
        review.length === 1
          ? `Why is ${first.name || 'an import'} waiting for me?`
          : `Why are ${review.length} imports waiting for me?`,
      live: true,
      body: (
        <>
          <p>
            SoulSync found a probable match for {review.length === 1 ? 'it' : 'each'}, but not one
            it trusts enough to file on its own. Approve it, or identify it if the match is wrong.
          </p>
          {itemRows(review, (i) =>
            [
              pct(i),
              i.identification_method,
              i.match ? `${i.match.matched_count} of ${i.match.total_tracks} tracks matched` : '',
            ]
              .filter(Boolean)
              .join(' · '),
          )}
          {more(review)}
          {showAttention ? <Acts>{showAttention}</Acts> : null}
        </>
      ),
    });
  }

  const unknown = items.filter((i) => i.status === 'needs_identify');
  if (unknown.length) {
    out.push({
      id: 'needs-identify',
      title:
        unknown.length === 1
          ? `Why couldn't SoulSync tell what ${unknown[0].name || 'this'} is?`
          : `Why couldn't SoulSync tell what ${unknown.length} imports are?`,
      live: true,
      body: (
        <>
          <p>
            Neither the tags nor a catalogue search gave a match it could trust, so{' '}
            {unknown.length === 1 ? 'it waits' : 'they wait'} for you to identify{' '}
            {unknown.length === 1 ? 'it' : 'them'} by hand.
          </p>
          {itemRows(unknown, (i) => i.error_message || `${plural(i.file_count, 'file')}`, true)}
          {more(unknown)}
          <Acts>
            {showAttention}
            <Act kind="ghost" onClick={() => readArticle('imp-matching')}>
              How the matcher works
            </Act>
          </Acts>
        </>
      ),
    });
  }

  const failed = items.filter((i) => i.status === 'failed' || i.status === 'partial');
  if (failed.length) {
    const first = failed[0];
    out.push({
      id: 'failed',
      title:
        failed.length === 1
          ? `Why didn't ${first.name || 'an import'} finish?`
          : `Why didn't ${failed.length} imports finish?`,
      live: true,
      body: (
        <>
          {failed.length === 1 ? (
            <p>
              {first.status === 'partial'
                ? `Some tracks imported and the rest didn't. ${sentence(first.error_message || '')}`.trim()
                : sentence(first.error_message || 'The import stopped before it finished')}
            </p>
          ) : (
            itemRows(
              failed,
              (i) =>
                i.error_message ||
                (i.status === 'partial' ? 'Some tracks imported' : 'No reason was recorded'),
              true,
            )
          )}
          {more(failed)}
          <Note>
            The files are still in the import folder.
            {failed.some((i) => i.status === 'failed') ? ' Retry or identify from the row.' : ''}
          </Note>
          {showAttention ? <Acts>{showAttention}</Acts> : null}
        </>
      ),
    });
  }

  const waiting = items.filter((i) => i.status === 'waiting');
  if (waiting.length && worker?.available) {
    if (!running) {
      out.push({
        id: 'auto-off',
        title:
          waiting.length === 1
            ? `Why isn't ${waiting[0].name || 'anything'} importing?`
            : `Why aren't ${waiting.length} items importing?`,
        live: true,
        body: (
          <>
            <p>
              Auto-import is off, so files in the import folder wait until you import them or turn
              it on.
            </p>
            {isAdmin ? (
              <Acts>
                <Act kind="primary" onClick={() => void pressPage('#auto-import-enabled')}>
                  Turn on auto-import
                </Act>
              </Acts>
            ) : null}
          </>
        ),
      });
    } else {
      out.push({
        id: 'waiting',
        title:
          waiting.length === 1
            ? `When will ${waiting[0].name || 'it'} be imported?`
            : `When will ${waiting.length} new items be imported?`,
        live: true,
        body: (
          <p>
            On the next auto-import scan, once {waiting.length === 1 ? 'its' : 'their'} files stop
            changing. Nothing to do.
          </p>
        ),
      });
    }
  }

  const problems = data.problems ?? [];
  if (problems.length) {
    out.push({
      id: 'unreadable',
      title:
        problems.length === 1
          ? 'Why are some files missing from the list?'
          : `Why are files in ${problems.length} folders missing from the list?`,
      live: true,
      tone: isAdmin ? 'warn' : undefined,
      body: (
        <>
          <p>
            SoulSync couldn't read {problems.length === 1 ? 'a folder' : 'these folders'}, so the
            files inside don't show here. They're not gone.
          </p>
          <Why>
            {problems.slice(0, 3).map((p) => (
              <WhyRow key={p.path} title={<Path>{p.path}</Path>} sub={p.error} wrap />
            ))}
          </Why>
        </>
      ),
    });
  }

  if (out.length) out[0] = { ...out[0], open: true };
  return out;
}

export function useImportAnswers(ctx: AnswerContext): PageAnswer[] {
  const inbox = useLive(fetchImportInbox, 10_000);
  if (!inbox.loaded) return [];
  return importAnswers(inbox.data, ctx);
}

registerPageHelp({ id: 'import', useAnswers: useImportAnswers });
