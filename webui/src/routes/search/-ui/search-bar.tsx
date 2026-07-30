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
  loading,
  onQueryChange,
  onSubmit,
  onCancel,
  idValue,
  onIdChange,
  onIdSubmit,
}: {
  query: string;
  loading: boolean;
  onQueryChange: (value: string) => void;
  /** Enter — bypasses the debounce entirely. */
  onSubmit: () => void;
  onCancel: () => void;
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
          <span className="enhanced-search-icon">🔍</span>
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
          {loading ? (
            <button
              className="enhanced-cancel-btn"
              id="enhanced-cancel-btn"
              type="button"
              title="Cancel search"
              onClick={onCancel}
            >
              ✕
            </button>
          ) : null}
        </div>

        {/* Paste a provider URL or a MusicBrainz id to resolve one exact release. */}
        <div className="enh-id-lookup">
          <span className="enh-id-lookup-icon">🔗</span>
          <input
            id="enh-id-input"
            className="enh-id-input"
            type="text"
            placeholder="Paste a link or ID"
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
            Go
          </button>
        </div>
      </div>
    </div>
  );
}
