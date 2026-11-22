import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';

import {
  BATCH_SIZE,
  type ParseTally,
  type SpotifyHistorySummary,
  batches,
  leftOutLine,
  readSpotifyExport,
  summaryLine,
} from '../-spotify-history';
import {
  STATS_QUERY_KEY,
  finishSpotifyHistoryImport,
  removeSpotifyHistory,
  sendSpotifyHistoryBatch,
  setSpotifyHistoryInStats,
  spotifyHistoryQueryOptions,
} from '../-stats.api';
import styles from './stats-page.module.css';

type Phase =
  | { kind: 'idle' }
  | { kind: 'reading' }
  | { kind: 'sending'; sent: number; total: number }
  | { kind: 'done'; summary: SpotifyHistorySummary; tally: ParseTally }
  | { kind: 'error'; message: string };

type ConfirmOptions = {
  title: string;
  message: string;
  confirmText: string;
  cancelText?: string;
  destructive?: boolean;
};

async function confirmRemove(): Promise<boolean> {
  const ask = (window as { showConfirmDialog?: (o: ConfirmOptions) => Promise<boolean> })
    .showConfirmDialog;
  const options = {
    title: 'Remove the Spotify history?',
    message:
      'Every play this import added comes out of your history. Plays ListenBrainz or Last.fm brought in stay.',
    confirmText: 'Remove',
    destructive: true,
  };
  if (typeof ask === 'function') return await ask(options);
  return window.confirm(options.message);
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/**
 * Import Spotify streaming history from the privacy export. The export is read
 * here, in the browser; only music plays of 30 seconds or more go to the
 * server, in batches.
 */
export function SpotifyHistoryImport({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const status = useQuery(spotifyHistoryQueryOptions());
  const [files, setFiles] = useState<File[]>([]);
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [includeChoice, setIncludeChoice] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const existing = status.data?.imported ? status.data : null;
  const includeInStats = includeChoice ?? existing?.include_in_stats ?? true;
  const working = phase.kind === 'reading' || phase.kind === 'sending' || busy;

  const refreshStats = () => {
    void queryClient.invalidateQueries({ queryKey: STATS_QUERY_KEY });
    // the server rebuilds the stats in the background; ask again once it has
    window.setTimeout(
      () => void queryClient.invalidateQueries({ queryKey: STATS_QUERY_KEY }),
      4000,
    );
  };

  const runImport = async () => {
    if (!files.length || working) return;
    setPhase({ kind: 'reading' });
    try {
      const { rows, tally } = await readSpotifyExport(files);
      if (!rows.length) {
        setPhase({
          kind: 'error',
          message:
            tally.files || tally.unreadable
              ? `No music plays to import. ${leftOutLine(tally)}`.trim()
              : 'No Spotify history found in these files.',
        });
        return;
      }
      const chunks = batches(rows, Math.min(BATCH_SIZE, status.data?.max_batch ?? BATCH_SIZE));
      let sent = 0;
      setPhase({ kind: 'sending', sent, total: rows.length });
      for (const chunk of chunks) {
        await sendSpotifyHistoryBatch(chunk, includeInStats);
        sent += chunk.length;
        setPhase({ kind: 'sending', sent, total: rows.length });
      }
      const summary = await finishSpotifyHistoryImport();
      queryClient.setQueryData(spotifyHistoryQueryOptions().queryKey, summary);
      setPhase({ kind: 'done', summary, tally });
      setFiles([]);
      refreshStats();
    } catch (error) {
      setPhase({ kind: 'error', message: message(error, 'The import stopped.') });
    }
  };

  const toggleInStats = async (next: boolean) => {
    setIncludeChoice(next);
    if (!existing) return;
    setBusy(true);
    try {
      const summary = await setSpotifyHistoryInStats(next);
      queryClient.setQueryData(spotifyHistoryQueryOptions().queryKey, summary);
      refreshStats();
    } catch (error) {
      setIncludeChoice(!next);
      window.showToast?.(message(error, 'Could not save that'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!(await confirmRemove())) return;
    setBusy(true);
    try {
      const removed = await removeSpotifyHistory();
      window.showToast?.(
        `Removed ${removed.toLocaleString('en-US')} imported ${removed === 1 ? 'play' : 'plays'}`,
        'success',
      );
      setPhase({ kind: 'idle' });
      setIncludeChoice(null);
      await status.refetch();
      refreshStats();
    } catch (error) {
      window.showToast?.(message(error, 'Could not remove the import'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const pick = (list: FileList | null) => {
    if (!list) return;
    setFiles(Array.from(list));
    if (phase.kind === 'error' || phase.kind === 'done') setPhase({ kind: 'idle' });
  };

  const shown = phase.kind === 'done' ? phase.summary : existing;

  return (
    <DialogFrame
      open
      onOpenChange={(next) => {
        if (!next && !working) onClose();
      }}
      aria-label="Import Spotify history"
    >
      <DialogHeader title="Import Spotify history" />
      <DialogBody>
        <div className={styles.spotifyImport}>
          <p className={styles.spotifyLead}>
            Ask Spotify for your data under Account, Privacy settings, then pick the zip it sends
            you (or the StreamingHistory and Streaming_History_Audio files inside it). The file is
            read here on this device. Only music plays of 30 seconds or more are sent: podcasts,
            audiobooks and private session plays stay out.
          </p>

          {shown ? (
            <div className={styles.spotifyCurrent} role="status">
              <b>{phase.kind === 'done' ? 'Imported' : 'Your Spotify history'}</b>
              <span>{summaryLine(shown)}</span>
              {shown.linked ? (
                <span className={styles.syncQuiet}>
                  {shown.linked.toLocaleString('en-US')} were already in your history from
                  ListenBrainz or Last.fm, so they were linked, not added twice.
                </span>
              ) : null}
              {phase.kind === 'done' && leftOutLine(phase.tally) ? (
                <span className={styles.syncQuiet}>{leftOutLine(phase.tally)}</span>
              ) : null}
            </div>
          ) : null}

          <label
            htmlFor={inputId}
            className={
              dragging ? `${styles.spotifyDrop} ${styles.spotifyDropOn}` : styles.spotifyDrop
            }
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              pick(e.dataTransfer.files);
            }}
          >
            <input
              ref={inputRef}
              id={inputId}
              type="file"
              multiple
              accept=".zip,.json,application/zip,application/json"
              className={styles.spotifyFile}
              disabled={working}
              onChange={(e) => pick(e.target.files)}
            />
            {files.length
              ? files.length === 1
                ? files[0].name
                : `${files.length} files picked`
              : 'Drop the zip or json files here, or choose them'}
          </label>

          <label className={styles.spotifyCheck}>
            <input
              type="checkbox"
              checked={includeInStats}
              disabled={working}
              onChange={(e) => void toggleInStats(e.target.checked)}
            />
            <span>
              Count these plays in Stats and Your Year. Off keeps them for your mixes and
              recommendations only.
            </span>
          </label>

          {phase.kind === 'reading' ? (
            <p className={styles.syncQuiet} role="status">
              Reading the export…
            </p>
          ) : null}
          {phase.kind === 'sending' ? (
            <div role="status" className={styles.spotifyProgress}>
              <span>
                Sending plays · {phase.sent.toLocaleString('en-US')} of{' '}
                {phase.total.toLocaleString('en-US')}
              </span>
              <progress
                aria-label="Spotify history import progress"
                max={phase.total}
                value={phase.sent}
              />
            </div>
          ) : null}
          {phase.kind === 'error' ? (
            <p className={styles.syncBad} role="alert">
              {phase.message}
            </p>
          ) : null}
        </div>
      </DialogBody>
      <DialogFooter>
        {existing ? (
          <button
            type="button"
            className={styles.linkButton}
            onClick={() => void remove()}
            disabled={working}
          >
            Remove this import
          </button>
        ) : null}
        <button
          type="button"
          className={styles.cap}
          onClick={() => void runImport()}
          disabled={!files.length || working}
        >
          {phase.kind === 'sending' || phase.kind === 'reading' ? 'Importing…' : 'Import'}
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}
