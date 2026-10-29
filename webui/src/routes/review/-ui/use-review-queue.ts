import type { Dispatch, SetStateAction, RefObject } from 'react';

import { useCallback, useEffect, useState } from 'react';

import {
  fetchReviewQueue,
  fetchReviewQueueModeTotals,
  type ReviewQueuePage,
  type ReviewQueueModeTotals,
} from '../-review.api';
import { QueueMode, queueModes } from './review-labels';
import { useRequestScope } from './use-request-scope';

type Inputs = {
  initialMode: string | null;
  selectedCaseRef: RefObject<string>;
  setCaseId: Dispatch<SetStateAction<string>>;
  setError: (value: string, scope?: string | null) => void;
};
export function useReviewQueue({ initialMode, selectedCaseRef, setCaseId, setError }: Inputs) {
  const [queue, setQueue] = useState<ReviewQueuePage | null>(null);

  const [queueTotals, setQueueTotals] = useState<ReviewQueueModeTotals | null>(null);

  const [page, setPage] = useState(0);

  const [query, setQuery] = useState('');

  const [category, setCategory] = useState('');

  const [queueMode, setQueueMode] = useState<QueueMode>(
    initialMode && queueModes.some((mode) => mode.value === initialMode)
      ? (initialMode as QueueMode)
      : 'review',
  );

  const [searchInput, setSearchInput] = useState('');

  const risk = queueModes.find((mode) => mode.value === queueMode)?.risk || '';

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

  const [queueLoading, setQueueLoading] = useState(false);

  const [queueError, setQueueError] = useState('');

  const queueScope = JSON.stringify([page, query, category, risk]);

  const requests = useRequestScope(queueScope);

  const refreshQueue = useCallback(async () => {
    const request = requests.start();
    setQueueLoading(true);
    setQueueError('');
    const current = request.isCurrent;
    void fetchReviewQueueModeTotals(query, category, request.signal)
      .then((totals) => {
        if (current()) setQueueTotals(totals);
      })
      .catch(() => undefined);
    try {
      const result = await fetchReviewQueue(page, query, category, risk, 30, request.signal);
      if (!current()) return null;
      setQueue(result);
      if (result.page !== page) setPage(result.page);
      return result;
    } catch (cause) {
      if (!current() || request.signal.aborted) return null;
      setQueueError((cause as Error).message);
      return null;
    } finally {
      if (current()) setQueueLoading(false);
    }
  }, [page, query, category, risk, queueScope]);

  useEffect(() => {
    let live = true;
    setQueue(null);
    void refreshQueue()
      .then((result) => {
        if (live && result && !selectedCaseRef.current && result.items[0])
          setCaseId(result.items[0].case_id);
      })
      .catch((cause: unknown) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
      requests.cancel();
    };
  }, [refreshQueue, setError]);
  return {
    queue,
    queueTotals,
    page,
    setPage,
    query,
    setQuery,
    category,
    setCategory,
    queueMode,
    setQueueMode,
    searchInput,
    setSearchInput,
    queueLoading,
    queueError,
    refreshQueue,
  };
}
