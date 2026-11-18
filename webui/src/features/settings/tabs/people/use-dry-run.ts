/**
 * The save bar's impact line: a moment after the last change, the same
 * request as Save with ?dry_run=1, so the bar says who it reaches before
 * anything is stored. An older answer never replaces a newer one.
 */

import { useEffect, useRef, useState } from 'react';

import type { Result } from './api';
import type { Impact } from './types';

export const DRY_RUN_DELAY_MS = 300;

export function useDryRun<B>(
  body: B | null,
  run: (body: B) => Promise<Result<{ impact: Impact | null }>>,
) {
  const [impact, setImpact] = useState<Impact | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const runRef = useRef(run);
  runRef.current = run;
  const key = body == null ? '' : JSON.stringify(body);

  useEffect(() => {
    const mine = ++seq.current;
    if (!key) {
      setImpact(null);
      setChecking(false);
      setError(null);
      return;
    }
    setChecking(true);
    setError(null);
    const timer = setTimeout(() => {
      void runRef.current(JSON.parse(key) as B).then((r) => {
        if (mine !== seq.current) return;
        setChecking(false);
        if (r.ok) setImpact(r.data.impact ?? { reaches: 0, changes: [], not_following: [] });
        else {
          setImpact(null);
          setError(r.message);
        }
      });
    }, DRY_RUN_DELAY_MS);
    return () => clearTimeout(timer);
  }, [key]);

  return { impact, checking, error };
}
