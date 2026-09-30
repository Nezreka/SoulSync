import { useMemo } from 'react';

import type { StudioFilters, StudioTrack } from '../-sample-studio.types';

import { QUALITY_TIER_LABEL, qualityTier, tempoBucket } from '../-sample-studio.helpers';
import { DEFAULT_FILTERS } from '../-sample-studio.types';
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
  { key: 'all', label: 'Quality' },
  { key: 'hires', label: 'Hi-Res 24-bit' },
  { key: 'lossless', label: 'Lossless 16-bit+' },
  { key: 'high', label: '320 kbps+' },
  { key: 'other', label: 'Standard' },
] as const;

const TEMPO_OPTIONS = [
  { key: 'all', label: 'Tempo' },
  { key: 'slow', label: 'Under 100 BPM' },
  { key: 'mid', label: '100–120 BPM' },
  { key: 'fast', label: '120–140 BPM' },
  { key: 'fastest', label: '140+ BPM' },
] as const;

const LENGTH_OPTIONS = [
  { key: 'all', label: 'Length' },
  { key: 'short', label: 'Under 2 min' },
  { key: 'medium', label: '2–5 min' },
  { key: 'long', label: '5+ min' },
] as const;

/** One compact filter: a native select styled as a pill, amber once set. */
function FilterSelect<K extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: K;
  options: readonly { key: K; label: string }[];
  onChange: (key: K) => void;
}) {
  return (
    <select
      className={styles.filterSelect}
      aria-label={label}
      data-active={value !== 'all'}
      value={value}
      onChange={(e) => onChange(e.target.value as K)}
    >
      {options.map((o) => (
        <option key={o.key} value={o.key}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

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
  const filtersActive =
    filters.quality !== 'all' || filters.tempo !== 'all' || filters.length !== 'all';
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
        <div className={styles.filterSelects} role="group" aria-label="Filters">
          <FilterSelect
            label="Quality"
            value={filters.quality}
            options={QUALITY_OPTIONS}
            onChange={(quality) => onFiltersChange({ ...filters, quality })}
          />
          <FilterSelect
            label="Tempo"
            value={filters.tempo}
            options={TEMPO_OPTIONS}
            onChange={(tempo) => onFiltersChange({ ...filters, tempo })}
          />
          <FilterSelect
            label="Length"
            value={filters.length}
            options={LENGTH_OPTIONS}
            onChange={(length) => onFiltersChange({ ...filters, length })}
          />
          {filtersActive && (
            <button
              type="button"
              className={styles.filterReset}
              onClick={() => onFiltersChange(DEFAULT_FILTERS)}
            >
              Reset
            </button>
          )}
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
