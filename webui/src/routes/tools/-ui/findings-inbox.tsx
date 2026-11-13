/**
 * The findings inbox: one row per finding TYPE, sorted into what fixing it
 * would do.
 *
 *   safe to fix      writes tags or data, touches no file, downloads nothing
 *   needs your call  moves, deletes or replaces files, or starts downloads
 *   review only      no automatic fix
 *
 * "3,000 findings" as a flat list is a wall you page through 30 at a time,
 * where every row looks equally urgent. Folded by type and sorted like this
 * it is a handful of decisions, and the first question about each one (can
 * this hurt my files?) is answered by where it sits.
 *
 * Opening a type shows that type alone, with a way back: its header, then
 * the same finding list every other view uses, scoped to it. It used to
 * unfold in place, which put thirty rows and their paging in the middle of
 * a list of other things.
 */

import type { ReactNode } from 'react';

import type { FindingGroup, FindingTypeInfo } from '../-tools.groups';

import { findingSeverityClass } from '../-tools.core';
import {
  findingTypeBlurb,
  groupCountForStatus,
  isSafeType,
  severityRank,
  sortInboxGroups,
} from '../-tools.groups';
import { useToolsMedia } from '../-tools.media';
import { FindingArtMosaic } from './finding-art';
import { STUDIO_CONFIG } from './operations-studio';

export interface FindingsInboxProps {
  /** Already filtered by the toolbar; ordering is this component's job. */
  groups: readonly FindingGroup[];
  typeInfo: (findingType: string) => FindingTypeInfo | undefined;
  /** Which count each row shows — follows the status segmented control. It
   *  also decides which bulk buttons a row offers. */
  status: string;
  /** the severity filter, so a row counts and colours only those rows */
  severity?: string;
  openType: string;
  onToggleOpen: (findingType: string) => void;
  onFixGroup: (group: FindingGroup, info: FindingTypeInfo | undefined) => void;
  onDismissGroup: (group: FindingGroup, info: FindingTypeInfo | undefined) => void;
  onReopenGroup: (group: FindingGroup, info: FindingTypeInfo | undefined) => void;
  /** True while a background bulk run owns the fix buttons. */
  busy: boolean;
  /** The scoped finding list, rendered under the type that is open. */
  children?: ReactNode;
}

type Bucket = 'safe' | 'call' | 'review';

const BUCKETS: ReadonlyArray<{ id: Bucket; title: string }> = [
  { id: 'safe', title: 'Safe to fix' },
  { id: 'call', title: 'Needs your call' },
  { id: 'review', title: 'Review only' },
];

function bucketOf(info: FindingTypeInfo | undefined): Bucket {
  if (isSafeType(info)) return 'safe';
  return info?.fixable ? 'call' : 'review';
}

function Chevron({ direction }: { direction: 'left' | 'right' }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={direction === 'left' ? 'M15 6l-6 6 6 6' : 'M9 6l6 6-6 6'} />
    </svg>
  );
}

export function FindingsInbox({
  groups,
  typeInfo,
  status,
  severity: severityFilter = '',
  openType,
  onToggleOpen,
  onFixGroup,
  onDismissGroup,
  onReopenGroup,
  busy,
  children,
}: FindingsInboxProps) {
  const media = useToolsMedia();
  const hints = STUDIO_CONFIG[media];
  const ordered = sortInboxGroups(groups, (findingType) =>
    Boolean(typeInfo(findingType)?.destructive),
  );

  if (ordered.length === 0) {
    return (
      <div className="repair-empty-state">
        <div className="repair-empty-title">All Clear</div>
        <div className="repair-empty-text">
          Nothing matches these filters. Your library is looking good.
        </div>
      </div>
    );
  }

  const opened = ordered.find((group) => group.finding_type === openType);

  const row = (group: FindingGroup) => {
    const info = typeInfo(group.finding_type);
    const label = info?.label || group.finding_type.replace(/_/g, ' ');
    const count = groupCountForStatus(group, status, severityFilter);
    const open = openType === group.finding_type;
    // filtered to one severity, every row shown has rows of it (review 5 R5-L-3)
    const severity = severityFilter || group.severity_max || 'info';
    const destructive = Boolean(info?.destructive);
    // A type with no handler gets NO button. The client used to guess at
    // fixability and offered two types a button that could only ever fail.
    // fix all / dismiss all act on open rows, reopen all on dismissed
    // ones: each only shows in a status view that lists those rows, so a
    // button never reaches rows the view hides (review 5 W3-TT)
    const showsPending = status === 'pending' || status === '';
    const showsDismissed = status === 'dismissed' || status === '';
    const canFix = Boolean(info?.fixable) && group.pending > 0 && showsPending;
    const blurb =
      findingTypeBlurb(group.finding_type, info) ||
      (info?.fixable ? '' : 'Review only — no automatic fix for this one.');

    return (
      <div
        className={`repair-inbox-group ${findingSeverityClass(severity)}${open ? ' open' : ''}`}
        data-finding-type={group.finding_type}
        id={`repair-group-${group.finding_type}`}
        key={group.finding_type}
      >
        <div
          className="repair-inbox-head"
          // a closed row is one big button into the type. the opened type's
          // header is a header: leaving is the back link's job, and a stray
          // click on a title must not throw the list away.
          {...(open
            ? {}
            : {
                role: 'button',
                tabIndex: 0,
                'aria-expanded': false,
                onClick: () => onToggleOpen(group.finding_type),
                onKeyDown: (event: React.KeyboardEvent) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onToggleOpen(group.finding_type);
                  }
                },
              })}
        >
          <FindingArtMosaic urls={group.art} file={!info?.fixable || destructive} />
          <span className="repair-inbox-text">
            <span className="repair-inbox-label">
              {/* worth a mark only when something is actually wrong */}
              {severity === 'info' ? null : (
                <span className={`repair-inbox-sev ${severity}`} title={severity} />
              )}
              {label}
              {destructive ? (
                <span
                  className="repair-inbox-risk"
                  title="Fixing this moves or deletes files — it always asks first"
                >
                  touches files
                </span>
              ) : null}
              {info?.acquires ? (
                <span
                  className="repair-inbox-risk acquires"
                  title="Fixing this starts downloads, so fix all safe leaves it alone"
                >
                  downloads
                </span>
              ) : null}
            </span>
            <span className="repair-inbox-blurb">{blurb}</span>
          </span>
          <span className="repair-inbox-count">{count.toLocaleString()}</span>
          <span className="repair-inbox-actions" onClick={(event) => event.stopPropagation()}>
            {canFix ? (
              <button
                type="button"
                className={`repair-inbox-btn${destructive ? ' destructive' : ''}`}
                disabled={busy}
                title={
                  destructive
                    ? `${info?.verb || 'Fix'} all ${group.pending.toLocaleString()} — asks before touching any file`
                    : `${info?.verb || 'Fix'} all ${group.pending.toLocaleString()}`
                }
                onClick={() => onFixGroup(group, info)}
              >
                {info?.verb || 'Fix'}
                {` all (${group.pending.toLocaleString()})`}
              </button>
            ) : null}
            {group.pending > 0 && showsPending ? (
              <button
                type="button"
                className="repair-inbox-btn ghost"
                disabled={busy}
                title="Dismiss this whole group — never show these again"
                onClick={() => onDismissGroup(group, info)}
              >
                Dismiss all
              </button>
            ) : null}
            {group.dismissed > 0 && showsDismissed ? (
              <button
                type="button"
                className="repair-inbox-btn ghost"
                disabled={busy}
                title="Reopen all dismissed findings of this type"
                onClick={() => onReopenGroup(group, info)}
              >
                Reopen all
              </button>
            ) : null}
            {open ? null : (
              <button
                type="button"
                className="repair-inbox-expand"
                aria-label={`Show ${label}`}
                title="Show the list"
                onClick={() => onToggleOpen(group.finding_type)}
              >
                <Chevron direction="right" />
              </button>
            )}
          </span>
        </div>

        {open ? <div className="repair-inbox-body">{children}</div> : null}
      </div>
    );
  };

  if (opened) {
    return (
      <div className="repair-inbox is-open">
        <button
          type="button"
          className="repair-inbox-back"
          onClick={() => onToggleOpen(opened.finding_type)}
        >
          <Chevron direction="left" />
          All findings
        </button>
        {row(opened)}
      </div>
    );
  }

  /* worst first still holds across sections: the one holding a broken file
     comes before the one holding forty missing lyrics. on a tie they keep
     their usual order, safe first. */
  const sections = BUCKETS.map((bucket, index) => {
    const rows = ordered.filter((group) => bucketOf(typeInfo(group.finding_type)) === bucket.id);
    const worst = Math.min(...rows.map((group) => severityRank(group.severity_max)), 99);
    return { bucket, rows, worst, index };
  })
    .filter((section) => section.rows.length > 0)
    .sort((a, b) => a.worst - b.worst || a.index - b.index);

  return (
    <div className="repair-inbox">
      {sections.map(({ bucket, rows }) => {
        const total = rows.reduce(
          (sum, group) => sum + groupCountForStatus(group, status, severityFilter),
          0,
        );
        const hint =
          bucket.id === 'safe'
            ? hints.safeHint
            : bucket.id === 'call'
              ? hints.callHint
              : hints.reviewHint;
        return (
          <section className={`repair-triage ${bucket.id}`} key={bucket.id}>
            <div className="tools-section-head">
              <h3 className="tools-section-title">{bucket.title}</h3>
              <span className="repair-triage-count">{total.toLocaleString()}</span>
              <span className="tools-section-blurb">{hint}</span>
            </div>
            {rows.map(row)}
          </section>
        );
      })}
    </div>
  );
}
