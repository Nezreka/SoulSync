import { useEffect, useState } from 'react';

import type { ExportFormat, ExportScope } from '../-library.export';

import { downloadExport, downloadLibraryM3u, fetchExport } from '../-library.export';
// The vanilla shared these very helpers between the DB-record inspector and
// this export modal (library.js:7369 "Reuses the DB-record modal aesthetic +
// helpers"); the import mirrors that shared-ness.
import { copyRecordText, jsonHighlightTokens } from '../../artist-detail/-artist-detail.db-record';

/**
 * Export Artists (openArtistExportModal, library.js:7372): scope tabs
 * (Watchlist | Library), format tabs (JSON / CSV / text), the external-links
 * toggle, the library-only counts toggle + M3U, and a syntax-highlighted
 * preview with Copy / Download.
 */
export function ExportArtistsModal({
  initialScope = 'watchlist',
  onClose,
}: {
  initialScope?: ExportScope;
  onClose: () => void;
}) {
  const [scope, setScope] = useState<ExportScope>(initialScope);
  const [format, setFormat] = useState<ExportFormat>('json');
  const [links, setLinks] = useState(false);
  const [contents, setContents] = useState(false);
  const [state, setState] = useState<
    | { kind: 'loading' }
    | { kind: 'error'; message: string }
    | { kind: 'loaded'; content: string; count: string }
  >({ kind: 'loading' });

  // "library counts" only applies to the library roster (7419-7428).
  const effectiveContents = scope === 'library' && contents;

  useEffect(() => {
    let cancelled = false;
    setState({ kind: 'loading' });
    fetchExport(scope, format, links, effectiveContents)
      .then((result) => {
        if (!cancelled) setState({ kind: 'loaded', ...result });
      })
      .catch((error: Error) => {
        if (!cancelled) setState({ kind: 'error', message: error.message || String(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [scope, format, links, effectiveContents]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const content = state.kind === 'loaded' ? state.content : '';

  return (
    <div
      className="arec-overlay visible"
      id="wl-export-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="arec-card" role="dialog" aria-label="Export artists">
        <div className="arec-header">
          <div className="arec-title-wrap">
            <div className="arec-title">
              <span className="arec-dot" />
              Export Artists
            </div>
            <div className="arec-tabs" id="wlx-scope" style={{ marginTop: 7 }}>
              {(['watchlist', 'library'] as ExportScope[]).map((s) => (
                <button
                  className={`arec-tab${scope === s ? ' active' : ''}`}
                  type="button"
                  data-scope={s}
                  key={s}
                  onClick={() => setScope(s)}
                >
                  {s === 'watchlist' ? 'Watchlist' : 'Library'}
                </button>
              ))}
            </div>
          </div>
          <button className="arec-close" id="wlx-close" title="Close (Esc)" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="arec-toolbar">
          <div className="arec-tabs" id="wlx-format">
            {(
              [
                ['json', 'JSON'],
                ['csv', 'CSV'],
                ['txt', 'Text'],
              ] as [ExportFormat, string][]
            ).map(([f, label]) => (
              <button
                className={`arec-tab${format === f ? ' active' : ''}`}
                type="button"
                data-fmt={f}
                key={f}
                onClick={() => setFormat(f)}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="wlx-opt">
            <input
              type="checkbox"
              id="wlx-links"
              checked={links}
              onChange={(e) => setLinks(e.target.checked)}
            />{' '}
            external links
          </label>
          {scope === 'library' ? (
            <label className="wlx-opt" id="wlx-contents-wrap">
              <input
                type="checkbox"
                id="wlx-contents"
                checked={contents}
                onChange={(e) => setContents(e.target.checked)}
              />{' '}
              library counts
            </label>
          ) : null}
          <div className="arec-actions">
            <button
              className="arec-btn"
              id="wlx-copy"
              type="button"
              onClick={() => {
                void copyRecordText(content).then(() =>
                  window.showToast?.('Export copied', 'success'),
                );
              }}
            >
              Copy
            </button>
            <button
              className="arec-btn"
              id="wlx-download"
              type="button"
              onClick={() => downloadExport(content, scope, format)}
            >
              Download
            </button>
            {/* A track-level export only makes sense for the library (7421). */}
            {scope === 'library' ? (
              <button
                className="arec-btn arec-btn-m3u"
                id="wlx-m3u"
                type="button"
                onClick={downloadLibraryM3u}
              >
                Download M3U
              </button>
            ) : null}
          </div>
        </div>
        <div className="arec-body" id="wlx-body">
          {state.kind === 'loading' ? (
            <div className="arec-loading">Building export…</div>
          ) : state.kind === 'error' ? (
            <div className="arec-error">Export failed: {state.message}</div>
          ) : format === 'json' ? (
            <pre className="arec-code">
              {jsonHighlightTokens(safeParse(state.content)).map((token, index) =>
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
            <pre className="arec-code">{state.content || '(empty)'}</pre>
          )}
        </div>
        <div className="arec-footer" id="wlx-footer">
          {state.kind === 'loaded' ? (
            <>
              <span>
                <b>{state.count}</b> {scope === 'library' ? 'library' : 'watchlist'} artists
              </span>
              <span className="arec-id">{format.toUpperCase()}</span>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function safeParse(content: string): unknown {
  try {
    return JSON.parse(content || '[]');
  } catch {
    return [];
  }
}
