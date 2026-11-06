/**
 * The playlist search box. Born on the mirrored tab; every tab with a
 * playlist list carries it now, same markup, same .library-search styling.
 */

export function playlistNameMatches(names: readonly unknown[], query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return names.some((n) => typeof n === 'string' && n.toLowerCase().includes(needle));
}

/** the empty-list line when the search, not the source, is what emptied it. */
export function noMatchText(query: string): string {
  return `No playlists match "${query.trim()}".`;
}

export function PlaylistSearch({
  value,
  onChange,
  className,
  icon,
}: {
  value: string;
  onChange: (value: string) => void;
  /**
   * A page that styles the box its own way. The legacy .library-search
   * classes are left off then, so their rules have nothing to fight.
   */
  className?: string;
  /** a leading glyph inside the box */
  icon?: React.ReactNode;
}) {
  const own = Boolean(className);
  return (
    <div className={own ? className : 'library-search'}>
      {icon}
      <input
        type="search"
        className={own ? undefined : 'library-search-input'}
        placeholder="Search playlists"
        aria-label="Search playlists"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          // Escape clears rather than blurs: a search box you cannot empty
          // without selecting the text is a box you have to fight.
          if (e.key === 'Escape') onChange('');
        }}
      />
      {value && (
        <button
          type="button"
          className={own ? undefined : 'library-search-clear'}
          aria-label="Clear search"
          onClick={() => onChange('')}
        >
          &times;
        </button>
      )}
    </div>
  );
}
