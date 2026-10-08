import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { FindingTypeInfo } from '../../tools/-tools.groups';

import {
  applyReviewDecision,
  fetchLastReconciliationRun,
  fetchReviewCase,
  fetchReviewApplyStatus,
  fetchReviewQueue,
  fetchReconciliationScanStatus,
  runReconciliationScan,
  startReadyChanges,
  type ReconciliationRun,
  type ReconciliationScanStatus,
  type ReviewCase,
  type ReviewFinding,
  type ReviewQueuePage,
} from '../-review.api';
import { fetchFindingTypes } from '../../tools/-tools.api';
import { findingTypeLabel } from '../../tools/-tools.core';
import { ReconciliationEvidence } from './reconciliation-evidence';
import styles from './review-page.module.css';

const requireChoice = new Set([
  'reconciliation_extra',
  'reconciliation_duplicate',
  'reconciliation_intent',
  'reconciliation_release',
]);

const emptyPlan = {
  ready: 0,
  manual: 0,
  destructive: 0,
  blocked: 0,
  total: 0,
  status: 'needs_review' as const,
  blocker_reasons: [] as string[],
  can_apply_ready: false,
  can_organize: false,
};

export function ReviewPage() {
  const [queue, setQueue] = useState<ReviewQueuePage | null>(null);
  const [caseId, setCaseId] = useState(
    () => new URLSearchParams(window.location.search).get('case') || '',
  );
  const [reviewCase, setReviewCase] = useState<ReviewCase | null>(null);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [catalog, setCatalog] = useState<Record<string, FindingTypeInfo>>({});
  const [busy, setBusy] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [applyProgress, setApplyProgress] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [scanStatus, setScanStatus] = useState<ReconciliationScanStatus | null>(null);
  const [lastRun, setLastRun] = useState<ReconciliationRun | null>(null);
  const wasScanning = useRef(false);

  useEffect(() => {
    let live = true;
    void fetchLastReconciliationRun()
      .then((run) => {
        if (live) setLastRun(run);
      })
      .catch(() => undefined);
    const poll = async () => {
      try {
        const status = await fetchReconciliationScanStatus();
        if (!live) return;
        setScanStatus(status);
        const scanning = status.current_job?.job_id === 'library_reconciliation';
        if (wasScanning.current && !scanning) {
          void fetchLastReconciliationRun()
            .then((run) => {
              if (live) setLastRun(run);
            })
            .catch(() => undefined);
        }
        wasScanning.current = scanning;
      } catch {
        // Status is supporting context; a temporary poll failure should not
        // hide the review queue or block a decision.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 15000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (searchInput !== query) {
        setPage(0);
        setCaseId('');
        setQuery(searchInput);
      }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput, query]);

  const refreshQueue = useCallback(async () => {
    const result = await fetchReviewQueue(page, query);
    setQueue(result);
    return result;
  }, [page, query]);

  useEffect(() => {
    let live = true;
    void fetchReviewQueue(page, query)
      .then((result) => {
        if (!live) return;
        setQueue(result);
        if (!caseId && result.items[0]) setCaseId(result.items[0].case_id);
      })
      .catch((cause: unknown) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
    // caseId is deliberately excluded: changing the selected case does not rescan the queue.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, query]);

  useEffect(() => {
    void fetchFindingTypes()
      .then((types) => {
        setCatalog(Object.fromEntries(types.map((type) => [type.type, type])));
      })
      .catch(() => setCatalog({}));
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (caseId) url.searchParams.set('case', caseId);
    else url.searchParams.delete('case');
    window.history.replaceState(null, '', url);
    if (!caseId) {
      setReviewCase(null);
      return;
    }
    let live = true;
    setReviewCase(null);
    setError('');
    void fetchReviewCase(caseId)
      .then((result) => {
        if (!live) return;
        setReviewCase(result);
        setActiveId((current) =>
          result.items.some((f) => f.id === current) ? current : (result.items[0]?.id ?? null),
        );
      })
      .catch((cause: unknown) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
  }, [caseId]);

  const active = useMemo(
    () => reviewCase?.items.find((finding) => finding.id === activeId) || reviewCase?.items[0],
    [reviewCase, activeId],
  );
  const needsReviewItems =
    reviewCase?.items.filter((finding) => finding.review_tier !== 'ready') || [];
  const readyItems = reviewCase?.items.filter((finding) => finding.review_tier === 'ready') || [];
  const itemIndex =
    active && reviewCase ? reviewCase.items.findIndex((f) => f.id === active.id) : -1;
  const caseIndex = queue?.items.findIndex((item) => item.case_id === caseId) ?? -1;
  const pageCount = queue ? Math.max(1, Math.ceil(queue.total / queue.limit)) : 1;
  const albumNumber = queue && caseIndex >= 0 ? page * queue.limit + caseIndex + 1 : null;
  const typeInfo = active ? catalog[active.finding_type] : undefined;
  const canApply = Boolean(typeInfo?.fixable) && active?.finding_type !== 'suspect_album_tag';
  const duplicateChoice = active?.finding_type === 'reconciliation_duplicate';
  const suggested = active?.review_tier === 'ready';
  const plan = reviewCase?.plan || emptyPlan;

  const selectCase = (next: string) => {
    setNotice('');
    setCaseId(next);
  };

  const refresh = async () => {
    setError('');
    try {
      const updatedQueue = await refreshQueue();
      if (caseId) {
        const updated = await fetchReviewCase(caseId).catch(() => null);
        setReviewCase(updated);
        if (!updated) setCaseId(updatedQueue.items[0]?.case_id || '');
      }
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  const runScan = async () => {
    setScanBusy(true);
    setError('');
    try {
      await runReconciliationScan();
      setNotice('Library reconciliation was queued. Refresh the queue after the scan finishes.');
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setScanBusy(false);
    }
  };

  const applyReady = async (scopeCaseId?: string, organize = false) => {
    const count = scopeCaseId ? plan.ready : 0;
    const confirmed = window.showConfirmDialog
      ? await window.showConfirmDialog({
          title: organize ? 'Apply and organize album' : 'Apply ready changes',
          message: organize
            ? `Apply ${count} supported metadata changes, then organize this album folder?`
            : 'Apply every metadata change in the queue that passed its album safety checks?',
          confirmText: organize ? 'Apply and organize' : 'Apply ready changes',
        })
      : window.confirm(
          scopeCaseId ? `Apply ${count} ready metadata changes?` : 'Apply all ready changes?',
        );
    if (!confirmed) return;
    setApplyBusy(true);
    setError('');
    setNotice('');
    try {
      const started = await startReadyChanges(scopeCaseId, organize);
      setApplyProgress(`Applying 0 of ${started.total || count} ready changes…`);
      const poll = async () => {
        const status = await fetchReviewApplyStatus();
        if (status.running) {
          setApplyProgress(
            `Applying ${status.done || 0} of ${status.total || started.total || count} ready changes…`,
          );
          window.setTimeout(() => void poll(), 750);
          return;
        }
        setApplyBusy(false);
        setApplyProgress('');
        if (status.error) {
          setError(status.error);
        } else if (status.failed) {
          setError(`${status.failed} ready change${status.failed === 1 ? '' : 's'} failed.`);
        } else {
          const applied = status.fixed || started.total || count;
          setNotice(
            organize
              ? `${applied} change${applied === 1 ? '' : 's'} applied; folder organization was queued.`
              : `${applied} ready change${applied === 1 ? '' : 's'} applied.`,
          );
        }
        await refresh();
      };
      window.setTimeout(() => void poll(), 500);
    } catch (cause) {
      setApplyBusy(false);
      setApplyProgress('');
      setError((cause as Error).message);
    }
  };

  const act = async (finding: ReviewFinding, action: 'fix' | 'dismiss', override?: string) => {
    let fixAction: string | null = override || null;
    if (action === 'fix' && finding.finding_type === 'reconciliation_extra') {
      fixAction = 'quarantine';
    }
    if (action === 'fix' && requireChoice.has(finding.finding_type) && !fixAction) return;
    if (
      action === 'fix' &&
      (typeInfo?.destructive || finding.finding_type === 'reconciliation_extra')
    ) {
      const confirmed = window.showConfirmDialog
        ? await window.showConfirmDialog({
            title: 'Apply repair',
            message: `Apply this action to ${finding.title}? Review the evidence and file path first.`,
            confirmText: 'Apply',
            destructive: true,
          })
        : window.confirm(`Apply this action to ${finding.title}?`);
      if (!confirmed) return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await applyReviewDecision(caseId, finding, action, fixAction);
      if (!result.success) throw new Error(result.error || 'The action did not complete');
      setNotice(result.message || (action === 'dismiss' ? 'Accepted as-is' : 'Repair applied'));
      const [updatedCase, updatedQueue] = await Promise.all([
        fetchReviewCase(caseId).catch(() => null),
        refreshQueue(),
      ]);
      if (updatedCase) {
        setReviewCase(updatedCase);
        const remaining = updatedCase.items;
        setActiveId(remaining[Math.min(itemIndex, remaining.length - 1)]?.id ?? null);
      } else {
        setReviewCase(null);
        const next = updatedQueue.items.find((item) => item.case_id !== caseId);
        setCaseId(next?.case_id || '');
        if (!next && updatedQueue.total > 0 && page > 0) setPage(0);
      }
    } catch (cause) {
      setError((cause as Error).message);
      // The backend may have updated the evidence or error on this finding.
      void fetchReviewCase(caseId)
        .then(setReviewCase)
        .catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className={`page-shell ${styles.page}`}>
      <header className={styles.header}>
        <div>
          <a href="/tools" className={styles.back}>
            ← Library Maintenance
          </a>
          <h1>Review Queue</h1>
          <p>Showing library reconciliation findings, one album at a time.</p>
        </div>
        <div className={styles.headerTools}>
          <button
            type="button"
            className={styles.safeAction}
            disabled={applyBusy || !queue?.ready_total}
            onClick={() => void applyReady()}
          >
            {applyBusy
              ? applyProgress || 'Applying…'
              : `Apply all ready changes (${queue?.ready_total || 0})`}
          </button>
          <button
            type="button"
            disabled={scanBusy || scanStatus?.current_job?.job_id === 'library_reconciliation'}
            onClick={() => void runScan()}
          >
            {scanBusy
              ? 'Queuing…'
              : scanStatus?.current_job?.job_id === 'library_reconciliation'
                ? 'Scanning…'
                : 'Scan library'}
          </button>
          <button type="button" onClick={() => void refresh()}>
            Refresh queue
          </button>
          <div className={styles.headerCount}>
            {queue ? `${queue.total} cases need attention` : 'Loading cases…'}
          </div>
        </div>
      </header>

      {error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className={styles.notice} role="status">
          {notice}
        </div>
      )}
      {(scanStatus?.current_job?.job_id === 'library_reconciliation' || lastRun) && (
        <div className={styles.scanStatus} role="status">
          {scanStatus?.current_job?.job_id === 'library_reconciliation'
            ? `Reconciliation running: ${scanStatus.current_job.progress?.scanned ?? 0} of ${scanStatus.current_job.progress?.total ?? '?'} folders (${scanStatus.current_job.progress?.percent ?? 0}%).`
            : lastRun?.status === 'completed'
              ? `Last reconciliation completed: ${lastRun.items_scanned} files checked.`
              : `Last reconciliation ${lastRun?.status}: ${lastRun?.error_text || 'No reason recorded'}. ${lastRun?.items_scanned ?? 0} files checked; a rerun reuses cached fingerprints.`}
        </div>
      )}

      <div className={styles.workspace}>
        <aside className={styles.sidebar} aria-label="Albums needing review">
          <label htmlFor="review-search">Find album or track</label>
          <input
            id="review-search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search findings"
          />
          <div className={styles.caseList}>
            {queue?.items.map((item) => (
              <button
                key={item.case_id}
                type="button"
                className={item.case_id === caseId ? styles.selectedCase : styles.caseButton}
                onClick={() => selectCase(item.case_id)}
              >
                <strong>{item.album || 'Unassigned files'}</strong>
                <span>{item.artist || item.case_id.split(':')[0]}</span>
                <small>
                  {item.plan
                    ? `${item.plan.ready ? `${item.plan.ready} ready · ` : ''}${item.plan.manual + item.plan.destructive + item.plan.blocked} review`
                    : `${item.pending} decision${item.pending === 1 ? '' : 's'}`}
                  {item.error_count ? ` · ${item.error_count} failed` : ''}
                </small>
              </button>
            ))}
          </div>
          <div className={styles.pagination}>
            <button
              type="button"
              disabled={page === 0}
              onClick={() => {
                setCaseId('');
                setPage(page - 1);
              }}
            >
              Previous
            </button>
            <span>
              Page {page + 1} of {pageCount}
            </span>
            <select
              aria-label="Jump to page"
              value={page}
              onChange={(event) => {
                setCaseId('');
                setPage(Number(event.target.value));
              }}
            >
              {Array.from({ length: pageCount }, (_, index) => (
                <option key={index} value={index}>
                  Page {index + 1}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={!queue || (page + 1) * queue.limit >= queue.total}
              onClick={() => {
                setCaseId('');
                setPage(page + 1);
              }}
            >
              Next
            </button>
          </div>
        </aside>

        <section className={styles.review} aria-label="Current review case">
          {reviewCase && active ? (
            <>
              <div className={styles.albumHeader}>
                <div>
                  <div className={styles.eyebrow}>
                    Album {albumNumber ?? '—'} of {queue?.total ?? '—'}
                    {' · '}
                    {reviewCase.count} open decision{reviewCase.count === 1 ? '' : 's'}
                  </div>
                  <h2>{reviewCase.album || 'Unassigned files'}</h2>
                  <p>{reviewCase.artist || 'Artist not established'}</p>
                </div>
                <div className={styles.nav}>
                  <button
                    type="button"
                    disabled={caseIndex <= 0}
                    onClick={() => selectCase(queue!.items[caseIndex - 1].case_id)}
                  >
                    ← Album
                  </button>
                  <button
                    type="button"
                    disabled={!queue || caseIndex < 0 || caseIndex >= queue.items.length - 1}
                    onClick={() => selectCase(queue!.items[caseIndex + 1].case_id)}
                  >
                    Album →
                  </button>
                </div>
              </div>

              <section className={styles.albumPlan} aria-label="Album reconciliation plan">
                <div className={styles.planSummary}>
                  <div>
                    <div className={styles.eyebrow}>Album reconciliation</div>
                    <strong>
                      {plan.status === 'ready'
                        ? 'Ready to reconcile'
                        : `${plan.manual + plan.destructive + plan.blocked} item${plan.manual + plan.destructive + plan.blocked === 1 ? '' : 's'} need review`}
                    </strong>
                    <p>
                      {plan.blocker_reasons[0] ||
                        'All proposed changes are supported by the album and audio evidence.'}
                    </p>
                  </div>
                  <div className={styles.planBadges}>
                    {plan.ready > 0 && <span data-tone="good">✓ {plan.ready} ready</span>}
                    {plan.manual > 0 && <span data-tone="warning">! {plan.manual} review</span>}
                    {plan.destructive > 0 && (
                      <span data-tone="danger">× {plan.destructive} file action</span>
                    )}
                    {plan.blocked > 0 && <span data-tone="muted">○ {plan.blocked} blocked</span>}
                  </div>
                </div>
                {plan.ready > 0 && (
                  <div className={styles.planActions}>
                    <button
                      type="button"
                      className={styles.primary}
                      disabled={applyBusy}
                      onClick={() => void applyReady(reviewCase.case_id)}
                    >
                      Apply {plan.ready} ready change{plan.ready === 1 ? '' : 's'}
                    </button>
                    {plan.can_organize && reviewCase.album_id != null && (
                      <button
                        type="button"
                        disabled={applyBusy}
                        onClick={() => void applyReady(reviewCase.case_id, true)}
                      >
                        Apply and organize folder
                      </button>
                    )}
                    <details>
                      <summary>Review ready changes</summary>
                      <ul>
                        {reviewCase.items
                          .filter((item) => item.review_tier === 'ready')
                          .map((item) => (
                            <li key={item.id}>{item.title}</li>
                          ))}
                      </ul>
                    </details>
                  </div>
                )}
              </section>

              <div className={styles.decisionHeader}>
                <span>
                  {active.review_tier === 'ready' ? 'Ready change' : 'Needs review'} ·{' '}
                  {findingTypeLabel(active.finding_type)} · Item {itemIndex + 1} of{' '}
                  {reviewCase.count}
                </span>
                <span className={styles.severity}>{active.severity}</span>
              </div>
              <h3>{active.title}</h3>
              <p className={styles.description}>
                {active.description || 'Review the evidence below.'}
              </p>

              <section
                className={styles.suggestion}
                data-tone={suggested ? 'good' : 'warning'}
                aria-label="Suggested resolution"
              >
                <span className={styles.suggestionIcon} aria-hidden="true">
                  {suggested ? '✓' : '!'}
                </span>
                <div>
                  <div className={styles.eyebrow}>Suggested resolution</div>
                  <strong>
                    {duplicateChoice
                      ? 'Choose the copy to keep'
                      : suggested
                        ? typeInfo?.verb || 'Apply repair'
                        : canApply
                          ? 'Choose the correct action after reviewing the evidence'
                          : 'Review manually'}
                  </strong>
                  <p>
                    {duplicateChoice
                      ? 'Both files identify as the same release track. Playlist membership is shown with each copy.'
                      : suggested
                        ? 'Evidence supports a direct repair.'
                        : active.review_reason || 'The evidence still requires a decision.'}
                  </p>
                </div>
              </section>

              <ReconciliationEvidence
                finding={active}
                busy={busy}
                onPin={
                  active.finding_type === 'reconciliation_release'
                    ? (index) => void act(active, 'fix', String(index))
                    : undefined
                }
                onKeepCopy={
                  duplicateChoice ? (trackId) => void act(active, 'fix', trackId) : undefined
                }
              />

              <div className={styles.actions}>
                {active.finding_type === 'reconciliation_intent' ? (
                  <>
                    <button
                      type="button"
                      disabled={busy}
                      className={styles.primary}
                      onClick={() => void act(active, 'fix', 'complete')}
                    >
                      I want the complete album
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void act(active, 'fix', 'partial')}
                    >
                      Playlist tracks or partial album
                    </button>
                  </>
                ) : (
                  canApply &&
                  active.finding_type !== 'reconciliation_release' &&
                  active.finding_type !== 'reconciliation_duplicate' && (
                    <button
                      type="button"
                      disabled={busy}
                      className={styles.primary}
                      onClick={() => void act(active, 'fix')}
                    >
                      {busy
                        ? 'Applying…'
                        : suggested
                          ? typeInfo?.verb || 'Apply repair'
                          : active.finding_type === 'reconciliation_extra'
                            ? 'Quarantine file'
                            : 'Choose resolution'}
                    </button>
                  )
                )}
                <button type="button" disabled={busy} onClick={() => void act(active, 'dismiss')}>
                  Accept as-is
                </button>
                <button
                  type="button"
                  disabled={busy || itemIndex <= 0}
                  onClick={() => setActiveId(reviewCase.items[itemIndex - 1].id)}
                >
                  ← Previous decision
                </button>
                <button
                  type="button"
                  disabled={busy || itemIndex >= reviewCase.items.length - 1}
                  onClick={() => setActiveId(reviewCase.items[itemIndex + 1].id)}
                >
                  Next decision →
                </button>
              </div>
              <div className={styles.trackList} aria-label="Other decisions on this album">
                {needsReviewItems.length > 0 && (
                  <section>
                    <h4>Needs review</h4>
                    <div>
                      {needsReviewItems.map((finding) => (
                        <button
                          type="button"
                          key={finding.id}
                          onClick={() => setActiveId(finding.id)}
                          aria-current={finding.id === active.id ? 'step' : undefined}
                          data-tier={finding.review_tier || 'manual'}
                        >
                          <span aria-hidden="true">
                            {finding.review_tier === 'destructive' ? '×' : '!'}
                          </span>{' '}
                          {findingTypeLabel(finding.finding_type)}: {finding.title}
                        </button>
                      ))}
                    </div>
                  </section>
                )}
                {readyItems.length > 0 && (
                  <section>
                    <h4>Ready changes</h4>
                    <div>
                      {readyItems.map((finding) => (
                        <button
                          type="button"
                          key={finding.id}
                          onClick={() => setActiveId(finding.id)}
                          aria-current={finding.id === active.id ? 'step' : undefined}
                          data-tier="ready"
                        >
                          <span aria-hidden="true">✓</span> {findingTypeLabel(finding.finding_type)}
                          : {finding.title}
                        </button>
                      ))}
                    </div>
                  </section>
                )}
              </div>
            </>
          ) : queue && queue.total === 0 ? (
            <div className={styles.empty}>
              No pending library reconciliation findings. Run a scan to check the library.
            </div>
          ) : (
            <div className={styles.empty}>Select an album to review.</div>
          )}
        </section>
      </div>
    </main>
  );
}
