import { useCallback, useEffect, useRef, useState } from 'react';

import type { LastfmTrackResult } from './-discover.lastfm-radio';
import type { DiscoverMix } from './-discover.mixes';

import {
  lastfmSelectionLabel,
  LASTFM_CONFIGURED_URL,
  LASTFM_GENERATE_ERROR,
  LASTFM_GENERATE_FAILED,
  LASTFM_RADIO_CARD_KIND,
  LASTFM_RADIO_GENERATE_URL,
  LASTFM_RADIO_PLAYLISTS_URL,
  LASTFM_SEARCH_DEBOUNCE_MS,
  lastfmGenerateBody,
  lastfmHasResults,
  lastfmQueryIsEmpty,
  lastfmQueryTooShort,
  lastfmSearchUrl,
} from './-discover.lastfm-radio';
import { lbPlaylistMix } from './-discover.listenbrainz';

/**
 * The Last.fm Radio controller.
 *
 * Transcribed from `initializeLastfmRadioSection` + `_loadLastfmRadioPlaylists`
 * + the search wiring + `_generateLastfmRadioFor` (discover.js 3227-3330,
 * 3336-3372), over the module that documents every quirk. The ones that
 * matter, each pinned:
 *
 * - the section exists only when Last.fm is CONFIGURED (3231-3237);
 *   `configured` stays null until the probe answers, and a failed probe
 *   leaves the section hidden;
 * - an EMPTY query hides the dropdown immediately, before the debounce
 *   (3243); a merely-SHORT one is checked INSIDE the debounced callback
 *   (3251) — it still schedules a timer that then does nothing, leaving the
 *   dropdown as it was;
 * - empty results hide the dropdown rather than showing a no-results row;
 * - picking a result generates IMMEDIATELY (3293): dropdown closes, input
 *   locks, and on success the persisted radio playlists reload; the failure
 *   toasts are the vanilla's two, server-message first.
 *
 * Generated radios become mix cards through the same `lbPlaylistMix` mapping
 * the ListenBrainz shelf uses — the vanilla calls one builder for both
 * (3577, "Shared by Last.fm Radio + ListenBrainz").
 */

export type LastfmToast = { message: string; level: 'error' };

export interface LastfmRadioController {
  /** null until the configured probe answers; false = section hidden. */
  configured: boolean | null;
  query: string;
  setQuery: (query: string) => void;
  results: LastfmTrackResult[];
  dropdownOpen: boolean;
  searching: boolean;
  generating: boolean;
  mixes: DiscoverMix[];
  loaded: boolean;
  pick: (track: LastfmTrackResult) => Promise<void>;
  clear: () => void;
  /** close the dropdown but KEEP the typed query - outside clicks. */
  dismiss: () => void;
}

export function useLastfmRadio(onToast: (toast: LastfmToast) => void): LastfmRadioController {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;

  const [configured, setConfigured] = useState<boolean | null>(null);
  const [query, setQueryState] = useState('');
  const [results, setResults] = useState<LastfmTrackResult[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [mixes, setMixes] = useState<DiscoverMix[]>([]);
  const [loaded, setLoaded] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchGen = useRef(0);

  const loadPlaylists = useCallback(async () => {
    try {
      const res = await fetch(LASTFM_RADIO_PLAYLISTS_URL);
      if (!res.ok) return;
      const data = (await res.json()) as {
        success?: boolean;
        playlists?: Record<string, unknown>[];
      };
      // An empty answer EMPTIES the shelf (3252-3255) — it does not keep
      // stale cards.
      if (!data.success || !data.playlists || data.playlists.length === 0) {
        setMixes([]);
        return;
      }
      setMixes(data.playlists.map((p) => lbPlaylistMix(p, LASTFM_RADIO_CARD_KIND)));
    } catch {
      /* the vanilla logs and moves on (3259) */
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch(LASTFM_CONFIGURED_URL);
        if (!res.ok) return; // configured stays null → hidden
        const data = (await res.json()) as { configured?: boolean };
        setConfigured(Boolean(data.configured));
        if (data.configured) {
          await loadPlaylists();
        }
      } catch {
        /* section stays hidden */
      } finally {
        setLoaded(true);
      }
    })();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [loadPlaylists]);

  const setQuery = useCallback((raw: string) => {
    setQueryState(raw);
    if (timer.current) clearTimeout(timer.current);
    // Empty hides NOW, before the debounce (3243) — and fires no request.
    if (lastfmQueryIsEmpty(raw)) {
      setDropdownOpen(false);
      setResults([]);
      setSearching(false);
      return;
    }
    timer.current = setTimeout(() => {
      const q = raw.trim();
      // Short is checked INSIDE the callback (3251): the timer fires and does
      // nothing, leaving the dropdown as it was.
      if (lastfmQueryTooShort(q)) return;
      searchGen.current += 1;
      const gen = searchGen.current;
      setSearching(true);
      setDropdownOpen(true);
      void (async () => {
        try {
          const res = await fetch(lastfmSearchUrl(q));
          const data = (await res.json()) as {
            results?: LastfmTrackResult[];
            error?: string;
          };
          if (searchGen.current !== gen) return;
          if (!res.ok) {
            // a server error used to be indistinguishable from "no results" -
            // the dropdown just hid. say what actually happened.
            toastRef.current({
              message: data.error || 'Last.fm search failed',
              level: 'error',
            });
            setDropdownOpen(false);
            setResults([]);
          } else if (!lastfmHasResults(data)) {
            // No results → the dropdown HIDES (3260), no empty row.
            setDropdownOpen(false);
            setResults([]);
          } else {
            setResults(data.results ?? []);
          }
        } catch {
          if (searchGen.current !== gen) return;
          setDropdownOpen(false);
          setResults([]);
        } finally {
          if (searchGen.current === gen) setSearching(false);
        }
      })();
    }, LASTFM_SEARCH_DEBOUNCE_MS);
  }, []);

  const clear = useCallback(() => {
    setQueryState('');
    setDropdownOpen(false);
    setResults([]);
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const dismiss = useCallback(() => {
    setDropdownOpen(false);
  }, []);

  const pick = useCallback(
    async (track: LastfmTrackResult) => {
      const name = track.name ?? '';
      const artist = track.artist ?? '';
      // 3290-3293: close, confirm the pick in the input, lock it, generate.
      setDropdownOpen(false);
      setQueryState(lastfmSelectionLabel(name, artist));
      setGenerating(true);
      try {
        const res = await fetch(LASTFM_RADIO_GENERATE_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(lastfmGenerateBody(name, artist)),
        });
        const data = (await res.json()) as { success?: boolean; error?: string };
        if (!data.success) {
          toastRef.current({ message: data.error || LASTFM_GENERATE_FAILED, level: 'error' });
          return;
        }
        await loadPlaylists();
        // the vanilla refreshed the LB state map after building the radio -
        // without it, the new card's Sync button no-ops on a missing state
        void window.loadListenBrainzPlaylistsFromBackend?.();
        setQueryState('');
      } catch {
        toastRef.current({ message: LASTFM_GENERATE_ERROR, level: 'error' });
      } finally {
        setGenerating(false);
      }
    },
    [loadPlaylists],
  );

  return {
    configured,
    query,
    setQuery,
    results,
    dropdownOpen,
    searching,
    generating,
    mixes,
    loaded,
    pick,
    clear,
    dismiss,
  };
}
