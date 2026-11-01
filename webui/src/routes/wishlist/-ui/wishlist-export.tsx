import { useEffect, useState } from 'react';

import {
  copyRecordText,
  jsonHighlightTokens,
} from '@/routes/artist-detail/-artist-detail.db-record';
import styles from '@/routes/library/-ui/library-v2-page.module.css';
import { LibraryToolDialog } from '@/routes/library/-ui/tool-dialog';

/**
 * Export the wishlist (c5pie): JSON or CSV, everything or one slice of it,
 * previewed before it is copied or downloaded. One dialog for both sides:
 * music slices by albums / singles, video by movies / TV shows.
 */
export type WishlistExportSide = 'music' | 'video';
type ExportFormat = 'json' | 'csv';

const SCOPES: Record<WishlistExportSide, [string, string][]> = {
  music: [
    ['all', 'Everything'],
    ['albums', 'Albums & EPs'],
    ['singles', 'Singles'],
  ],
  video: [
    ['all', 'Everything'],
    ['movie', 'Movies'],
    ['show', 'TV shows'],
  ],
};

/** the endpoint for one side, slice and format */
export function wishlistExportUrl(
  side: WishlistExportSide,
  scope: string,
  format: ExportFormat,
): string {
  if (side === 'video') {
    return `/api/video/wishlist/export?format=${format}${scope === 'all' ? '' : `&kind=${scope}`}`;
  }
  return `/api/wishlist/export?format=${format}${scope === 'all' ? '' : `&category=${scope}`}`;
}

export function wishlistExportFileName(
  side: WishlistExportSide,
  scope: string,
  format: ExportFormat,
): string {
  const slice = scope === 'all' ? '' : `_${scope}`;
  return `${side === 'video' ? 'video_wishlist' : 'wishlist'}${slice}.${format}`;
}

export function WishlistExportDialog({
  side,
  initialScope = 'all',
  onClose,
}: {
  side: WishlistExportSide;
  initialScope?: string;
  onClose: () => void;
}) {
  const [scope, setScope] = useState(
    SCOPES[side].some(([key]) => key === initialScope) ? initialScope : 'all',
  );
  const [format, setFormat] = useState<ExportFormat>('csv');
  const [state, setState] = useState<
    | { kind: 'loading' }
    | { kind: 'error'; message: string }
    | { kind: 'loaded'; content: string; count: string }
  >({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: 'loading' });
    fetch(wishlistExportUrl(side, scope, format))
      .then(async (response) => {
        const content = await response.text();
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return { content, count: response.headers.get('X-Export-Count') || '?' };
      })
      .then((result) => {
        if (!cancelled) setState({ kind: 'loaded', ...result });
      })
      .catch((error: Error) => {
        if (!cancelled) setState({ kind: 'error', message: error.message || String(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [side, scope, format]);

  const content = state.kind === 'loaded' ? state.content : '';
  const download = () => {
    const name = wishlistExportFileName(side, scope, format);
    const blob = new Blob([content], { type: format === 'csv' ? 'text/csv' : 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    window.showToast?.(`Saved ${name}`, 'success');
  };

  return (
    <LibraryToolDialog
      title="Export wishlist"
      description="Pick what to export and the format, then copy or download it."
      onClose={onClose}
      footer={
        state.kind === 'loaded' ? (
          <span className={styles.previewQuiet}>
            <b>{state.count}</b> {state.count === '1' ? 'item' : 'items'} · {format.toUpperCase()}
          </span>
        ) : null
      }
    >
      <div className={styles.previewToolbar}>
        <div className="arec-tabs" aria-label="What to export">
          {SCOPES[side].map(([key, label]) => (
            <button
              className={`arec-tab${scope === key ? ' active' : ''}`}
              aria-pressed={scope === key}
              type="button"
              key={key}
              onClick={() => setScope(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="arec-tabs" aria-label="Format">
          {(
            [
              ['csv', 'CSV'],
              ['json', 'JSON'],
            ] as [ExportFormat, string][]
          ).map(([key, label]) => (
            <button
              className={`arec-tab${format === key ? ' active' : ''}`}
              aria-pressed={format === key}
              type="button"
              key={key}
              onClick={() => setFormat(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="arec-actions">
          <button
            className={styles.btnGhost}
            disabled={state.kind !== 'loaded'}
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
            className={styles.btnGhost}
            disabled={state.kind !== 'loaded'}
            type="button"
            onClick={download}
          >
            Download
          </button>
        </div>
      </div>
      <div className={`arec-body ${styles.exportPreview}`}>
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
    </LibraryToolDialog>
  );
}

function safeParse(content: string): unknown {
  try {
    return JSON.parse(content || '[]');
  } catch {
    return [];
  }
}
