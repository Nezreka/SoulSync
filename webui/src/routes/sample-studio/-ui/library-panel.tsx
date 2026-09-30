import { useMemo } from 'react';

import type { StudioFilters, StudioTrack } from '../-sample-studio.types';

import { QUALITY_TIER_LABEL, qualityTier, tempoBucket } from '../-sample-studio.helpers';
import styles from './sample-studio-page.module.css';

interface LibraryPanelProps {
  tracks: StudioTrack[];
  isLoading: boolean;
  searchError: boolean;
  query: string;
  onQueryChange: (q: string) => void;
  filters: StudioFilters;
  onFiltersChange: (f: StudioFilters) => void;
  selectedId: number | null;
  onSelect: (track: StudioTrack) => void;
}

const QUALITY_OPTIONS = [
  { key: 'all', label: 'All quality' },
  { key: 'hires', label: 'Hi-Res 24-bit' },
  { key: 'lossless', label: 'Lossless 16-bit+' },
  { key: 'high', label: '320 kbps+' },
  { key: 'other', label: 'Standard' },
] as const;

const TEMPO_OPTIONS = [
  { key: 'all', label: 'Any tempo' },
  { key: 'slow', label: '< 100' },
  { key: 'mid', label: '100–120' },
  { key: 'fast', label: '120–140' },
  { key: 'fastest', label: '140+' },
] as const;

const LENGTH_OPTIONS = [
  { key: 'all', label: 'Any length' },
  { key: 'short', label: '< 2 min' },
  { key: 'medium', label: '2–5 min' },
  { key: 'long', label: '5+ min' },
] as const;

function lengthBucket(duration: number | null | undefined): 'short' | 'medium' | 'long' | null {
  if (typeof duration !== 'number' || !Number.isFinite(duration) || duration <= 0) return null;
  if (duration < 120) return 'short';
  if (duration < 300) return 'medium';
  return 'long';
}

export function LibraryPanel({
  tracks,
  isLoading,
  searchError,
  query,
  onQueryChange,
  filters,
  onFiltersChange,
  selectedId,
  onSelect,
}: LibraryPanelProps) {
  const filtered = useMemo(() => {
    return tracks.filter((t) => {
      if (filters.quality !== 'all') {
        const tier = qualityTier(t.bitrate, t.file_path);
        if (tier !== filters.quality) return false;
      }
      if (filters.tempo !== 'all') {
        // Library BPM column; unknown BPM can't be excluded by a tempo filter.
        const bucket = tempoBucket(t.bpm);
        if (bucket !== null && bucket !== filters.tempo) return false;
      }
      if (filters.length !== 'all' && lengthBucket(t.duration) !== filters.length) {
        return false;
      }
      return true;
    });
  }, [tracks, filters]);

  return (
    <div className={styles.column}>
      <div className={styles.columnHeader}>
        <span>Library</span>
        <span className={styles.resultCount}>{filtered.length} tracks</span>
      </div>
      <div className={styles.searchRow}>
        <input
          className={styles.searchInput}
          type="search"
          placeholder="Search your library…"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          aria-label="Search library"
        />
        <div className={styles.filterRow} role="group" aria-label="Quality filter">
          {QUALITY_OPTIONS.map((o) => (
            <button
              key={o.key}
              type="button"
              className={styles.filterPill}
              data-active={filters.quality === o.key}
              onClick={() => onFiltersChange({ ...filters, quality: o.key })}
            >
              {o.label}
            </button>
          ))}
        </div>
        <div className={styles.filterRow} role="group" aria-label="Tempo filter">
          {TEMPO_OPTIONS.map((o) => (
            <button
              key={o.key}
              type="button"
              className={styles.filterPill}
              data-active={filters.tempo === o.key}
              onClick={() => onFiltersChange({ ...filters, tempo: o.key })}
            >
              {o.label}
            </button>
          ))}
        </div>
        <div className={styles.filterRow} role="group" aria-label="Length filter">
          {LENGTH_OPTIONS.map((o) => (
            <button
              key={o.key}
              type="button"
              className={styles.filterPill}
              data-active={filters.length === o.key}
              onClick={() => onFiltersChange({ ...filters, length: o.key })}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
      <div className={styles.scroll}>
        {isLoading ? (
          <>
            <div className={styles.shimmer} />
            <div className={styles.shimmer} />
            <div className={styles.shimmer} />
          </>
        ) : searchError && !isLoading ? (
          <div className={styles.emptyHint}>
            Search failed — check your connection and try again.
          </div>
        ) : filtered.length === 0 ? (
          <div className={styles.emptyHint}>
            {query.trim()
              ? 'No tracks match. Try a different search or loosen the filters.'
              : 'Type to search your library — or pick from your recently added tracks.'}
          </div>
        ) : (
          filtered.map((t) => {
            const tier = qualityTier(t.bitrate, t.file_path);
            return (
              <button
                key={t.id}
                type="button"
                className={styles.trackRow}
                data-selected={selectedId === t.id}
                onClick={() => onSelect(t)}
              >
                <span className={styles.trackMeta}>
                  <span className={styles.trackTitle}>{t.title || 'Untitled'}</span>
                  <span className={styles.trackSub}>
                    {[t.artist_name, t.album_title].filter(Boolean).join(' · ') || 'Unknown artist'}
                  </span>
                </span>
                {tier !== 'unknown' && (
                  <span className={styles.qualityBadge} data-tier={tier}>
                    {QUALITY_TIER_LABEL[tier as keyof typeof QUALITY_TIER_LABEL] ?? tier}
                  </span>
                )}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
