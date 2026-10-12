import { useCallback, useEffect, useState } from 'react';

import type { RepairJob, RepairJobProgress } from '../-tools.types';
import type { ReviewQueueModeTotals } from '../../review/-review.api';

import { fetchReviewQueueModeTotals, runReconciliationScan } from '../../review/-review.api';

interface ReviewQueueEntryProps {
  job?: RepairJob;
  progress?: RepairJobProgress;
  onScanStarted: () => void;
}

export function ReviewQueueEntry({ job, progress, onScanStarted }: ReviewQueueEntryProps) {
  const [totals, setTotals] = useState<ReviewQueueModeTotals | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const load = useCallback(async () => {
    try {
      setTotals(await fetchReviewQueueModeTotals());
      setError('');
    } catch {
      setError('Album review status is unavailable.');
    }
  }, []);

  useEffect(() => {
    void load();
    const onFocus = () => void load();
    const onVisible = () => {
      if (!document.hidden) void load();
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [load, job?.is_running, job?.last_run?.finished_at, progress?.status]);

  const startScan = async () => {
    setStarting(true);
    try {
      await runReconciliationScan();
      onScanStarted();
      setError('');
    } catch (cause) {
      setError((cause as Error).message || 'Could not start the album scan.');
    } finally {
      setStarting(false);
    }
  };

  const lastRun = job?.last_run;
  const lastScan = lastRun?.finished_at
    ? `Last scan ${new Date(lastRun.finished_at).toLocaleString()}`
    : 'No completed album scan yet';

  return (
    <section className="review-queue-entry" aria-label="Album review status">
      <div className="review-queue-entry-copy">
        <span className="review-queue-entry-eyebrow">Album review</span>
        <h3>{totals?.review ? `${totals.review} albums need decisions` : 'Review album plans'}</h3>
        <p>
          Compare local tracks with a release, decide album wide changes, then apply the approved
          plan.
        </p>
        <span className="review-queue-entry-scan">
          {progress?.status === 'running' || (job?.is_running && progress?.status !== 'finished')
            ? 'Album scan running'
            : lastScan}
          {lastRun?.status === 'failed' ? ' · Last scan failed' : ''}
        </span>
        {error ? (
          <span className="review-queue-entry-error" role="status">
            {error}
          </span>
        ) : null}
      </div>
      <div className="review-queue-entry-actions">
        <a href="/review?mode=review" className="btn btn--primary">
          Needs input {totals ? `(${totals.review})` : ''}
        </a>
        <a href="/review?mode=ready" className="btn btn--secondary">
          Ready to apply {totals ? `(${totals.ready})` : ''}
        </a>
        <a href="/review?mode=files" className="btn btn--secondary">
          File actions {totals ? `(${totals.files})` : ''}
        </a>
        <button
          className="btn btn--secondary"
          type="button"
          disabled={
            starting ||
            progress?.status === 'running' ||
            (job?.is_running && progress?.status !== 'finished')
          }
          onClick={() => void startScan()}
        >
          {starting ||
          progress?.status === 'running' ||
          (job?.is_running && progress?.status !== 'finished')
            ? 'Scanning…'
            : 'Scan albums'}
        </button>
      </div>
    </section>
  );
}
