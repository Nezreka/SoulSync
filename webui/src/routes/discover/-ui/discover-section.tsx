import type { DiscoverSectionId } from '../-discover.layout';

import { isSectionVisible, SECTION_EMPTY_POLICY } from '../-discover.layout';

/**
 * The shell every discover shelf shares.
 *
 * Transcribed from the repeated `.discover-section` block in index.html
 * (4549 onward) — header, title, subtitle, an optional actions cluster, and the
 * content area below.
 *
 * The vanilla writes this markup out once per section and then toggles
 * `style.display` on each. Which sections may vanish and which must stay put
 * even when empty is already decided by `-discover.layout`, so this asks that
 * module rather than re-deciding per section — a shelf that hides itself when it
 * should have shown an empty state simply disappears, with nothing in the
 * console and no obvious way to tell it was ever meant to be there.
 */

export interface DiscoverSectionProps {
  id: DiscoverSectionId;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  /** Buttons in the header's right-hand cluster. */
  actions?: React.ReactNode;
  /** How many items the section has, which is what decides hide vs empty state. */
  count: number;
  /**
   * Whether this section's data has finished loading.
   *
   * An empty-state section renders with zero rows ONLY once loaded — before
   * that it stays out of the layout entirely, which is what stops a flash of
   * "No recent releases found" on every cold page load. The vanilla's loaders
   * bail before creating the section when the fetch failed, or when there is
   * nothing to show at all (no current season, say).
   *
   * Rows outrank this: content is proof the fetch answered.
   */
  loaded: boolean;
  /**
   * The element id, when it differs from the layout key.
   *
   * Most DiscoverSectionIds ARE the vanilla's DOM id. Three are not: they are
   * layout keys for sections the vanilla identified by class alone, or — like
   * Last.fm Radio — by a different id (`lastfm-radio-section`). Rendering the
   * key there would detach the stylesheet rule that hides and shows it.
   */
  domId?: string;
  /** Overrides the layout module's message for this section. */
  emptyMessage?: string;
  children?: React.ReactNode;
}

export function DiscoverSection({
  id,
  title,
  subtitle,
  actions,
  count,
  loaded,
  domId,
  emptyMessage,
  children,
}: DiscoverSectionProps) {
  if (!isSectionVisible(id, count > 0, loaded)) return null;

  const policy = SECTION_EMPTY_POLICY[id];
  // `loaded` is NOT re-checked here, and does not need to be: reaching this line
  // with no rows means isSectionVisible already returned true, which for an
  // empty-state section requires the load to have completed. Re-testing it
  // would be a condition no input can make false.
  const showEmpty = count === 0 && policy?.kind === 'empty-state';

  return (
    // The id is used VERBATIM. DiscoverSectionId already matches the vanilla's
    // DOM ids, several of which end in '-section' themselves — appending one
    // here would produce `listening-recs-section-section` and quietly detach
    // every stylesheet rule and scroll target that names it.
    <div className="discover-section" id={domId ?? id}>
      <div className="discover-section-header">
        <div>
          <h2 className="discover-section-title">{title}</h2>
          {subtitle && <p className="discover-section-subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="discover-section-actions">{actions}</div>}
      </div>
      {showEmpty ? (
        <div className="discover-section-empty">
          {emptyMessage ?? (policy.kind === 'empty-state' ? policy.message : '')}
        </div>
      ) : (
        children
      )}
    </div>
  );
}
