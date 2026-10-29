import type { Dispatch, SetStateAction, RefObject } from 'react';

import { useEffect, useRef, useState } from 'react';

import { fetchReviewApplyStatus } from '../-review.api';

type Inputs = {
  setError: (value: string, scope?: string | null) => void;
  setNotice: Dispatch<SetStateAction<string>>;
  setGlobalNotice: Dispatch<SetStateAction<string>>;
  selectedCaseRef: RefObject<string>;
  refreshRef: RefObject<() => Promise<boolean>>;
};
export function useReviewOperation({
  setError,
  setNotice,
  setGlobalNotice,
  selectedCaseRef,
  refreshRef,
}: Inputs) {
  const [applyBusy, setApplyBusy] = useState(false);

  const [operationCases, setOperationCases] = useState<string[]>([]);

  const [applyProgress, setApplyProgress] = useState('');

  const [operationCase, setOperationCase] = useState<string | null>(
    window.sessionStorage.getItem('review-operation-case'),
  );

  const operationRef = useRef<string | null>(window.sessionStorage.getItem('review-operation'));

  const rememberOperation = (id?: string, albumCase?: string) => {
    setOperationCases(albumCase ? [albumCase] : []);
    setOperationCase(albumCase || null);
    if (albumCase) window.sessionStorage.setItem('review-operation-case', albumCase);
    else window.sessionStorage.removeItem('review-operation-case');
    operationRef.current = id || 'current';
    window.sessionStorage.setItem('review-operation', operationRef.current);
    setApplyBusy(true);
  };

  useEffect(() => {
    let live = true;
    let pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const requestedOperation = operationRef.current;
        const status = await fetchReviewApplyStatus(
          requestedOperation && requestedOperation !== 'current' ? requestedOperation : undefined,
        );
        if (!live || requestedOperation !== operationRef.current) return;
        if (!operationRef.current && status.running && status.review_approved) {
          operationRef.current = status.operation_id || 'current';
          window.sessionStorage.setItem('review-operation', operationRef.current);
        }
        if (!operationRef.current) return;
        setOperationCases(status.review_case_ids || []);
        const reportCase =
          status.review_scope === 'global'
            ? null
            : status.review_scope === 'album'
              ? status.review_case_ids?.[0] || null
              : operationCase;
        if (status.review_scope)
          setOperationCase(
            status.review_scope === 'album' ? status.review_case_ids?.[0] || null : null,
          );
        if (!status.operation_id && operationRef.current !== 'current') {
          setError(
            'The server no longer has this operation report. Review the refreshed album before retrying.',
            reportCase,
          );
        } else if (
          status.operation_id &&
          operationRef.current !== 'current' &&
          status.operation_id !== operationRef.current
        ) {
          setError(
            'Another operation has replaced this progress report. Refreshing the current album.',
            reportCase,
          );
        } else if (status.running) {
          setApplyBusy(true);
          setApplyProgress(
            status.phase === 'requests'
              ? 'Adding requested tracks to the wishlist…'
              : status.phase === 'file_actions'
                ? 'Saving replacement handoffs and applying selected file actions…'
                : status.phase === 'waiting_for_scan'
                  ? 'Waiting for reconciliation to finish before applying…'
                  : status.phase === 'queueing_organization'
                    ? 'Queueing folder organization…'
                    : status.phase === 'organizing'
                      ? 'Organizing album files…'
                      : status.phase === 'reanalyzing' || status.phase === 'queueing_reanalysis'
                        ? 'Checking the completed album and refreshing its plan…'
                        : `Processing ${status.done || 0} of ${status.total || 0} album actions…`,
          );
          return;
        } else if (
          status.error ||
          status.failed ||
          status.blocked ||
          status.not_attempted ||
          status.stopped
        ) {
          setError(
            `${status.fixed || 0} actions applied${status.blocked ? ` · ${status.blocked} blocked` : ''}${status.failed ? ` · ${status.failed} failed` : ''}${status.not_attempted ? ` · ${status.not_attempted} not attempted` : ''}. ${status.error || status.errors?.[0]?.error || 'The operation stopped before completing.'}`,
            reportCase,
          );
        } else {
          const message = `${status.fixed || 0} album action${status.fixed === 1 ? '' : 's'} completed.${status.organize_queued ? ' Album files organized.' : ''}${status.reconciliation_queued ? ' Album plan refreshed.' : ''}`;
          const targetCase =
            status.review_scope === 'album' ? status.review_case_ids?.[0] : operationCase;
          if (status.review_scope === 'global' || !targetCase) setGlobalNotice(message);
          else if (selectedCaseRef.current === targetCase) setNotice(message);
        }
        operationRef.current = null;
        window.sessionStorage.removeItem('review-operation');
        window.sessionStorage.removeItem('review-operation-case');
        setApplyBusy(false);
        setApplyProgress('');
        await refreshRef.current();
      } catch {
        if (live && operationRef.current)
          setApplyProgress(
            'Progress temporarily unavailable. The server operation may still be running; reconnecting…',
          );
      } finally {
        pending = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), applyBusy ? 1000 : 10000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [applyBusy, operationCase]);
  return {
    applyBusy,
    setApplyBusy,
    operationCases,
    applyProgress,
    setApplyProgress,
    operationCase,
    rememberOperation,
  };
}
