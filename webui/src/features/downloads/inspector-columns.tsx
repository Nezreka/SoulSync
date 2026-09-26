import { useState } from 'react';

import type { GrabRule, InspectorCandidate, RejectedSummary } from './inspector';

import { decisionPill, rejectionSummary } from './decisions';
import {
  DOWNLOAD_SERVICE_ICONS,
  DOWNLOAD_SERVICE_LABELS,
  msClock,
  overrideBlockedReason,
  overrideConfirm,
  scoreClass,
} from './inspector';

/**
 * One source's column in the candidate inspector: the accepted hits (pick one
 * by radio), then a quiet "Show N rejected" section with the reason pill, the
 * wanted-vs-found evidence, and "Grab anyway" behind a confirm.
 */

/** What the inspector searched for, as the evidence table compares it. */
export interface InspectorExpected {
  name?: string;
  artist?: string;
  album?: string;
  duration_ms?: number;
}

export interface SourceColumnData {
  source: string;
  candidates: InspectorCandidate[];
  rejected: RejectedSummary;
}

export function SourceColumn({
  column,
  selectedIdx,
  bestIdx,
  onPick,
  expected,
  onOverride,
  cannotGrab,
}: {
  column: SourceColumnData;
  selectedIdx: number | null;
  bestIdx: number;
  onPick: (globalIdx: number) => void;
  expected: InspectorExpected;
  onOverride: (row: InspectorCandidate) => void;
  cannotGrab?: GrabRule;
}) {
  const [showRejected, setShowRejected] = useState(false);
  const { rejected } = column;
  return (
    <div className="rdl-src-col">
      <div className="rdl-src-col-header">
        <span className="rdl-src-col-icon">{DOWNLOAD_SERVICE_ICONS[column.source] || '📦'}</span>
        <span className="rdl-src-col-label">
          {DOWNLOAD_SERVICE_LABELS[column.source] || column.source}
        </span>
        <span className="rdl-src-col-count">{column.candidates.length}</span>
      </div>
      <div className="rdl-src-col-body">
        {column.candidates.length === 0 ? (
          <div className="rdl-src-col-empty">
            {rejected.total > 0 ? 'Nothing passed' : 'No results'}
          </div>
        ) : (
          column.candidates.slice(0, 10).map((c) => {
            const confPct = Math.round((c.confidence || 0) * 100);
            const confCls = scoreClass(confPct);
            const isRec = c._globalIdx === bestIdx;
            const refusal = cannotGrab?.(c) ?? null;
            const unpickable = c.blacklisted || refusal != null;
            return (
              <label
                className={`rdl-src-item${unpickable ? ' blacklisted' : ''}${isRec ? ' recommended' : ''}`}
                title={refusal ?? undefined}
                key={c._globalIdx}
              >
                {unpickable ? (
                  <div className="rdl-src-radio-placeholder" />
                ) : (
                  <input
                    type="radio"
                    name="source-choice"
                    value={c._globalIdx}
                    checked={selectedIdx === c._globalIdx}
                    onChange={() => onPick(c._globalIdx)}
                  />
                )}
                <div className="rdl-src-item-body">
                  <div className="rdl-src-item-top">
                    <div className="rdl-src-item-name" title={String(c.filename || '')}>
                      {c.display_name}
                    </div>
                    {isRec ? <span className="rdl-src-recommended">Best</span> : null}
                  </div>
                  <CandidateDetails row={c} source={column.source} />
                  <div className="rdl-src-conf-bar">
                    <div
                      className={`rdl-src-conf-fill ${confCls}`}
                      style={{ width: `${confPct}%` }}
                    />
                  </div>
                </div>
                <div className={`rdl-src-conf-pct ${confCls}`}>{confPct}%</div>
                {c.blacklisted ? <span className="rdl-src-bl">Blacklisted</span> : null}
                {refusal && !c.blacklisted ? (
                  <span className="rdl-src-bl">Can&apos;t pick here</span>
                ) : null}
              </label>
            );
          })
        )}
        {rejected.total > 0 ? (
          <div className="rdl-rej">
            <button
              type="button"
              className="rdl-rej-toggle"
              aria-expanded={showRejected}
              onClick={() => setShowRejected((v) => !v)}
            >
              {showRejected ? 'Hide' : 'Show'} {rejected.total} rejected
            </button>
            {showRejected ? (
              <>
                <div className="rdl-rej-summary">{rejectionSummary(rejected.counts)}</div>
                {rejected.rows.map((row) => (
                  <RejectedRow
                    row={row}
                    cannotGrab={cannotGrab}
                    source={column.source}
                    expected={expected}
                    onOverride={onOverride}
                    key={`${row.username}|${row.filename}`}
                  />
                ))}
                {rejected.rows.length < rejected.total ? (
                  <div className="rdl-rej-more">
                    Showing the closest {rejected.rows.length} of {rejected.total}
                  </div>
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function CandidateDetails({ row, source }: { row: InspectorCandidate; source: string }) {
  const dur = msClock(row.duration);
  return (
    <div className="rdl-src-item-details">
      {row.quality ? <span className="rdl-src-fmt">{row.quality}</span> : null}
      {row.bitrate ? <span className="rdl-src-detail">{row.bitrate}k</span> : null}
      <span className="rdl-src-detail">{row.size_display}</span>
      {dur ? <span className="rdl-src-detail">{dur}</span> : null}
      {source === 'soulseek' ? (
        <span className="rdl-src-detail rdl-src-user">{row.username}</span>
      ) : null}
      {source === 'soulseek' && row.free_upload_slots != null ? (
        <span className="rdl-src-detail">{row.free_upload_slots} slots</span>
      ) : null}
    </div>
  );
}

function RejectedRow({
  row,
  source,
  expected,
  onOverride,
  cannotGrab,
}: {
  row: InspectorCandidate;
  source: string;
  expected: InspectorExpected;
  onOverride: (row: InspectorCandidate) => void;
  cannotGrab?: GrabRule;
}) {
  const [open, setOpen] = useState(false);
  const blocked = overrideBlockedReason(row) ?? cannotGrab?.(row) ?? null;
  const stage = row.decision?.stage || 'decision';

  const grab = async () => {
    const ok = await window.showConfirmDialog?.(
      overrideConfirm(row, expected, expected.duration_ms),
    );
    if (ok) onOverride(row);
  };

  return (
    <div
      className={`rdl-src-item rdl-rej-item${open ? ' open' : ''}`}
      data-code={row.decision?.code}
    >
      <div className="rdl-src-radio-placeholder" />
      <div className="rdl-src-item-body">
        <div className="rdl-src-item-top">
          <div className="rdl-src-item-name" title={String(row.filename || '')}>
            {row.display_name}
          </div>
          <span className={`rdl-rej-pill stage-${stage}`}>
            {decisionPill(row, expected.duration_ms)}
          </span>
        </div>
        <CandidateDetails row={row} source={source} />
        <div className="rdl-rej-actions">
          <button
            type="button"
            className="rdl-rej-why"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? 'Hide details' : 'Why?'}
          </button>
          <button
            type="button"
            className="rdl-rej-grab"
            disabled={blocked != null}
            title={blocked ?? undefined}
            onClick={() => void grab()}
          >
            Grab anyway
          </button>
        </div>
        {open ? <Evidence row={row} expected={expected} blocked={blocked} /> : null}
      </div>
    </div>
  );
}

function Evidence({
  row,
  expected,
  blocked,
}: {
  row: InspectorCandidate;
  expected: InspectorExpected;
  blocked: string | null;
}) {
  const d = row.decision;
  const score = d?.score ?? row.confidence;
  const lines: [string, string, string][] = [
    ['Title', expected.name || '', row.title || row.display_name || ''],
    ['Artist', expected.artist || '', row.artist || ''],
    ['Length', msClock(expected.duration_ms), msClock(row.duration)],
    ['Quality', 'your profile', row.quality_label || row.quality || ''],
  ];
  return (
    <div className="rdl-rej-evidence">
      <div className="rdl-rej-reason">
        {d?.detail || decisionPill(row, expected.duration_ms)}
        {score != null ? (
          <span className="rdl-rej-score"> · match {Math.round(score * 100)}%</span>
        ) : null}
      </div>
      <table className="rdl-rej-grid">
        <thead>
          <tr>
            <th scope="col">
              <span className="lib-sr-only">Field</span>
            </th>
            <th scope="col">Wanted</th>
            <th scope="col">Found</th>
          </tr>
        </thead>
        <tbody>
          {lines.map(([label, want, found]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{want || '—'}</td>
              <td>{found || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {blocked ? <div className="rdl-rej-blocked">{blocked}</div> : null}
    </div>
  );
}
