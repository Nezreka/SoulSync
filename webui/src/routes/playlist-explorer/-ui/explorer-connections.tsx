/**
 * The SVG layer under the tree (pages-extra.js:870-1016).
 *
 * The paths themselves are measured from the laid-out DOM by the interaction
 * controller; this component only draws what it is handed. The gradients come
 * straight from `--accent-rgb` in CSS rather than being read back through
 * getComputedStyle the way the vanilla built its <defs>.
 */

export interface ExplorerPath {
  id: string;
  d: string;
  stroke: string;
  strokeWidth: string;
  /** The draw-on animation only runs for the first paint after a build (:1008). */
  animated: boolean;
  /** Path length, needed for the dash offset that animates the stroke. */
  length: number;
}

export interface ExplorerConnectionsProps {
  width: number;
  height: number;
  paths: ExplorerPath[];
}

export function ExplorerConnections({ width, height, paths }: ExplorerConnectionsProps) {
  return (
    <svg
      className="explorer-svg"
      id="explorer-svg"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
    >
      <defs>
        <linearGradient id="explorer-grad-root" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" style={{ stopColor: 'rgba(var(--accent-rgb), 0.25)' }} />
          <stop offset="100%" style={{ stopColor: 'rgba(var(--accent-rgb), 0.06)' }} />
        </linearGradient>
        <linearGradient id="explorer-grad-album" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" style={{ stopColor: 'rgba(var(--accent-rgb), 0.15)' }} />
          <stop offset="100%" style={{ stopColor: 'rgba(var(--accent-rgb), 0.04)' }} />
        </linearGradient>
      </defs>
      {paths.map((path) => (
        <path
          key={path.id}
          className={path.animated ? 'explorer-line explorer-line-animated' : 'explorer-line'}
          d={path.d}
          stroke={path.stroke}
          strokeWidth={path.strokeWidth}
          fill="none"
          style={
            path.animated
              ? { strokeDasharray: path.length, strokeDashoffset: path.length }
              : undefined
          }
        />
      ))}
    </svg>
  );
}
