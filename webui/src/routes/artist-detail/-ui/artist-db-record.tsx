import { useEffect, useState } from 'react';

import type { ArtistInfo } from '../-artist-detail.types';

import {
  type ArtistRecordPayload,
  copyRecordText,
  downloadRecord,
  jsonHighlightTokens,
  matchesRecordFilter,
  recordFooterStats,
  recordRows,
  showsDbRecordButton,
} from '../-artist-detail.db-record';

interface Props {
  artist: ArtistInfo;
  isSourceArtist: boolean;
}

/**
 * The "DB Record" inspector button and its modal.
 *
 * The vanilla appended the button to #artist-hero-section by hand and rebuilt
 * it on every artist; here it is part of the hero, which is why the
 * library-only condition is a render guard rather than a display toggle.
 */
export function ArtistDbRecord({ artist, isSourceArtist }: Props) {
  const [open, setOpen] = useState(false);
  if (!showsDbRecordButton(artist, isSourceArtist)) return null;

  return (
    <>
      <button
        type="button"
        className="artist-db-record-btn"
        id="artist-db-record-btn"
        title="Inspect everything the database knows about this artist"
        onClick={() => setOpen(true)}
      >
        <svg
          viewBox="0 0 24 24"
          width="14"
          height="14"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <ellipse cx="12" cy="5" rx="8" ry="3" />
          <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
          <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
        </svg>
        <span>DB Record</span>
      </button>

      {open ? (
        <ArtistRecordModal
          artistId={artist.id}
          artistName={artist.name || 'Artist'}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

function ArtistRecordModal({
  artistId,
  artistName,
  onClose,
}: {
  artistId: unknown;
  artistName: string;
  onClose: () => void;
}) {
  const [payload, setPayload] = useState<ArtistRecordPayload | null>(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'fields' | 'json'>('fields');
  const [filter, setFilter] = useState('');
  // The overlay fades in on the frame AFTER mount; without the two-step the
  // CSS transition has no starting state to animate from.
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const frame = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`/api/artist/${encodeURIComponent(String(artistId))}/record`, {
          signal: controller.signal,
        });
        const data = (await response.json()) as ArtistRecordPayload;
        if (!data?.success) throw new Error(data?.error || 'Request failed');
        setPayload(data);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError((err as Error).message || String(err));
      }
    })();
    return () => controller.abort();
  }, [artistId]);

  const record = payload?.record ?? {};
  const stats = payload ? recordFooterStats(payload) : null;

  return (
    <div
      id="artist-record-overlay"
      className={`arec-overlay${visible ? ' visible' : ''}`}
      // Only a click on the backdrop itself closes; a click that started inside
      // the card must not.
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="arec-card" role="dialog" aria-label="Artist database record">
        <div className="arec-header">
          <div className="arec-title-wrap">
            <div className="arec-title">
              <span className="arec-dot" />
              Artist DB Record
            </div>
            <div className="arec-sub" id="arec-sub">
              {artistName}
            </div>
          </div>
          <button className="arec-close" id="arec-close" title="Close (Esc)" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="arec-toolbar">
          <div className="arec-tabs">
            <button
              className={`arec-tab${tab === 'fields' ? ' active' : ''}`}
              data-tab="fields"
              onClick={() => setTab('fields')}
            >
              Fields
            </button>
            <button
              className={`arec-tab${tab === 'json' ? ' active' : ''}`}
              data-tab="json"
              onClick={() => setTab('json')}
            >
              JSON
            </button>
          </div>
          {/* Hidden with visibility, not display: the toolbar keeps its layout
              so the action buttons do not shift when you switch tabs. */}
          <input
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
            <button
              className="arec-btn"
              id="arec-copy"
              onClick={() => {
                void copyRecordText(JSON.stringify(record, null, 2));
                window.showToast?.('Full record copied as JSON', 'success');
              }}
            >
              Copy JSON
            </button>
            <button
              className="arec-btn"
              id="arec-download"
              onClick={() =>
                window.showToast?.(`Saved ${downloadRecord(record, artistName)}`, 'success')
              }
            >
              Save .json
            </button>
          </div>
        </div>

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
                  <button
                    className="arec-rowcopy"
                    title="Copy value"
                    data-copy={row.copyValue}
                    onClick={() => {
                      void copyRecordText(row.copyValue);
                      window.showToast?.('Value copied', 'success');
                    }}
                  >
                    ⧉
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="arec-footer" id="arec-footer">
          {stats ? (
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
    </div>
  );
}
