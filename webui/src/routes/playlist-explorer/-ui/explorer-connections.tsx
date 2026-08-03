/**
 * The SVG layer under the tree (_explorerEnsureDefs :886, _explorerDrawCurve :989).
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
          // The vanilla measured getTotalLength() to seed the dash. pathLength
          // normalises the curve to 1 unit instead, so the same
          // explorer-line-draw keyframe (dashoffset -> 0) draws it on exactly,
          // with no measurement to get wrong.
          pathLength={path.animated ? 1 : undefined}
          style={path.animated ? { strokeDasharray: 1, strokeDashoffset: 1 } : undefined}
        />
      ))}
    </svg>
  );
}
