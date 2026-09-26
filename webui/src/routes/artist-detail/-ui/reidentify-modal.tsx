import { useEffect, useState } from 'react';

import type { ReidentifyResult, ReidentifySource } from '../-artist-detail.reidentify';

import {
  applyReidentifyRequest,
  fetchReidentifySources,
  rankReidentifyResults,
  reidentifyResultBits,
  reidentifySearchRequest,
} from '../-artist-detail.reidentify';

/**
 * The re-identify modal (#889, openReidentifyModal library.js:7519): hero with
 * the current filing, source tabs (active source pre-selected, auto-searched
 * on open), ISRC-first results, and the replace-original checkbox defaulting
 * ON.
 */
export function ReidentifyModal({
  trackId,
  trackTitle,
  artistName,
  albumTitle,
  imageUrl,
  onClose,
}: {
  trackId: unknown;
  trackTitle: string;
  artistName: string;
  albumTitle: string;
  imageUrl: string;
  onClose: () => void;
}) {
  const [sources, setSources] = useState<ReidentifySource[] | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [query, setQuery] = useState(`${trackTitle || ''} ${artistName || ''}`.trim());
  const [state, setState] = useState<
    | { kind: 'idle' }
    | { kind: 'loading' }
    | { kind: 'empty'; message: string }
    | { kind: 'results'; rows: ReidentifyResult[] }
  >({ kind: 'idle' });
  const [selected, setSelected] = useState<ReidentifyResult | null>(null);
  const [replace, setReplace] = useState(true);
  const [staging, setStaging] = useState(false);

  const search = async (activeSource: string | null, value: string) => {
    if (!value.trim() || !activeSource) return;
    setSelected(null);
    setState({ kind: 'loading' });
    try {
      const rows = await reidentifySearchRequest(activeSource, value.trim());
      if (rows.length === 0) {
        setState({
          kind: 'empty',
          message: 'No releases found. Try refining the search or another source tab.',
        });
      } else {
        setState({ kind: 'results', rows: rankReidentifyResults(rows) });
      }
    } catch {
      setState({ kind: 'empty', message: 'Search failed. Try another source.' });
    }
  };

  useEffect(() => {
    let cancelled = false;
    void fetchReidentifySources().then((list) => {
      if (cancelled) return;
      setSources(list);
      if (list.length > 0) {
        const active = list.find((s) => s.active) || list[0];
        setSource(active.source);
        void search(active.source, query);
      } else {
        setState({ kind: 'empty', message: 'No configured metadata source to search.' });
      }
    });
    return () => {
      cancelled = true;
    };
    // One tab load + auto-search per mounted modal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pickTab = (next: string) => {
    if (next === source) return;
    setSource(next);
    void search(next, query);
  };

  const confirm = async () => {
    if (!selected || staging) return;
    setStaging(true);
    try {
      const message = await applyReidentifyRequest(trackId, selected, replace);
      window.showToast?.(message, 'success');
      onClose();
    } catch (error) {
      window.showToast?.((error as Error).message || 'Re-identify failed', 'error');
      setStaging(false);
    }
  };

  return (
    <div
      className="modal-overlay"
      id="reid-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="reid-modal" id="reid-modal">
        <div className="reid-hero">
          <div className="reid-hero-decor">
            <div
              className="reid-hero-bg"
              id="reid-hero-bg"
              style={imageUrl ? { backgroundImage: `url('${imageUrl}')` } : undefined}
            />
            <div className="reid-hero-overlay" />
          </div>
          <span className="reid-close" onClick={onClose}>
            ×
          </span>
          <div className="reid-hero-content">
            <div
              className={`reid-hero-art${imageUrl ? '' : ' empty'}`}
              id="reid-hero-art"
              style={imageUrl ? { backgroundImage: `url('${imageUrl}')` } : undefined}
            />
            <div className="reid-hero-meta">
              <div className="reid-hero-eyebrow">Re-identify track</div>
              <div className="reid-hero-title" id="reid-hero-title">
                {trackTitle || 'Track'}
              </div>
              <div className="reid-hero-sub" id="reid-hero-sub">
                {(artistName || '') + (albumTitle ? ` · currently in “${albumTitle}”` : '')}
              </div>
            </div>
          </div>
        </div>

        <div className="reid-tabs" id="reid-tabs">
          {sources && sources.length === 0 ? (
            <span className="reid-tab active">No metadata sources available</span>
          ) : (
            (sources ?? []).map((s) => (
              <div
                className={`reid-tab${s.source === source ? ' active' : ''}`}
                key={s.source}
                onClick={() => pickTab(s.source)}
              >
                {s.label || s.source}
              </div>
            ))
          )}
        </div>

        <div className="reid-search">
          <svg className="reid-search-icon" viewBox="0 0 24 24" width="18" height="18">
            <path
              fill="currentColor"
              d="M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.7.7l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z"
            />
          </svg>
          <input
            type="text"
            id="reid-search-input"
            className="reid-search-input"
            placeholder="Search for the track…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void search(source, query);
            }}
          />
          <button
            className="reid-search-btn"
            id="reid-search-btn"
            type="button"
            onClick={() => void search(source, query)}
          >
            Search
          </button>
        </div>

        <div className="reid-results" id="reid-results">
          {state.kind === 'idle' ? (
            <div className="reid-state">
              <div className="reid-state-icon">💿</div>
              <p>
                Pick the release this track should be filed under — the same song may appear on a
                single, an EP, and an album.
              </p>
            </div>
          ) : state.kind === 'loading' ? (
            <>
              <div className="reid-state">
                <div className="reid-spinner" />
                <p>Searching…</p>
              </div>
              <div className="reid-skel" />
              <div className="reid-skel" />
              <div className="reid-skel" />
            </>
          ) : state.kind === 'empty' ? (
            <div className="reid-state">
              <div className="reid-state-icon">🔍</div>
              <p>{state.message}</p>
            </div>
          ) : (
            state.rows.map((r, n) => {
              const badge = (r.album_type || 'album').toLowerCase();
              const bits = reidentifyResultBits(r);
              return (
                <div
                  className={`reid-result${selected === r ? ' selected' : ''}`}
                  style={{ animationDelay: `${Math.min(n * 0.03, 0.3)}s` }}
                  key={n}
                  onClick={() => setSelected(r)}
                >
                  <div
                    className="reid-result-art"
                    style={
                      r.image_url
                        ? { backgroundImage: `url('${encodeURI(r.image_url)}')` }
                        : undefined
                    }
                  >
                    {r.image_url ? null : <span>♪</span>}
                  </div>
                  <div className="reid-result-info">
                    <div className="reid-result-title">{r.track_title || ''}</div>
                    <div className="reid-result-release">
                      {r.album_name || 'Unknown release'}
                      {r.artist_name ? ` · ${r.artist_name}` : ''}
                    </div>
                  </div>
                  <div className="reid-result-meta">
                    <span className={`reid-badge ${badge}`}>{badge}</span>
                    {bits ? <span className="reid-result-detail">{bits}</span> : null}
                    <span className="reid-result-check" />
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="reid-footer">
          <label
            className="reid-replace"
            title="When on, the original file is deleted after the track is re-filed under the new release."
          >
            <input
              type="checkbox"
              id="reid-replace"
              checked={replace}
              onChange={(e) => setReplace(e.target.checked)}
            />
            <span className="reid-replace-box" />
            <span className="reid-replace-text">
              Replace the original file <em>(recommended)</em>
            </span>
          </label>
          <div className="reid-footer-actions">
            <button className="btn btn--secondary" type="button" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn btn--primary"
              id="reid-confirm-btn"
              type="button"
              disabled={!selected || staging}
              onClick={() => void confirm()}
            >
              {staging ? 'Staging…' : 'Re-identify'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
