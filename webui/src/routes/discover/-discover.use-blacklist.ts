import { useCallback, useEffect, useRef, useState } from 'react';

import type { BlacklistEntry, BlacklistSearchArtist } from './-discover.blacklist';

import {
  BLACKLIST_BLOCK_ERROR,
  BLACKLIST_SEARCH_DEBOUNCE_MS,
  BLACKLIST_SEARCH_URL,
  BLACKLIST_URL,
  blacklistBlockBody,
  blacklistBlockEffects,
  blacklistDeleteUrl,
  blacklistEntries,
  blacklistQueryTooShort,
  blacklistSearchBody,
  blacklistSearchResults,
  blacklistUnblockedToast,
} from './-discover.blacklist';

/**
 * The Blocked Artists modal's controller.
 *
 * Transcribed from `openDiscoveryBlacklistModal` + `_dblSearch` +
 * `_dblBlockFromSearch` + `_dblLoadList` + `unblockDiscoveryArtist`
 * (discover.js 5058-5186), over the pure module. The behaviours that matter:
 *
 * - the min-length gate runs BEFORE the debounce (5093): a short query hides
 *   the dropdown immediately rather than scheduling a timer that does nothing;
 * - a failed search hides the dropdown outright (5119) — no error row;
 * - a successful block toasts, CLEARS the search, and reloads the list
 *   (`blacklistBlockEffects`) — clearing is what stops the blocked artist
 *   still sitting in the results offering a dead Block button;
 * - unblock toasts and reloads; failures on either toast their error copy.
 *
 * `results === null` is the vanilla's `display:none` dropdown, which is what
 * the BlacklistModal component renders from.
 */

export type BlacklistToast = { message: string; level: 'success' | 'error' };

export interface BlacklistController {
  open: boolean;
  openModal: () => void;
  closeModal: () => void;
  query: string;
  setQuery: (query: string) => void;
  results: BlacklistSearchArtist[] | null;
  entries: BlacklistEntry[];
  listPhase: 'loading' | 'error' | 'ready';
  block: (artistName: string) => Promise<void>;
  unblock: (entry: BlacklistEntry) => Promise<void>;
}

export function useBlacklist(onToast: (toast: BlacklistToast) => void): BlacklistController {
  const [open, setOpen] = useState(false);
  const [query, setQueryState] = useState('');
  const [results, setResults] = useState<BlacklistSearchArtist[] | null>(null);
  const [entries, setEntries] = useState<BlacklistEntry[]>([]);
  const [listPhase, setListPhase] = useState<'loading' | 'error' | 'ready'>('loading');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toastRef = useRef(onToast);
  toastRef.current = onToast;

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const loadList = useCallback(async () => {
    setListPhase('loading');
    try {
      const res = await fetch(BLACKLIST_URL);
      const data = (await res.json()) as Parameters<typeof blacklistEntries>[0];
      setEntries(blacklistEntries(data));
      setListPhase('ready');
    } catch {
      setListPhase('error');
    }
  }, []);

  const openModal = useCallback(() => {
    // Fresh every open, like the vanilla's rebuilt overlay (5059).
    setOpen(true);
    setQueryState('');
    setResults(null);
    void loadList();
  }, [loadList]);

  const closeModal = useCallback(() => {
    setOpen(false);
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const setQuery = useCallback((raw: string) => {
    setQueryState(raw);
    if (timer.current) clearTimeout(timer.current);
    // BEFORE the debounce (5093): a short query hides the dropdown now.
    if (blacklistQueryTooShort(raw)) {
      setResults(null);
      return;
    }
    timer.current = setTimeout(() => {
      void (async () => {
        try {
          const res = await fetch(BLACKLIST_SEARCH_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(blacklistSearchBody(raw.trim())),
          });
          const data = (await res.json()) as Parameters<typeof blacklistSearchResults>[0];
          setResults(blacklistSearchResults(data));
        } catch {
          // A failed search hides the dropdown outright (5119).
          setResults(null);
        }
      })();
    }, BLACKLIST_SEARCH_DEBOUNCE_MS);
  }, []);

  const block = useCallback(
    async (artistName: string) => {
      try {
        const res = await fetch(BLACKLIST_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(blacklistBlockBody(artistName)),
        });
        const data = (await res.json()) as { success?: boolean };
        const effects = blacklistBlockEffects(artistName, data);
        if (!effects) return;
        toastRef.current({ message: effects.toast, level: 'success' });
        setQueryState('');
        setResults(null);
        void loadList();
      } catch {
        toastRef.current({ message: BLACKLIST_BLOCK_ERROR, level: 'error' });
      }
    },
    [loadList],
  );

  const unblock = useCallback(
    async (entry: BlacklistEntry) => {
      try {
        const res = await fetch(blacklistDeleteUrl(entry.id ?? ''), { method: 'DELETE' });
        const data = (await res.json()) as { success?: boolean };
        if (!data.success) return;
        toastRef.current({
          message: blacklistUnblockedToast(entry.artist_name ?? ''),
          level: 'success',
        });
        void loadList();
      } catch {
        toastRef.current({ message: 'Error unblocking artist', level: 'error' });
      }
    },
    [loadList],
  );

  return {
    open,
    openModal,
    closeModal,
    query,
    setQuery,
    results,
    entries,
    listPhase,
    block,
    unblock,
  };
}
