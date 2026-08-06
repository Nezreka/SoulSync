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
const loadedSections = new Map<string, unknown[]>();

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
  // Hydrated from the cache on the FIRST render, not in an effect: a section
  // that was loaded earlier this session must come back with its ITEMS, not
  // just with the flag saying it once had some. The vanilla gets this free by
  // hiding the rendered DOM rather than removing it.
  const cached = loadedSections.get(sectionKey) as T[] | undefined;
  const [status, setStatus] = useState<BeatportSectionStatus>(cached ? 'ready' : 'idle');
  const [items, setItems] = useState<T[]>(cached ?? []);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const loadRef = useRef(load);
  loadRef.current = load;

  /**
   * Destructured on purpose. Depending on `config` itself would make an inline
   * `config={{…}}` re-run the effect every render — and since the effect calls
   * setStatus, that is an infinite loop rather than a slow one. The two fields
   * are primitives, so identity cannot bite.
   */
  const { marksLoadedBeforeFetch, onFailure } = config;

  const reload = useCallback(() => {
    loadedSections.delete(sectionKey);
    setReloadToken((token) => token + 1);
  }, [sectionKey]);

  useEffect(() => {
    // Already loaded this session: the vanilla's guard, honoured so a tab
    // switch does not re-scrape Beatport.
    const alreadyLoaded = loadedSections.get(sectionKey);
    if (alreadyLoaded) {
      setItems(alreadyLoaded as T[]);
      setStatus('ready');
      return;
    }

    // 31-34: the hero claims the slot before it fetches, so a failure is never
    // retried for the rest of the session.
    // The hero claims the slot with an EMPTY list, which is exactly right: it
    // has nothing to show and will keep its static placeholders.
    if (marksLoadedBeforeFetch) loadedSections.set(sectionKey, []);

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
          loadedSections.set(sectionKey, result);
          return;
        }
        // An empty list is a FAILURE here, matching every vanilla loader:
        // `data.success && data.tracks && data.tracks.length > 0`.
        setStatus('failed');
        if (onFailure === 'error-block') setErrorMessage(defaultErrorMessage);
      } catch (error) {
        // 58 and its four twins: leaving the page mid-fetch is not an error.
        if (controller.signal.aborted) return;
        if (error instanceof Error && error.name === 'AbortError') return;
        setStatus('failed');
        if (onFailure === 'error-block') {
          setErrorMessage(error instanceof Error ? error.message : defaultErrorMessage);
        }
      }
    })();

    return () => controller.abort();
  }, [sectionKey, marksLoadedBeforeFetch, onFailure, defaultErrorMessage, reloadToken]);

  return { status, items, errorMessage, reload };
}
