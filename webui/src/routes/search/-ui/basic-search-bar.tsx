/**
 * The basic-search input, its Cancel affordance and the Search button.
 *
 * The ✕ here is a CANCEL button, not a clear button — the opposite of the
 * enhanced bar's. It is hidden until a search is in flight and aborts it
 * (search.js:18, downloads.js:4366/4429). Getting those two the wrong way
 * round is easy: they look identical and sit six lines apart in the markup.
 *
 * `disabled` while searching mirrors the vanilla, which disabled both the input
 * and the button for the duration so a second Enter could not stack a search
 * on top of the one running.
 */
export function BasicSearchBar({
  query,
  searching,
  onQueryChange,
  onSubmit,
  onCancel,
}: {
  query: string;
  searching: boolean;
  onQueryChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="bs-search-bar">
      <div className="bs-search-input-wrap">
        <svg
          className="bs-search-icon"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="9" cy="9" r="6" />
          <path d="M15 15l3 3" />
        </svg>
        <input
          type="text"
          id="downloads-search-input"
          placeholder="Search artists, albums, tracks…"
          autoComplete="off"
          spellCheck={false}
          value={query}
          disabled={searching}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onSubmit();
          }}
        />
        <button
          id="downloads-cancel-btn"
          className={`bs-cancel-btn${searching ? '' : ' hidden'}`}
          type="button"
          aria-label="Cancel"
          onClick={onCancel}
        >
          ✕
        </button>
      </div>
      <button
        id="downloads-search-btn"
        className="bs-search-btn"
        type="button"
        disabled={searching}
        onClick={onSubmit}
      >
        Search
      </button>
    </div>
  );
}

/**
 * The status line: spinner, message, animated dots.
 *
 * Both animations are hidden outside a search — they are the only motion on
 * the page, and leaving them running reads as a search that never finished.
 */
export function BasicStatusBar({ status, searching }: { status: string; searching: boolean }) {
  return (
    <div className="bs-status-bar">
      <div className={`spinner-animation${searching ? '' : ' hidden'}`} />
      <span id="search-status-text" className="bs-status-text">
        {status}
      </span>
      <div className={`dots-animation${searching ? '' : ' hidden'}`} />
    </div>
  );
}
