import type { SearchControllerState } from '../-search.use-controller';

import { sourceLabel, visibleSources } from '../-search.helpers';
import { SOURCE_LABELS } from '../-search.types';

/**
 * The source picker.
 *
 * Five states stack on one icon, and each means something different:
 *   active    — the source whose results are on screen
 *   cached    — already fetched for this query; clicking is instant
 *   loading   — in flight
 *   fallback  — the server served something ELSE for this source
 *   unconfigured — no credentials; clicking goes to Settings, NOT to a search
 *
 * The last one is the important one: an unconfigured icon must not become the
 * active source, or the page shows an empty result set and blames the provider.
 */
export function SourceRow({
  state,
  onSelect,
  onOpenSettings,
}: {
  state: SearchControllerState;
  onSelect: (source: string) => void;
  onOpenSettings: (source: string) => void;
}) {
  return (
    <div className="enh-source-row" id="enh-source-row" role="tablist">
      {visibleSources(state.enabledExperimental).map((source) => {
        const info = SOURCE_LABELS[source];
        if (!info) return null;

        const configured = state.configuredSources[source] !== false;
        const loading = state.loadingSources.has(source);
        const classes = ['enh-source-icon'];
        if (source === state.activeSource) classes.push('active');
        if (state.sources[source]) classes.push('cached');
        if (loading) classes.push('loading');
        if (state.fallbacks[source]) classes.push('fallback-warning');
        if (!configured) classes.push('unconfigured');

        const served = state.fallbacks[source];
        // The wording is the vanilla's, and the two phrasings differ on purpose:
        // the ICON says "served from" (shared-helpers.js:316) while the banner
        // above the results says "showing" (search.js:98).
        const title = !configured
          ? `${info.text} — set up in Settings`
          : served
            ? `${info.text} unavailable — served from ${sourceLabel(served)}`
            : info.text;

        return (
          <button
            key={source}
            type="button"
            className={classes.join(' ')}
            data-source={source}
            title={title}
            role="tab"
            aria-selected={source === state.activeSource}
            onClick={(event) => {
              // The row re-renders on click, which detaches the node the event
              // came from; stopping propagation here is what keeps the
              // document-level outside-click handler from also firing.
              event.stopPropagation();
              if (!configured) {
                onOpenSettings(source);
                return;
              }
              onSelect(source);
            }}
          >
            <span className="enh-source-icon-glyph">
              {/* An in-flight source shows an hourglass INSTEAD of its logo —
                  shared-helpers.js:325-330. The `loading` class animates the
                  button, but the glyph swap is what makes it unmistakable. */}
              {loading ? (
                '⏳'
              ) : info.logo ? (
                <img src={info.logo} alt="" loading="lazy" />
              ) : (
                info.icon
              )}
            </span>
            <span className="enh-source-icon-label">{info.text}</span>
          </button>
        );
      })}
    </div>
  );
}
