import { useEffect, useMemo, useState } from 'react';

import type { FindingAlbumGroup } from '../-tools.api';
import type { FindingTypeInfo } from '../-tools.groups';
import type { RepairFinding } from '../-tools.types';

import {
  albumFindingAction,
  fetchFindingTypes,
  fetchRepairFindings,
  reopenFinding,
} from '../-tools.api';
import {
  findingRedownloadTrackId,
  findingRowFixLabel,
  findingSeverityIcon,
  findingTypeLabel,
} from '../-tools.core';
import { VinylCoverFallback } from './album-cover-fallback';

export interface AlbumInspectionTrayProps {
  group: FindingAlbumGroup;
  status: string;
  /** the grid's own filters, so the tray lists what the card counted */
  jobId?: string;
  findingType?: string;
  onClose: () => void;
  onFixFinding: (finding: RepairFinding) => Promise<void>;
  onDismissFinding: (id: number) => Promise<void>;
  onInspectRedownload: (finding: RepairFinding) => void;
  onRefresh: () => void;
  onReopenFinding?: (id: number) => Promise<void>;
}

/** what the tray loads at most; the header says when a group holds more */
const TRAY_LIMIT = 100;

// before the served catalog loads, the client list is the only guess
const FALLBACK_DESTRUCTIVE = new Set(['corrupt_audio', 'fake_lossless', 'orphan_file']);

/** the group's identity as the server keys it, or null when it has none.
 *  the tray asks for exactly these rows and never searches by title: a
 *  short title like "FM" matched 100 other albums' findings, and the tray
 *  showed, fixed and dismissed those (review 5 W3-TU-1). */
export function groupIdentity(group: FindingAlbumGroup) {
  const groupBy = group.group_by === 'artist' ? 'artist' : 'album';
  const artist = group.artist == null ? '' : String(group.artist);
  const album = group.album == null ? '' : String(group.album);
  if (!artist || (groupBy === 'album' && !album)) return null;
  return { groupBy, artist, album: groupBy === 'album' ? album : null } as const;
}

export function AlbumInspectionTray({
  group,
  status,
  jobId,
  findingType,
  onClose,
  onFixFinding,
  onDismissFinding,
  onInspectRedownload,
  onRefresh,
  onReopenFinding,
}: AlbumInspectionTrayProps) {
  const [findings, setFindings] = useState<RepairFinding[] | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);
  const [types, setTypes] = useState<FindingTypeInfo[]>([]);

  const albumName = group.album || 'Unknown Album';
  const artistName = group.artist || 'Unknown Artist';
  const coverArt = group.album_thumb_url || group.artist_thumb_url;
  const identity = groupIdentity(group);

  useEffect(() => {
    let live = true;
    void fetchFindingTypes().then((rows) => {
      if (live) setTypes(rows);
    });
    return () => {
      live = false;
    };
  }, []);

  const typeMap = useMemo(() => new Map(types.map((info) => [info.type, info])), [types]);

  /** the row's fix label. the served catalog decides, like the findings list
   *  does: the client list misses types the server can fix (corrupt audio
   *  with a track, mbid mismatch, path mismatch...), so those had no button
   *  here (review 5 W3-TU-1). the client list only covers the first load. */
  const rowFixLabel = (finding: RepairFinding): string | null => {
    const special = findingRowFixLabel(finding);
    if (special === 'Open album' || special === 'Delete File') return special;
    const info = typeMap.get(finding.finding_type);
    if (info) return info.fixable ? info.verb || 'Fix' : null;
    return special;
  };
  const isDestructive = (type: string) => {
    const info = typeMap.get(type);
    return info ? info.destructive : FALLBACK_DESTRUCTIVE.has(type);
  };

  const loadAlbumFindings = async () => {
    setLoading(true);
    setActionError(null);
    if (!identity) {
      // no exact identity, no rows: a guess is how another album's findings
      // got fixed from this tray
      setFindings([]);
      setTotal(0);
      setActionError('This group has no artist or album name, so its issues can’t be matched.');
      setLoading(false);
      return;
    }
    try {
      const result = await fetchRepairFindings({
        group: identity,
        jobId,
        findingType,
        status: status || 'pending',
        page: 0,
        limit: TRAY_LIMIT,
      });
      setFindings(result.items || []);
      setTotal(result.total || 0);
    } catch (err) {
      setFindings([]);
      setTotal(0);
      setActionError((err as Error).message || 'Failed to load album issues');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAlbumFindings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group.key, group.group_by, status, jobId, findingType]);

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

  const markBusy = (ids: number[]) => setBusyIds(new Set(ids));

  const handleFixAllOnAlbum = async () => {
    if (!identity || !findings || findings.length === 0) return;
    const pending = findings.filter((f) => f.status === 'pending');
    // only what has a fix: the server skips the rest anyway
    const fixable = pending.filter((f) => Boolean(rowFixLabel(f)));
    if (fixable.length === 0) return;

    if (fixable.some((f) => isDestructive(f.finding_type))) {
      const confirmed = await window.showConfirmDialog?.({
        title: 'Resolve Album Issues',
        message: `This album has ${fixable.length} issues, including files that will be moved to quarantine and re-downloaded. Proceed?`,
        confirmText: 'Resolve & Replace',
        destructive: false,
      });
      if (!confirmed) return;
    }

    markBusy(fixable.map((f) => f.id));
    let message = '';
    let tone = 'success';
    try {
      // one call, scoped on the server: it fixes only this group's pending
      // rows among these ids, in bulk (never an explicit single fix, so the
      // album completeness gate can't wishlist other artists from here)
      const result = await albumFindingAction(
        'fix',
        identity,
        fixable.map((f) => f.id),
        { jobId, findingType },
      );
      const fixed = result.fixed ?? 0;
      const failed = result.failed ?? 0;
      message =
        failed > 0
          ? `Fixed ${fixed} of ${result.total ?? fixable.length} issues on "${albumName}", ${failed} failed`
          : `Resolved ${fixed} issue${fixed === 1 ? '' : 's'} on "${albumName}"`;
      if (failed > 0) tone = 'warning';
    } catch (err) {
      message = (err as Error).message || 'Failed to resolve album issues';
      tone = 'error';
    }
    setBusyIds(new Set());
    await loadAlbumFindings();
    onRefresh();
    window.showToast?.(message, tone);
  };

  const handleDismissAllOnAlbum = async () => {
    if (!identity || !findings || findings.length === 0) return;
    const pending = findings.filter((f) => f.status === 'pending');
    if (pending.length === 0) return;
    markBusy(pending.map((f) => f.id));
    let message = '';
    let tone = 'info';
    try {
      const result = await albumFindingAction(
        'dismiss',
        identity,
        pending.map((f) => f.id),
        { jobId, findingType },
      );
      const updated = result.updated ?? 0;
      message = `Dismissed ${updated} issue${updated === 1 ? '' : 's'} on "${albumName}"`;
    } catch (err) {
      message = (err as Error).message || 'Failed to dismiss album issues';
      tone = 'error';
    }
    setBusyIds(new Set());
    await loadAlbumFindings();
    onRefresh();
    window.showToast?.(message, tone);
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
    return [
      'quality_upgrade',
      'dead_file',
      'short_preview_track',
      'corrupt_audio',
      'fake_lossless',
    ].includes(type);
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

      {!loading && findings && total > findings.length ? (
        <div className="album-tray-alert note" role="note">
          <span>
            Showing the first {findings.length} of {total.toLocaleString()} issues. Album actions
            cover the ones shown.
          </span>
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
              const fixLabel = rowFixLabel(f);
              const busy = busyIds.has(f.id);
              // Only a finding with a catalogue track behind it can open the
              // redownload search; the rest fall back to their row fix.
              const isRedl =
                isRedownloadFinding(f.finding_type) && Boolean(findingRedownloadTrackId(f));

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
