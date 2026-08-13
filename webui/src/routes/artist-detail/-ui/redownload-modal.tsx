import { useEffect, useRef, useState } from 'react';

import type { EnhancedAlbum, EnhancedTrack } from '../-artist-detail.enhanced';
import type {
  RedownloadCandidate,
  RedownloadMetadataResponse,
  RedownloadMetadataResult,
} from '../-artist-detail.redownload';

import {
  bestCandidateIndex,
  DOWNLOAD_SERVICE_ICONS,
  DOWNLOAD_SERVICE_LABELS,
  METADATA_SOURCE_ICONS,
  METADATA_SOURCE_LABELS,
  msClock,
  pollRedownloadProgress,
  scoreClass,
  searchRedownloadMetadata,
  startRedownloadRequest,
  stopRedownloadProgress,
  streamRedownloadSources,
  trackFormatBadge,
} from '../-artist-detail.redownload';

/**
 * The 3-step redownload modal (showTrackRedownloadModal, library.js:3348):
 * choose metadata → choose a download source (columns stream in per source) →
 * live download progress. The chosen metadata lives in component state — the
 * vanilla kept it in an implicit global (`selectedMeta`, 3497) that leaked
 * onto window.
 */
export function RedownloadModal({
  track,
  album,
  artistName,
  onReload,
  onClose,
}: {
  track: EnhancedTrack;
  album: EnhancedAlbum;
  artistName: string;
  /** A finished download re-fetches the enhanced payload (3871-3873). */
  onReload: () => void;
  onClose: () => void;
}) {
  const [step, setStep] = useState(1);
  const [metadata, setMetadata] = useState<RedownloadMetadataResponse | null>(null);
  const [metadataError, setMetadataError] = useState('');
  const [choice, setChoice] = useState<{ source: string; index: number } | null>(null);

  const [candidates, setCandidates] = useState<RedownloadCandidate[]>([]);
  const [columns, setColumns] = useState<{ source: string; candidates: RedownloadCandidate[] }[]>(
    [],
  );
  const [streamDone, setStreamDone] = useState(false);
  const [streamError, setStreamError] = useState('');
  const [pickedIdx, setPickedIdx] = useState<number | null>(null);
  const [deleteOld, setDeleteOld] = useState(true);

  const [progress, setProgress] = useState({ pct: 0, text: 'Starting download...' });
  const [downloading, setDownloading] = useState<RedownloadCandidate | null>(null);
  const [startError, setStartError] = useState('');
  const chosenMetaRef = useRef<RedownloadMetadataResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    searchRedownloadMetadata(track.id)
      .then((data) => {
        if (cancelled) return;
        setMetadata(data);
        const sources = Object.keys(data.metadata_results || {});
        const best = data.best_match?.source || sources[0];
        if (best && (data.metadata_results[best] || []).length > 0) {
          setChoice({ source: best, index: 0 });
        }
      })
      .catch((error: Error) => {
        if (!cancelled) setMetadataError(error.message);
      });
    return () => {
      cancelled = true;
      stopRedownloadProgress();
    };
    // One search per mounted modal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const goToSources = () => {
    if (!choice || !metadata) {
      window.showToast?.('Select a metadata source first', 'error');
      return;
    }
    const meta = { ...metadata.metadata_results[choice.source][choice.index] };
    meta._source = choice.source;
    chosenMetaRef.current = meta;
    setStep(2);
    void streamRedownloadSources(track.id, meta, (source, fresh, all) => {
      setColumns((prev) => [...prev, { source, candidates: fresh }]);
      setCandidates([...all]);
    })
      .then(() => setStreamDone(true))
      .catch((error: Error) => setStreamError(error.message));
  };

  // The recommendation follows the stream until the user picks for themselves.
  // (The vanilla's freshly-rendered checked radio silently STOLE a manual pick
  // whenever a later column landed — the explicit pick fixes that.)
  const bestIdx = bestCandidateIndex(candidates);
  const selectedIdx = pickedIdx ?? (bestIdx >= 0 ? bestIdx : null);
  const anySelectable = candidates.some((c) => !c.blacklisted);

  const startDownload = async () => {
    const candidate = selectedIdx != null ? candidates[selectedIdx] : null;
    if (!candidate) {
      window.showToast?.('Select a download source', 'error');
      return;
    }
    setDownloading(candidate);
    setStep(3);
    try {
      await startRedownloadRequest(track.id, chosenMetaRef.current ?? {}, candidate, deleteOld);
      pollRedownloadProgress({
        onTick: setProgress,
        onComplete: () => {
          setTimeout(() => {
            onClose();
            onReload();
          }, 2000);
        },
        onTimeout: () =>
          setProgress((prev) => ({
            ...prev,
            text: 'Download may still be in progress. Check the dashboard.',
          })),
      });
    } catch (error) {
      setStartError((error as Error).message);
    }
  };

  const fmt = trackFormatBadge(track.file_path);
  const thumb = metadata?.current_track?.thumb_url;

  return (
    <div
      id="redownload-overlay"
      className="redownload-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="redownload-modal">
        <div className="redownload-header">
          <div>
            <h3>Redownload Track</h3>
            <p className="redownload-header-sub">
              Find the correct version and download from your preferred source
            </p>
          </div>
          <button className="redownload-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="redownload-current" id="redownload-current">
          <div className="redownload-current-art" id="redownload-current-art">
            {thumb ? <img src={thumb} alt="" /> : <div className="redownload-art-empty">🎵</div>}
          </div>
          <div className="redownload-current-info">
            <div className="redownload-current-title">{String(track.title || '')}</div>
            <div className="redownload-current-meta">
              {artistName} · {String(album.title || '')}
            </div>
          </div>
          <div className="redownload-current-badges">
            {fmt ? <span className="redownload-badge fmt">{fmt}</span> : null}
            {track.bitrate ? (
              <span className="redownload-badge bitrate">{String(track.bitrate)}k</span>
            ) : null}
          </div>
        </div>
        <div className="redownload-steps">
          <div className={`redownload-step${step === 1 ? ' active' : ''}`} data-step="1">
            <span className="redownload-step-num">1</span> Choose Metadata
          </div>
          <div className="redownload-step-line" />
          <div className={`redownload-step${step === 2 ? ' active' : ''}`} data-step="2">
            <span className="redownload-step-num">2</span> Choose Source
          </div>
          <div className="redownload-step-line" />
          <div className={`redownload-step${step === 3 ? ' active' : ''}`} data-step="3">
            <span className="redownload-step-num">3</span> Downloading
          </div>
        </div>

        <div className="redownload-body" id="redownload-body">
          {step === 1 ? (
            metadataError ? (
              <div className="redownload-error">Error: {metadataError}</div>
            ) : !metadata ? (
              <div className="redownload-loading">
                <div className="server-search-spinner" />
                Searching metadata sources...
              </div>
            ) : Object.keys(metadata.metadata_results).length === 0 ? (
              <div className="redownload-error">
                No metadata sources available. Check your Spotify/iTunes/Deezer connections.
              </div>
            ) : (
              <MetadataColumns metadata={metadata} choice={choice} onChoose={setChoice} />
            )
          ) : null}

          {step === 2 ? (
            <div className="rdl-src-columns" id="rdl-src-columns">
              {streamError ? (
                <div className="redownload-error">Error: {streamError}</div>
              ) : columns.length === 0 ? (
                streamDone ? (
                  <div className="rdl-src-col-empty">No download sources found for this track.</div>
                ) : (
                  <div className="redownload-loading" id="rdl-src-loading">
                    <div className="server-search-spinner" />
                    Searching download sources...
                  </div>
                )
              ) : (
                columns.map((column) => (
                  <SourceColumn
                    column={column}
                    selectedIdx={selectedIdx}
                    bestIdx={bestIdx}
                    onPick={setPickedIdx}
                    key={column.source}
                  />
                ))
              )}
            </div>
          ) : null}

          {step === 3 ? (
            startError ? (
              <div className="redownload-error">Download failed: {startError}</div>
            ) : (
              <div className="redownload-progress">
                <div className="redownload-progress-title">
                  Downloading: {downloading?.display_name}
                </div>
                <div className="redownload-progress-from">
                  from{' '}
                  {downloading?.source_service === 'soulseek'
                    ? downloading?.username
                    : downloading?.source_service || 'unknown'}
                </div>
                <div className="redownload-progress-bar-wrap">
                  <div
                    className="redownload-progress-bar"
                    id="redownload-progress-bar"
                    style={{ width: `${progress.pct}%` }}
                  />
                </div>
                <div className="redownload-progress-status" id="redownload-progress-status">
                  {progress.text}
                </div>
              </div>
            )
          ) : null}
        </div>

        {step === 1 && metadata && Object.keys(metadata.metadata_results).length > 0 ? (
          <div className="redownload-sticky-footer">
            <div className="redownload-actions">
              <button className="redownload-btn secondary" type="button" onClick={onClose}>
                Cancel
              </button>
              <button
                className="redownload-btn primary"
                id="redownload-next-btn"
                type="button"
                onClick={goToSources}
              >
                Search Download Sources →
              </button>
            </div>
          </div>
        ) : null}

        {step === 2 ? (
          <div className="redownload-sticky-footer">
            <label className="redownload-delete-old">
              <input
                type="checkbox"
                id="redownload-delete-old-check"
                checked={deleteOld}
                onChange={(e) => setDeleteOld(e.target.checked)}
              />
              Delete old file after successful download
            </label>
            <div className="redownload-actions">
              <button className="redownload-btn secondary" type="button" onClick={onClose}>
                Cancel
              </button>
              <button
                className="redownload-btn primary"
                id="redownload-start-btn"
                type="button"
                disabled={!anySelectable}
                onClick={() => void startDownload()}
              >
                {anySelectable ? 'Download Selected' : 'Waiting for results...'}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function MetadataColumns({
  metadata,
  choice,
  onChoose,
}: {
  metadata: RedownloadMetadataResponse;
  choice: { source: string; index: number } | null;
  onChoose: (choice: { source: string; index: number }) => void;
}) {
  const sources = Object.keys(metadata.metadata_results);
  return (
    <div className="redownload-columns">
      {sources.map((source) => {
        const results = metadata.metadata_results[source] || [];
        return (
          <div className="redownload-source-col" key={source}>
            <div className="redownload-col-header">
              <span className="redownload-col-icon">{METADATA_SOURCE_ICONS[source] || '📋'}</span>
              <span className="redownload-col-label">
                {METADATA_SOURCE_LABELS[source] || source}
              </span>
              <span className="redownload-col-count">{results.length}</span>
            </div>
            <div className="redownload-col-results">
              {results.length === 0 ? (
                <div className="redownload-col-empty">No results</div>
              ) : (
                results.slice(0, 8).map((r, i) => {
                  const pct = Math.round((r.match_score || 0) * 100);
                  const dur = msClock(r.duration_ms);
                  return (
                    <label
                      className="redownload-result"
                      data-source={source}
                      data-index={i}
                      key={i}
                    >
                      <input
                        type="radio"
                        name="metadata-choice"
                        value={`${source}|${i}`}
                        checked={choice?.source === source && choice.index === i}
                        onChange={() => onChoose({ source, index: i })}
                      />
                      <div className="redownload-result-art">
                        {r.image_url ? (
                          <img src={r.image_url} loading="lazy" alt="" />
                        ) : (
                          <div className="redownload-art-empty" />
                        )}
                      </div>
                      <div className="redownload-result-info">
                        <div className="redownload-result-title">
                          {r.name}
                          {r.is_current_match ? (
                            <span className="redownload-current-badge"> current</span>
                          ) : null}
                        </div>
                        <div className="redownload-result-meta">
                          {r.artist}
                          {r.album ? ` · ${r.album}` : ''}
                        </div>
                      </div>
                      <div className="redownload-result-right">
                        <div className={`redownload-result-score ${scoreClass(pct)}`}>{pct}%</div>
                        {dur ? <div className="redownload-result-dur">{dur}</div> : null}
                      </div>
                    </label>
                  );
                })
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SourceColumn({
  column,
  selectedIdx,
  bestIdx,
  onPick,
}: {
  column: { source: string; candidates: RedownloadCandidate[] };
  selectedIdx: number | null;
  bestIdx: number;
  onPick: (globalIdx: number) => void;
}) {
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
          <div className="rdl-src-col-empty">No results</div>
        ) : (
          column.candidates.slice(0, 10).map((c) => {
            const confPct = Math.round((c.confidence || 0) * 100);
            const confCls = scoreClass(confPct);
            const isRec = c._globalIdx === bestIdx;
            const dur = msClock(c.duration);
            return (
              <label
                className={`rdl-src-item${c.blacklisted ? ' blacklisted' : ''}${isRec ? ' recommended' : ''}`}
                key={c._globalIdx}
              >
                {c.blacklisted ? (
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
                  <div className="rdl-src-item-details">
                    {c.quality ? <span className="rdl-src-fmt">{c.quality}</span> : null}
                    {c.bitrate ? <span className="rdl-src-detail">{c.bitrate}k</span> : null}
                    <span className="rdl-src-detail">{c.size_display}</span>
                    {dur ? <span className="rdl-src-detail">{dur}</span> : null}
                    {column.source === 'soulseek' ? (
                      <span className="rdl-src-detail rdl-src-user">{c.username}</span>
                    ) : null}
                    {column.source === 'soulseek' && c.free_upload_slots != null ? (
                      <span className="rdl-src-detail">{c.free_upload_slots} slots</span>
                    ) : null}
                  </div>
                  <div className="rdl-src-conf-bar">
                    <div
                      className={`rdl-src-conf-fill ${confCls}`}
                      style={{ width: `${confPct}%` }}
                    />
                  </div>
                </div>
                <div className={`rdl-src-conf-pct ${confCls}`}>{confPct}%</div>
                {c.blacklisted ? <span className="rdl-src-bl">Blacklisted</span> : null}
              </label>
            );
          })
        )}
      </div>
    </div>
  );
}
