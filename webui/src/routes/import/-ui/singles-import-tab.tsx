import { useEffect } from 'react';

import { Button, Checkbox, TextInput } from '@/components/form/form';

import type { SingleSearchState } from '../-import.store';
import type { ImportTrackResult } from '../-import.types';
import type { ImportStagingFile } from '../-import.types';

import styles from './import-page.module.css';
import { searchImportTracks } from '../-import.api';
import { formatDuration, getStagingFileKey } from '../-import.helpers';
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
    syncSinglesWorkflow,
    toggleAllSingles,
    toggleSingleInStore,
  } = useSinglesImportWorkflow();

  useEffect(() => {
    syncSinglesWorkflow(stagingFiles);
  }, [stagingFiles, syncSinglesWorkflow]);

  const openSingleSearchPanel = (file: ImportStagingFile) => {
    const fileKey = getStagingFileKey(file);
    if (openSingleSearch === fileKey) {
      setOpenSingleSearch(null);
      return;
    }

    setOpenSingleSearch(fileKey);
    const defaultQuery =
      [file?.artist, file?.title].filter(Boolean).join(' ') ||
      (file?.filename || '').replace(/\.[^.]+$/, '');
    ensureSingleSearch(fileKey, defaultQuery);
    if (defaultQuery && !singleSearches[fileKey]?.results.length) {
      void runSingleSearch(fileKey, defaultQuery);
    }
  };

  const runSingleSearch = async (fileKey: string, query: string) => {
    const trimmed = query.trim();
    if (!trimmed) return;

    setSingleSearch(fileKey, (current) => ({
      query: trimmed,
      loading: true,
      error: null,
      results: current.results,
    }));

    try {
      const payload = await searchImportTracks(trimmed);
      setSingleSearch(fileKey, {
        query: trimmed,
        loading: false,
        error: null,
        results: payload.tracks ?? [],
      });
    } catch (error) {
      setSingleSearch(fileKey, {
        query: trimmed,
        loading: false,
        error: getErrorMessage(error),
        results: [],
      });
    }
  };

  const selectSingleMatch = (fileKey: string, track: ImportTrackResult) => {
    selectSingleMatchInStore(fileKey, track);
  };

  const processSingles = () => {
    const filesToProcess = stagingFiles.flatMap((file) => {
      const fileKey = getStagingFileKey(file);
      if (!selectedSingles.has(fileKey)) return [];
      const manualMatch = singlesManualMatches[fileKey];
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
      openSearchKey={openSingleSearch}
      searchStates={singleSearches}
      selected={selectedSingles}
      onOpenSearch={openSingleSearchPanel}
      onProcessSingles={processSingles}
      onRunSearch={runSingleSearch}
      onSearchQueryChange={(fileKey, query) => {
        setSingleSearch(fileKey, (current) => ({
          query,
          loading: current.loading,
          error: current.error,
          results: current.results,
        }));
      }}
      onSelectAll={() => toggleAllSingles(stagingFiles)}
      onSelectMatch={selectSingleMatch}
      onToggleSingle={toggleSingleInStore}
    />
  );
}

export function SinglesImportPanel({
  files,
  manualMatches,
  openSearchKey,
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
  manualMatches: Record<string, ImportTrackResult>;
  openSearchKey: string | null;
  searchStates: Record<string, SingleSearchState>;
  selected: Set<string>;
  onOpenSearch: (file: ImportStagingFile) => void;
  onProcessSingles: () => void;
  onRunSearch: (fileKey: string, query: string) => void;
  onSearchQueryChange: (fileKey: string, query: string) => void;
  onSelectAll: () => void;
  onSelectMatch: (fileKey: string, track: ImportTrackResult) => void;
  onToggleSingle: (fileKey: string) => void;
}) {
  const selectedCount = files.filter((file) => selected.has(getStagingFileKey(file))).length;
  const allSelected = files.length > 0 && selectedCount === files.length;

  return (
    <>
      <div className={styles.importPageSinglesHeader}>
        <div className={styles.importPageSinglesActions}>
          <Button type="button" className={styles.importPageSecondaryBtn} onClick={onSelectAll}>
            <span id="import-page-select-all-text">
              {allSelected ? 'Deselect All' : 'Select All'}
            </span>
          </Button>
          <Button
            type="button"
            className={styles.importPageProcessBtn}
            id="import-page-singles-process-btn"
            disabled={selectedCount === 0}
            onClick={onProcessSingles}
          >
            Process Selected ({selectedCount})
          </Button>
        </div>
      </div>
      <div className={styles.importPageSinglesList} id="import-page-singles-list">
        {files.length === 0 ? (
          <div className={styles.importPageEmptyState}>No audio files found in import folder</div>
        ) : (
          files.map((file) => {
            const fileKey = getStagingFileKey(file);
            const manualMatch = manualMatches[fileKey];
            const isSelected = selected.has(fileKey);
            const searchState = searchStates[fileKey];
            return (
              <div
                key={fileKey}
                className={`${styles.importPageSingleItem} ${
                  manualMatch ? styles.matched : ''
                }`}
                data-single-key={fileKey}
              >
                <label className={styles.importPageSingleCheckboxWrap}>
                  <Checkbox
                    checked={isSelected}
                    aria-label={`Select ${file.filename}`}
                    onCheckedChange={() => onToggleSingle(fileKey)}
                  />
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
                        onClick={() => onOpenSearch(file)}
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
                    onClick={() => onOpenSearch(file)}
                  >
                    🔍 Identify
                  </button>
                </div>
                {openSearchKey === fileKey ? (
                  <SingleSearchPanel
                    fileKey={fileKey}
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
  fileKey,
  searchState,
  onQueryChange,
  onRunSearch,
  onSelectMatch,
}: {
  fileKey: string;
  searchState: SingleSearchState | undefined;
  onQueryChange: (fileKey: string, query: string) => void;
  onRunSearch: (fileKey: string, query: string) => void;
  onSelectMatch: (fileKey: string, track: ImportTrackResult) => void;
}) {
  const query = searchState?.query ?? '';

  return (
    <div className={styles.importPageSingleSearchPanel}>
      <div className={styles.importPageSingleSearchBar}>
        <TextInput
          type="text"
          className={styles.importPageSingleSearchInput}
          value={query}
          placeholder="Search artist - title..."
          onChange={(event) => onQueryChange(fileKey, event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onRunSearch(fileKey, query);
          }}
        />
        <Button
          type="button"
          className={styles.importPageSingleSearchGo}
          onClick={() => onRunSearch(fileKey, query)}
        >
          Search
        </Button>
      </div>
      <div className={styles.importPageSingleSearchResults}>
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
              onClick={() => onSelectMatch(fileKey, track)}
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
