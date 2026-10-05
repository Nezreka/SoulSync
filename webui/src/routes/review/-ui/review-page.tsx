import { useCallback, useEffect, useMemo, useState } from 'react';

import type { FindingTypeInfo } from '../../tools/-tools.groups';

import {
  applyReviewDecision,
  fetchReviewCase,
  fetchReviewQueue,
  runReconciliationScan,
  type ReviewCase,
  type ReviewFinding,
  type ReviewQueuePage,
} from '../-review.api';
import { fetchFindingTypes } from '../../tools/-tools.api';
import { findingTypeLabel } from '../../tools/-tools.core';
import { FindingDetail } from '../../tools/-ui/finding-detail';
import { useFindingPrompts } from '../../tools/-ui/finding-prompts';
import styles from './review-page.module.css';

const requireChoice = new Set([
  'orphan_file',
  'dead_file',
  'acoustid_mismatch',
  'quality_upgrade',
  'missing_discography_track',
  'duplicate_tracks',
  'suspect_album_tag',
  'reconciliation_extra',
  'reconciliation_intent',
  'reconciliation_release',
]);

function value(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.map(valueAsText).join(', ') || '—';
  return valueAsText(value);
}

function valueAsText(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return JSON.stringify(value);
}

function evidence(finding: ReviewFinding) {
  const d = finding.details || {};
  const audio = (
    d.acoustid && typeof d.acoustid === 'object' && !Array.isArray(d.acoustid) ? d.acoustid : {}
  ) as Record<string, unknown>;
  const current = (
    d.current_tags && typeof d.current_tags === 'object' && !Array.isArray(d.current_tags)
      ? d.current_tags
      : {}
  ) as Record<string, unknown>;
  return [
    [
      'Audio / AcoustID',
      audio.status || d.acoustid_status || d.acoustid_title || d.matched_title || d.detected_title,
    ],
    [
      'AcoustID candidates',
      audio.recordings || d.candidates || d.acoustid_recordings || d.recording_mbids,
    ],
    ['Embedded tags', current.title || d.current_title || d.track_title || d.file_title || d.title],
    ['Filename', finding.file_path?.split('/').pop()],
    ['Folder', d.album_folder || finding.file_path?.split('/').slice(0, -1).join('/')],
    ['Expected track', d.expected_title || d.canonical_title || d.source_title],
    ['Expected album', d.album_title || d.album || d.album_name],
    ['Duration', d.duration_ms || d.file_duration_ms || d.duration],
    ['Recording MBID', d.musicbrainz_recording_id || d.recording_mbid],
    ['Proposed changes', d.changes],
    ['Why this slot', d.match_reasons],
  ] as const;
}

export function ReviewPage() {
  const [queue, setQueue] = useState<ReviewQueuePage | null>(null);
  const [caseId, setCaseId] = useState(
    () => new URLSearchParams(window.location.search).get('case') || '',
  );
  const [reviewCase, setReviewCase] = useState<ReviewCase | null>(null);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState('');
  const [catalog, setCatalog] = useState<Record<string, FindingTypeInfo>>({});
  const [busy, setBusy] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const prompts = useFindingPrompts();

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
  const itemIndex =
    active && reviewCase ? reviewCase.items.findIndex((f) => f.id === active.id) : -1;
  const caseIndex = queue?.items.findIndex((item) => item.case_id === caseId) ?? -1;
  const typeInfo = active ? catalog[active.finding_type] : undefined;
  const canApply = Boolean(typeInfo?.fixable) && active?.finding_type !== 'suspect_album_tag';
  const suggested = canApply && active && !requireChoice.has(active.finding_type);

  const selectCase = (next: string) => {
    setNotice('');
    setCaseId(next);
  };

  const refresh = async () => {
    setError('');
    try {
      await refreshQueue();
      if (caseId) {
        const updated = await fetchReviewCase(caseId).catch(() => null);
        setReviewCase(updated);
        if (!updated) setCaseId('');
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

  const act = async (finding: ReviewFinding, action: 'fix' | 'dismiss', override?: string) => {
    let fixAction: string | null = override || null;
    if (action === 'fix' && !override) {
      switch (finding.finding_type) {
        case 'orphan_file':
          fixAction = await prompts.promptOrphan();
          break;
        case 'dead_file':
          fixAction = await prompts.promptDeadFile();
          break;
        case 'acoustid_mismatch': {
          const d = finding.details || {};
          fixAction = await prompts.promptAcoustid(
            d.ambiguous && Array.isArray(d.candidates) ? (d.candidates as string[]) : undefined,
          );
          break;
        }
        case 'quality_upgrade':
          fixAction = await prompts.promptQuality();
          break;
        case 'missing_discography_track':
          fixAction = await prompts.promptBackfill(1);
          break;
        case 'reconciliation_extra':
          fixAction = 'quarantine';
          break;
      }
      if (requireChoice.has(finding.finding_type) && !fixAction) return;
      if (fixAction === 'ignore' || fixAction === 'dismiss') action = 'dismiss';
    }
    if (action === 'fix' && typeInfo?.destructive) {
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
          <p>Review one decision at a time with its album and file evidence.</p>
        </div>
        <div className={styles.headerTools}>
          <button type="button" disabled={scanBusy} onClick={() => void runScan()}>
            {scanBusy ? 'Queuing…' : 'Scan library'}
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

      <div className={styles.workspace}>
        <aside className={styles.sidebar} aria-label="Albums needing review">
          <label htmlFor="review-search">Find album or track</label>
          <input
            id="review-search"
            value={query}
            onChange={(event) => {
              setPage(0);
              setQuery(event.target.value);
            }}
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
                  {item.pending} decision{item.pending === 1 ? '' : 's'}
                  {item.error_count ? ` · ${item.error_count} failed` : ''}
                </small>
              </button>
            ))}
          </div>
          <div className={styles.pagination}>
            <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <span>Page {page + 1}</span>
            <button
              type="button"
              disabled={!queue || (page + 1) * queue.limit >= queue.total}
              onClick={() => setPage(page + 1)}
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
                    Album {caseIndex >= 0 ? caseIndex + 1 : '—'}
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

              <div className={styles.decisionHeader}>
                <span>
                  {findingTypeLabel(active.finding_type)} · Decision {itemIndex + 1} of{' '}
                  {reviewCase.count}
                </span>
                <span className={styles.severity}>{active.severity}</span>
              </div>
              <h3>{active.title}</h3>
              <p className={styles.description}>
                {active.description || 'Review the evidence below.'}
              </p>

              <section className={styles.suggestion} aria-label="Suggested resolution">
                <div className={styles.eyebrow}>Suggested resolution</div>
                <strong>
                  {suggested
                    ? typeInfo?.verb || 'Apply repair'
                    : canApply
                      ? 'Choose the correct action after reviewing the evidence'
                      : 'Review manually'}
                </strong>
                <p>
                  {suggested
                    ? 'This maintenance job has a direct repair action. Check the evidence and apply it below.'
                    : 'The available evidence does not justify an automatic choice for this finding.'}
                </p>
              </section>

              <div className={styles.evidence}>
                <section>
                  <h4>Evidence</h4>
                  <dl>
                    {evidence(active)
                      .filter(([, v]) => v !== null && v !== undefined)
                      .map(([label, raw]) => (
                        <div key={label}>
                          <dt>{label}</dt>
                          <dd>{value(raw)}</dd>
                        </div>
                      ))}
                  </dl>
                </section>
                <section>
                  <h4>Finding details</h4>
                  <FindingDetail
                    finding={active}
                    onKeepDuplicate={(_id, trackId) => {
                      void act(active, 'fix', trackId);
                    }}
                    onApplyCoverArt={(_id, target) => {
                      void act(active, 'fix', target);
                    }}
                  />
                  {active.last_error && (
                    <p className={styles.error}>Last attempt: {active.last_error}</p>
                  )}
                </section>
              </div>

              <div className={styles.actions}>
                {active.finding_type === 'reconciliation_release' ? (
                  Array.isArray(active.details?.candidates) ? (
                    active.details.candidates.map((choice, index) => {
                      const release = choice as {
                        source?: string;
                        release_id?: string;
                        matched?: number;
                      };
                      return (
                        <button
                          type="button"
                          key={`${release.source}-${release.release_id}`}
                          disabled={busy}
                          onClick={() => void act(active, 'fix', String(index))}
                        >
                          Pin {release.source} {release.release_id} ({release.matched ?? '?'}{' '}
                          matched)
                        </button>
                      );
                    })
                  ) : null
                ) : active.finding_type === 'reconciliation_intent' ? (
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
                  active.finding_type !== 'duplicate_tracks' && (
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
                {reviewCase.items.map((finding) => (
                  <button
                    type="button"
                    key={finding.id}
                    onClick={() => setActiveId(finding.id)}
                    aria-current={finding.id === active.id ? 'step' : undefined}
                  >
                    {findingTypeLabel(finding.finding_type)}: {finding.title}
                  </button>
                ))}
              </div>
            </>
          ) : queue && queue.total === 0 ? (
            <div className={styles.empty}>
              No pending findings. Run a maintenance scan to check the library.
            </div>
          ) : (
            <div className={styles.empty}>Select an album to review.</div>
          )}
        </section>
      </div>
      {prompts.promptNode}
    </main>
  );
}
