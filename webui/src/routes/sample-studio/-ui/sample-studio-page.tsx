import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { StashEntry, StemName, StudioFilters, StudioTrack } from '../-sample-studio.types';

import {
  deleteStashEntry,
  SAMPLE_STUDIO_QUERY_KEY,
  stashAudioUrl,
  studioAnalysisQueryOptions,
  studioPeaksQueryOptions,
  studioStashQueryOptions,
  studioTrackSearchQueryOptions,
} from '../-sample-studio.api';
import { analysisErrorMessage, isAnalysisError } from '../-sample-studio.types';
import { DEFAULT_FILTERS } from '../-sample-studio.types';
import { LibraryPanel } from './library-panel';
import styles from './sample-studio-page.module.css';
import { StashPanel } from './stash-panel';
import StemsPanel from './stems-panel';
import { WaveformEditor } from './waveform-editor';

export function SampleStudioPage() {
  useReactPageShell('sample-studio');
  const queryClient = useQueryClient();

  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [filters, setFilters] = useState<StudioFilters>(DEFAULT_FILTERS);
  const [selected, setSelected] = useState<StudioTrack | null>(null);
  const [stemSource, setStemSource] = useState<StemName | null>(null);
  const [stashError, setStashError] = useState<string | null>(null);
  const debounceRef = useRef(0);
  const stashAudioRef = useRef<HTMLAudioElement | null>(null);

  // Debounce the search input so we don't hammer /api/library/tracks.
  const onQueryChange = (q: string) => {
    setQuery(q);
    window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => setDebouncedQuery(q), 300);
  };

  const tracksQuery = useQuery(studioTrackSearchQueryOptions(debouncedQuery));
  const analysisQuery = useQuery(studioAnalysisQueryOptions(selected?.id ?? null));
  const peaksQuery = useQuery(studioPeaksQueryOptions(selected?.id ?? null, 1500, stemSource));
  const stashQuery = useQuery(studioStashQueryOptions());

  const analysis = analysisQuery.data;
  const analysisPending =
    !!selected && !!analysis && !isAnalysisError(analysis.status) && analysis.status !== 'done';
  const analysisError =
    selected && analysis && isAnalysisError(analysis.status)
      ? analysisErrorMessage(analysis.status)
      : null;

  const retryAnalysis = () => {
    void queryClient.invalidateQueries({
      queryKey: [...SAMPLE_STUDIO_QUERY_KEY, 'analysis', selected?.id] as const,
    });
  };

  // A stem only makes sense for the track it was separated from.
  const selectTrack = (track: StudioTrack | null) => {
    setSelected(track);
    setStemSource(null);
  };

  const refreshStash = () => {
    setStashError(null);
    void queryClient.invalidateQueries({
      queryKey: [...SAMPLE_STUDIO_QUERY_KEY, 'stash'] as const,
    });
  };

  const playStashEntry = (entry: StashEntry) => {
    const audio = stashAudioRef.current;
    if (!audio) return;
    audio.src = stashAudioUrl(entry.id);
    audio.load();
    void audio.play().catch(() => {});
  };

  const removeStashEntry = (entryId: number) => {
    setStashError(null);
    void deleteStashEntry(entryId).then(refreshStash, (e: unknown) => {
      setStashError(e instanceof Error ? e.message : 'Delete failed');
    });
  };

  return (
    <div className={styles.page}>
      <LibraryPanel
        tracks={tracksQuery.data ?? []}
        isLoading={tracksQuery.isLoading}
        query={query}
        onQueryChange={onQueryChange}
        filters={filters}
        onFiltersChange={setFilters}
        selectedId={selected?.id ?? null}
        onSelect={selectTrack}
      />
      {selected ? (
        <div className={styles.editorColumn}>
          <WaveformEditor
            track={selected}
            analysis={analysis}
            analysisPending={analysisPending}
            analysisError={analysisError}
            onRetryAnalysis={retryAnalysis}
            peaks={peaksQuery.data}
            stemSource={stemSource}
            onSavedStashEntry={refreshStash}
          />
          <StemsPanel trackId={selected.id} activeStem={stemSource} onSelectStem={setStemSource} />
        </div>
      ) : (
        <div className={styles.column}>
          <div className={styles.columnHeader}>
            <span>Editor</span>
          </div>
          <div className={styles.editorEmpty}>
            <h2>Your library is the sample pack.</h2>
            <p>
              Pick a track on the left to open it in the waveform editor. Set in and out points,
              loop a region, shift the pitch, match a tempo, then save the chop to your stash.
            </p>
            <div className={styles.tourSteps}>
              <div className={styles.tourStep}>
                <strong>1 · Pick</strong>
                <span>Search or browse your library</span>
              </div>
              <div className={styles.tourStep}>
                <strong>2 · Chop</strong>
                <span>Drag the amber handles, pitch it, tempo-match it</span>
              </div>
              <div className={styles.tourStep}>
                <strong>3 · Stash</strong>
                <span>Save it — file plus a bookmark you can re-cut</span>
              </div>
            </div>
          </div>
        </div>
      )}
      <StashPanel
        entries={stashQuery.data}
        isLoading={stashQuery.isLoading}
        error={(stashQuery.error as Error | null) ?? (stashError ? new Error(stashError) : null)}
        onPlay={playStashEntry}
        onDelete={removeStashEntry}
      />
      <audio ref={stashAudioRef} preload="none" style={{ display: 'none' }} />
    </div>
  );
}
