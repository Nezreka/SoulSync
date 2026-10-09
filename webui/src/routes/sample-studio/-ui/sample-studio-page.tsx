import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { StashEntry, StemName, StudioFilters, StudioTrack } from '../-sample-studio.types';

import {
  deleteStashEntry,
  lookupStudioTrack,
  SAMPLE_STUDIO_QUERY_KEY,
  stashAudioUrl,
  studioAnalysisQueryOptions,
  studioPeaksQueryOptions,
  studioStashQueryOptions,
  studioTrackSearchQueryOptions,
} from '../-sample-studio.api';
import { analysisErrorMessage, analysisLoadError, isAnalysisError } from '../-sample-studio.types';
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
  // Bumped by Try again: re-fires the analysis query with ?retry=1 so a
  // sticky worker error is cleared and the track re-queues.
  const [analysisRetryNonce, setAnalysisRetryNonce] = useState(0);
  // Handed to the editor when a stash entry is re-opened: the full recipe
  // (region, pitch, tempo, FX) to restore. The nonce re-fires restores of
  // the same entry.
  const [restore, setRestore] = useState<{ entry: StashEntry; nonce: number } | null>(null);
  const debounceRef = useRef(0);
  const stashAudioRef = useRef<HTMLAudioElement | null>(null);

  // Debounce the search input so we don't hammer /api/library/tracks.
  const onQueryChange = (q: string) => {
    setQuery(q);
    window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => setDebouncedQuery(q), 300);
  };

  const tracksQuery = useQuery(studioTrackSearchQueryOptions(debouncedQuery));
  const analysisQuery = useQuery(
    studioAnalysisQueryOptions(selected?.id ?? null, analysisRetryNonce),
  );
  const peaksQuery = useQuery(studioPeaksQueryOptions(selected?.id ?? null, 1500, stemSource));
  const stashQuery = useQuery(studioStashQueryOptions());

  const analysis = analysisQuery.data;
  const analysisPending =
    !!selected && !!analysis && !isAnalysisError(analysis.status) && analysis.status !== 'done';
  const analysisError =
    selected && analysis && isAnalysisError(analysis.status)
      ? analysisErrorMessage(analysis.status)
      : selected && analysisQuery.error
        ? analysisLoadError(analysisQuery.error)
        : null;

  const retryAnalysis = () => {
    // New query key -> refetch with ?retry=1: clears the sticky worker
    // error server-side and queues the track again.
    setAnalysisRetryNonce((n) => n + 1);
  };

  // A stem only makes sense for the track it was separated from.
  const selectTrack = (track: StudioTrack | null) => {
    setSelected(track);
    setStemSource(null);
    setAnalysisRetryNonce(0);
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
    const entry = stashQuery.data?.find((e) => e.id === entryId);
    void (async () => {
      // deleting removes the audio file from the sample folder too, so ask first
      const ok = window.showConfirmDialog
        ? await window.showConfirmDialog({
            title: 'Delete this chop?',
            message: `“${entry?.name ?? 'This chop'}” and its audio file will be removed from your sample folder.`,
            confirmText: 'Delete',
            destructive: true,
          })
        : true;
      if (!ok) return;
      try {
        await deleteStashEntry(entryId);
        refreshStash();
      } catch (e) {
        setStashError(e instanceof Error ? e.message : 'Delete failed');
      }
    })();
  };

  /** Re-open a stash entry: find its source track, select it, and hand the
   *  full recipe to the editor. */
  const restoreStashEntry = (entry: StashEntry) => {
    setStashError(null);
    // stash rows used to come back numeric while tracks came back as text,
    // so this never matched and reopening a chop reloaded its own track
    if (selected && String(selected.id) === String(entry.track_id)) {
      setStemSource(entry.stem ?? null);
      setRestore({ entry, nonce: Date.now() });
      return;
    }
    void (async () => {
      try {
        const track = await lookupStudioTrack(entry.track_id, entry.track_title, entry.artist_name);
        if (!track) {
          setStashError(
            `Couldn’t find “${entry.track_title}” in your library — it may have been removed.`,
          );
          return;
        }
        // selectTrack clears the stem source; the entry's source wins.
        selectTrack(track);
        setStemSource(entry.stem ?? null);
        setRestore({ entry, nonce: Date.now() });
      } catch (e) {
        setStashError(e instanceof Error ? e.message : 'Could not re-open that chop');
      }
    })();
  };

  return (
    <div className={styles.page}>
      <LibraryPanel
        tracks={tracksQuery.data ?? []}
        isLoading={tracksQuery.isLoading}
        searchError={tracksQuery.isError}
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
            restore={restore}
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
        onRestore={restoreStashEntry}
      />
      <audio ref={stashAudioRef} preload="none" style={{ display: 'none' }} />
    </div>
  );
}
