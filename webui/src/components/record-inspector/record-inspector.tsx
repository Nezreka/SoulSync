import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

import { useAccessibleModal } from '@/components/dialog/use-accessible-modal';
import { Button, TextInput } from '@/components/form/form';

import {
  type RecordPayload,
  copyRecordText,
  downloadRecord,
  jsonHighlightTokens,
  matchesRecordFilter,
  recordFooterStats,
  recordRows,
} from './record-helpers';
import './record-inspector.css';
export function RecordInspector({
  name,
  onClose,
  endpoint,
  recordKind = 'Artist',
  extraRecord,
  title,
  sourceDescription,
}: {
  endpoint?: string;
  recordKind?: string;
  extraRecord?: Record<string, unknown>;
  title?: string;
  sourceDescription?: string;
  name: string;
  onClose: () => void;
}) {
  const [payload, setPayload] = useState<RecordPayload | null>(null);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [tab, setTab] = useState<'fields' | 'json'>('fields');
  const [filter, setFilter] = useState('');
  // The overlay fades in on the frame AFTER mount; without the two-step the
  // CSS transition has no starting state to animate from.
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const frame = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  const modal = useAccessibleModal(onClose);

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    setPayload(null);
    void (async () => {
      try {
        if (!endpoint) {
          setPayload({ success: true, record: {} });
          return;
        }
        const response = await fetch(endpoint, {
          signal: controller.signal,
        });
        const data = (await response.json()) as RecordPayload;
        if (!data?.success) throw new Error(data?.error || 'Request failed');
        setPayload(data);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError((err as Error).message || String(err));
      }
    })();
    return () => controller.abort();
  }, [endpoint]);

  const record = { ...payload?.record, ...extraRecord };
  const stats = payload ? recordFooterStats(payload) : null;

  return createPortal(
    <div
      id="artist-record-overlay"
      className={`record-inspector arec-overlay${visible ? ' visible' : ''}`}
      // Only a click on the backdrop itself closes; a click that started inside
      // the card must not.
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={modal.ref}
        className="arec-card"
        data-shared-modal=""
        aria-modal="true"
        role="dialog"
        aria-label={title || `${recordKind} database record`}
      >
        <div className="arec-header">
          <div className="arec-title-wrap">
            <div className="arec-title">
              <span className="arec-dot" />
              {title || `${recordKind} DB Record`}
            </div>
            <div className="arec-sub" id="arec-sub">
              {name}
            </div>
          </div>
          <Button
            size="sm"
            aria-label="Close record"
            className="arec-close"
            id="arec-close"
            title="Close (Esc)"
            onClick={onClose}
          >
            ×
          </Button>
        </div>

        <div className="arec-toolbar">
          <div className="arec-tabs">
            <Button
              size="sm"
              className={`arec-tab${tab === 'fields' ? ' active' : ''}`}
              data-tab="fields"
              aria-pressed={tab === 'fields'}
              onClick={() => setTab('fields')}
            >
              Fields
            </Button>
            <Button
              size="sm"
              className={`arec-tab${tab === 'json' ? ' active' : ''}`}
              data-tab="json"
              aria-pressed={tab === 'json'}
              onClick={() => setTab('json')}
            >
              JSON
            </Button>
          </div>
          {/* Hidden with visibility, not display: the toolbar keeps its layout
                so the action buttons do not shift when you switch tabs. */}
          <TextInput
            aria-label="Filter record fields"
            id="arec-filter"
            className="arec-filter"
            type="text"
            placeholder="filter fields…"
            autoComplete="off"
            spellCheck={false}
            style={{ visibility: tab === 'json' ? 'hidden' : undefined }}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <div className="arec-actions">
            <Button
              size="sm"
              className="arec-btn"
              id="arec-copy"
              onClick={() => {
                void copyRecordText(JSON.stringify(record, null, 2))
                  .then(() => setFeedback('Full record copied as JSON'))
                  .catch(() => setFeedback('Could not copy record'));
              }}
            >
              Copy JSON
            </Button>
            <Button
              size="sm"
              className="arec-btn"
              id="arec-download"
              onClick={() => window.showToast?.(`Saved ${downloadRecord(record, name)}`, 'success')}
            >
              Save .json
            </Button>
          </div>
        </div>

        <p className="record-source">
          {sourceDescription ||
            (endpoint
              ? 'Library database record fetched when opened.'
              : 'Review evidence snapshot; no current database record is available.')}{' '}
          {extraRecord
            ? 'Embedded tags and audio identity below are snapshots from the review analysis, not a fresh file read.'
            : ''}
        </p>
        <div className="arec-body" id="arec-body">
          {error ? (
            <div className="arec-error">Could not load record: {error}</div>
          ) : !payload ? (
            <div className="arec-loading">Loading record…</div>
          ) : tab === 'json' ? (
            <pre className="arec-code">
              {jsonHighlightTokens(record).map((token, index) =>
                token.className ? (
                  <span className={token.className} key={index}>
                    {token.text}
                  </span>
                ) : (
                  token.text
                ),
              )}
            </pre>
          ) : (
            <div className="arec-fields">
              {recordRows(record).map((row) => (
                <div
                  className={`arec-row${row.isEmpty ? ' is-empty' : ''}`}
                  key={row.key}
                  data-field={row.filterKey}
                  style={{ display: matchesRecordFilter(row, filter) ? undefined : 'none' }}
                >
                  <span className="arec-key">{row.key}</span>
                  <span className="arec-val">
                    {row.isEmpty ? (
                      <span className="arec-null">null</span>
                    ) : row.isJson ? (
                      <span className="arec-json">{row.text}</span>
                    ) : (
                      row.text
                    )}
                  </span>
                  <Button
                    size="sm"
                    className="arec-rowcopy"
                    title="Copy value"
                    data-copy={row.copyValue}
                    onClick={() => {
                      void copyRecordText(row.copyValue)
                        .then(() => setFeedback('Value copied'))
                        .catch(() => setFeedback('Could not copy value'));
                    }}
                  >
                    ⧉
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="arec-footer" id="arec-footer">
          <span role="status">{feedback}</span>
          {stats && recordKind === 'Artist' ? (
            <>
              <span>
                <b>{stats.fields}</b> fields
              </span>
              <span>
                <b>{stats.albums}</b> albums
              </span>
              <span>
                <b>{stats.tracks}</b> tracks
              </span>
              <span>
                <b>{stats.matched}</b> sources matched
              </span>
              <span className="arec-id">id {stats.id}</span>
            </>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}
