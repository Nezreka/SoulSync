/**
 * The findings inbox — one row per finding TYPE, not per finding.
 *
 * The complaint this answers: "3,000 findings" was a wall you paged through
 * 30 at a time, where every row looked equally urgent and the only bulk
 * control was a Fix All that could span types with incompatible actions.
 * Folded by type it is four decisions, ordered worst-first, with the
 * destructive ones deliberately below the safe ones in each band.
 *
 * ONE group opens at a time. The open group hosts the existing finding list,
 * scoped to its type — so every per-row feature (select, fix, dismiss, detail,
 * pagination) survives untouched instead of being reimplemented per group.
 */

import type { ReactNode } from 'react';

import type { FindingGroup, FindingTypeInfo } from '../-tools.groups';

import { findingSeverityClass, findingSeverityIcon } from '../-tools.core';
import { findingTypeBlurb, groupCountForStatus, sortInboxGroups } from '../-tools.groups';

export interface FindingsInboxProps {
  /** Already filtered by the toolbar; ordering is this component's job. */
  groups: readonly FindingGroup[];
  typeInfo: (findingType: string) => FindingTypeInfo | undefined;
  /** Which count each row shows — follows the status segmented control. */
  status: string;
  openType: string;
  onToggleOpen: (findingType: string) => void;
  onFixGroup: (group: FindingGroup, info: FindingTypeInfo | undefined) => void;
  onDismissGroup: (group: FindingGroup, info: FindingTypeInfo | undefined) => void;
  /** True while a background bulk run owns the fix buttons. */
  busy: boolean;
  /** The scoped finding list, rendered inside whichever group is open. */
  children?: ReactNode;
}

export function FindingsInbox({
  groups,
  typeInfo,
  status,
  openType,
  onToggleOpen,
  onFixGroup,
  onDismissGroup,
  busy,
  children,
}: FindingsInboxProps) {
  const ordered = sortInboxGroups(groups, (findingType) =>
    Boolean(typeInfo(findingType)?.destructive),
  );

  if (ordered.length === 0) {
    return (
      <div className="repair-empty-state">
        <div className="repair-empty-icon">&#10003;</div>
        <div className="repair-empty-title">All Clear</div>
        <div className="repair-empty-text">
          Nothing matches these filters. Your library is looking good.
        </div>
      </div>
    );
  }

  return (
    <div className="repair-inbox">
      {ordered.map((group) => {
        const info = typeInfo(group.finding_type);
        const label = info?.label || group.finding_type.replace(/_/g, ' ');
        const count = groupCountForStatus(group, status);
        const open = openType === group.finding_type;
        const severity = group.severity_max || 'info';
        const destructive = Boolean(info?.destructive);
        // A type with no handler gets NO button. The client used to guess at
        // fixability and offered two types a button that could only ever fail.
        const canFix = Boolean(info?.fixable) && group.pending > 0;

        return (
          <div
            className={`repair-inbox-group ${findingSeverityClass(severity)}${open ? ' open' : ''}`}
            data-finding-type={group.finding_type}
            id={`repair-group-${group.finding_type}`}
            key={group.finding_type}
          >
            <div
              className="repair-inbox-head"
              role="button"
              tabIndex={0}
              aria-expanded={open}
              onClick={() => onToggleOpen(group.finding_type)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onToggleOpen(group.finding_type);
                }
              }}
            >
              <span className={`repair-inbox-sev ${severity}`} title={severity}>
                {findingSeverityIcon(severity)}
              </span>
              <span className="repair-inbox-count">{count.toLocaleString()}</span>
              <span className="repair-inbox-text">
                <span className="repair-inbox-label">
                  {label}
                  {destructive ? (
                    <span
                      className="repair-inbox-risk"
                      title="Fixing this moves or deletes files — it always asks first"
                    >
                      touches files
                    </span>
                  ) : null}
                </span>
                <span className="repair-inbox-blurb">
                  {findingTypeBlurb(group.finding_type) ||
                    (info?.fixable ? '' : 'Review only — no automatic fix for this one.')}
                </span>
              </span>
              <span className="repair-inbox-actions" onClick={(event) => event.stopPropagation()}>
                {canFix ? (
                  <button
                    type="button"
                    className={`repair-inbox-btn${destructive ? ' destructive' : ''}`}
                    disabled={busy}
                    title={
                      destructive
                        ? `${info?.verb || 'Fix'} — asks before touching any file`
                        : `${info?.verb || 'Fix'} all ${group.pending.toLocaleString()}`
                    }
                    onClick={() => onFixGroup(group, info)}
                  >
                    {info?.verb || 'Fix'}
                    {destructive ? '…' : ` all (${group.pending.toLocaleString()})`}
                  </button>
                ) : null}
                {group.pending > 0 ? (
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
                <button
                  type="button"
                  className={`repair-inbox-expand${open ? ' open' : ''}`}
                  aria-label={open ? `Hide ${label}` : `Show ${label}`}
                  title={open ? 'Hide the list' : 'Show the list'}
                  onClick={() => onToggleOpen(group.finding_type)}
                >
                  &#9660;
                </button>
              </span>
            </div>

            {open ? <div className="repair-inbox-body">{children}</div> : null}
          </div>
        );
      })}
    </div>
  );
}
