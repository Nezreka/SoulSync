import { useEffect, useRef, useState } from 'react';

import type { SourceDownload } from '../-artist-detail.manage-actions';

import {
  blacklistSourceRequest,
  fetchTrackSourceInfo,
  SOURCE_SERVICES,
  sourceInfoRows,
} from '../-artist-detail.manage-actions';

/**
 * Download-provenance popover (showTrackSourceInfo, library.js:3192): where a
 * track's file actually came from, with a blacklist action when the download
 * carried a Soulseek username + filename. Anchored beside the ℹ button; the
 * vanilla centered it when opened from the mobile action sheet (anchor null).
 */

interface Props {
  trackId: unknown;
  trackTitle: string;
  /** Position anchor; null centers the popover (mobile sheet path). */
  anchor: HTMLElement | null;
  onClose: () => void;
}

type State =
  | { kind: 'loading' }
  | { kind: 'empty' }
  | { kind: 'error'; message: string }
  | { kind: 'loaded'; downloads: SourceDownload[] };

/** Mirror of the vanilla's placement math (3205-3218). */
function anchoredPosition(anchor: HTMLElement): { left: number; top: number } {
  const rect = anchor.getBoundingClientRect();
  const popW = 360;
  let left = rect.left - popW - 8;
  if (left < 10) left = rect.right + 8;
  let top = rect.top - 20;
  if (top + 300 > window.innerHeight) top = window.innerHeight - 310;
  return { left, top: Math.max(10, top) };
}

export function SourceInfoPopover({ trackId, trackTitle, anchor, onClose }: Props) {
  const [state, setState] = useState<State>({ kind: 'loading' });
  const [blacklisted, setBlacklisted] = useState(false);
  const popRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchTrackSourceInfo(trackId)
      .then((downloads) => {
        if (cancelled) return;
        setState(downloads.length ? { kind: 'loaded', downloads } : { kind: 'empty' });
      })
      .catch((e: Error) => {
        if (!cancelled) setState({ kind: 'error', message: e.message });
      });
    return () => {
      cancelled = true;
    };
  }, [trackId]);

  useEffect(() => {
    // Outside click closes; delayed registration so the opening click itself
    // does not immediately dismiss (the vanilla's 100ms guard, 3229).
    const onDocClick = (e: MouseEvent) => {
      const el = popRef.current;
      if (el && !el.contains(e.target as Node) && e.target !== anchor) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const timer = setTimeout(() => document.addEventListener('click', onDocClick), 100);
    document.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('click', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [anchor, onClose]);

  const style: React.CSSProperties = anchor
    ? { ...anchoredPosition(anchor) }
    : { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' };

  const dl = state.kind === 'loaded' ? state.downloads[0] : null;
  const displayFile = dl?.source_filename
    ? dl.source_filename.replace(/\\/g, '/').split('/').pop() || 'Unknown'
    : 'Unknown';

  const blacklist = async () => {
    if (!dl) return;
    const svcLabel = SOURCE_SERVICES[dl.source_service || '']?.label || dl.source_service;
    const from = dl.source_service === 'soulseek' ? dl.source_username : svcLabel;
    const confirmed = await window.showConfirmDialog?.({
      title: 'Blacklist Source',
      message: `Blacklist "${displayFile}" from ${from}? This source will be skipped in future downloads.`,
      confirmText: 'Blacklist',
      destructive: true,
    });
    if (!confirmed) return;
    try {
      const result = await blacklistSourceRequest(dl, trackTitle);
      if (result.success) {
        window.showToast?.('Source blacklisted', 'success');
        setBlacklisted(true);
      } else {
        window.showToast?.(result.error || 'Failed to blacklist', 'error');
      }
    } catch (e) {
      window.showToast?.('Error: ' + (e as Error).message, 'error');
    }
  };

  return (
    <div
      id="source-info-popover"
      className="source-info-popover visible"
      style={style}
      ref={popRef}
    >
      <div className="source-info-header">
        <span className="source-info-title">Source Info</span>
        <button className="source-info-close" type="button" onClick={onClose}>
          ×
        </button>
      </div>
      {state.kind === 'loading' ? (
        <div className="source-info-loading">
          <div className="server-search-spinner" />
          Loading source info...
        </div>
      ) : state.kind === 'error' ? (
        <div className="source-info-empty">Error loading source info: {state.message}</div>
      ) : state.kind === 'empty' ? (
        <div className="source-info-empty">
          No download source data available for this track. Source tracking starts with new
          downloads.
        </div>
      ) : dl ? (
        <>
          <div className="source-info-body">
            {sourceInfoRows(dl).map((row) => (
              <div className="source-info-row" key={row.label}>
                <span className="source-info-label">{row.label}</span>
                <span
                  className={
                    'source-info-value' +
                    (row.mono ? ' source-info-mono' : '') +
                    (row.label === 'Original File' ? ' source-info-ellipsis' : '')
                  }
                  style={row.tone === 'error' ? { color: '#ef5350' } : undefined}
                  title={row.label === 'Original File' ? dl.source_filename || '' : undefined}
                >
                  {row.value}
                </span>
              </div>
            ))}
          </div>
          {dl.source_username && dl.source_filename ? (
            <div className="source-info-actions">
              <button
                className="source-info-blacklist-btn"
                type="button"
                disabled={blacklisted}
                onClick={blacklist}
              >
                {blacklisted ? '⛔ Blacklisted' : '⛔ Blacklist This Source'}
              </button>
            </div>
          ) : null}
          {state.downloads.length > 1 ? (
            <div className="source-info-history">
              {state.downloads.length} download records for this track
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
