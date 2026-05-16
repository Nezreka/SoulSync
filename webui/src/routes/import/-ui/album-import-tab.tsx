import { useQuery } from '@tanstack/react-query';
import { type DragEvent, type KeyboardEvent, useState } from 'react';

import { Button, TextInput } from '@/components/form/form';

import type { ImportAlbumResult } from '../-import.types';
import styles from './import-page.module.css';

import {
  importStagingGroupsQueryOptions,
  importStagingSuggestionsQueryOptions,
  matchImportAlbum,
  searchImportAlbums,
} from '../-import.api';
import {
  getDisplayedMatchFile,
  getEffectiveAlbumMatches,
  getTrackDisplayInfo,
  getUnmatchedStagingFiles,
  IMPORT_PLACEHOLDER_IMAGE,
} from '../-import.helpers';
import { useAlbumImportWorkflow } from '../-import.store';
import {
  fallbackImage,
  getErrorMessage,
  useImportQueueActions,
  useImportStaging,
} from './import-shared';

function useAlbumImportViewModel() {
  const { refreshStaging, stagingFiles } = useImportStaging();
  const [dragOverTrack, setDragOverTrack] = useState<number | null>(null);
  const [tapSelectedChip, setTapSelectedChip] = useState<number | null>(null);
  const groupsQuery = useQuery({
    ...importStagingGroupsQueryOptions(),
  });
  const suggestionsQuery = useQuery({
    ...importStagingSuggestionsQueryOptions(),
  });
  const { addQueueJob } = useImportQueueActions();
  const {
    albumMatch,
    albumMatchError,
    albumMatchLoading,
    albumQuery,
    albumResults,
    albumSearchError,
    albumSearchLoading,
    autoGroupFilePaths,
    clearAutoGroupFilePaths,
    matchOverrides,
    resetAlbumWorkflow,
    selectedAlbum,
    setAlbumMatch,
    setAlbumMatchError,
    setAlbumMatchLoading,
    setAlbumQuery,
    setAlbumResults,
    setAlbumSearchContext,
    setAlbumSearchError,
    setAlbumSearchLoading,
    setMatchOverrides,
    setSelectedAlbum,
  } = useAlbumImportWorkflow();

  const resetAlbumSearch = () => {
    setDragOverTrack(null);
    setTapSelectedChip(null);
    resetAlbumWorkflow();
    refreshStaging();
  };

  const runAlbumSearch = async (query: string, filePaths: string[] | null = null) => {
    const trimmed = query.trim();
    if (!trimmed) return;

    setAlbumSearchContext(trimmed, filePaths);

    try {
      const payload = await searchImportAlbums(trimmed);
      setAlbumResults(payload.albums ?? []);
    } catch (error) {
      setAlbumSearchError(getErrorMessage(error));
    } finally {
      setAlbumSearchLoading(false);
    }
  };

  const selectAlbum = async (album: ImportAlbumResult) => {
    setSelectedAlbum(album);
    setAlbumMatch(null);
    setAlbumMatchError(null);
    setAlbumMatchLoading(true);

    try {
      const payload = await matchImportAlbum({
        albumId: album.id,
        source: album.source,
        albumName: album.name,
        albumArtist: album.artist,
        filePaths: autoGroupFilePaths,
      });
      setAlbumMatch(payload);
      setMatchOverrides({});
      setTapSelectedChip(null);
      setDragOverTrack(null);
    } catch (error) {
      setAlbumMatchError(getErrorMessage(error));
    } finally {
      clearAutoGroupFilePaths();
      setAlbumMatchLoading(false);
    }
  };

  const assignMatchFile = (trackIndex: number, stagingFileIndex: number) => {
    setMatchOverrides((current) => {
      const next = { ...current };
      for (const [key, value] of Object.entries(next)) {
        if (value === stagingFileIndex) {
          delete next[Number(key)];
        }
      }
      next[trackIndex] = stagingFileIndex;
      return next;
    });
    setTapSelectedChip(null);
  };

  const unmatchTrack = (trackIndex: number) => {
    setMatchOverrides((current) => {
      const next = { ...current };
      delete next[trackIndex];
      if (albumMatch?.matches?.[trackIndex]?.staging_file) {
        next[trackIndex] = -1;
      }
      return next;
    });
  };

  const processAlbum = () => {
    const album = albumMatch?.album;
    const matches = albumMatch?.matches ?? [];
    if (!album || matches.length === 0) return;

    const effectiveMatches = getEffectiveAlbumMatches(matches, stagingFiles, matchOverrides);
    if (effectiveMatches.length === 0) return;

    addQueueJob({
      type: 'album',
      label: album.name,
      sublabel: `${album.artist} - ${effectiveMatches.length} tracks`,
      imageUrl: album.image_url,
      items: effectiveMatches,
      albumData: album,
    });
    resetAlbumSearch();
  };

  return {
    albumMatch,
    albumMatchError,
    albumMatchLoading,
    albumQuery,
    albumResults,
    albumSearchError,
    albumSearchLoading,
    dragOverTrack,
    groups: groupsQuery.data?.groups ?? [],
    matchOverrides,
    onAlbumQueryChange: setAlbumQuery,
    onAutoRematch: () => {
      setMatchOverrides({});
      setTapSelectedChip(null);
      setDragOverTrack(null);
    },
    onBackToSearch: resetAlbumSearch,
    onDragOverTrack: setDragOverTrack,
    onProcessAlbum: processAlbum,
    onRunGroupSearch: (group: {
      album: string;
      artist: string;
      file_count: number;
      file_paths: string[];
    }) => {
      void runAlbumSearch(`${group.artist} ${group.album}`, group.file_paths);
    },
    onRunSearch: () => {
      void runAlbumSearch(albumQuery);
    },
    onSelectAlbum: (album: ImportAlbumResult) => {
      void selectAlbum(album);
    },
    onTapAssign: assignMatchFile,
    onTapSelectChip: (index: number) => {
      setTapSelectedChip((current) => (current === index ? null : index));
    },
    onUnmatchTrack: unmatchTrack,
    selectedAlbum,
    stagingFiles,
    suggestions: suggestionsQuery.data?.suggestions ?? [],
    suggestionsReady: suggestionsQuery.data?.ready ?? true,
    tapSelectedChip,
  };
}

type AlbumImportViewModel = ReturnType<typeof useAlbumImportViewModel>;

export function AlbumImportTab() {
  const viewModel = useAlbumImportViewModel();

  return <AlbumImportPanelContent viewModel={viewModel} />;
}

function AlbumImportPanelContent({ viewModel }: { viewModel: AlbumImportViewModel }) {
  const {
    albumMatch,
    albumMatchError,
    albumMatchLoading,
    albumQuery,
    albumResults,
    albumSearchError,
    albumSearchLoading,
    groups,
    onAlbumQueryChange,
    onBackToSearch,
    onRunGroupSearch,
    onRunSearch,
    onSelectAlbum,
    selectedAlbum,
    suggestions,
    suggestionsReady,
  } = viewModel;

  const showingMatch = selectedAlbum || albumMatchLoading || albumMatchError || albumMatch;

  return (
    <>
      <div
        id="import-page-album-search-section"
        className={showingMatch ? styles.hidden : ''}
      >
        {albumResults === null && (
          <>
            {groups.length > 0 && (
              <div id="import-page-auto-groups" className={styles.importPageAutoGroups}>
                <div className={styles.importPageSectionLabel}>Auto-Detected Albums</div>
                <div className={styles.importPageAlbumGrid}>
                  {groups.map((group, index) => (
                  <button
                      key={`${group.artist}-${group.album}-${index}`}
                      type="button"
                      className={`${styles.importPageAlbumCard} ${styles.importPageAutoGroupCard}`}
                      onClick={() => onRunGroupSearch(group)}
                    >
                      <div className={styles.importPageAutoGroupCount}>{group.file_count}</div>
                      <div className={styles.importPageAutoGroupInfo}>
                        <div className={styles.importPageAlbumCardTitle} title={group.album}>
                          {group.album}
                        </div>
                        <div className={styles.importPageAlbumCardArtist} title={group.artist}>
                          {group.artist} · {group.file_count} tracks
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className={styles.importPageSuggestions} id="import-page-suggestions">
              <div className={styles.importPageSectionLabel}>Suggested from your import folder</div>
              <div className={styles.importPageAlbumGrid} id="import-page-suggestions-grid">
                {suggestions.length > 0 ? (
                  suggestions.map((album) => (
                    <AlbumCard
                      key={`${album.source || 'source'}-${album.id}`}
                      album={album}
                      onSelect={onSelectAlbum}
                    />
                  ))
                ) : suggestionsReady ? null : (
                  <div className={styles.importPageEmptyState}>Loading suggestions...</div>
                )}
              </div>
            </div>
          </>
        )}

        <div className={styles.importPageSearchBar}>
          <TextInput
            type="text"
            id="import-page-album-search-input"
            className={styles.importPageSearchInput}
            placeholder="Search for an album..."
            value={albumQuery}
            onChange={(event) => onAlbumQueryChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') onRunSearch();
            }}
          />
          <Button type="button" className={styles.importPageSearchBtn} onClick={onRunSearch}>
            Search
          </Button>
          <Button
            type="button"
            className={`${styles.importPageClearBtn} ${albumResults === null ? styles.hidden : ''}`}
            id="import-page-album-clear-btn"
            title="Clear search"
            onClick={onBackToSearch}
          >
            x
          </Button>
        </div>

        <div className={styles.importPageAlbumGrid} id="import-page-album-results">
          {albumSearchLoading ? (
            <div className={styles.importPageEmptyState}>Searching...</div>
          ) : albumSearchError ? (
            <div className={styles.importPageEmptyState}>Error: {albumSearchError}</div>
          ) : albumResults?.length === 0 ? (
            <div className={styles.importPageEmptyState}>No albums found</div>
          ) : (
            albumResults?.map((album) => (
              <AlbumCard
                key={`${album.source || 'source'}-${album.id}`}
                album={album}
                onSelect={onSelectAlbum}
              />
            ))
          )}
        </div>
      </div>

      <div
        id="import-page-album-match-section"
        className={showingMatch ? '' : styles.hidden}
      >
        {albumMatchLoading ? (
          <div className={styles.importPageEmptyState}>Matching files to tracklist...</div>
        ) : albumMatchError ? (
          <div className={styles.importPageEmptyState}>Error: {albumMatchError}</div>
        ) : albumMatch?.album ? (
          <AlbumMatchPanel viewModel={viewModel} />
        ) : (
          <div className={styles.importPageEmptyState}>Select an album to start matching files.</div>
        )}
      </div>
    </>
  );
}

function AlbumCard({
  album,
  onSelect,
}: {
  album: ImportAlbumResult;
  onSelect: (album: ImportAlbumResult) => void;
}) {
  return (
    <button type="button" className={styles.importPageAlbumCard} onClick={() => onSelect(album)}>
      <img
        src={album.image_url || IMPORT_PLACEHOLDER_IMAGE}
        alt={album.name}
        loading="lazy"
        onError={fallbackImage}
      />
      <div className={styles.importPageAlbumCardTitle} title={album.name}>
        {album.name}
      </div>
      <div className={styles.importPageAlbumCardArtist} title={album.artist}>
        {album.artist}
      </div>
      <div className={styles.importPageAlbumCardMeta}>
        {album.total_tracks || 0} tracks · {album.release_date?.substring(0, 4) || ''}
      </div>
    </button>
  );
}

function AlbumMatchPanel({ viewModel }: { viewModel: AlbumImportViewModel }) {
  const {
    albumMatch,
    albumMatchError,
    albumMatchLoading,
    dragOverTrack,
    matchOverrides,
    onAutoRematch,
    onBackToSearch,
    onDragOverTrack,
    onProcessAlbum,
    onTapAssign,
    onTapSelectChip,
    onUnmatchTrack,
    stagingFiles,
    tapSelectedChip,
  } = viewModel;

  const effectiveMatches = getEffectiveAlbumMatches(
    albumMatch?.matches ?? [],
    stagingFiles,
    matchOverrides,
  );
  const unmatchedFiles = getUnmatchedStagingFiles(
    albumMatch?.matches ?? [],
    stagingFiles,
    matchOverrides,
  );
  const matchedCount = effectiveMatches.length;

  return albumMatchLoading ? (
    <div className={styles.importPageEmptyState}>Matching files to tracklist...</div>
  ) : albumMatchError ? (
    <div className={styles.importPageEmptyState}>Error: {albumMatchError}</div>
  ) : albumMatch?.album ? (
    <>
      <div className={styles.importPageAlbumHero} id="import-page-album-hero">
        <img
          src={albumMatch.album.image_url || IMPORT_PLACEHOLDER_IMAGE}
          alt={albumMatch.album.name}
          loading="lazy"
          onError={fallbackImage}
        />
        <div className={styles.importPageAlbumHeroInfo}>
          <div className={styles.importPageAlbumHeroTitle}>{albumMatch.album.name}</div>
          <div className={styles.importPageAlbumHeroArtist}>{albumMatch.album.artist}</div>
          <div className={styles.importPageAlbumHeroMeta}>
            {albumMatch.album.total_tracks || albumMatch.matches?.length || 0} tracks ·{' '}
            {albumMatch.album.release_date?.substring(0, 4) || ''}
          </div>
        </div>
      </div>

      <div className={styles.importPageMatchHeader}>
        <h3>Track Matching</h3>
        <div className={styles.importPageMatchActions}>
          <Button
            type="button"
            className={styles.importPageSecondaryBtn}
            onClick={onAutoRematch}
          >
            Re-match Automatically
          </Button>
          <Button
            type="button"
            className={styles.importPageBackBtn}
            onClick={onBackToSearch}
          >
            Back to Search
          </Button>
        </div>
      </div>

      <div className={styles.importPageMatchList} id="import-page-match-list">
        {(albumMatch.matches ?? []).map((match, index) => {
          const trackInfo = getTrackDisplayInfo(match, index);
          const { confidence, file } = getDisplayedMatchFile(
            match,
            index,
            stagingFiles,
            matchOverrides,
          );
          const confidencePercent = Math.round(confidence * 100);
          return (
            <div
              key={`${trackInfo.displayTrackNumber}-${trackInfo.name}-${index}`}
              className={`${styles.importPageMatchRow} ${
                file ? styles.matched : ''
              } ${dragOverTrack === index ? styles.dragOver : ''}`}
              onClick={() => {
                if (tapSelectedChip !== null) onTapAssign(index, tapSelectedChip);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = 'move';
                onDragOverTrack(index);
              }}
              onDragLeave={() => onDragOverTrack(null)}
              onDrop={(event) => {
                event.preventDefault();
                onDragOverTrack(null);
                const stagingFileIndex = Number(event.dataTransfer.getData('text/plain'));
                if (Number.isFinite(stagingFileIndex)) onTapAssign(index, stagingFileIndex);
              }}
            >
              <span className={styles.importPageMatchNum}>{trackInfo.displayTrackNumber}</span>
              <span className={styles.importPageMatchTrack}>{trackInfo.name}</span>
              <span
                className={`${styles.importPageMatchFile} ${
                  file ? styles.hasFile : ''
                }`}
              >
                {file ? (
                  <>
                    <span className={styles.importPageMatchFileName}>{file.filename}</span>
                    <span
                      className={`${styles.importPageMatchConfidence} ${
                        confidence >= 0.7 ? '' : styles.low
                      }`}
                    >
                      {confidencePercent}%
                    </span>
                  </>
                ) : (
                  <span className={styles.importPageMatchDropZone}>Drop a file here</span>
                )}
              </span>
              <span>
                {file ? (
                  <Button
                    type="button"
                    className={styles.importPageMatchUnmatch}
                    onClick={(event) => {
                      event.stopPropagation();
                      onUnmatchTrack(index);
                    }}
                  >
                    x
                  </Button>
                ) : null}
              </span>
            </div>
          );
        })}
      </div>

      <div className={styles.importPageUnmatchedPool} id="import-page-unmatched-pool">
        <div className={styles.importPagePoolLabel}>
          Unmatched Files (<span id="import-page-unmatched-count">{unmatchedFiles.length}</span>)
        </div>
        <div className={styles.importPagePoolChips} id="import-page-pool-chips">
          {unmatchedFiles.length === 0 ? (
            <span className={styles.importPagePoolEmpty}>All files matched</span>
          ) : (
            unmatchedFiles.map(({ file, index }) => (
              <span
                key={`${file.full_path}-${index}`}
                role="button"
                tabIndex={0}
                className={`${styles.importPageFileChip} ${
                  tapSelectedChip === index ? styles.selected : ''
                }`}
                draggable
                onClick={(event) => {
                  event.stopPropagation();
                  onTapSelectChip(index);
                }}
                onDragStart={(event: DragEvent<HTMLSpanElement>) => {
                  event.dataTransfer.setData('text/plain', String(index));
                  event.dataTransfer.effectAllowed = 'move';
                }}
                onKeyDown={(event: KeyboardEvent<HTMLSpanElement>) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onTapSelectChip(index);
                  }
                }}
              >
                {file.filename}
              </span>
            ))
          )}
        </div>
      </div>

      <div className={styles.importPageMatchFooter}>
        <div className={styles.importPageMatchStats} id="import-page-match-stats">
          {matchedCount} of {albumMatch.matches?.length ?? 0} tracks matched
        </div>
        <Button
          type="button"
          className={styles.importPageProcessBtn}
          id="import-page-album-process-btn"
          disabled={matchedCount === 0}
          onClick={onProcessAlbum}
        >
          Process {matchedCount} Track{matchedCount === 1 ? '' : 's'}
        </Button>
      </div>
    </>
  ) : (
    <div className={styles.importPageEmptyState}>Select an album to start matching files.</div>
  );
}
