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
  /**
   * What KIND of thing this is.
   *
   * Eleven cards with identical chrome read as filler, because they were not
   * eleven of the same thing: five of them just open a modal, four are
   * long-running operations with progress, two are one-shot server actions.
   * A modal-opener given the Database Updater's footprint is what made the
   * whole strip look undifferentiated.
   */
  variant?: 'operation' | 'launcher';
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
  variant = 'operation',
}: ToolCardProps) {
  return (
    <div
      className={`tool-card${variant === 'launcher' ? ' tool-card--launcher' : ''}`}
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
  /** One line on what the section is FOR. A title alone is a taxonomy; this
   *  is a reason to look. */
  blurb?: string;
  /** `R,G,B` for `--tile-glow` — the same device the job families and the
   *  arcade tiles use, so the whole page reads as one system. */
  glow?: string;
  children: ReactNode;
}

/**
 * A container of tools, on the same chassis as the maintenance job families.
 *
 * The class and the title node are unchanged so the existing style.css and
 * mobile.css rules keep applying; the container chrome is additive.
 */
export function ToolsSection({ title, blurb, glow, children }: ToolsSectionProps) {
  return (
    <div
      className="tools-section tools-container"
      style={glow ? ({ ['--tile-glow' as string]: glow } as React.CSSProperties) : undefined}
    >
      <div className="tools-container-head">
        <h3 className="tools-section-title">{title}</h3>
        {blurb ? <span className="tools-container-blurb">{blurb}</span> : null}
      </div>
      <div className="tools-grid">{children}</div>
    </div>
  );
}
