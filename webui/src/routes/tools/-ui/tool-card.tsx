/**
 * The shared `.tool-card` chrome, transcribed from index.html's tools region so
 * the existing style.css / mobile.css rules keep applying unchanged.
 *
 * Two things here are load-bearing beyond looks:
 *
 * 1. **The card `id` must survive.** helper.js anchors its help popovers on
 *    `#db-updater-card`, `#metadata-updater-card`, `#duplicate-cleaner-card`,
 *    `#discovery-pool-card`, `#media-scan-card`, `#backup-manager-card`,
 *    `#metadata-cache-card` and `#blacklist-card`, and its search walks UP the
 *    DOM matching those selectors. Drop an id and that card silently stops
 *    being findable from the helper.
 *
 * 2. **The `?` button opens the VANILLA modal.** `#tool-help-modal` and
 *    `TOOL_HELP_CONTENT` both live outside this page, so the button calls
 *    `window.openToolHelpModal(toolId)` rather than reimplementing it. The
 *    vanilla wired these by querying `.tool-help-button[data-tool]` from
 *    `initializeToolsPage`, which won't run for a React page — so the handler is
 *    bound directly here instead. `data-tool` is still emitted because that is
 *    what the markup contract has always been.
 */

import type { ReactNode } from 'react';

export interface ToolCardStat {
  label: string;
  value: ReactNode;
  /**
   * Goes on the `.stat-item-value` span ITSELF, exactly as in the markup — not
   * on a nested node. The vanilla progress handlers look these up by id and the
   * class is on the same element, so splitting them apart would leave anything
   * that touches the element's class or style writing to the wrong node.
   */
  valueId?: string;
  /** Only the discovery pool's "Failed" counter carries its own colours. */
  valueStyle?: React.CSSProperties;
}

export interface ToolCardProps {
  /** Kept verbatim — helper.js anchors on these. */
  id: string;
  title: string;
  /** `data-tool` for the `?` button. Omit for the four cards that have none. */
  helpTool?: string;
  /** The description paragraph. Some cards add extra classes to it. */
  info?: ReactNode;
  infoClassName?: string;
  stats?: ToolCardStat[];
  statsId?: string;
  statsClassName?: string;
  controls?: ReactNode;
  progress?: ReactNode;
  children?: ReactNode;
  hidden?: boolean;
}

export function ToolCard({
  id,
  title,
  helpTool,
  info,
  infoClassName,
  stats,
  statsId,
  statsClassName,
  controls,
  progress,
  children,
  hidden,
}: ToolCardProps) {
  return (
    <div
      className="tool-card"
      id={id}
      // The vanilla hides the Plex-only cards with an inline style rather than a
      // class, and `checkAndShowMediaScanForPlex` sets display:flex to show them
      // again — so the shown state is flex, not block.
      style={hidden ? { display: 'none' } : undefined}
    >
      <div className="tool-card-header">
        <h4 className="tool-card-title">{title}</h4>
        {helpTool ? (
          <button
            type="button"
            className="tool-help-button"
            data-tool={helpTool}
            title="Learn more about this tool"
            onClick={(event) => {
              event.stopPropagation();
              window.openToolHelpModal?.(helpTool);
            }}
          >
            ?
          </button>
        ) : null}
      </div>

      {info ? <p className={infoClassName || 'tool-card-info'}>{info}</p> : null}

      {stats?.length ? (
        <div className={statsClassName || 'tool-card-stats'} id={statsId}>
          {stats.map((stat) => (
            <div className="stat-item" key={stat.label}>
              <span className="stat-item-label">{stat.label}</span>
              <span className="stat-item-value" id={stat.valueId} style={stat.valueStyle}>
                {stat.value}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {controls ? <div className="tool-card-controls">{controls}</div> : null}
      {progress ? <div className="tool-card-progress-section">{progress}</div> : null}
      {children}
    </div>
  );
}

export interface ToolProgressProps {
  phase: string;
  details: string;
  /** 0-100. The vanilla writes this straight into style.width. */
  percent: number;
  phaseId: string;
  barId: string;
  detailsId: string;
  /** The duplicate cleaner and db updater turn the bar red on error. */
  barColor?: string;
}

/**
 * The three-part progress block every long-running tool card carries. Ids are
 * preserved: nothing in the React page needs them, but the vanilla socket
 * handlers still write to these elements until P6 rewires them, and keeping
 * them costs nothing.
 */
export function ToolProgress({
  phase,
  details,
  percent,
  phaseId,
  barId,
  detailsId,
  barColor,
}: ToolProgressProps) {
  return (
    <>
      <p className="progress-phase-label" id={phaseId}>
        {phase}
      </p>
      <div className="progress-bar-container">
        <div
          className="progress-bar-fill"
          id={barId}
          style={{ width: `${percent}%`, backgroundColor: barColor }}
        />
      </div>
      <p className="progress-details-label" id={detailsId}>
        {details}
      </p>
    </>
  );
}

export interface ToolsSectionProps {
  title: string;
  children: ReactNode;
}

/** `.tools-section` > title + `.tools-grid`. */
export function ToolsSection({ title, children }: ToolsSectionProps) {
  return (
    <div className="tools-section">
      <h3 className="tools-section-title">{title}</h3>
      <div className="tools-grid">{children}</div>
    </div>
  );
}
