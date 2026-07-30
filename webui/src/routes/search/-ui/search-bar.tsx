import { useEffect, useRef } from 'react';

/** Marker the native listener stamps on an event it has already reported. */
type ClaimedEvent = { _nativeHandled?: boolean };

/**
 * The query input and the ID-lookup box.
 *
 * The input is CONTROLLED but must also honour a programmatic write. The global
 * download widget (downloads.js:5753) sets `#enhanced-search-input.value`
 * directly and dispatches `new Event('input', {bubbles:true})` to hand a query
 * over from elsewhere in the app. React does NOT fire onChange for that: it
 * patches the input's own `value` descriptor to track the last value it knows
 * about, and a direct assignment goes through that patched setter, so by the
 * time the event arrives React sees no change at all. The native listener is the
 * only thing making the handoff work, and dropping it would break the widget
 * silently.
 *
 * Both listeners see a real keystroke though, so one of them has to yield. The
 * NATIVE one claims the event and the React one stands down — that order is
 * forced, not chosen: React delegates at the root container, so the element's
 * own listener always runs first and a flag set inside onChange would be set too
 * late to suppress anything. (The React path still earns its keep: a `change`
 * event with no preceding `input` — as fireEvent.change produces — reaches
 * onChange and nothing else.)
 */
export function SearchBar({
  query,
  onQueryChange,
  onSubmit,
  onClear,
  idValue,
  onIdChange,
  onIdSubmit,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  /** Enter — bypasses the debounce entirely. */
  onSubmit: () => void;
  /**
   * The ✕ CLEARS the box; it does not cancel a search.
   *
   * Worth being blunt about because the first version of this got it backwards:
   * search.js:241-243 shows the button whenever the input has ANY text and
   * hides it when empty — nothing to do with a request being in flight — and
   * search.js:278-283 empties the input and closes the dropdown when it is
   * clicked. Tying it to a loading flag hides it while you type and turns it
   * into a different feature.
   */
  onClear: () => void;
  idValue: string;
  onIdChange: (value: string) => void;
  onIdSubmit: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const changeRef = useRef(onQueryChange);
  changeRef.current = onQueryChange;

  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    const onNativeInput = (event: Event) => {
      // Claimed before anything else, so onChange knows to stand down when this
      // same event reaches the root.
      (event as ClaimedEvent)._nativeHandled = true;
      const value = (event.target as HTMLInputElement).value;
      // A programmatic write of the text already on screen is not an edit.
      if (value !== query) changeRef.current(value);
    };
    element.addEventListener('input', onNativeInput);
    return () => element.removeEventListener('input', onNativeInput);
  }, [query]);

  return (
    <div className="enhanced-search-input-wrapper">
      <div className="enhanced-search-bar-container">
        <div className="enhanced-search-wrapper">
          {/* ✨ in a div, both as in index.html:4155 — the stylesheet sizes and
              positions this by class, and the glyph is part of the page's look. */}
          <div className="enhanced-search-icon">✨</div>
          <input
            ref={inputRef}
            id="enhanced-search-input"
            type="text"
            placeholder="Search for artists, albums, or tracks..."
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
          {query ? (
            <button
              className="enhanced-cancel-btn"
              id="enhanced-cancel-btn"
              type="button"
              title="Clear search"
              onClick={onClear}
            >
              ✕
            </button>
          ) : null}
        </div>

        {/* Paste a provider URL or a MusicBrainz id to resolve one exact release.
            The placeholder names the four providers on purpose — it is the only
            place the feature explains itself (#775). */}
        <div className="enh-id-lookup">
          <span className="enh-id-lookup-icon">🔗</span>
          <input
            id="enh-id-input"
            className="enh-id-input"
            type="text"
            placeholder="…or paste a Spotify / Apple Music / MusicBrainz / Deezer link, or a MusicBrainz ID"
            autoComplete="off"
            spellCheck={false}
            value={idValue}
            onChange={(event) => onIdChange(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                onIdSubmit();
              }
            }}
          />
          <button className="enh-id-btn" id="enh-id-btn" type="button" onClick={onIdSubmit}>
            Look up
          </button>
        </div>
      </div>
    </div>
  );
}
