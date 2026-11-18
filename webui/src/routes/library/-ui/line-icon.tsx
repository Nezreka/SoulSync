/** The library page's stroke icons, for the buttons that used emoji. Same
 *  paths as the page's own icon set, so a toolbar reads as one family. */
const PATHS = {
  radio: 'M4 10h16v10H4zM7 10l10-5M8.5 15h.01M13 14h4M13 17h4',
  download: 'M12 3v12M8 11l4 4 4-4M5 21h14',
  filter: 'M4 6h16M7 12h10M10 18h4',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5',
  flame:
    'M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1.1-2.1-.2-4.1 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3a2.5 2.5 0 0 0 2.5 2.5z',
} as const;

export function LineIcon({ name, size = 15 }: { name: keyof typeof PATHS; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flexShrink: 0, verticalAlign: '-2px' }}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
