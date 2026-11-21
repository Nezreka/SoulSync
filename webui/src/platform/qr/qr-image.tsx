/**
 * A QR code as an inline SVG, drawn from the vendored generator
 * (qrcodegen.ts). Dark modules on a light square with a 4-module quiet
 * zone, so a phone camera reads it whatever the page theme.
 */

import { Ecc, QrCode } from './qrcodegen';

const QUIET = 4;

/** the code's modules as one SVG path, and the side of its square */
export function qrPath(text: string): { size: number; d: string } {
  const qr = QrCode.encodeText(text, Ecc.MEDIUM);
  const parts: string[] = [];
  for (let y = 0; y < qr.size; y++)
    for (let x = 0; x < qr.size; x++)
      if (qr.getModule(x, y)) parts.push(`M${x + QUIET},${y + QUIET}h1v1h-1z`);
  return { size: qr.size + QUIET * 2, d: parts.join('') };
}

export function QrImage({
  text,
  label,
  className,
}: {
  text: string;
  label: string;
  className?: string;
}) {
  const { size, d } = qrPath(text);
  return (
    <svg
      className={className}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
    >
      <rect width={size} height={size} style={{ fill: 'var(--text-primary)' }} />
      <path d={d} style={{ fill: 'var(--bg-primary)' }} />
    </svg>
  );
}
