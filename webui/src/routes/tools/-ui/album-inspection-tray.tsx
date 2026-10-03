import { useEffect, useState } from 'react';

import type { FindingAlbumGroup } from '../-tools.api';
import type { RepairFinding } from '../-tools.types';

import { fetchRepairFindings, fixFinding, dismissFinding, reopenFinding } from '../-tools.api';
import { findingFixLabel, findingSeverityIcon, findingTypeLabel } from '../-tools.core';
import { VinylCoverFallback } from './album-cover-fallback';

export interface AlbumInspectionTrayProps {
  group: FindingAlbumGroup;
  status: string;
  onClose: () => void;
  onFixFinding: (finding: RepairFinding) => Promise<void>;
  onDismissFinding: (id: number) => Promise<void>;
  onInspectRedownload: (finding: RepairFinding) => void;
  onRefresh: () => void;
  onReopenFinding?: (id: number) => Promise<void>;
}

export function AlbumInspectionTray({
  group,
  status,
  onClose,
  onFixFinding,
  onDismissFinding,
  onInspectRedownload,
  onRefresh,
  onReopenFinding,
}: AlbumInspectionTrayProps) {
  const [findings, setFindings] = useState<RepairFinding[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);

  const albumName = group.album || 'Unknown Album';
  const artistName = group.artist || 'Unknown Artist';
  const coverArt = group.album_thumb_url || group.artist_thumb_url;

  const loadAlbumFindings = async () => {
    setLoading(true);
    setActionError(null);
    try {
      const query = group.album || group.artist || '';
      const result = await fetchRepairFindings({
        q: query,
        status: status || 'pending',
        page: 0,
        limit: 100,
      });

      // Filter findings strictly for this album/artist if query returned related rows
      const filtered = (result.items || []).filter((item) => {
        const d = item.details || {};
        const matchAlbum = d.album_title === group.album || d.album === group.album;
        const matchArtist =
          d.artist_name === group.artist ||
          d.artist === group.artist ||
          d.expected_artist === group.artist;
        if (group.group_by === 'artist') return matchArtist;
        return (
          matchAlbum || (matchArtist && String(item.file_path || '').includes(group.album || ''))
        );
      });

      setFindings(filtered.length > 0 ? filtered : result.items || []);
    } catch (err) {
      setActionError((err as Error).message || 'Failed to load album issues');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAlbumFindings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group.key, status]);

  const handleFixOne = async (finding: RepairFinding) => {
    setBusyIds((prev) => new Set(prev).add(finding.id));
    try {
      await onFixFinding(finding);
      await loadAlbumFindings();
    } finally {
      setBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(finding.id);
        return next;
      });
    }
  };

  const handleDismissOne = async (id: number) => {
    setBusyIds((prev) => new Set(prev).add(id));
    try {
      await onDismissFinding(id);
      await loadAlbumFindings();
    } finally {
      setBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const handleReopenOne = async (id: number) => {
    setBusyIds((prev) => new Set(prev).add(id));
    try {
      if (onReopenFinding) {
        await onReopenFinding(id);
      } else {
        await reopenFinding(id);
      }
      await loadAlbumFindings();
      onRefresh();
      window.showToast?.('Issue reopened and moved back to Pending', 'info');
    } catch (err) {
      window.showToast?.((err as Error).message || 'Failed to reopen issue', 'error');
    } finally {
      setBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const handleFixAllOnAlbum = async () => {
    if (!findings || findings.length === 0) return;
    const fixable = findings.filter(
      (f) => f.status === 'pending' && Boolean(findingFixLabel(f.finding_type)),
    );
    if (fixable.length === 0) return;

    const hasDestructive = fixable.some(
      (f) => f.finding_type === 'corrupt_audio' || f.finding_type === 'orphan_file',
    );
    if (hasDestructive) {
      const confirmed = await window.showConfirmDialog?.({
        title: 'Resolve Album Issues',
        message: `This album has ${fixable.length} issues, including files that will be moved to quarantine and re-downloaded. Proceed?`,
        confirmText: 'Resolve & Replace',
        destructive: false,
      });
      if (!confirmed) return;
    }

    for (const f of fixable) {
      setBusyIds((prev) => new Set(prev).add(f.id));
      try {
        await fixFinding(f.id);
      } catch {}
    }
    setBusyIds(new Set());
    await loadAlbumFindings();
    onRefresh();
    window.showToast?.(`Resolved all issues on "${albumName}"`, 'success');
  };

  const handleDismissAllOnAlbum = async () => {
    if (!findings || findings.length === 0) return;
    const pending = findings.filter((f) => f.status === 'pending');
    for (const f of pending) {
      setBusyIds((prev) => new Set(prev).add(f.id));
      try {
        await dismissFinding(f.id);
      } catch {}
    }
    setBusyIds(new Set());
    await loadAlbumFindings();
    onRefresh();
    window.showToast?.(`Dismissed issues on "${albumName}"`, 'info');
  };

  const handleReopenAllOnAlbum = async () => {
    if (!findings || findings.length === 0) return;
    const resolvedOrDismissed = findings.filter(
      (f) => f.status === 'resolved' || f.status === 'dismissed',
    );
    for (const f of resolvedOrDismissed) {
      setBusyIds((prev) => new Set(prev).add(f.id));
      try {
        await reopenFinding(f.id);
      } catch {}
    }
    setBusyIds(new Set());
    await loadAlbumFindings();
    onRefresh();
    window.showToast?.(`Reopened all issues on "${albumName}"`, 'info');
  };

  const isRedownloadFinding = (type: string) => {
    return ['quality_upgrade', 'dead_file', 'short_preview_track', 'corrupt_audio'].includes(type);
  };

  return (
    <div
      className="album-inspection-tray"
      id="album-inspection-tray"
      role="region"
      aria-label="Album Issue Inspector"
    >
      <div className="album-tray-header">
        <div className="album-tray-art-wrap">
          {coverArt ? (
            <img src={coverArt} alt="" className="album-tray-art" />
          ) : (
            <VinylCoverFallback
              name={albumName}
              initialChar={albumName.charAt(0).toUpperCase() || '♪'}
            />
          )}
        </div>

        <div className="album-tray-info">
          <div className="album-tray-badges">
            <span className="album-tray-type-badge">Album Action Center</span>
            {group.worst_quality ? (
              <span className="album-tray-quality-badge">{group.worst_quality}</span>
            ) : null}
            {group.error_count && group.error_count > 0 ? (
              <span className="album-tray-error-badge">⚠️ {group.error_count} Failed Fixes</span>
            ) : null}
          </div>
          <h3 className="album-tray-title">{albumName}</h3>
          <p className="album-tray-artist">{artistName}</p>
        </div>

        <div className="album-tray-actions">
          {status === 'pending' || !status ? (
            <>
              <button
                type="button"
                className="btn btn--sm btn--primary album-tray-btn"
                onClick={() => void handleFixAllOnAlbum()}
                title="Automatically resolve all safe issues on this album"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                >
                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                </svg>
                Resolve Album
              </button>
              <button
                type="button"
                className="btn btn--sm btn--secondary album-tray-btn"
                onClick={() => void handleDismissAllOnAlbum()}
                title="Dismiss all issues on this album"
              >
                Dismiss All
              </button>
            </>
          ) : status === 'resolved' ? (
            <>
              <span className="album-tray-status-tag resolved">
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                >
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                All Resolved
              </span>
              <button
                type="button"
                className="btn btn--sm btn--secondary album-tray-btn"
                onClick={() => void handleReopenAllOnAlbum()}
                title="Reopen all resolved issues on this album"
              >
                ↺ Reopen Album
              </button>
            </>
          ) : status === 'dismissed' ? (
            <>
              <span className="album-tray-status-tag dismissed">Dismissed</span>
              <button
                type="button"
                className="btn btn--sm btn--secondary album-tray-btn"
                onClick={() => void handleReopenAllOnAlbum()}
                title="Reopen all dismissed issues on this album"
              >
                ↺ Reopen Album
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="album-tray-close"
            onClick={onClose}
            aria-label="Close album tray"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      {actionError ? (
        <div className="album-tray-alert error">
          <span>⚠️</span>
          <span>{actionError}</span>
        </div>
      ) : null}

      <div className="album-tray-body">
        {loading ? (
          <div className="album-tray-loading">
            <div className="server-search-spinner" />
            <span>Auditing issues for {albumName}...</span>
          </div>
        ) : !findings || findings.length === 0 ? (
          <div className="album-tray-empty">
            <span>✨ No issues found on this album for the current filter.</span>
          </div>
        ) : (
          <div className="album-tray-tracks-list">
            {findings.map((f, idx) => {
              const details = (f.details as Record<string, any>) || {};
              const fixLabel = findingFixLabel(f.finding_type);
              const busy = busyIds.has(f.id);
              const isRedl = isRedownloadFinding(f.finding_type);

              // Never display database row ID (f.entity_id) as track number!
              const rawNum = details.track_number;
              const trackNumStr =
                typeof rawNum === 'number' && rawNum > 0
                  ? String(rawNum).padStart(2, '0')
                  : typeof rawNum === 'string' && /^\d+$/.test(rawNum.trim())
                    ? String(parseInt(rawNum.trim(), 10)).padStart(2, '0')
                    : String(idx + 1).padStart(2, '0');

              const formatStr = details.current_quality || details.current_format || details.format;

              return (
                <div
                  key={f.id}
                  className={`album-tray-track-card ${f.severity} ${
                    f.last_error ? 'has-error' : ''
                  }`}
                >
                  <div className="album-track-main">
                    <div className="album-track-num-box">{trackNumStr}</div>

                    <div className="album-track-meta">
                      <div className="album-track-title-row">
                        <span className="album-track-title">
                          {details.track_title || details.title || f.title}
                        </span>
                        <span className={`album-track-issue-tag ${f.finding_type}`}>
                          {findingTypeLabel(f.finding_type)}
                        </span>
                        {formatStr ? (
                          <span className="album-track-format-tag">{formatStr}</span>
                        ) : null}
                      </div>

                      <div className="album-track-desc">
                        {f.description || details.description || f.file_path}
                      </div>

                      {f.finding_type === 'corrupt_audio' ? (
                        <div className="album-track-diagnostic corrupt">
                          <span className="diagnostic-icon">🔴</span>
                          <span className="diagnostic-text">
                            <strong>Corrupt Audio:</strong>{' '}
                            {details.error_type ||
                              'Frame checksum mismatch or header damage. Re-downloading replaces and quarantines this file.'}
                          </span>
                        </div>
                      ) : f.finding_type === 'fake_lossless' ? (
                        <div className="album-track-diagnostic fake">
                          <span className="diagnostic-icon">🟡</span>
                          <span className="diagnostic-text">
                            <strong>Spectral Transcode:</strong>{' '}
                            {details.spectral_status ||
                              'Frequency drops off sharply at 16.0 kHz. File appears to be an upscaled lossy transcode.'}
                          </span>
                        </div>
                      ) : null}

                      {f.last_error ? (
                        <div className="album-track-error-callout" role="alert">
                          <span className="error-icon">⚠️</span>
                          <span className="error-text">
                            <strong>Last attempt failed:</strong> {f.last_error}
                          </span>
                          {fixLabel && (
                            <button
                              type="button"
                              className="album-error-retry-btn"
                              disabled={busy}
                              onClick={() => void handleFixOne(f)}
                              title="Retry resolving this track"
                            >
                              {busy ? '...' : 'Retry'}
                            </button>
                          )}
                        </div>
                      ) : null}
                    </div>

                    <div className="album-track-actions">
                      {f.status === 'pending' ? (
                        <>
                          {isRedl ? (
                            <button
                              type="button"
                              className="btn btn--xs btn--primary album-action-btn redownload"
                              disabled={busy}
                              onClick={() => onInspectRedownload(f)}
                              title="Inspect and re-download across Soulseek, Tidal, Qobuz, Deezer, YouTube"
                            >
                              <svg
                                width="12"
                                height="12"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2.5"
                              >
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                <polyline points="7 10 12 15 17 10" />
                                <line x1="12" y1="15" x2="12" y2="3" />
                              </svg>
                              Re-download
                            </button>
                          ) : fixLabel ? (
                            <button
                              type="button"
                              className="btn btn--xs btn--primary album-action-btn"
                              disabled={busy}
                              onClick={() => void handleFixOne(f)}
                            >
                              {busy ? '...' : fixLabel}
                            </button>
                          ) : null}

                          <button
                            type="button"
                            className="btn btn--xs btn--secondary album-action-btn dismiss"
                            disabled={busy}
                            onClick={() => void handleDismissOne(f.id)}
                            title="Dismiss issue"
                          >
                            Dismiss
                          </button>
                        </>
                      ) : f.status === 'resolved' ? (
                        <div className="album-track-status-group">
                          <span className="album-track-status-pill resolved">
                            <svg
                              width="11"
                              height="11"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="3"
                            >
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                            Fixed
                          </span>
                          <button
                            type="button"
                            className="album-action-reopen-btn"
                            disabled={busy}
                            onClick={() => void handleReopenOne(f.id)}
                            title="Reopen issue and move back to Pending"
                          >
                            ↺ Reopen
                          </button>
                        </div>
                      ) : (
                        <div className="album-track-status-group">
                          <span className={`album-track-status-pill ${f.status}`}>{f.status}</span>
                          <button
                            type="button"
                            className="album-action-reopen-btn"
                            disabled={busy}
                            onClick={() => void handleReopenOne(f.id)}
                            title="Reopen issue and move back to Pending"
                          >
                            ↺ Reopen
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
