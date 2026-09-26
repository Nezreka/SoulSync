import { useEffect, useRef } from 'react';

import styles from './search.module.css';

/** Marker the native listener stamps on an event it has already reported. */
type ClaimedEvent = { _nativeHandled?: boolean };

/**
 * The search field: the source pill, the input, and a clear button.
 *
 * pasting a Spotify / Apple Music / Deezer / MusicBrainz link (or a bare
 * MusicBrainz id) into it resolves that exact release, so there is no second
 * lookup box any more. the page decides; this only reports the text.
 *
 * The input is CONTROLLED but must also honour a programmatic write. The global
 * download widget (downloads.js) sets `#enhanced-search-input.value` directly and
 * dispatches `new Event('input', {bubbles:true})` to hand a query over from
 * elsewhere in the app. React does NOT fire onChange for that: it patches the
 * input's own `value` descriptor to track the last value it knows about, and a
 * direct assignment goes through that patched setter, so by the time the event
 * arrives React sees no change at all. The native listener is the only thing
 * making the handoff work, and dropping it would break the widget silently.
 *
 * Both listeners see a real keystroke though, so one of them has to yield. The
 * NATIVE one claims the event and the React one stands down. That order is
 * forced, not chosen: React delegates at the root container, so the element's
 * own listener always runs first and a flag set inside onChange would be set too
 * late to suppress anything. (The React path still earns its keep: a `change`
 * event with no preceding `input`, as fireEvent.change produces, reaches
 * onChange and nothing else.)
 */
export function SearchBar({
  query,
  onQueryChange,
  onSubmit,
  onClear,
  picker,
  searching = false,
  placeholder = 'Artists, albums, tracks, or paste a link',
}: {
  query: string;
  onQueryChange: (value: string) => void;
  /** Enter, bypasses the debounce entirely. */
  onSubmit: () => void;
  /** the ✕ CLEARS the box, it does not cancel a search */
  onClear: () => void;
  /** the source pill that sits inside the field */
  picker?: React.ReactNode;
  searching?: boolean;
  placeholder?: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const changeRef = useRef(onQueryChange);
  changeRef.current = onQueryChange;

  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    const onNativeInput = (event: Event) => {
      (event as ClaimedEvent)._nativeHandled = true;
      const value = (event.target as HTMLInputElement).value;
      if (value !== query) changeRef.current(value);
    };
    element.addEventListener('input', onNativeInput);
    return () => element.removeEventListener('input', onNativeInput);
  }, [query]);

  return (
    <div className={styles.searchBox} id="enhanced-search-bar" role="search">
      {picker}
      <input
        ref={inputRef}
        id="enhanced-search-input"
        className={styles.input}
        type="text"
        aria-label="Search"
        autoComplete="off"
        spellCheck={false}
        placeholder={placeholder}
        value={query}
        onChange={(event) => {
          if ((event.nativeEvent as ClaimedEvent)._nativeHandled) return;
          onQueryChange(event.currentTarget.value);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            onSubmit();
          }
        }}
      />
      {searching ? <span className={styles.spinner} role="status" aria-label="Searching" /> : null}
      {query ? (
        <button
          className={styles.clear}
          id="enhanced-cancel-btn"
          type="button"
          aria-label="Clear search"
          title="Clear search"
          onClick={onClear}
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
