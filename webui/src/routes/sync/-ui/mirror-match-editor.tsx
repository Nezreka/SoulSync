/**
 * The pre-sync match editor (#1958). A mirrored playlist that has not synced
 * has no server playlist for the compare editor to open, but it does have the
 * library track each song will sync as: the background library check stamps
 * it on every song, and a manual match overrides it. This shows that, side by
 * side like the compare editor and with its rows, and lets the user pick a
 * different version (the extended mix over the radio edit) or say "not this
 * one" before the first sync. A pick is stored the way the compare editor
 * stores a swap, so the sync uses it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { LibrarySearchTrack, MirroredMatch, PresyncTrack } from '../-sync.server';

import {
  applyPickedTrack,
  canUnmatch,
  checkMirrorLibrarySlice,
  compareSourceIcon,
  compareSourceLabel,
  compareStats,
  fetchPresyncMatches,
  presyncUnmatch,
  setPresyncMatch,
} from '../-sync.server';
import { ServerRow, SourceRow } from './server-compare-editor';
import { ServerSearchOverlay } from './server-search-overlay';

const FILTERS = ['all', 'matched', 'missing'] as const;
type PresyncFilter = (typeof FILTERS)[number];

const LOADING_STYLE = {
  textAlign: 'center',
  padding: 30,
  color: 'var(--white-a20)',
  fontSize: 12,
} as const;

/** how many check slices the editor runs before showing what it has */
const MAX_CHECK_SLICES = 40;

export function presyncFilterLabel(
  filter: PresyncFilter,
  stats: { matched: number; missing: number; total: number },
): string {
  if (filter === 'matched') return `In your library (${stats.matched})`;
  if (filter === 'missing') return `Will download (${stats.missing})`;
  return `All (${stats.total})`;
}

export function presyncFooterText(stats: { matched: number; missing: number }): string {
  const total = stats.matched + stats.missing;
  return `${stats.matched}/${total} already in your library · ${stats.missing} to download on sync`;
}

export function MirrorMatchEditor({
  mirrored,
  onBack,
}: {
  mirrored: MirroredMatch;
  onBack: () => void;
}) {
  const [tracks, setTracks] = useState<PresyncTrack[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [meta, setMeta] = useState('Loading…');
  const [filter, setFilter] = useState<PresyncFilter>('all');
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [search, setSearch] = useState<number | null>(null);
  const runId = useRef(0);
  const sourceScroll = useRef<HTMLDivElement>(null);
  const libraryScroll = useRef<HTMLDivElement>(null);
  const title = mirrored.display_name || mirrored.name;

  const load = useCallback(async () => {
    const id = ++runId.current;
    setLoaded(false);
    setMeta('Loading…');
    try {
      let response = await fetchPresyncMatches(mirrored.id);
      // songs the background check hasn't reached: check them now, so every
      // row says what the sync will really do
      for (let i = 0; i < MAX_CHECK_SLICES && (response.unchecked ?? 0) > 0; i++) {
        if (runId.current !== id) return;
        setMeta(`Checking your library… ${response.unchecked} left`);
        const slice = await checkMirrorLibrarySlice(mirrored.id);
        if (!slice.success) break;
        response = await fetchPresyncMatches(mirrored.id);
      }
      if (runId.current !== id) return;
      if (!response.success) {
        setMeta(response.error || 'Failed to load');
        return;
      }
      setTracks(response.tracks ?? []);
      setLoaded(true);
      setMeta(`${response.source_track_count ?? 0} songs · not on your server yet`);
    } catch (error) {
      if (runId.current !== id) return;
      setMeta(`Error: ${error instanceof Error ? error.message : 'unknown error'}`);
    }
  }, [mirrored.id]);

  useEffect(() => {
    void load();
    return () => {
      runId.current++;
    };
  }, [load]);

  const syncing = useRef(false);
  const syncScroll = useCallback((from: HTMLDivElement | null, to: HTMLDivElement | null) => {
    if (!from || !to || syncing.current) return;
    syncing.current = true;
    const maxFrom = from.scrollHeight - from.clientHeight;
    const maxTo = to.scrollHeight - to.clientHeight;
    if (maxFrom > 0 && maxTo > 0) to.scrollTop = (from.scrollTop / maxFrom) * maxTo;
    requestAnimationFrame(() => {
      syncing.current = false;
    });
  }, []);

  const selectPair = useCallback((index: number, side: 'source' | 'library') => {
    setHighlighted(index);
    const other = side === 'source' ? libraryScroll.current : sourceScroll.current;
    other
      ?.querySelector(`[data-pair-id="pair-${index}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, []);

  const pick = useCallback(
    async (newTrackId: string, resolvePicked: () => LibrarySearchTrack | undefined) => {
      if (search === null) return false;
      const track = tracks[search];
      if (!track) return false;
      const picked = resolvePicked();
      try {
        const response = await setPresyncMatch(
          mirrored.id,
          track.mirror_track_id,
          newTrackId,
          picked?.title || '',
        );
        if (!response.success) {
          window.showToast?.(response.error || 'Could not save the match', 'error');
          return false;
        }
        window.showToast?.(response.message || 'Match saved', 'success');
        const index = search;
        setSearch(null);
        if (picked) {
          setTracks(
            (current) => applyPickedTrack(current, index, newTrackId, picked) as PresyncTrack[],
          );
        } else {
          void load();
        }
        return true;
      } catch (error) {
        window.showToast?.(
          `Error: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
        return false;
      }
    },
    [load, mirrored.id, search, tracks],
  );

  const unmatch = useCallback(
    async (index: number) => {
      const track = tracks[index];
      if (!track || !canUnmatch(track)) return;
      try {
        const response = await presyncUnmatch(
          mirrored.id,
          track.mirror_track_id,
          track.server_track?.id ?? '',
        );
        if (!response.success) {
          window.showToast?.(response.error || 'Could not remove the match', 'error');
          return;
        }
        window.showToast?.('Match removed. The sync downloads this song instead.', 'success');
        setTracks((current) =>
          current.map((row, i) =>
            i === index ? { ...row, server_track: null, match_status: 'missing' } : row,
          ),
        );
      } catch (error) {
        window.showToast?.(
          `Error: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [mirrored.id, tracks],
  );

  const stats = compareStats(tracks);
  const isHidden = (track: PresyncTrack) => filter !== 'all' && track.match_status !== filter;

  return (
    <div id="server-editor">
      <div className="server-editor-header">
        <button type="button" className="server-editor-back" onClick={onBack}>
          ← Back
        </button>
        <div className="server-editor-info">
          <h4 className="server-editor-name">{title}</h4>
          <span className="server-editor-meta">{meta}</span>
        </div>
        <div className="server-editor-stats">
          <div className="server-editor-stat">
            <div className="server-editor-stat-num matched">{stats.matched}</div>
            <div className="server-editor-stat-label">In library</div>
          </div>
          <div className="server-editor-stat">
            <div className="server-editor-stat-num missing">{stats.missing}</div>
            <div className="server-editor-stat-label">To download</div>
          </div>
        </div>
        <button
          type="button"
          className="server-editor-refresh"
          title="Check again"
          onClick={() => void load()}
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
            <path d="M21 2v6h-6M3 12a9 9 0 0115.356-6.356L21 8M3 22v-6h6M21 12a9 9 0 01-15.356 6.356L3 16" />
          </svg>
        </button>
      </div>

      <div className="server-no-source-banner">
        <span>
          Not on your server yet. This is what each song will sync as: swap any of them for a
          different version you have, or pick one the check missed. Your picks are used when you
          sync.
        </span>
      </div>

      <div className="server-editor-filters">
        {FILTERS.map((f) => (
          <button
            type="button"
            key={f}
            className={`discog-filter${filter === f ? ' active' : ''}`}
            data-filter={f}
            onClick={() => setFilter(f)}
          >
            {presyncFilterLabel(f, stats)}
          </button>
        ))}
      </div>

      <div className="server-compare-columns">
        <div className="server-compare-col source">
          <div className="server-col-header">
            <span className="server-col-icon">{compareSourceIcon(mirrored.source)}</span>
            <span className="server-col-label">{compareSourceLabel(mirrored.source, true)}</span>
            <span className="server-col-count">{tracks.length} tracks</span>
          </div>
          <div
            className="server-col-scroll"
            ref={sourceScroll}
            onScroll={() => syncScroll(sourceScroll.current, libraryScroll.current)}
          >
            {loaded ? (
              tracks.map((track, index) => (
                <SourceRow
                  key={track.mirror_track_id}
                  track={track}
                  index={index}
                  hidden={isHidden(track)}
                  highlighted={highlighted === index}
                  onSelect={() => selectPair(index, 'source')}
                />
              ))
            ) : (
              <div style={LOADING_STYLE}>{meta}</div>
            )}
          </div>
        </div>

        <div className="server-compare-col server">
          <div className="server-col-header">
            <span className="server-col-icon">📚</span>
            <span className="server-col-label">Your library</span>
            <span className="server-col-count">{stats.matched} found</span>
          </div>
          <div
            className="server-col-scroll"
            ref={libraryScroll}
            onScroll={() => syncScroll(libraryScroll.current, sourceScroll.current)}
          >
            {loaded ? (
              tracks.map((track, index) => (
                <ServerRow
                  key={track.mirror_track_id}
                  track={track}
                  index={index}
                  hidden={isHidden(track)}
                  highlighted={highlighted === index}
                  presync
                  onSelect={() => selectPair(index, 'library')}
                  onSwap={(i) => setSearch(i)}
                  onUnmatch={(i) => void unmatch(i)}
                  onFindAndAdd={(i) => setSearch(i)}
                />
              ))
            ) : (
              <div style={LOADING_STYLE}>{meta}</div>
            )}
          </div>
        </div>
      </div>

      <div className="server-editor-footer">{presyncFooterText(stats)}</div>

      {search !== null && tracks[search] && (
        <ServerSearchOverlay
          key={tracks[search].mirror_track_id}
          track={tracks[search]}
          mode="replace"
          title="Pick a version from your library"
          note="Saved as this song's match. The sync uses it."
          onClose={() => setSearch(null)}
          onSelect={pick}
        />
      )}
    </div>
  );
}
