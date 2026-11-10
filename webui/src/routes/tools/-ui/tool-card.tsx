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
  valueId?: string;
  valueStyle?: React.CSSProperties;
}

/** the picture on a tool's tile. a name, so a card says what it is and the
 *  drawing lives in one place. */
export type ToolIcon =
  | 'database'
  | 'tags'
  | 'copies'
  | 'server'
  | 'sparkle'
  | 'review'
  | 'link'
  | 'backup'
  | 'transfer'
  | 'cache'
  | 'block'
  | 'film'
  | 'image'
  | 'grid'
  | 'rename'
  | 'tool';

const ICON_PATHS: Record<ToolIcon, ReactNode> = {
  database: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
    </>
  ),
  tags: (
    <>
      <path d="M3 12V4h8l10 10-8 8z" />
      <circle cx="7.5" cy="8.5" r="1.2" />
    </>
  ),
  copies: (
    <>
      <rect x="8" y="8" width="12" height="12" rx="2" />
      <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
    </>
  ),
  server: (
    <>
      <rect x="3" y="4" width="18" height="7" rx="2" />
      <rect x="3" y="13" width="18" height="7" rx="2" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </>
  ),
  sparkle: (
    <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z" />
  ),
  review: (
    <>
      <path d="M4 6h10M4 12h7M4 18h6" />
      <path d="M14.5 16.5l2.5 2.5 4.5-5" />
    </>
  ),
  link: (
    <>
      <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
      <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
    </>
  ),
  backup: (
    <>
      <path d="M12 3l8 3v6c0 4.4-3.2 8-8 9-4.8-1-8-4.6-8-9V6z" />
      <path d="M9 12l2.2 2.2L15.5 10" />
    </>
  ),
  transfer: <path d="M4 8h13m0 0l-3.5-3.5M17 8l-3.5 3.5M20 16H7m0 0l3.5-3.5M7 16l3.5 3.5" />,
  cache: (
    <>
      <path d="M12 3l9 5-9 5-9-5z" />
      <path d="M3 13l9 5 9-5M3 17.5l9 5 9-5" opacity="0.55" />
    </>
  ),
  block: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M5.7 5.7l12.6 12.6" />
    </>
  ),
  film: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" />
    </>
  ),
  image: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <circle cx="8.5" cy="9.5" r="1.5" />
      <path d="M21 16l-5-5-8 8" />
    </>
  ),
  grid: (
    <>
      <rect x="3" y="3" width="8" height="8" rx="1.5" />
      <rect x="13" y="3" width="8" height="8" rx="1.5" />
      <rect x="3" y="13" width="8" height="8" rx="1.5" />
      <rect x="13" y="13" width="8" height="8" rx="1.5" />
    </>
  ),
  rename: (
    <>
      <path d="M4 7V5h10v2M9 5v14M7 19h4" />
      <path d="M16 12h5M18.5 9.5v10" />
    </>
  ),
  tool: (
    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z" />
  ),
};

export interface ToolCardProps {
  id: string;
  title: string;
  /** the tile's picture */
  icon?: ToolIcon;
  /** the one tool the section leads with: full width, bigger numbers */
  feature?: boolean;
  helpTool?: string;
  info?: ReactNode;
  infoClassName?: string;
  stats?: ToolCardStat[];
  statsId?: string;
  statsClassName?: string;
  controls?: ReactNode;
  progress?: ReactNode;
  children?: ReactNode;
  hidden?: boolean;
  variant?: 'operation' | 'launcher';
}

/** a stat whose value is words ("Never", "Wishlist / Sync"), not a number:
 *  it reads at text size, where a number gets to be big */
function isWordy(value: ReactNode): boolean {
  if (typeof value === 'number') return false;
  if (typeof value !== 'string') return false;
  return !/^[\d.,]+(\s?(KB|MB|GB|TB|%))?$/i.test(value.trim());
}

/**
 * One tool, as a card with the same anatomy every time: a picture and what
 * it is, the numbers that say where it stands, and at the bottom what it is
 * doing now beside the button that starts it.
 *
 * The cards used to be a title, a paragraph, a box of label:value pairs and a
 * button, all the same size, so the page read as a form. The numbers lead
 * now, a tool that is running shows it in place, and nothing sits in a box
 * inside a box.
 */
export function ToolCard({
  id,
  title,
  icon = 'tool',
  feature = false,
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
      className={`tool-card${variant === 'launcher' ? ' tool-card--launcher' : ''}${
        feature ? ' tool-card--feature' : ''
      }`}
      id={id}
      // the Plex-only cards are hidden with an inline style, the way the
      // vanilla did; shown is whatever the stylesheet says a card is
      style={hidden ? { display: 'none' } : undefined}
    >
      <div className="tool-card-top">
        <span className="tool-card-icon" aria-hidden="true">
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {ICON_PATHS[icon]}
          </svg>
        </span>
        <div className="tool-card-heading">
          <div className="tool-card-header">
            <h4 className="tool-card-title">{title}</h4>
            {helpTool ? (
              <button
                type="button"
                className="tool-help-button"
                data-tool={helpTool}
                title="Learn more about this tool"
                aria-label={`Learn more about ${title}`}
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
        </div>
      </div>

      {stats?.length ? (
        <div className={statsClassName || 'tool-card-stats'} id={statsId}>
          {stats.map((stat) => (
            <div className={`stat-item${isWordy(stat.value) ? ' wordy' : ''}`} key={stat.label}>
              <span className="stat-item-value" id={stat.valueId} style={stat.valueStyle}>
                {stat.value}
              </span>
              <span className="stat-item-label">{stat.label.replace(/:\s*$/, '')}</span>
            </div>
          ))}
        </div>
      ) : null}

      {children}

      {controls || progress ? (
        <div className="tool-card-bottom">
          {progress ? <div className="tool-card-progress-section">{progress}</div> : null}
          {controls ? <div className="tool-card-controls">{controls}</div> : null}
        </div>
      ) : null}
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
          className={`progress-bar-fill${percent > 0 ? '' : ' is-empty'}`}
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
export function ToolsSection({ title, blurb, children }: ToolsSectionProps) {
  return (
    <section className="tools-section">
      <div className="tools-section-head">
        <h3 className="tools-section-title">{title}</h3>
        {blurb ? <span className="tools-section-blurb">{blurb}</span> : null}
      </div>
      <div className="tools-grid">{children}</div>
    </section>
  );
}
