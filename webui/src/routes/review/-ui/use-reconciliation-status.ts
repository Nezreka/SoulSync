import { useEffect, useRef, useState } from 'react';

import {
  fetchLastReconciliationRun,
  fetchReconciliationScanStatus,
  runReconciliationScan,
  type ReconciliationRun,
  type ReconciliationScanStatus,
} from '../-review.api';
import { usePolledStatus } from '../../tools/-tools.polling';

type Inputs = { setError: (value: string, scope?: string | null) => void };
export function useReconciliationStatus({ setError }: Inputs) {
  const [scanBusy, setScanBusy] = useState(false);

  const [globalNotice, setGlobalNotice] = useState('');

  const [scanStatus, setScanStatus] = useState<ReconciliationScanStatus | null>(null);

  const [lastRun, setLastRun] = useState<ReconciliationRun | null>(null);

  const wasScanning = useRef(false);

  const scanHistoryLive = useRef(true);

  useEffect(() => {
    scanHistoryLive.current = true;
    void fetchLastReconciliationRun()
      .then((run) => {
        if (scanHistoryLive.current) {
          setLastRun(run);
        }
      })
      .catch(() => undefined);
    return () => {
      scanHistoryLive.current = false;
    };
  }, []);

  usePolledStatus({
    fetcher: () => fetchReconciliationScanStatus().catch(() => null),
    isRunning: (status) => status.current_job?.job_id === 'library_reconciliation',
    intervalMs: 15000,
    pollWhileIdle: true,
    onState: (status) => {
      setScanStatus(status);
      const scanning = status.current_job?.job_id === 'library_reconciliation';
      if (scanning)
        setGlobalNotice((value) =>
          value.startsWith('Library reconciliation queued.') ? '' : value,
        );
      if (wasScanning.current && !scanning) {
        void fetchLastReconciliationRun()
          .then((run) => {
            if (scanHistoryLive.current) {
              setLastRun(run);
              if (run?.status === 'completed') setGlobalNotice('Library reconciliation complete.');
            }
          })
          .catch(() => undefined);
      }
      wasScanning.current = scanning;
    },
  });

  useEffect(() => {
    if (globalNotice !== 'Library reconciliation complete.') return;
    const timer = window.setTimeout(() => setGlobalNotice(''), 6000);
    return () => window.clearTimeout(timer);
  }, [globalNotice]);

  const runScan = async () => {
    setScanBusy(true);
    setError('');
    try {
      await runReconciliationScan();
      setGlobalNotice('Library reconciliation queued. Progress will appear here.');
    } catch (cause) {
      setError((cause as Error).message, null);
    } finally {
      setScanBusy(false);
    }
  };
  return {
    scanBusy,
    globalNotice,
    setGlobalNotice,
    scanStatus,
    lastRun,
    runScan,
  };
}
