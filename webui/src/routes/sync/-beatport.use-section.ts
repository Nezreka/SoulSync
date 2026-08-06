/**
 * The load lifecycle every Beatport slider section shares.
 *
 * The three failure behaviours and the two "when is it marked loaded" rules all
 * come from BEATPORT_SLIDERS, so a section cannot accidentally inherit its
 * neighbour's — which is exactly what happened in the vanilla, five times over.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { BeatportSliderConfig } from './-beatport.core';

/**
 * Session-scoped, mirroring the vanilla's `dataset.initialized` /
 * `isInitialized`.
 *
 * WHY THIS EXISTS, since a module-level cache in React deserves justification:
 * the vanilla holds its loaded state in the DOM, which survives tab switches
 * because the markup is only hidden, never removed. A React section that
 * unmounts on tab change would re-fetch on every visit — and these endpoints
 * SCRAPE BEATPORT, slowly and rate-limited. Re-fetching per visit is not a
 * neutral refactor; it is a behaviour change with an external cost.
 *
 * Only successful loads are recorded, except for the hero, which the vanilla
 * marks before it even fetches (see marksLoadedBeforeFetch).
 */
const loadedSections = new Set<string>();

/** Test seam. Nothing in production clears this — a reload is the reset. */
export function resetBeatportSectionCache(): void {
  loadedSections.clear();
}

export function hasLoadedBeatportSection(key: string): boolean {
  return loadedSections.has(key);
}

export type BeatportSectionStatus = 'idle' | 'loading' | 'ready' | 'failed';

export interface BeatportSectionState<T> {
  status: BeatportSectionStatus;
  items: T[];
  /** Only ever set when `config.onFailure === 'error-block'`. */
  errorMessage: string | null;
  reload: () => void;
}

export interface UseBeatportSectionOptions<T> {
  /** Cache key. Distinct per section, and per genre where it varies. */
  sectionKey: string;
  config: BeatportSliderConfig;
  /** Resolves the items, or throws / returns null to mean failure. */
  load: (signal: AbortSignal) => Promise<T[] | null>;
  /** The 'error-block' copy, e.g. 'No releases available'. */
  defaultErrorMessage?: string;
}

export function useBeatportSection<T>({
  sectionKey,
  config,
  load,
  defaultErrorMessage = 'Failed to load',
}: UseBeatportSectionOptions<T>): BeatportSectionState<T> {
  const [status, setStatus] = useState<BeatportSectionStatus>('idle');
  const [items, setItems] = useState<T[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(() => {
    loadedSections.delete(sectionKey);
    setReloadToken((token) => token + 1);
  }, [sectionKey]);

  useEffect(() => {
    // Already loaded this session: the vanilla's guard, honoured so a tab
    // switch does not re-scrape Beatport.
    if (loadedSections.has(sectionKey)) {
      setStatus((current) => (current === 'idle' ? 'ready' : current));
      return;
    }

    // 31-34: the hero claims the slot before it fetches, so a failure is never
    // retried for the rest of the session.
    if (config.marksLoadedBeforeFetch) loadedSections.add(sectionKey);

    const controller = new AbortController();
    setStatus('loading');
    setErrorMessage(null);

    void (async () => {
      try {
        const result = await loadRef.current(controller.signal);
        if (controller.signal.aborted) return;
        if (result && result.length > 0) {
          setItems(result);
          setStatus('ready');
          // Unconditional: a pre-marked section is already present, and Set
          // membership makes the second add a no-op.
          loadedSections.add(sectionKey);
          return;
        }
        // An empty list is a FAILURE here, matching every vanilla loader:
        // `data.success && data.tracks && data.tracks.length > 0`.
        setStatus('failed');
        if (config.onFailure === 'error-block') setErrorMessage(defaultErrorMessage);
      } catch (error) {
        // 58 and its four twins: leaving the page mid-fetch is not an error.
        if (controller.signal.aborted) return;
        if (error instanceof Error && error.name === 'AbortError') return;
        setStatus('failed');
        if (config.onFailure === 'error-block') {
          setErrorMessage(error instanceof Error ? error.message : defaultErrorMessage);
        }
      }
    })();

    return () => controller.abort();
  }, [sectionKey, config, defaultErrorMessage, reloadToken]);

  return { status, items, errorMessage, reload };
}
