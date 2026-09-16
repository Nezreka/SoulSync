import { useCallback, useEffect, useRef, useState } from 'react';

import type { MatchSearchResult } from '../-artist-detail.enrich-match';
import type { MatchRow } from '../-artist-detail.matches';
import type { ArtistInfo } from '../-artist-detail.types';

import {
  applyManualMatchRequest,
  clearMatchRequest,
  runEnrichmentRequest,
  searchServiceRequest,
} from '../-artist-detail.enrich-match';
import { audioDbLogoUrl, SERVICE_LOGOS } from '../-artist-detail.hero';
import {
  buildMatchRows,
  matchSummary,
  relativeTime,
  showsFixMatchButton,
} from '../-artist-detail.matches';
import { BodyPortal } from './portal';

interface Props {
  artist: ArtistInfo;
  isSourceArtist: boolean;
  isAdmin: boolean;
  /** The page re-reads the artist after any match changes. */
  onChanged: () => void;
}

/**
 * The "Wrong match?" button beside DB Record and the panel it opens.
 *
 * The same rematch the enhanced view's header chips offer, one row per
 * source, reachable from the standard page. The chips stay where they are;
 * this is the way in for someone who does not know they exist.
 */
export function ArtistFixMatch({ artist, isSourceArtist, isAdmin, onChanged }: Props) {
  const [open, setOpen] = useState(false);
  if (!showsFixMatchButton(artist, isSourceArtist, isAdmin)) return null;

  return (
    <>
      <button
        type="button"
        className="artist-db-record-btn artist-fix-match-btn"
        id="artist-fix-match-btn"
        title="Wrong bio, photo or discography? Check what this artist is matched to on each source"
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
          <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
          <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          <path d="M4 4l2 2M18 18l2 2" />
        </svg>
        <span>Wrong match?</span>
      </button>

      {open ? (
        <ArtistMatchesModal
          artistId={artist.id}
          artistName={artist.name || 'Artist'}
          onChanged={onChanged}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

interface ModalProps {
  artistId: unknown;
  artistName: string;
  onChanged: () => void;
  onClose: () => void;
}

type Results =
  | { kind: 'hint'; text: string }
  | { kind: 'loading' }
  | { kind: 'loaded'; results: MatchSearchResult[] };

export function ArtistMatchesModal({ artistId, artistName, onChanged, onClose }: ModalProps) {
  const [rows, setRows] = useState<MatchRow[] | null>(null);
  const [error, setError] = useState('');
  /** Which source's search panel is open, if any. */
  const [editing, setEditing] = useState<string | null>(null);
  /** Which source has a request in flight; its buttons lock meanwhile. */
  const [busy, setBusy] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const frame = requestAnimationFrame(() => setVisible(true));
    return () => {
      mounted.current = false;
      cancelAnimationFrame(frame);
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/artist/${encodeURIComponent(String(artistId))}/record`);
      const data = (await response.json()) as {
        success?: boolean;
        error?: string;
        record?: Record<string, unknown>;
      };
      if (!data?.success) throw new Error(data?.error || 'Request failed');
      if (!mounted.current) return;
      setRows(buildMatchRows(data.record ?? {}));
      setError('');
    } catch (err) {
      if (!mounted.current) return;
      setError((err as Error).message || String(err));
    }
  }, [artistId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      // One step back per press: a search panel closes before the modal does.
      if (editing) setEditing(null);
      else onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [editing, onClose]);

  /** Every write re-reads the row and tells the page, whichever button did it. */
  const afterChange = async () => {
    await load();
    onChanged();
  };

  const pick = async (row: MatchRow, result: MatchSearchResult) => {
    setBusy(row.svc);
    try {
      await applyManualMatchRequest({
        entityType: 'artist',
        entityId: artistId,
        // A proxied result matches through its real provider.
        service: result.provider || row.svc,
        serviceId: result.id,
        artistId,
      });
      window.showToast?.(`${row.label} now points at ${result.name || result.id}`, 'success');
      setEditing(null);
      await afterChange();
    } catch (err) {
      window.showToast?.(`Match failed: ${(err as Error).message}`, 'error');
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const clear = async (row: MatchRow) => {
    const confirmed = await window.showConfirmDialog?.({
      title: `Clear ${row.label} match`,
      message: `Forget the ${row.label} match for ${artistName}? It goes back to "Not found" until you pick one or a lookup finds it again.`,
      confirmText: 'Clear match',
      destructive: true,
    });
    if (!confirmed) return;
    setBusy(row.svc);
    try {
      await clearMatchRequest({
        entityType: 'artist',
        entityId: artistId,
        service: row.svc,
        artistId,
      });
      window.showToast?.(`Cleared ${row.label} match`, 'success');
      if (editing === row.svc) setEditing(null);
      await afterChange();
    } catch (err) {
      window.showToast?.(`Clear failed: ${(err as Error).message}`, 'error');
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const retry = async (row: MatchRow) => {
    setBusy(row.svc);
    try {
      // toasts its own progress and result
      await runEnrichmentRequest({
        entityType: 'artist',
        entityId: artistId,
        service: row.svc,
        name: artistName,
        artistName,
        artistId,
      });
      await afterChange();
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const summary = rows ? matchSummary(rows) : null;

  return (
    <BodyPortal>
      <div
        id="artist-matches-overlay"
        className={`arec-overlay${visible ? ' visible' : ''}`}
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div className="arec-card amx-card" role="dialog" aria-label="Artist source matches">
          <div className="arec-header">
            <div className="arec-title-wrap">
              <div className="arec-title">Source matches</div>
              <div className="arec-sub amx-sub">
                {artistName}
                {summary ? (
                  <>
                    {' · '}
                    <span className="amx-summary">
                      {`${summary.matched} of ${summary.total} sources matched`}
                    </span>
                  </>
                ) : null}
              </div>
            </div>
            <button className="arec-close" title="Close (Esc)" onClick={onClose}>
              ×
            </button>
          </div>

          <p className="amx-intro">
            Wrong releases, photo or bio usually mean one source is matched to the wrong artist.
            Open a link to check, then pick the right one. The discography and photo follow the new
            match straight away.
          </p>

          <div className="amx-body">
            {error ? (
              <div className="amx-error">{error}</div>
            ) : !rows ? (
              <div className="amx-loading">Loading matches…</div>
            ) : (
              rows.map((row) => (
                <MatchRowView
                  key={row.svc}
                  row={row}
                  artistName={artistName}
                  editing={editing === row.svc}
                  busy={busy === row.svc}
                  locked={busy !== null && busy !== row.svc}
                  onEdit={() => setEditing(editing === row.svc ? null : row.svc)}
                  onCancel={() => setEditing(null)}
                  onPick={(result) => void pick(row, result)}
                  onClear={() => void clear(row)}
                  onRetry={() => void retry(row)}
                />
              ))
            )}
          </div>
        </div>
      </div>
    </BodyPortal>
  );
}

interface RowProps {
  row: MatchRow;
  artistName: string;
  editing: boolean;
  busy: boolean;
  /** Another row's request is in flight. */
  locked: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onPick: (result: MatchSearchResult) => void;
  onClear: () => void;
  onRetry: () => void;
}

function MatchRowView({
  row,
  artistName,
  editing,
  busy,
  locked,
  onEdit,
  onCancel,
  onPick,
  onClear,
  onRetry,
}: RowProps) {
  const disabled = busy || locked;
  const when = relativeTime(row.attempted);
  return (
    <div className={`amx-row state-${row.state}${editing ? ' editing' : ''}`} data-svc={row.svc}>
      <div className="amx-row-main">
        <ServiceLogo svc={row.svc} label={row.label} />
        <div className="amx-row-info">
          <div className="amx-row-head">
            <span className="amx-row-label">{row.label}</span>
            <span className={`amx-pill ${row.state}`}>{busy ? 'Working…' : row.stateLabel}</span>
          </div>
          <div className="amx-row-detail">
            {row.value ? (
              row.url ? (
                <a
                  className="amx-row-link"
                  href={row.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open on the source site to check it is the right artist"
                >
                  {row.value} ↗
                </a>
              ) : (
                <span className="amx-row-id">{row.value}</span>
              )
            ) : (
              <span className="amx-row-none">No match stored</span>
            )}
            {when ? <span className="amx-row-when">last lookup {when}</span> : null}
          </div>
        </div>
        <div className="amx-row-actions">
          <button
            type="button"
            className={`amx-btn primary${editing ? ' active' : ''}`}
            disabled={disabled}
            onClick={onEdit}
          >
            {row.state === 'matched' ? 'Change' : 'Find match'}
          </button>
          {row.canAutoLookup ? (
            <button
              type="button"
              className="amx-btn"
              disabled={disabled}
              title={`Run the automatic ${row.label} lookup again`}
              onClick={onRetry}
            >
              Auto
            </button>
          ) : null}
          {row.state === 'matched' ? (
            <button
              type="button"
              className="amx-btn danger"
              disabled={disabled}
              title="Forget this match"
              onClick={onClear}
            >
              Clear
            </button>
          ) : null}
        </div>
      </div>
      {editing ? (
        <MatchSearch
          row={row}
          defaultQuery={artistName}
          busy={busy}
          onPick={onPick}
          onCancel={onCancel}
        />
      ) : null}
    </div>
  );
}

function MatchSearch({
  row,
  defaultQuery,
  busy,
  onPick,
  onCancel,
}: {
  row: MatchRow;
  defaultQuery: string;
  busy: boolean;
  onPick: (result: MatchSearchResult) => void;
  onCancel: () => void;
}) {
  const [query, setQuery] = useState(defaultQuery);
  const [results, setResults] = useState<Results>({ kind: 'loading' });
  const searchedOnOpen = useRef(false);

  const search = async (value: string) => {
    if (!value.trim()) {
      setResults({ kind: 'hint', text: 'Type a name to search' });
      return;
    }
    setResults({ kind: 'loading' });
    try {
      const found = await searchServiceRequest(row.svc, 'artist', value);
      setResults(
        found.length
          ? { kind: 'loaded', results: found }
          : { kind: 'hint', text: 'Nothing found. Try another spelling, or paste an id or link.' },
      );
    } catch (err) {
      setResults({ kind: 'hint', text: `Search failed: ${(err as Error).message}` });
    }
  };

  useEffect(() => {
    if (searchedOnOpen.current) return;
    searchedOnOpen.current = true;
    void search(defaultQuery);
    // the opening search runs once, with the artist's own name
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="amx-search">
      <div className="amx-search-row">
        <input
          type="text"
          className="amx-search-input"
          placeholder={
            row.svc === 'musicbrainz'
              ? `Search ${row.label}, or paste a MusicBrainz id or url`
              : `Search ${row.label}…`
          }
          value={query}
          autoFocus
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void search(query);
          }}
        />
        <button type="button" className="amx-btn primary" onClick={() => void search(query)}>
          Search
        </button>
        <button type="button" className="amx-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <div className="amx-results">
        {results.kind === 'hint' ? (
          <div className="amx-results-hint">{results.text}</div>
        ) : results.kind === 'loading' ? (
          <div className="amx-results-hint">Searching {row.label}…</div>
        ) : (
          results.results.map((result) => {
            const current = row.value !== null && String(result.id) === row.value;
            return (
              <div
                className={`amx-result${current ? ' current' : ''}`}
                key={`${result.provider || row.svc}:${result.id}`}
              >
                <ResultImage src={result.image} />
                <div className="amx-result-info">
                  <div className="amx-result-name">{result.name || 'Unknown'}</div>
                  {result.extra ? <div className="amx-result-extra">{result.extra}</div> : null}
                  <div className="amx-result-id">
                    {result.id}
                    {result.provider && result.provider !== row.svc
                      ? ` via ${result.provider}`
                      : ''}
                  </div>
                </div>
                {current ? (
                  <span className="amx-result-current">Current</span>
                ) : (
                  <button
                    type="button"
                    className="amx-btn primary"
                    disabled={busy}
                    onClick={() => onPick(result)}
                  >
                    Use this
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function ServiceLogo({ svc, label }: { svc: string; label: string }) {
  const [broken, setBroken] = useState(false);
  const src =
    svc === 'audiodb' ? audioDbLogoUrl() : (SERVICE_LOGOS as Record<string, string>)[svc] || '';
  if (!src || broken) {
    return (
      <div className="amx-logo amx-logo-text" aria-hidden="true">
        {label.slice(0, 2).toUpperCase()}
      </div>
    );
  }
  return <img className="amx-logo" src={src} alt="" onError={() => setBroken(true)} />;
}

function ResultImage({ src }: { src?: string }) {
  const [broken, setBroken] = useState(false);
  if (!src || broken) return <div className="amx-result-img placeholder">🎵</div>;
  return <img className="amx-result-img" src={src} alt="" onError={() => setBroken(true)} />;
}
