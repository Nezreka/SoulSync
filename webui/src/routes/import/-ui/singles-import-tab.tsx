import type { SingleSearchState } from '../-import.store';
import type { ImportTrackResult } from '../-import.types';
import type { ImportStagingFile } from '../-import.types';

import styles from './import-page.module.css';
import { searchImportTracks } from '../-import.api';
import { formatDuration } from '../-import.helpers';
import { useSinglesImportWorkflow } from '../-import.store';
import {
  fallbackImage,
  getErrorMessage,
  useImportQueueActions,
  useImportStaging,
} from './import-shared';

export function SinglesImportTab() {
  const { refreshStaging, stagingFiles } = useImportStaging();
  const { addQueueJob } = useImportQueueActions();
  const {
    clearSinglesSelection,
    ensureSingleSearch,
    openSingleSearch,
    selectedSingles,
    selectSingleMatchInStore,
    setOpenSingleSearch,
    setSingleSearch,
    singleSearches,
    singlesManualMatches,
    toggleAllSingles,
    toggleSingleInStore,
  } = useSinglesImportWorkflow();

  const openSingleSearchPanel = (index: number) => {
    if (openSingleSearch === index) {
      setOpenSingleSearch(null);
      return;
    }

    setOpenSingleSearch(index);
    const file = stagingFiles[index];
    const defaultQuery =
      [file?.artist, file?.title].filter(Boolean).join(' ') ||
      (file?.filename || '').replace(/\.[^.]+$/, '');
    ensureSingleSearch(index, defaultQuery);
    if (defaultQuery && !singleSearches[index]?.results.length) {
      void runSingleSearch(index, defaultQuery);
    }
  };

  const runSingleSearch = async (index: number, query: string) => {
    const trimmed = query.trim();
    if (!trimmed) return;

    setSingleSearch(index, (current) => ({
      query: trimmed,
      loading: true,
      error: null,
      results: current.results,
    }));

    try {
      const payload = await searchImportTracks(trimmed);
      setSingleSearch(index, {
        query: trimmed,
        loading: false,
        error: null,
        results: payload.tracks ?? [],
      });
    } catch (error) {
      setSingleSearch(index, {
        query: trimmed,
        loading: false,
        error: getErrorMessage(error),
        results: [],
      });
    }
  };

  const selectSingleMatch = (fileIndex: number, track: ImportTrackResult) => {
    selectSingleMatchInStore(fileIndex, track);
  };

  const processSingles = () => {
    if (selectedSingles.size === 0) return;
    const filesToProcess = Array.from(selectedSingles).flatMap((index) => {
      const file = stagingFiles[index];
      if (!file) return [];
      const manualMatch = singlesManualMatches[index];
      return manualMatch ? [{ ...file, manual_match: manualMatch }] : [file];
    });

    if (filesToProcess.length === 0) return;

    addQueueJob({
      type: 'singles',
      label: `${filesToProcess.length} Single${filesToProcess.length === 1 ? '' : 's'}`,
      sublabel:
        filesToProcess
          .map((file) => file.title || file.filename)
          .slice(0, 3)
          .join(', ') + (filesToProcess.length > 3 ? '...' : ''),
      imageUrl: null,
      items: filesToProcess,
    });

    clearSinglesSelection();
    refreshStaging();
  };

  return (
    <SinglesImportPanel
      files={stagingFiles}
      manualMatches={singlesManualMatches}
      openSearchIndex={openSingleSearch}
      searchStates={singleSearches}
      selected={selectedSingles}
      onOpenSearch={openSingleSearchPanel}
      onProcessSingles={processSingles}
      onRunSearch={runSingleSearch}
      onSearchQueryChange={(index, query) => {
        setSingleSearch(index, (current) => ({
          query,
          loading: current.loading,
          error: current.error,
          results: current.results,
        }));
      }}
      onSelectAll={() => toggleAllSingles(stagingFiles.length)}
      onSelectMatch={selectSingleMatch}
      onToggleSingle={toggleSingleInStore}
    />
  );
}

export function SinglesImportPanel({
  files,
  manualMatches,
  openSearchIndex,
  searchStates,
  selected,
  onOpenSearch,
  onProcessSingles,
  onRunSearch,
  onSearchQueryChange,
  onSelectAll,
  onSelectMatch,
  onToggleSingle,
}: {
  files: ImportStagingFile[];
  manualMatches: Record<number, ImportTrackResult>;
  openSearchIndex: number | null;
  searchStates: Record<number, SingleSearchState>;
  selected: Set<number>;
  onOpenSearch: (index: number) => void;
  onProcessSingles: () => void;
  onRunSearch: (index: number, query: string) => void;
  onSearchQueryChange: (index: number, query: string) => void;
  onSelectAll: () => void;
  onSelectMatch: (fileIndex: number, track: ImportTrackResult) => void;
  onToggleSingle: (index: number) => void;
}) {
  const allSelected = files.length > 0 && selected.size === files.length;

  return (
    <>
      <div className={styles.importPageSinglesHeader}>
        <div className={styles.importPageSinglesActions}>
          <button type="button" className={styles.importPageSecondaryBtn} onClick={onSelectAll}>
            <span id="import-page-select-all-text">
              {allSelected ? 'Deselect All' : 'Select All'}
            </span>
          </button>
          <button
            type="button"
            className={styles.importPageProcessBtn}
            id="import-page-singles-process-btn"
            disabled={selected.size === 0}
            onClick={onProcessSingles}
          >
            Process Selected ({selected.size})
          </button>
        </div>
      </div>
              <div className={styles.importPageSinglesList} id="import-page-singles-list">
        {files.length === 0 ? (
          <div className={styles.importPageEmptyState}>No audio files found in import folder</div>
        ) : (
          files.map((file, index) => {
            const manualMatch = manualMatches[index];
            const isSelected = selected.has(index);
            const searchState = searchStates[index];
            return (
              <div
                key={`${file.full_path}-${index}`}
                className={`${styles.importPageSingleItem} ${
                  manualMatch ? styles.matched : ''
                }`}
                data-single-idx={index}
              >
                <label className={styles.importPageSingleCheckboxWrap}>
                  <input
                    type="checkbox"
                    aria-label={`Select ${file.filename}`}
                    className={styles.importPageSingleCheckboxInput}
                    checked={isSelected}
                    onChange={() => onToggleSingle(index)}
                  />
                  <span className={styles.importPageSingleCheckbox} aria-hidden="true" />
                </label>
                <div className={styles.importPageSingleInfo}>
                  <div className={styles.importPageSingleFilename}>{file.filename}</div>
                  <div className={styles.importPageSingleMeta}>
                    {file.title ? <span>{file.title}</span> : null}
                    {file.artist ? <span>{file.artist}</span> : null}
                    {file.extension ? <span>{file.extension}</span> : null}
                  </div>
                  {manualMatch ? (
                    <div className={styles.importPageSingleMatchedInfo}>
                      ✓ {manualMatch.name} - {manualMatch.artist}
                      <button
                        type="button"
                        className={styles.importPageSingleMatchedChange}
                        onClick={() => onOpenSearch(index)}
                      >
                        change
                      </button>
                    </div>
                  ) : null}
                </div>
                <div className={styles.importPageSingleActions}>
                  <button
                    type="button"
                    className={styles.importPageIdentifyBtn}
                    onClick={() => onOpenSearch(index)}
                  >
                    🔍 Identify
                  </button>
                </div>
                {openSearchIndex === index ? (
                  <SingleSearchPanel
                    fileIndex={index}
                    searchState={searchState}
                    onQueryChange={onSearchQueryChange}
                    onRunSearch={onRunSearch}
                    onSelectMatch={onSelectMatch}
                  />
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </>
  );
}

function SingleSearchPanel({
  fileIndex,
  searchState,
  onQueryChange,
  onRunSearch,
  onSelectMatch,
}: {
  fileIndex: number;
  searchState: SingleSearchState | undefined;
  onQueryChange: (index: number, query: string) => void;
  onRunSearch: (index: number, query: string) => void;
  onSelectMatch: (fileIndex: number, track: ImportTrackResult) => void;
}) {
  const query = searchState?.query ?? '';

  return (
    <div className={styles.importPageSingleSearchPanel}>
      <div className={styles.importPageSingleSearchBar}>
        <input
          type="text"
          className={styles.importPageSingleSearchInput}
          value={query}
          placeholder="Search artist - title..."
          onChange={(event) => onQueryChange(fileIndex, event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onRunSearch(fileIndex, query);
          }}
        />
        <button
          type="button"
          className={styles.importPageSingleSearchGo}
          onClick={() => onRunSearch(fileIndex, query)}
        >
          Search
        </button>
      </div>
      <div className={styles.importPageSingleSearchResults} id={`import-single-results-${fileIndex}`}>
        {searchState?.loading ? (
          <div className={styles.importPageEmptyState}>Searching...</div>
        ) : searchState?.error ? (
          <div className={styles.importPageEmptyState}>Error: {searchState.error}</div>
        ) : searchState?.results.length === 0 ? (
          <div className={styles.importPageEmptyState}>No results found</div>
        ) : (
          searchState?.results.map((track, index) => (
            <button
              key={`${track.source || 'source'}-${track.id}-${index}`}
              type="button"
              className={styles.importPageSingleResultItem}
              onClick={() => onSelectMatch(fileIndex, track)}
            >
              {track.image_url ? (
                <img
                  className={styles.importPageSingleResultImg}
                  src={track.image_url}
                  alt=""
                  onError={fallbackImage}
                />
              ) : null}
              <div className={styles.importPageSingleResultInfo}>
                <div className={styles.importPageSingleResultName}>
                  {track.name} - {track.artist}
                </div>
                <div className={styles.importPageSingleResultDetail}>
                  {track.album}
                  {track.duration_ms ? ` - ${formatDuration(track.duration_ms)}` : ''}
                </div>
              </div>
              <span className={styles.importPageSingleResultSelect}>Select</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
